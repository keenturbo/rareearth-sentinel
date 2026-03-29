import json
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import yaml

from .briefs import PremiumBriefGenerator, brief_quality_score
from ..utils import now_cst, isoformat


PREMIUM_GENERATION_STATUS_FILENAME = "premium_generation_status.json"


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


def _collected_at_date_key(record):
    """Return CST date key based on collected_at (ingestion time) only."""
    ca = record.get("collected_at", "")
    if not ca:
        return ""
    dt = _parse_date(ca)
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        dt = dt.astimezone(now_cst().tzinfo)
    except Exception:
        pass
    return dt.strftime("%Y-%m-%d")


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


def _merge_global_index(base_dir, entries):
    if not entries:
        return
    index_path = os.path.join(base_dir, "reports_index.jsonl")
    merged = {}
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(entry, dict) and entry.get("report_id"):
                        merged[entry["report_id"]] = entry
        except OSError:
            merged = {}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("report_id"):
            merged[entry["report_id"]] = entry
    merged_list = list(merged.values())
    merged_list.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    with open(index_path, "w", encoding="utf-8") as f:
        for entry in merged_list:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _premium_generation_status_path(config):
    return os.path.join(config.data_dir, "status", PREMIUM_GENERATION_STATUS_FILENAME)


def _write_generation_status(config, payload):
    path = _premium_generation_status_path(config)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _base_generation_status(config, date_key, today_input_records=0, eligible_records=0, limit=0):
    return {
        "date": date_key,
        "generated_at": isoformat(now_cst()),
        "today_input_records": int(today_input_records or 0),
        "eligible_records": int(eligible_records or 0),
        "top_n": int(limit or 0),
        "brief_generated_count": 0,
        "index_written_count": 0,
        "reports_index_updated": False,
        "status": "unknown",
        "notes": [],
    }


