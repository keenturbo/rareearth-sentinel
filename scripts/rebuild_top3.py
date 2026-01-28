import argparse
import json
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from src.config import Config
from src.utils import now_cst


def _parse_dt(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None


def _load_index(path):
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and entry.get("report_id"):
                entries.append(entry)
    return entries


def _load_records(path):
    record_map = {}
    if not os.path.exists(path):
        return record_map
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("id"):
                record_map[record["id"]] = record
    return record_map


def _entry_key(entry):
    created_at = entry.get("created_at") or entry.get("date") or ""
    dt = _parse_dt(created_at)
    if not dt:
        return 0
    return dt.timestamp()


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


def _build_item(entry):
    preview_zh = entry.get("preview_zh") or {}
    preview_en = entry.get("preview_en") or {}

    value_zh = str(preview_zh.get("value", "")).strip()
    report_contains_zh = str(preview_zh.get("preview", "")).strip()
    value_en = str(preview_en.get("value", "")).strip()
    report_contains_en = str(preview_en.get("preview", "")).strip()

    if not (value_zh or report_contains_zh) or not (value_en or report_contains_en):
        return None

    title_zh = str(preview_zh.get("title", "")).strip()
    title_en = str(preview_en.get("title", "")).strip()
    if not title_zh:
        title_zh = str(entry.get("source_title", "")).strip()

    title = title_zh or title_en or str(entry.get("source_title", "")).strip()

    report_id = str(entry.get("report_id", "")).strip()
    if not report_id:
        return None

    return {
        "report_id": report_id,
        "title": title,
        "title_zh": title_zh,
        "title_en": title_en,
        "value_zh": value_zh,
        "report_contains_zh": report_contains_zh,
        "value_en": value_en,
        "report_contains_en": report_contains_en,
    }


def rebuild_top3(config, limit=3):
    index_path = os.path.join(config.premium_dir, "reports_index.jsonl")
    entries = _load_index(index_path)
    entries.sort(key=_entry_key, reverse=True)

    record_map = _load_records(os.path.join(config.data_dir, "intel.jsonl"))
    selected_entries = _select_top3_entries(entries, record_map, limit=limit)

    items = []
    for entry in selected_entries:
        item = _build_item(entry)
        if not item:
            continue
        items.append(item)
        if len(items) >= limit:
            break

    payload = {
        "date": now_cst().strftime("%Y-%m-%d"),
        "generated_at": now_cst().isoformat(timespec="seconds"),
        "items": items,
    }

    status_dir = os.path.join(config.data_dir, "status")
    os.makedirs(status_dir, exist_ok=True)
    out_path = os.path.join(status_dir, "top3_list.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out_path, len(items)


def main():
    parser = argparse.ArgumentParser(description="Rebuild Top3 list from premium index")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    config = Config.from_env()
    path, count = rebuild_top3(config, limit=args.limit)
    print(f"[top3] rebuilt {count} items -> {path}")


if __name__ == "__main__":
    main()
