import json
import os
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import yaml

from .briefs import PremiumBriefGenerator, brief_quality_score
from ..utils import now_cst


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def _today_dir(base_dir):
    today = now_cst().strftime("%Y-%m-%d")
    path = os.path.join(base_dir, today)
    _ensure_dir(path)
    return path, today


def _date_dir(base_dir, date_key):
    path = os.path.join(base_dir, date_key)
    _ensure_dir(path)
    return path


def _brief_filename(report_id, lang):
    return f"brief_{report_id}_{lang}.json"


def _brief_md_filename(report_id, lang):
    return f"brief_{report_id}_{lang}.md"


def _build_report_id(record):
    return record.get("id") or f"event_{int(now_cst().timestamp())}"


def _report_preview(brief, lang="zh"):
    if not brief:
        return {}
    title = brief.get("conclusion", "")
    value = brief.get("value_proposition", "")
    preview_text = "; ".join(brief.get("fact_checks", [])[:2])
    if lang == "en":
        if not preview_text:
            preview_text = "Includes fact checks, key numbers, and next milestones."
    else:
        if not preview_text:
            preview_text = "包含事实核查、关键数字与追踪节点。"
    return {
        "title": title,
        "value": value,
        "preview": preview_text,
    }


def _brief_to_markdown(brief, lang="zh"):
    if not brief:
        return ""
    lines = []
    if lang == "en":
        lines.append("# Event Brief")
        lines.append(f"**Conclusion**: {brief.get('conclusion','')}")
        lines.append("\n## Fact Checks")
        for item in brief.get("fact_checks", []):
            lines.append(f"- {item}")
        if brief.get("fact_sources"):
            lines.append("\n## Fact Sources")
            for item in brief.get("fact_sources", []):
                lines.append(f"- {item}")
        lines.append("\n## Key Numbers")
        for item in brief.get("key_numbers", []):
            lines.append(f"- {item}")
        lines.append("\n## Impact Score")
        lines.append(f"- {brief.get('impact_score', '')}/10")
        lines.append("\n## Tracking Nodes")
        for item in brief.get("tracking_nodes", []):
            lines.append(f"- {item}")
        if brief.get("confidence_notes"):
            lines.append("\n## Evidence Notes")
            for item in brief.get("confidence_notes", []):
                lines.append(f"- {item}")
        lines.append("\n## Why It Matters")
        lines.append(brief.get("value_proposition", ""))
    else:
        lines.append("# 事件简报")
        lines.append(f"**结论**：{brief.get('conclusion','')}")
        lines.append("\n## 事实核查")
        for item in brief.get("fact_checks", []):
            lines.append(f"- {item}")
        if brief.get("fact_sources"):
            lines.append("\n## 核查来源")
            for item in brief.get("fact_sources", []):
                lines.append(f"- {item}")
        lines.append("\n## 关键数字")
        for item in brief.get("key_numbers", []):
            lines.append(f"- {item}")
        lines.append("\n## 对中国冲击评分")
        lines.append(f"- {brief.get('impact_score', '')}/10")
        lines.append("\n## 追踪节点")
        for item in brief.get("tracking_nodes", []):
            lines.append(f"- {item}")
        if brief.get("confidence_notes"):
            lines.append("\n## 证据说明")
            for item in brief.get("confidence_notes", []):
                lines.append(f"- {item}")
        lines.append("\n## 价值说明")
        lines.append(brief.get("value_proposition", ""))
    return "\n".join(lines).strip()


def _should_generate(record, config):
    credibility = record.get("credibility", {}) or {}
    score = credibility.get("score", 0)
    importance = record.get("importance_score", 0) or 0
    return score >= config.premium_min_credibility and importance >= config.premium_min_importance


def _score_for_top(record):
    try:
        risk = int(record.get("risk_score", 0) or 0)
    except (TypeError, ValueError):
        risk = 0
    try:
        importance = int(record.get("importance_score", 0) or 0)
    except (TypeError, ValueError):
        importance = 0
    credibility = record.get("credibility", {}) or {}
    credibility_score = credibility.get("score", 0)
    source_type = str(record.get("source_type", ""))
    official_bonus = 5 if source_type.startswith("official") else 0
    return 0.4 * risk + 0.3 * importance * 20 + 0.2 * credibility_score + 0.1 * official_bonus