def generate_daily_briefs(config, records, limit=None):
    today_key = now_cst().strftime("%Y-%m-%d")
    effective_limit = limit or config.premium_top_n

    if not config.premium_enabled:
        payload = _base_generation_status(config, today_key, limit=effective_limit)
        payload["status"] = "disabled"
        payload["notes"].append("premium_disabled")
        _write_generation_status(config, payload)
        return []

    # Fix: use collected_at (ingestion time) not published_at (article date)
    # to match original behavior — articles collected today should be candidates
    # regardless of when they were originally published.
    today_input_records = [r for r in records if _collected_at_date_key(r) == today_key]
    eligible_today_records = [r for r in today_input_records if _should_generate(r, config)]
    status_payload = _base_generation_status(
        config,
        today_key,
        today_input_records=len(today_input_records),
        eligible_records=len(eligible_today_records),
        limit=effective_limit,
    )

    if not today_input_records:
        status_payload["status"] = "no_today_records"
        status_payload["notes"].append("today_input_records_empty")
        _write_generation_status(config, status_payload)
        return []

    if not eligible_today_records:
        status_payload["status"] = "no_eligible_records"
        status_payload["notes"].append("eligible_records_empty")
        _write_generation_status(config, status_payload)
        return []

    today_records = sorted(eligible_today_records, key=_score_for_top, reverse=True)
    top_records = today_records[:effective_limit]

    generator = PremiumBriefGenerator(config)
    output_dir, date_key = _today_dir(config.premium_dir)
    official_map = _load_official_domains(config)

    generator_none_count = 0
    quality_blocked_count = 0
    write_error = ""

    def _generate_view_entries(record, base_report_id):
        nonlocal generator_none_count, quality_blocked_count
        view_entries = []
        for view in ("policy", "industry", "capital"):
            report_id = f"{base_report_id}-{view}"
            entry = {
                "report_id": report_id,
                "event_id": record.get("id"),
                "tier": "brief",
                "price": 9,
                "date": date_key,
                "view": view,
                "source_title": record.get("title", ""),
                "official_source_id": _infer_official_source(record, official_map),
                "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            }
            entry_has_any = False
            impact_score_value = None

            for lang in ("zh", "en"):
                brief = generator.generate(record, lang=lang, view=view)
                if brief is None:
                    generator_none_count += 1
                    continue
                quality = brief_quality_score(brief)
                if quality < config.premium_min_quality:
                    quality_blocked_count += 1
                    continue
                entry_has_any = True
                entry[f"quality_{lang}"] = quality
                entry[f"preview_{lang}"] = _report_preview(brief, lang=lang)
                impact_val = brief.get("impact_score")
                if impact_val is not None:
                    entry[f"impact_score_{lang}"] = impact_val
                    if impact_score_value is None:
                        impact_score_value = impact_val

                report_path = os.path.join(output_dir, _brief_filename(report_id, lang))
                with open(report_path, "w", encoding="utf-8") as f:
                    json.dump(brief, f, ensure_ascii=False, indent=2)

                md_path = os.path.join(output_dir, _brief_md_filename(report_id, lang))
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(_brief_to_markdown(brief, lang=lang))

            if impact_score_value is not None:
                entry["impact_score"] = impact_score_value
            if entry_has_any:
                view_entries.append(entry)
        return view_entries

    major_candidates = []
    prepared_entries = []

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
        impact_score_value = None

        for lang in ("zh", "en"):
            brief = generator.generate(record, lang=lang)
            if brief is None:
                generator_none_count += 1
                continue
            quality = brief_quality_score(brief)
            if quality < config.premium_min_quality:
                quality_blocked_count += 1
                continue
            entry_has_any = True
            entry[f"quality_{lang}"] = quality
            entry[f"preview_{lang}"] = _report_preview(brief, lang=lang)
            impact_val = brief.get("impact_score")
            if impact_val is not None:
                entry[f"impact_score_{lang}"] = impact_val
                if impact_score_value is None:
                    impact_score_value = impact_val

            report_path = os.path.join(output_dir, _brief_filename(report_id, lang))
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(brief, f, ensure_ascii=False, indent=2)

            md_path = os.path.join(output_dir, _brief_md_filename(report_id, lang))
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(_brief_to_markdown(brief, lang=lang))

        if impact_score_value is not None:
            entry["impact_score"] = impact_score_value

        if entry_has_any:
            prepared_entries.append((record, entry, report_id))
            try:
                risk_score = int(record.get("risk_score", 0) or 0)
            except (TypeError, ValueError):
                risk_score = 0
            try:
                impact_score_val = int(impact_score_value or 0)
            except (TypeError, ValueError):
                impact_score_val = 0
            if risk_score >= 85 or impact_score_val >= 9:
                major_candidates.append(
                    (risk_score, impact_score_val, record.get("id"))
                )

    status_payload["brief_generated_count"] = len(prepared_entries)
    status_payload["notes"].append(f"generator_none_count={generator_none_count}")
    status_payload["notes"].append(f"quality_blocked_count={quality_blocked_count}")

    major_event_id = ""
    if major_candidates:
        major_candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        major_event_id = major_candidates[0][2]

    index_entries = []
    for record, entry, report_id in prepared_entries:
        if major_event_id and record.get("id") == major_event_id:
            view_entries = _generate_view_entries(record, report_id)
            if len(view_entries) == 3:
                index_entries.extend(view_entries)
            else:
                index_entries.append(entry)
        else:
            index_entries.append(entry)

    if not prepared_entries:
        status_payload["status"] = "generation_failed"
        status_payload["notes"].append("prepared_entries_empty")
        _write_generation_status(config, status_payload)
        return []

    if index_entries:
        try:
            index_path = os.path.join(output_dir, "index.json")
            _merge_index(index_path, index_entries)
            _merge_global_index(config.premium_dir, index_entries)
            status_payload["index_written_count"] = len(index_entries)
            status_payload["reports_index_updated"] = True
            status_payload["status"] = "healthy"
        except Exception as exc:
            write_error = f"write_error:{type(exc).__name__}:{exc}"
            status_payload["status"] = "write_failed"
            status_payload["notes"].append(write_error)
            _write_generation_status(config, status_payload)
            raise
    else:
        status_payload["status"] = "generation_failed"
        status_payload["notes"].append("index_entries_empty")
        _write_generation_status(config, status_payload)
        return []

    _write_generation_status(config, status_payload)
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
        _merge_global_index(config.premium_dir, index_entries)

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

    if entries_by_date:
        all_entries = [entry for entries in entries_by_date.values() for entry in entries]
        _merge_global_index(config.premium_dir, all_entries)

    return [entry for entries in entries_by_date.values() for entry in entries]
