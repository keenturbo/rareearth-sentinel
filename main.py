import argparse
import html as html_lib
import json
import os
import re
import signal
import time
import traceback
from dataclasses import replace
from urllib.parse import urlparse

from src.config import Config
from src.sources.registry import get_sources
from src.fetcher import Fetcher
from src.extractor import GeminiExtractor
from src.storage import Storage
from src.reporter import Reporter
from src.context import ContextBuilder, VectorContextBuilder
from src.embeddings import EmbeddingStore
from src.normalizer import Normalizer
from src.utils import clean_text, isoformat, now_cst, is_homepage_url
from src.datasets import build_datasets
from src.premium import generate_daily_briefs, backfill_daily_briefs
from src.telegram import post_telegram_records, post_telegram_top3_due

try:
    from src.social_copy_ai import generate_social_copy_bundle
except Exception:
    generate_social_copy_bundle = None

CANDIDATE_CONFIDENCE_MIN = 60
CANDIDATE_LEN_MIN = 2
CANDIDATE_LEN_MAX = 80

_TOP3_PREFIXES = [
    "要点：",
    "要点:",
    "要点",
    "今日要点：",
    "今日要点",
    "TL;DR:",
    "TL;DR",
    "Key angles:",
    "Key angles",
    "Key points:",
    "Key points",
    "Highlights:",
    "Highlights",
]


def _context_lines(context_summary, limit=0):
    if not context_summary:
        return []
    lines = []
    for raw in context_summary.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("-"):
            line = line[1:].strip()
        if line:
            lines.append(line)
        if limit and len(lines) >= limit:
            break
    return lines


def _load_candidate_seen(path):
    seen = set()
    if not os.path.exists(path):
        return seen
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            keyword = str(data.get("keyword", "")).strip().lower()
            if keyword:
                seen.add(keyword)
    return seen


def _has_keyword_chars(text):
    return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", text))


def _guess_keyword_type(name):
    lower = name.lower()
    if any(
        token in lower
        for token in (
            "project",
            "mine",
            "deposit",
            "basin",
            "plant",
            "refinery",
            "smelter",
            "processing site",
        )
    ):
        return "project"
    if any(token in name for token in ("项目", "矿区", "矿床", "矿山")):
        return "project"
    if any(
        token in lower
        for token in (
            "ministry",
            "government",
            "agency",
            "bank",
            "fund",
            "commission",
            "authority",
            "institute",
            "university",
            "bureau",
            "administration",
            "council",
            "department",
        )
    ):
        return "institution"
    if any(
        token in name
        for token in ("研究院", "大学", "学院", "基金", "银行", "政府", "委员会")
    ):
        return "institution"
    return "company"