def _parse_date(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None


def _record_date_key(record):
    dt = _parse_date(record.get("published_at", ""))
    if not dt:
        dt = _parse_date(record.get("collected_at", ""))
    if not dt:
        return now_cst().strftime("%Y-%m-%d")
    try:
        dt = dt.astimezone(now_cst().tzinfo)
    except (ValueError, OSError):
        pass
    return dt.date().isoformat()


def _brief_exists(base_dir, date_key, report_id, lang):
    path = os.path.join(base_dir, date_key, _brief_filename(report_id, lang))
    return os.path.exists(path)


def _load_official_domains(config):
    path = config.official_sources_file
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        return {}
    entries = []
    if isinstance(data, dict):
        entries = data.get("sources", [])
    elif isinstance(data, list):
        entries = data
    domain_map = {}
    for entry in entries:
        domain = entry.get("domain")
        if not domain and entry.get("url"):
            domain = urlparse(entry.get("url")).netloc
        if not domain:
            continue
        domain = domain.lower().lstrip("www.")
        source_id = entry.get("id") or domain
        domain_map[domain] = source_id
    return domain_map


def _infer_official_source(record, domain_map=None):
    official_id = str(record.get("official_source_id", "")).strip()
    if official_id:
        return official_id
    source_type = str(record.get("source_type", "")).strip().lower()
    if source_type.startswith("official:"):
        return source_type.split(":", 1)[1]
    if source_type.startswith("official"):
        return source_type
    domain_map = domain_map or {}
    url = record.get("resolved_url") or record.get("url") or ""
    if url:
        domain = urlparse(url).netloc.lower().lstrip("www.")
        return domain_map.get(domain, "")
    return ""


def _merge_index(index_path, entries):
    existing = []
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except (OSError, json.JSONDecodeError):
            existing = []
    merged = {}
    for entry in existing:
        if isinstance(entry, dict) and entry.get("report_id"):
            merged[entry["report_id"]] = entry
    for entry in entries:
        if isinstance(entry, dict) and entry.get("report_id"):
            merged[entry["report_id"]] = entry
    merged_list = list(merged.values())
    merged_list.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(merged_list, f, ensure_ascii=False, indent=2)


def generate_daily_briefs(config, records, limit=None):
    if not config.premium_enabled:
        return []

    today = now_cst().date()
    today_records = []
    for record in records:
        collected_at = record.get("collected_at", "")
        if not collected_at:
            continue
        try:
            dt = datetime.fromisoformat(collected_at)
        except ValueError:
            continue
        if dt.date() != today:
            continue
        if not _should_generate(record, config):
            continue
        today_records.append(record)

    if not today_records:
        return []

    today_records = sorted(today_records, key=_score_for_top, reverse=True)
    limit = limit or config.premium_top_n
    top_records = today_records[:limit]

    generator = PremiumBriefGenerator(config)
    output_dir, date_key = _today_dir(config.premium_dir)
    official_map = _load_official_domains(config)

    index_entries = []
    for record in top_records:
        report_id = _build_report_id(record)
        entry = {
            "report_id": report_id,
            "event_id": record.get("id"),
            "tier": "brief",
            "price": 9,
            "date": date_key,
            "source_title": record.get("title", ""),
            "official_source_id": _infer_official_source(record, official_map),
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        entry_has_any = False

        for lang in ("zh", "en"):
            brief = generator.generate(record, lang=lang)
            if brief is None:
                continue
            quality = brief_quality_score(brief)
            if quality < config.premium_min_quality:
                continue
            entry_has_any = True
            entry[f"quality_{lang}"] = quality
            entry[f"preview_{lang}"] = _report_preview(brief, lang=lang)

            report_path = os.path.join(output_dir, _brief_filename(report_id, lang))
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(brief, f, ensure_ascii=False, indent=2)

            md_path = os.path.join(output_dir, _brief_md_filename(report_id, lang))
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(_brief_to_markdown(brief, lang=lang))

        if entry_has_any:
            index_entries.append(entry)

    if index_entries:
        index_path = os.path.join(output_dir, "index.json")
        _merge_index(index_path, index_entries)

    return index_entries


def _should_backfill(record, backfill_missing=False):
    if backfill_missing:
        return bool(record.get("content"))
    return bool(record.get("content"))


def backfill_daily_briefs(config, records, limit=None, backfill_missing=False):
    if not config.premium_enabled:
        return []

    eligible = [r for r in records if _should_generate(r, config) and _should_backfill(r, backfill_missing)]
    if not eligible:
        return []

    eligible = sorted(eligible, key=_score_for_top, reverse=True)
    if limit:
        eligible = eligible[:limit]

    generator = PremiumBriefGenerator(config)
    output_dir, date_key = _today_dir(config.premium_dir)
    official_map = _load_official_domains(config)

    index_entries = []

    for record in eligible:
        report_id = _build_report_id(record)
        entry = {
            "report_id": report_id,
            "event_id": record.get("id"),
            "tier": "brief",
            "price": 9,
            "date": date_key,
            "source_title": record.get("title", ""),
            "official_source_id": _infer_official_source(record, official_map),
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        entry_has_any = False

        for lang in ("zh", "en"):
            brief = generator.generate(record, lang=lang)
            if brief is None:
                continue
            quality = brief_quality_score(brief)
            if quality < config.premium_min_quality:
                continue
            entry_has_any = True
            entry[f"quality_{lang}"] = quality
            entry[f"preview_{lang}"] = _report_preview(brief, lang=lang)

            report_path = os.path.join(output_dir, _brief_filename(report_id, lang))
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(brief, f, ensure_ascii=False, indent=2)

            md_path = os.path.join(output_dir, _brief_md_filename(report_id, lang))
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(_brief_to_markdown(brief, lang=lang))

        if entry_has_any:
            record["premium_brief"] = True
            index_entries.append(entry)

    if index_entries:
        index_path = os.path.join(output_dir, "index.json")
        _merge_index(index_path, index_entries)

    return index_entries


def backfill_briefs_by_date(config, records, limit=None, backfill_missing=False):
    if not config.premium_enabled:
        return []

    eligible = [r for r in records if _should_generate(r, config) and _should_backfill(r, backfill_missing)]
    if not eligible:
        return []

    eligible = sorted(eligible, key=_score_for_top, reverse=True)
    if limit:
        eligible = eligible[:limit]

    generator = PremiumBriefGenerator(config)
    official_map = _load_official_domains(config)
    entries_by_date = {}

    for record in eligible:
        date_key = _record_date_key(record)
        report_id = _build_report_id(record)

        if backfill_missing:
            if _brief_exists(config.premium_dir, date_key, report_id, "zh") or _brief_exists(
                config.premium_dir, date_key, report_id, "en"
            ):
                continue

        output_dir = _date_dir(config.premium_dir, date_key)
        entry = {
            "report_id": report_id,
            "event_id": record.get("id"),
            "tier": "brief",
            "price": 9,
            "date": date_key,
            "source_title": record.get("title", ""),
            "official_source_id": _infer_official_source(record, official_map),
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        entry_has_any = False

        for lang in ("zh", "en"):
            if _brief_exists(config.premium_dir, date_key, report_id, lang):
                continue
            brief = generator.generate(record, lang=lang)
            if brief is None:
                continue
            quality = brief_quality_score(brief)
            if quality < config.premium_min_quality:
                continue
            entry_has_any = True
            entry[f"quality_{lang}"] = quality
            entry[f"preview_{lang}"] = _report_preview(brief, lang=lang)

            report_path = os.path.join(output_dir, _brief_filename(report_id, lang))
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(brief, f, ensure_ascii=False, indent=2)

            md_path = os.path.join(output_dir, _brief_md_filename(report_id, lang))
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(_brief_to_markdown(brief, lang=lang))

        if entry_has_any:
            entries_by_date.setdefault(date_key, []).append(entry)

    for date_key, entries in entries_by_date.items():
        index_path = os.path.join(config.premium_dir, date_key, "index.json")
        _merge_index(index_path, entries)

    return [entry for entries in entries_by_date.values() for entry in entries]
