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


def _normalize_punct(text):
    return (
        str(text or "")
        .replace("。;", "。")
        .replace("；;", "；")
        .replace(".;", ".")
        .replace(";;", ";")
    )


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


def _extract_angle_line(raw, lang="zh", limit=60):
    text = _strip_top3_prefixes(raw)
    if not text:
        return ""
    text = _normalize_punct(text)
    text = text.replace("\r", "\n")
    parts = [seg.strip() for seg in re.split(r"[•\n]+", text) if seg.strip()]
    if parts:
        text = parts[0]
    text = re.sub(r"^[\-\*\u2022]+\s*", "", text)
    text = text.strip(" \t-—:：")
    text = re.sub(r"\s+", " ", text)

    if lang == "zh":
        for sep in ("。", "；", "！", "？"):
            if sep in text:
                text = text.split(sep)[0].strip()
                break
    else:
        for sep in (".", ";", "!", "?"):
            if sep in text:
                text = text.split(sep)[0].strip()
                break

    if limit and len(text) > limit:
        text = text[:limit].rstrip("，。;；, .") + "…"
    return text


def _angle_meta(report_id, idx, lang="zh"):
    rid = str(report_id or "").lower()
    if rid.endswith("-policy") or rid.endswith("_policy"):
        return ("📜", "政策" if lang == "zh" else "Policy")
    if rid.endswith("-industry") or rid.endswith("_industry"):
        return ("🏭", "产业" if lang == "zh" else "Industry")
    if rid.endswith("-capital") or rid.endswith("_capital"):
        return ("💰", "资本" if lang == "zh" else "Capital")

    fallback_zh = [("📜", "信号一"), ("🏭", "信号二"), ("💰", "信号三")]
    fallback_en = [("📜", "Signal 1"), ("🏭", "Signal 2"), ("💰", "Signal 3")]
    fallback = fallback_zh if lang == "zh" else fallback_en
    if idx < len(fallback):
        return fallback[idx]
    return ("🔹", "要点" if lang == "zh" else "Signal")


def _pick_primary_report_id(top_items, valid_report_ids):
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


def _build_home_aggregate_html(top3_data, valid_report_ids, lang="zh"):
    items = top3_data.get("items") if isinstance(top3_data, dict) else []
    if not isinstance(items, list) or not items:
        if lang == "zh":
            return '<div class="meta">暂无重点追踪</div>'
        return '<div class="meta">No top track yet.</div>'

    if lang == "zh":
        topic_title = "今日重点专题"
        reason_label = "要点"
        preview_label = "报告包含"
        btn_label = "解锁深度报告"
        subdir = "cn"
        sep = "："
        line_limit = 60
        preview_limit = 110
        raw_key_value = "value_zh"
        raw_key_preview = "report_contains_zh"
        raw_key_title = "title_zh"
        fallback_title = "title"
    else:
        topic_title = "Today's Focus"
        reason_label = "Key signals"
        preview_label = "Includes"
        btn_label = "Unlock Deep Report"
        subdir = "en"
        sep = ": "
        line_limit = 140
        preview_limit = 180
        raw_key_value = "value_en"
        raw_key_preview = "report_contains_en"
        raw_key_title = "title_en"
        fallback_title = "title"

    reasons = []
    preview_text = ""

    for idx, item in enumerate(items[:3]):
        raw = (
            item.get(raw_key_value)
            or item.get(raw_key_preview)
            or item.get(raw_key_title)
            or item.get(fallback_title)
            or ""
        )
        line = _extract_angle_line(raw, lang=lang, limit=line_limit)
        if not line:
            line = _extract_angle_line(
                item.get(raw_key_title) or item.get(fallback_title) or "",
                lang=lang,
                limit=line_limit,
            )
        emoji, label = _angle_meta(item.get("report_id"), idx, lang=lang)
        if line:
            reasons.append(
                f'<div class="premium-reason"><span class="label-gray">{html_lib.escape(emoji + " " + label)}</span>{sep}{html_lib.escape(line)}</div>'
            )
        else:
            reasons.append(
                f'<div class="premium-reason"><span class="label-gray">{html_lib.escape(emoji + " " + label)}</span></div>'
            )

        if not preview_text:
            candidate_preview = _extract_angle_line(
                item.get(raw_key_preview) or "",
                lang=lang,
                limit=preview_limit,
            )
            preview_text = candidate_preview

    preview_html = ""
    if preview_text:
        preview_html = (
            f'<div class="premium-preview"><span class="label-gray">{preview_label}</span>{sep}{html_lib.escape(preview_text)}</div>'
        )

    primary_report_id = _pick_primary_report_id(items, valid_report_ids)
    if primary_report_id:
        unlock = f"/{subdir}/pay/{primary_report_id}.html"
    else:
        unlock = "mailto:sentinel@dataspices.com"

    reasons_html = "".join(reasons)
    return (
        '<div class="premium-item premium-aggregate">'
        f'<div class="premium-topic">{html_lib.escape(topic_title)}</div>'
        f'<div class="premium-reason"><span class="label-gray">{reason_label}</span></div>'
        f'{reasons_html}'
        f'{preview_html}'
        f'<a class="premium-btn" href="{html_lib.escape(unlock)}">{html_lib.escape(btn_label)}</a>'
        '</div>'
    )


def _replace_top_tracks_section(raw_html, section_title, aggregate_html):
    pattern = re.compile(
        r'(<section class="section">\s*<div class="section-title">'
        + re.escape(section_title)
        + r"</div>)(.*?)(</section>)",
        re.S,
    )
    if not pattern.search(raw_html):
        return raw_html, False
    replaced = pattern.sub(
        lambda m: f"{m.group(1)}\n    {aggregate_html}\n  {m.group(3)}",
        raw_html,
        count=1,
    )
    return replaced, True


def _postprocess_home_top_tracks(config):
    top3_path = os.path.join(config.data_dir, "status", "top3_list.json")
    top3_data = _load_json_dict(top3_path)
    if not top3_data.get("items"):
        return

    reports_index_path = os.path.join(config.premium_dir, "reports_index.jsonl")
    valid_report_ids = _load_reports_index_ids(reports_index_path)

    aggregate_cn = _build_home_aggregate_html(
        top3_data, valid_report_ids, lang="zh"
    )
    aggregate_en = _build_home_aggregate_html(
        top3_data, valid_report_ids, lang="en"
    )

    targets = [
        (os.path.join(config.static_dir, "index.html"), "zh", "今日重点追踪（Top 3）", aggregate_cn),
        (os.path.join(config.static_dir, "cn", "index.html"), "zh", "今日重点追踪（Top 3）", aggregate_cn),
        (os.path.join(config.static_dir, "en", "index.html"), "en", "Top Tracks (Top 3)", aggregate_en),
    ]

    for path, _lang, title, aggregate_html in targets:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw_html = f.read()
        except OSError:
            continue

        updated_html, replaced = _replace_top_tracks_section(
            raw_html, title, aggregate_html
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
    args = parser.parse_args()

    config = Config.from_env()

    if args.rebuild:
        _run_rebuild(config)
        return

    if args.build_datasets:
        _run_build_datasets(config)
        return

    run_pipeline(config)


if __name__ == "__main__":
    main()