def _append_keyword_candidates(
    record,
    config,
    seen,
    min_confidence=CANDIDATE_CONFIDENCE_MIN,
):
    try:
        confidence = int(record.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        confidence = 0
    if confidence < min_confidence:
        return 0

    entities = record.get("entities") or []
    if isinstance(entities, str):
        entities = [
            seg.strip()
            for seg in re.split(r"[\n,;]+", entities)
            if seg.strip()
        ]
    elif not isinstance(entities, list):
        entities = []

    cleaned = []
    for item in entities:
        text = str(item).strip()
        if not text:
            continue
        if len(text) < CANDIDATE_LEN_MIN or len(text) > CANDIDATE_LEN_MAX:
            continue
        if not _has_keyword_chars(text):
            continue
        cleaned.append(text)

    if not cleaned:
        return 0

    candidates = []
    created_at = isoformat(now_cst())
    for keyword in cleaned:
        key = keyword.lower()
        if key in seen:
            continue
        candidates.append(
            {
                "keyword": keyword,
                "type": _guess_keyword_type(keyword),
                "record_id": record.get("id", ""),
                "title": record.get("title", ""),
                "confidence": confidence,
                "created_at": created_at,
            }
        )
        seen.add(key)

    if not candidates:
        return 0

    path = os.path.join(
        config.data_dir, "keywords", "keyword_candidates.jsonl"
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for entry in candidates:
            f.write(json.dumps(entry, ensure_ascii=False))
            f.write("\n")
    return len(candidates)


def _normalize_title_key(text):
    if not text:
        return ""
    text = re.sub(r"\s+", " ", str(text)).strip().lower()
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", text)
    return text


def _company_key(record):
    if not record:
        return ""
    company = str(record.get("company", "")).strip()
    if not company:
        entities = record.get("entities") or []
        if isinstance(entities, str):
            entities = [
                seg.strip()
                for seg in re.split(r"[，,;；\n]+", entities)
                if seg.strip()
            ]
        elif not isinstance(entities, list):
            entities = []
        if entities:
            company = str(entities[0]).strip()
    return _normalize_title_key(company)


def _entry_title(entry, record):
    preview_zh = entry.get("preview_zh") or {}
    preview_en = entry.get("preview_en") or {}
    title = (
        preview_zh.get("title")
        or preview_en.get("title")
        or entry.get("source_title")
        or (record.get("title") if record else "")
    )
    return str(title or "").strip()


def _source_key(record, entry):
    url = ""
    if record:
        url = record.get("resolved_url") or record.get("url") or ""
    domain = ""
    if url:
        domain = urlparse(url).netloc.lower().lstrip("www.")
    if not domain:
        domain = str(entry.get("official_source_id") or "").strip().lower()
    return domain


def _entry_impact_score(entry):
    for key in ("impact_score", "impact_score_zh", "impact_score_en"):
        value = entry.get(key)
        if value in ("", None):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _entry_risk_score(entry, record_map):
    event_id = entry.get("event_id")
    if not event_id:
        return 0
    record = record_map.get(event_id)
    if not record:
        return 0
    try:
        return int(record.get("risk_score", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _dedupe_entries(entries, record_map, limit=3):
    seen_company = set()
    seen_title = set()
    seen_source = set()
    deduped = []
    for entry in entries:
        record = (
            record_map.get(entry.get("event_id"))
            if entry.get("event_id")
            else None
        )
        company = _company_key(record)
        title_key = _normalize_title_key(_entry_title(entry, record))
        source = _source_key(record, entry)
        if (company and company in seen_company) or (
            title_key and title_key in seen_title
        ) or (source and source in seen_source):
            continue
        if company:
            seen_company.add(company)
        if title_key:
            seen_title.add(title_key)
        if source:
            seen_source.add(source)
        deduped.append(entry)
        if limit and len(deduped) >= limit:
            break
    return deduped


def _select_major_event_entries(entries, record_map):
    candidates = []
    for entry in entries:
        event_id = entry.get("event_id")
        if not event_id:
            continue
        risk_score = _entry_risk_score(entry, record_map)
        impact_score = _entry_impact_score(entry)
        if risk_score >= 85 or impact_score >= 9:
            candidates.append((risk_score, impact_score, event_id))
    if not candidates:
        return []
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    major_event_id = candidates[0][2]
    view_entries = [
        entry
        for entry in entries
        if entry.get("event_id") == major_event_id
        and entry.get("view") in ("policy", "industry", "capital")
    ]
    if len(view_entries) < 3:
        return []
    order = {"policy": 0, "industry": 1, "capital": 2}
    view_entries.sort(key=lambda e: order.get(e.get("view"), 99))
    return view_entries[:3]


def _select_top3_entries(entries, record_map, limit=3):
    major_entries = _select_major_event_entries(entries, record_map)
    if major_entries:
        return major_entries
    return _dedupe_entries(entries, record_map, limit=limit)


def _build_top3_items(entries, record_map):
    items = []
    for entry in entries[:3]:
        event_id = entry.get("event_id")
        report_id = entry.get("report_id")
        if not event_id or not report_id:
            continue
        preview_zh = entry.get("preview_zh") or {}
        preview_en = entry.get("preview_en") or {}
        value_zh = preview_zh.get("value") or ""
        report_contains_zh = preview_zh.get("preview") or ""
        value_en = preview_en.get("value") or ""
        report_contains_en = preview_en.get("preview") or ""
        title_zh = str(preview_zh.get("title") or "").strip()
        title_en = str(preview_en.get("title") or "").strip()
        record = record_map.get(event_id)
        if record:
            if not title_zh:
                title_zh = str(record.get("title", "")).strip()
            title = (
                title_zh
                or title_en
                or str(record.get("title", "")).strip()
                or str(entry.get("source_title", "")).strip()
            )
            summary_zh = record.get("summary_zh", "")
            summary_en = record.get("summary_en", "")
            expert_summary = record.get("expert_summary", "")
            expert_summary_en = record.get("expert_summary_en", "")
            country = record.get("country_primary", "")
            action_type = record.get("action_type", "")
            risk_level = record.get("risk_level", "")
            risk_score = record.get("risk_score", "")
            impact_score = record.get("impact_score", "")
            published_at = record.get("published_at", "")
            collected_at = record.get("collected_at", "")
        else:
            if not title_zh:
                title_zh = str(entry.get("source_title", "")).strip()
            title = (
                title_zh
                or title_en
                or str(entry.get("source_title", "")).strip()
            )
            summary_zh = (
                preview_zh.get("preview")
                or preview_zh.get("value")
                or ""
            )
            summary_en = (
                preview_en.get("preview")
                or preview_en.get("value")
                or ""
            )
            expert_summary = preview_zh.get("value") or ""
            expert_summary_en = preview_en.get("value") or ""
            country = entry.get("country", "")
            action_type = entry.get("action_type", "")
            risk_level = entry.get("risk_level", "")
            risk_score = entry.get("risk_score", "") or entry.get(
                "importance_score", ""
            )
            impact_score = (
                entry.get("impact_score", "")
                or entry.get("impact_score_zh", "")
                or entry.get("impact_score_en", "")
            )
            published_at = entry.get("published_at", "")
            collected_at = entry.get("created_at", "")
        items.append(
            {
                "report_id": report_id,
                "event_id": event_id,
                "title": title,
                "title_zh": title_zh,
                "title_en": title_en,
                "summary_zh": summary_zh,
                "summary_en": summary_en,
                "expert_summary": expert_summary,
                "expert_summary_en": expert_summary_en,
                "value_zh": value_zh,
                "report_contains_zh": report_contains_zh,
                "value_en": value_en,
                "report_contains_en": report_contains_en,
                "country": country,
                "action_type": action_type,
                "risk_level": risk_level,
                "risk_score": risk_score,
                "impact_score": impact_score,
                "published_at": published_at,
                "collected_at": collected_at,
            }
        )
    return items


def _write_top3_list(config, items, date_key):
    path = os.path.join(config.data_dir, "status", "top3_list.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "date": date_key,
        "generated_at": isoformat(now_cst()),
        "items": items,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _load_json_dict(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_reports_index_ids(path):
    ids = set()
    if not os.path.exists(path):
        return ids
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            report_id = str(row.get("report_id") or "").strip()
            if report_id:
                ids.add(report_id)
    return ids


def _normalize_punct_topic(text):
    cleaned = str(text or "")
    replacements = [
        ("。;", "。"),
        ("；;", "；"),
        (".;", "."),
        (";;", ";"),
        ("。；", "。"),
        ("；。", "。"),
    ]
    for src, dst in replacements:
        cleaned = cleaned.replace(src, dst)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _clip_text(text, limit):
    text = str(text or "").strip()
    if not text or not limit or len(text) <= limit:
        return text
    return text[:limit].rstrip("，。;；, .") + "…"


def _to_int_topic(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_compare_topic(text):
    return re.sub(r"[^\w\u4e00-\u9fff]", "", str(text or "").lower())


def _is_similar_topic(a, b):
    na = _normalize_compare_topic(a)
    nb = _normalize_compare_topic(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    sa = set(na)
    sb = set(nb)
    union = sa | sb
    if not union:
        return False
    return (len(sa & sb) / len(union)) >= 0.72


def _strip_top3_prefixes(text):
    raw = str(text or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    for prefix in _TOP3_PREFIXES:
        if lowered.startswith(prefix.lower()):
            raw = raw[len(prefix):].lstrip(" :：-—•*\t")
            break
    raw = re.sub(r"^[\-\*\u2022]+\s*", "", raw)
    return raw.strip()


def _extract_angle_candidates(raw, lang="zh", limit=60):
    text = _normalize_punct_topic(_strip_top3_prefixes(raw))
    if not text:
        return []

    pieces = []
    for seg in re.split(r"[•\n]+", text):
        seg = seg.strip()
        if not seg:
            continue
        seg = re.sub(
            r"^(政策|产业|资本|policy|industry|capital|signal\s*\d+)\s*[:：-]\s*",
            "",
            seg,
            flags=re.I,
        )
        seg = seg.strip(" -—:：")
        if not seg:
            continue
        if lang == "zh":
            seg = re.split(r"[。！？；]", seg)[0].strip()
        else:
            seg = re.split(r"[.!?;]", seg)[0].strip()
        if not seg:
            continue
        pieces.append(_clip_text(seg, limit))

    if not pieces:
        text = text.strip(" -—:：")
        if text:
            pieces.append(_clip_text(text, limit))

    deduped = []
    for p in pieces:
        if not any(_is_similar_topic(p, q) for q in deduped):
            deduped.append(p)
    return deduped


def _view_from_report_id_topic(report_id):
    rid = str(report_id or "").lower()
    if rid.endswith("-policy") or rid.endswith("_policy"):
        return "policy"
    if rid.endswith("-industry") or rid.endswith("_industry"):
        return "industry"
    if rid.endswith("-capital") or rid.endswith("_capital"):
        return "capital"
    return ""


def _angle_meta(view, idx, lang="zh"):
    if view == "policy":
        return ("📜", "政策" if lang == "zh" else "Policy")
    if view == "industry":
        return ("🏭", "产业" if lang == "zh" else "Industry")
    if view == "capital":
        return ("💰", "资本" if lang == "zh" else "Capital")

    fallback_zh = [("📜", "信号一"), ("🏭", "信号二"), ("💰", "信号三")]
    fallback_en = [("📜", "Signal 1"), ("🏭", "Signal 2"), ("💰", "Signal 3")]
    fallback = fallback_zh if lang == "zh" else fallback_en
    if idx < len(fallback):
        return fallback[idx]
    return ("🔹", "要点" if lang == "zh" else "Signal")


def _collect_item_candidates(item, lang="zh", line_limit=60):
    if lang == "zh":
        keys = ["value_zh", "report_contains_zh", "title_zh", "title"]
    else:
        keys = ["value_en", "report_contains_en", "title_en", "title"]
    candidates = []
    for key in keys:
        value = item.get(key)
        if not value:
            continue
        candidates.extend(_extract_angle_candidates(value, lang=lang, limit=line_limit))
    deduped = []
    for c in candidates:
        if c and not any(_is_similar_topic(c, x) for x in deduped):
            deduped.append(c)
    return deduped


def _pick_distinct_line(candidates, used_lines):
    for c in candidates:
        if c and not any(_is_similar_topic(c, u) for u in used_lines):
            used_lines.append(c)
            return c
    if candidates:
        chosen = candidates[0]
        used_lines.append(chosen)
        return chosen
    return ""


def _pick_primary_report_id_topic(top_items, valid_report_ids):
    ids = []
    for item in top_items:
        report_id = str((item or {}).get("report_id") or "").strip()
        if report_id:
            ids.append(report_id)
    if not ids:
        return ""
    if valid_report_ids:
        for report_id in ids:
            if report_id in valid_report_ids:
                return report_id
    return ids[0]


def _extract_topic_tokens(text, lang="zh"):
    raw = str(text or "")
    tokens = set()
    if lang == "zh":
        for t in re.findall(r"[\u4e00-\u9fff]{2,6}", raw):
            if t not in {"今日", "重点", "专题", "报告", "包含", "信号", "建议", "机会", "窗口"}:
                tokens.add(t)
    for t in re.findall(r"[A-Za-z]{4,}", raw.lower()):
        if t not in {"today", "focus", "report", "includes", "signal", "window", "action"}:
            tokens.add(t)
    return tokens


def _iter_daily_actions(daily_report):
    if not isinstance(daily_report, dict):
        return []
    keys = [
        "strategy",
        "strategic",
        "recommendations_strategy",
        "tactics",
        "tactical",
        "recommendations_tactical",
        "action_items",
        "bullets",
    ]
    out = []
    for key in keys:
        value = daily_report.get(key)
        if isinstance(value, list):
            for item in value:
                text = str(item or "").strip()
                if text:
                    out.append(text)
        elif isinstance(value, str):
            for seg in re.split(r"[\n;；]+", value):
                seg = seg.strip()
                if seg:
                    out.append(seg)
    return out


def _pick_opportunity_window(daily_report, topic_text, lang="zh"):
    candidates = _iter_daily_actions(daily_report)
    if not candidates:
        return ""
    topic_tokens = _extract_topic_tokens(topic_text, lang=lang)

    def score_line(line):
        norm = _normalize_compare_topic(line)
        score = 0
        for token in topic_tokens:
            if _normalize_compare_topic(token) in norm:
                score += 1
        return score

    best = None
    best_score = -1
    for line in candidates:
        s = score_line(line)
        if s > best_score:
            best = line
            best_score = s

    selected = best or candidates[0]
    selected = _normalize_punct_topic(selected)
    return _clip_text(selected, 66 if lang == "zh" else 140)


def _build_evidence_anchor(items, lang="zh"):
    anchors = []
    title_keys = ["title_zh", "title"] if lang == "zh" else ["title_en", "title"]
    for item in items[:3]:
        title = ""
        for key in title_keys:
            value = str(item.get(key) or "").strip()
            if value:
                title = value
                break
        if not title:
            continue
        title = _clip_text(_normalize_punct_topic(title), 24 if lang == "zh" else 56)
        if title and not any(_is_similar_topic(title, a) for a in anchors):
            anchors.append(title)
        if len(anchors) >= 2:
            break
    if lang == "zh":
        return "；".join(anchors)
    return "; ".join(anchors)


def _select_top1_item_topic(items):
    if not items:
        return None
    pref = {"policy": 2, "industry": 1, "capital": 0}

    def key_fn(item):
        risk = _to_int_topic(item.get("risk_score"))
        impact = _to_int_topic(item.get("impact_score"))
        view = _view_from_report_id_topic(item.get("report_id"))
        return (risk, impact, pref.get(view, -1))

    return sorted(items, key=key_fn, reverse=True)[0]


def _build_home_top1_fallback(item, valid_report_ids, lang="zh"):
    if not item:
        if lang == "zh":
            return '<div class="meta">暂无重点追踪</div>'
        return '<div class="meta">No top track yet.</div>'

    if lang == "zh":
        core_label = "核心结论"
        signal_label = "核心信号"
        preview_label = "报告包含"
        btn_label = "解锁深度报告"
        subdir = "cn"
        sep = "："
        raw_value = "value_zh"
        raw_preview = "report_contains_zh"
        raw_title = "title_zh"
        line_limit = 62
    else:
        core_label = "Conclusion"
        signal_label = "Key signal"
        preview_label = "Includes"
        btn_label = "Unlock Deep Report"
        subdir = "en"
        sep = ": "
        raw_value = "value_en"
        raw_preview = "report_contains_en"
        raw_title = "title_en"
        line_limit = 140

    core = _extract_angle_candidates(item.get(raw_title) or item.get("title"), lang=lang, limit=line_limit)
    core_text = core[0] if core else (item.get("title") or "")

    signal_candidates = _extract_angle_candidates(
        item.get(raw_value) or item.get(raw_preview) or item.get("title"),
        lang=lang,
        limit=line_limit,
    )
    signal_text = signal_candidates[0] if signal_candidates else ""

    preview_candidates = _extract_angle_candidates(item.get(raw_preview), lang=lang, limit=90 if lang == "zh" else 160)
    preview_text = preview_candidates[0] if preview_candidates else ""

    primary_report_id = _pick_primary_report_id_topic([item], valid_report_ids)
    unlock = f"/{subdir}/pay/{primary_report_id}.html" if primary_report_id else "mailto:sentinel@dataspices.com"

    preview_html = ""
    if preview_text:
        preview_html = f'<div class="premium-preview"><span class="label-gray">{preview_label}</span>{sep}{html_lib.escape(preview_text)}</div>'

    return (
        '<div class="premium-item premium-aggregate">'
        f'<div class="premium-reason"><span class="label-gray">{core_label}</span>{sep}{html_lib.escape(core_text)}</div>'
        f'<div class="premium-reason"><span class="label-gray">{signal_label}</span>{sep}{html_lib.escape(signal_text)}</div>'
        f'{preview_html}'
        f'<a class="premium-btn" href="{html_lib.escape(unlock)}">{html_lib.escape(btn_label)}</a>'
        '</div>'
    )


def _build_home_aggregate_html(top3_data, valid_report_ids, daily_report, lang="zh"):
    items = top3_data.get("items") if isinstance(top3_data, dict) else []
    if not isinstance(items, list) or not items:
        if lang == "zh":
            return '<div class="meta">暂无重点追踪</div>'
        return '<div class="meta">No top track yet.</div>'

    if lang == "zh":
        core_label = "核心结论"
        view_label = "三视角"
        opp_label = "机会窗口"
        anchor_label = "证据锚点"
        preview_label = "报告包含"
        btn_label = "解锁深度报告"
        subdir = "cn"
        sep = "："
        line_limit = 60
        preview_limit = 96
        min_len = 260
        max_len = 420
        raw_key_value = "value_zh"
        raw_key_preview = "report_contains_zh"
        raw_key_title = "title_zh"
    else:
        core_label = "Conclusion"
        view_label = "Three angles"
        opp_label = "Opportunity"
        anchor_label = "Evidence"
        preview_label = "Includes"
        btn_label = "Unlock Deep Report"
        subdir = "en"
        sep = ": "
        line_limit = 140
        preview_limit = 170
        min_len = 220
        max_len = 620
        raw_key_value = "value_en"
        raw_key_preview = "report_contains_en"
        raw_key_title = "title_en"

    ordered_views = ["policy", "industry", "capital"]
    candidates_by_view = {"policy": [], "industry": [], "capital": []}
    pool = []
    used = []

    for item in items[:3]:
        view = _view_from_report_id_topic(item.get("report_id"))
        candidates = _collect_item_candidates(item, lang=lang, line_limit=line_limit)
        if view in candidates_by_view:
            candidates_by_view[view].extend(candidates)
        pool.extend(candidates)

    view_lines = []
    for idx, view in enumerate(ordered_views):
        merged_candidates = candidates_by_view.get(view, []) + pool
        line = _pick_distinct_line(merged_candidates, used)
        if not line:
            fallback_title = _extract_angle_candidates(
                (items[idx].get(raw_key_title) if idx < len(items) else "")
                or (items[idx].get("title") if idx < len(items) else ""),
                lang=lang,
                limit=line_limit,
            )
            line = fallback_title[0] if fallback_title else ""
        emoji, label = _angle_meta(view, idx, lang=lang)
        view_lines.append((emoji, label, line))

    if sum(1 for _, _, line in view_lines if line) < 2:
        return _build_home_top1_fallback(_select_top1_item_topic(items), valid_report_ids, lang=lang)

    core_candidates = _extract_angle_candidates(
        items[0].get(raw_key_title)
        or items[0].get(raw_key_value)
        or items[0].get("title"),
        lang=lang,
        limit=74 if lang == "zh" else 150,
    )
    core_text = core_candidates[0] if core_candidates else (items[0].get("title") or "")

    topic_text = " ".join([core_text] + [line for _, _, line in view_lines if line])
    opp_text = _pick_opportunity_window(daily_report, topic_text, lang=lang)
    anchor_text = _build_evidence_anchor(items, lang=lang)

    preview_text = ""
    preview_candidates = _extract_angle_candidates(
        items[0].get(raw_key_preview) or "",
        lang=lang,
        limit=preview_limit,
    )
    if preview_candidates:
        preview_text = preview_candidates[0]

    approx_len = len(core_text) + sum(len(line) for _, _, line in view_lines)
    approx_len += len(opp_text) + len(anchor_text) + len(preview_text)

    if approx_len > max_len:
        core_text = _clip_text(core_text, 64 if lang == "zh" else 130)
        view_lines = [
            (emoji, label, _clip_text(line, 50 if lang == "zh" else 120))
            for emoji, label, line in view_lines
        ]
        opp_text = _clip_text(opp_text, 52 if lang == "zh" else 120)
        anchor_text = _clip_text(anchor_text, 44 if lang == "zh" else 110)
        if lang == "zh":
            preview_text = ""

    if approx_len < min_len and not preview_text:
        preview_candidates = _extract_angle_candidates(
            items[0].get(raw_key_value) or "",
            lang=lang,
            limit=preview_limit,
        )
        if preview_candidates:
            preview_text = preview_candidates[0]

    primary_report_id = _pick_primary_report_id_topic(items, valid_report_ids)
    unlock = f"/{subdir}/pay/{primary_report_id}.html" if primary_report_id else "mailto:sentinel@dataspices.com"

    lines_html = []
    lines_html.append(
        f'<div class="premium-reason"><span class="label-gray">{core_label}</span>{sep}{html_lib.escape(_normalize_punct_topic(core_text))}</div>'
    )
    lines_html.append(
        f'<div class="premium-reason"><span class="label-gray">{view_label}</span></div>'
    )
    for emoji, label, line in view_lines:
        lines_html.append(
            f'<div class="premium-reason"><span class="label-gray">{html_lib.escape(emoji + " " + label)}</span>{sep}{html_lib.escape(_normalize_punct_topic(line))}</div>'
        )

    if opp_text:
        lines_html.append(
            f'<div class="premium-reason"><span class="label-gray">{opp_label}</span>{sep}{html_lib.escape(_normalize_punct_topic(opp_text))}</div>'
        )
    if anchor_text:
        lines_html.append(
            f'<div class="premium-preview"><span class="label-gray">{anchor_label}</span>{sep}{html_lib.escape(_normalize_punct_topic(anchor_text))}</div>'
        )
    if preview_text:
        lines_html.append(
            f'<div class="premium-preview"><span class="label-gray">{preview_label}</span>{sep}{html_lib.escape(_normalize_punct_topic(preview_text))}</div>'
        )

    return (
        '<div class="premium-item premium-aggregate">'
        f'{"".join(lines_html)}'
        f'<a class="premium-btn" href="{html_lib.escape(unlock)}">{html_lib.escape(btn_label)}</a>'
        '</div>'
    )


def _build_home_ai_topic_html(topic_text, items, valid_report_ids, lang="zh"):
    raw = str(topic_text or "").strip()
    if not raw:
        return ""

    lines = []
    for line in raw.splitlines():
        s = _normalize_punct_topic(line).strip()
        if not s:
            continue
        low = s.lower()
        if re.search(r"https?://", s):
            continue
        if s.startswith("#"):
            continue
        if s.startswith("私信回复") or low.startswith("dm for full report"):
            continue
        if low in {"today's focus", "todays focus", "top tracks"}:
            continue
        if re.search(r"^(要点|今日要点|三视角)\s*[:：]?", s):
            continue
        if re.search(r"^(TL\s*;?\s*DR|Key\s*angles?|Key\s*points?)\s*[:：]?", s, re.I):
            continue
        if s.startswith("【") and s.endswith("】"):
            continue
        s = s.replace("...", "").replace("…", "").strip()
        if s:
            lines.append(s)

    if not lines:
        return ""

    norm_lines = []
    for s in lines:
        label = ""
        body = ""
        if "：" in s:
            label, body = s.split("：", 1)
            label = label.strip()
            body = body.strip()
            if not label or not body:
                label = ""
        elif ":" in s and lang == "en":
            label, body = s.split(":", 1)
            label = label.strip()
            body = body.strip()
            if not label or not body:
                label = ""
        if label:
            norm_lines.append((label, body))
        else:
            norm_lines.append(("", s))

    dedup = []
    used = []
    for k, v in norm_lines:
        text = f"{k}:{v}" if k else v
        if any(_is_similar_topic(text, u) for u in used):
            continue
        used.append(text)
        dedup.append((k, v))

    if lang == "zh":
        subdir = "cn"
        btn_label = "解锁深度报告"
        sep = "："
    else:
        subdir = "en"
        btn_label = "Unlock Deep Report"
        sep = ": "

    primary_report_id = _pick_primary_report_id_topic(items or [], valid_report_ids)
    unlock = f"/{subdir}/pay/{primary_report_id}.html" if primary_report_id else "mailto:sentinel@dataspices.com"

    out = []
    for k, v in dedup[:8]:
        if k:
            out.append(
                f'<div class="premium-reason"><span class="label-gray">{html_lib.escape(k)}</span>{sep}{html_lib.escape(v)}</div>'
            )
        else:
            out.append(f'<div class="premium-reason">{html_lib.escape(v)}</div>')

    return (
        '<div class="premium-item premium-aggregate">'
        + ''.join(out)
        + f'<a class="premium-btn" href="{html_lib.escape(unlock)}">{html_lib.escape(btn_label)}</a>'
        + '</div>'
    )


def _replace_top_tracks_section(raw_html, title_candidates, new_title, aggregate_html):
    for title in title_candidates:
        pattern = re.compile(
            r'(<section class="section">\s*<div class="section-title">)'
            + re.escape(title)
            + r'(</div>)(.*?)(</section>)',
            re.S,
        )
        if not pattern.search(raw_html):
            continue

        replaced = pattern.sub(
            lambda m: f"{m.group(1)}{new_title}{m.group(2)}\n    {aggregate_html}\n  {m.group(4)}",
            raw_html,
            count=1,
        )
        return replaced, True
    return raw_html, False


def _postprocess_home_top_tracks(config):
    top3_path = os.path.join(config.data_dir, "status", "top3_list.json")
    top3_data = _load_json_dict(top3_path)
    if not top3_data.get("items"):
        return

    reports_index_path = os.path.join(config.premium_dir, "reports_index.jsonl")
    valid_report_ids = _load_reports_index_ids(reports_index_path)

    daily_zh = _load_json_dict(os.path.join(config.data_dir, "status", "daily_report_zh.json"))
    daily_en = _load_json_dict(os.path.join(config.data_dir, "status", "daily_report_en.json"))

    social_bundle = None
    if callable(generate_social_copy_bundle):
        try:
            social_bundle = generate_social_copy_bundle(
                config,
                top3_data,
                daily_zh,
                daily_en,
                valid_report_ids,
                timeout_seconds=8,
            )
        except Exception:
            social_bundle = None

    if not social_bundle:
        try:
            _social_cache_path = os.path.join(config.data_dir, "status", "social_copy_generated.json")
            with open(_social_cache_path, "r", encoding="utf-8") as _f:
                _cached_bundle = json.load(_f)
            if isinstance(_cached_bundle, dict) and (_cached_bundle.get("cn_topic") or _cached_bundle.get("en_topic")):
                social_bundle = _cached_bundle
        except Exception:
            pass

    top_items = top3_data.get("items") if isinstance(top3_data, dict) else []

    aggregate_cn = ""
    aggregate_en = ""
    if social_bundle:
        aggregate_cn = _build_home_ai_topic_html(
            social_bundle.get("cn_topic"), top_items, valid_report_ids, lang="zh"
        )
        aggregate_en = _build_home_ai_topic_html(
            social_bundle.get("en_topic"), top_items, valid_report_ids, lang="en"
        )

    if not aggregate_cn:
        aggregate_cn = _build_home_aggregate_html(
            top3_data, valid_report_ids, daily_zh, lang="zh"
        )
    if not aggregate_en:
        aggregate_en = _build_home_aggregate_html(
            top3_data, valid_report_ids, daily_en, lang="en"
        )

    targets = [
        (
            os.path.join(config.static_dir, "index.html"),
            ["今日重点追踪（Top 3）", "今日重点专题"],
            "今日重点专题",
            aggregate_cn,
        ),
        (
            os.path.join(config.static_dir, "cn", "index.html"),
            ["今日重点追踪（Top 3）", "今日重点专题"],
            "今日重点专题",
            aggregate_cn,
        ),
        (
            os.path.join(config.static_dir, "en", "index.html"),
            ["Top Tracks (Top 3)", "Today's Focus"],
            "Today's Focus",
            aggregate_en,
        ),
    ]

    for path, title_candidates, new_title, aggregate_html in targets:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw_html = f.read()
        except OSError:
            continue

        updated_html, replaced = _replace_top_tracks_section(
            raw_html, title_candidates, new_title, aggregate_html
        )
        if not replaced:
            continue

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(updated_html)
        except OSError:
            continue


def _build_context_builder(config, records):
    mode = (config.context_mode or "token").strip().lower()
    if mode == "vector":
        embedding_store = EmbeddingStore(config)
        embedding_store.warmup(records, limit=config.embedding_warmup_limit)
        return (
            VectorContextBuilder(
                records,
                embedding_store,
                max_items=config.context_max_items,
                lookback_days=config.context_window_days,
            ),
            embedding_store,
        )
    return (
        ContextBuilder(
            records,
            max_items=config.context_max_items,
            lookback_days=config.context_window_days,
        ),
        None,
    )


def run_pipeline(config):
    storage = Storage(config)
    seen_ids = storage.load_existing_ids()
    candidate_path = os.path.join(
        config.data_dir, "keywords", "keyword_candidates.jsonl"
    )
    candidate_seen = _load_candidate_seen(candidate_path)
    sources = get_sources(config)
    fetcher = Fetcher(config)
    extractor = GeminiExtractor(config)
    reporter = Reporter(config)

    history_records = list(storage.iter_records())
    record_map = {r.get("id"): r for r in history_records if r.get("id")}
    context_builder, _embedding_store = _build_context_builder(config, history_records)
    normalizer = Normalizer(history_records, config)

    stats = {
        "total_items": 0,
        "seen": 0,
        "fetch_fail": 0,
        "too_short": 0,
        "analyze_fail": 0,
        "analyze_en_fail": 0,
        "saved": 0,
        "summary_fallback": 0,
    }

    items_by_source = []
    total_items = 0
    for source in sources:
        items = source.fetch_items()
        items_by_source.append((source, items))
        total_items += len(items)

    stats["total_items"] = total_items
    started_at = isoformat(now_cst())
    processed_items = 0
    current_info = {}
    new_records = []
    exit_error = {"message": ""}

    def write_status(running, finished=False, error=""):
        if running and processed_items < total_items:
            current_index = processed_items + 1
        else:
            current_index = processed_items

        status = {
            "running": running,
            "started_at": started_at,
            "updated_at": isoformat(now_cst()),
            "finished_at": isoformat(now_cst()) if finished else "",
            "total_items": total_items,
            "processed_items": processed_items,
            "current_index": current_index,
            "current": current_info or {},
            "stats": stats,
            "error": error,
        }
        storage.write_status_json(status)

    def handle_signal(signum, _frame):
        exit_error["message"] = f"signal:{signum}"
        raise SystemExit(1)

    handled_signals = [signal.SIGTERM, signal.SIGINT]
    if hasattr(signal, "SIGHUP"):
        handled_signals.append(signal.SIGHUP)
    for sig in handled_signals:
        try:
            signal.signal(sig, handle_signal)
        except Exception:
            continue

    write_status(running=True)

    try:
        for source, items in items_by_source:
            print(f"[source] {source.__class__.__name__}: {len(items)} items")

            for item in items:
                title = (item.get("title") or "").strip()
                url = item.get("url")
                current_info = {
                    "title": title,
                    "url": url,
                    "source": source.__class__.__name__,
                }
                print(f"[item] {title[:120]}")
                write_status(running=True)

                try:
                    if storage.is_seen(url, seen_ids):
                        stats["seen"] += 1
                        print("  - skip: seen")
                        continue

                    fetch_result = fetcher.fetch(url)
                    content = (fetch_result.get("content") or "").strip()
                    fetch_status = fetch_result.get("status")
                    resolved_url = fetch_result.get("resolved_url")
                    content_source = "fulltext"

                    source_type = str(item.get("source_type") or "")
                    if source_type.startswith("official:"):
                        resolved_host = urlparse(resolved_url or "").netloc
                        if is_homepage_url(resolved_url) or resolved_host == "news.google.com":
                            stats["fetch_fail"] += 1
                            print("  - skip: official_homepage")
                            continue

                    summary = (item.get("summary") or "").strip()
                    fallback_text = f"{title}\n\n{summary}".strip()

                    if (not content) or (len(content) < config.min_content_chars):
                        if len(fallback_text) >= config.summary_min_chars:
                            content = fallback_text
                            content_source = "rss_summary"
                            stats["summary_fallback"] += 1
                            print("  - fallback: title+summary")
                        else:
                            if not content:
                                stats["fetch_fail"] += 1
                                print("  - skip: fetch_fail")
                            else:
                                stats["too_short"] += 1
                                print(f"  - skip: too_short ({len(content)} chars)")
                            continue

                    content = clean_text(content)

                    if content_source == "rss_summary":
                        if len(content) < config.summary_min_chars:
                            stats["too_short"] += 1
                            print(f"  - skip: too_short ({len(content)} chars)")
                            continue
                    else:
                        if len(content) < config.min_content_chars:
                            stats["too_short"] += 1
                            print(f"  - skip: too_short ({len(content)} chars)")
                            continue

                    if config.fetch_delay_seconds > 0:
                        time.sleep(config.fetch_delay_seconds)

                    context_summary = context_builder.build_context(
                        title, summary or content[:600], exclude_id=""
                    )
                    context_lines = _context_lines(
                        context_summary, limit=config.context_max_items
                    )

                    # Chinese analysis (China perspective)
                    analysis = extractor.analyze(content, context_summary=context_summary)
                    if analysis is None:
                        stats["analyze_fail"] += 1
                        print("  - skip: analyze_fail (zh)")
                        continue

                    # English analysis (Western perspective)
                    analysis_en = extractor.analyze_en(content, context_summary=context_summary)
                    if analysis_en is None:
                        stats["analyze_en_fail"] += 1
                        print("  - warn: analyze_en_fail (en), continuing with zh only")
                        analysis_en = {}

                    normalized, credibility = normalizer.normalize_and_score(
                        item, analysis, content, content_source
                    )

                    record = storage.build_record(
                        item,
                        analysis,
                        content,
                        content_source=content_source,
                        fetch_status=fetch_status,
                        resolved_url=resolved_url,
                        related_context=context_lines,
                        analysis_with_context=bool(context_summary),
                        normalized=normalized,
                        credibility=credibility,
                        analysis_en=analysis_en,
                    )
                    storage.append_record(record)
                    context_builder.add_record(record)
                    normalizer.add_record(record)
                    seen_ids.add(record["id"])
                    record_map[record["id"]] = record
                    stats["saved"] += 1
                    new_records.append(record)
                    _append_keyword_candidates(record, config, candidate_seen)
                    print("  + saved (zh + en)")

                    if config.analyze_delay_seconds > 0:
                        time.sleep(config.analyze_delay_seconds)
                finally:
                    processed_items += 1
                    write_status(running=True)

        premium_entries = []
        if config.premium_enabled:
            date_key = now_cst().strftime("%Y-%m-%d")
            try:
                premium_entries = generate_daily_briefs(
                    config, list(storage.iter_records())
                )
            except Exception as exc:
                print(f"[premium] error: {type(exc).__name__}: {exc}")
                traceback.print_exc()
                premium_entries = []
            try:
                top3_entries = _select_top3_entries(
                    premium_entries, record_map, limit=3
                )
                top3_items = _build_top3_items(top3_entries, record_map)
                _write_top3_list(config, top3_items, date_key)
            except Exception as exc:
                print(f"[top3] error: {exc}")

        reporter.rebuild(storage)
        _postprocess_home_top_tracks(config)
        try:
            posted, total = post_telegram_records(new_records, config)
            if total:
                print(f"[telegram] posted {posted}/{total}")
            posted_en, total_en = post_telegram_records(
                new_records, config, lang="en"
            )
            if total_en:
                print(f"[telegram-en] posted {posted_en}/{total_en}")
        except Exception as exc:
            print(f"[telegram] error: {exc}")
        try:
            posted_top3, total_top3 = post_telegram_top3_due(config)
            if total_top3:
                print(f"[top3] posted {posted_top3}/{total_top3}")
        except Exception as exc:
            print(f"[top3] error: {exc}")
        write_status(running=False, finished=True)
        return stats
    except BaseException as exc:
        error = exit_error["message"] or str(exc)
        write_status(running=False, finished=True, error=error)
        raise


def _run_rebuild(config):
    storage = Storage(config)
    reporter = Reporter(config)
    reporter.rebuild(storage)
    _postprocess_home_top_tracks(config)


def _run_build_datasets(config):
    storage = Storage(config)
    records = list(storage.iter_records())
    build_datasets(config, records)


def main():
    parser = argparse.ArgumentParser(description="RareEarth Sentinel")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild static pages")
    parser.add_argument(
        "--build-datasets",
        action="store_true",
        help="Generate training datasets from stored records",
    )
    parser.add_argument(
        "--build-products",
        action="store_true",
        help="Build pricing / weekly-pack / task-sprint product pages",
    )
    args = parser.parse_args()

    config = Config.from_env()

    if args.rebuild:
        _run_rebuild(config)
        return

    if args.build_datasets:
        _run_build_datasets(config)
        return

    if args.build_products:
        try:
            from scripts.build_product_pages import build_product_pages
        except Exception as exc:
            raise SystemExit(f"failed to import product page builder: {exc}")
        build_product_pages(config)
        return

    run_pipeline(config)


if __name__ == "__main__":
    main()
