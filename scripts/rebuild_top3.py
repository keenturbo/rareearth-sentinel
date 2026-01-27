import argparse
import json
import os
from datetime import datetime
from email.utils import parsedate_to_datetime

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


def _entry_key(entry):
    created_at = entry.get("created_at") or entry.get("date") or ""
    dt = _parse_dt(created_at)
    if not dt:
        return 0
    return dt.timestamp()


def _build_item(entry):
    preview_zh = entry.get("preview_zh") or {}
    preview_en = entry.get("preview_en") or {}

    value_zh = str(preview_zh.get("value", "")).strip()
    report_contains_zh = str(preview_zh.get("preview", "")).strip()
    value_en = str(preview_en.get("value", "")).strip()
    report_contains_en = str(preview_en.get("preview", "")).strip()

    if not (value_zh or report_contains_zh) or not (value_en or report_contains_en):
        return None

    title = (
        str(preview_zh.get("title", "")).strip()
        or str(preview_en.get("title", "")).strip()
        or str(entry.get("source_title", "")).strip()
    )

    report_id = str(entry.get("report_id", "")).strip()
    if not report_id:
        return None

    return {
        "report_id": report_id,
        "title": title,
        "value_zh": value_zh,
        "report_contains_zh": report_contains_zh,
        "value_en": value_en,
        "report_contains_en": report_contains_en,
    }


def rebuild_top3(config, limit=3):
    index_path = os.path.join(config.premium_dir, "reports_index.jsonl")
    entries = _load_index(index_path)
    entries.sort(key=_entry_key, reverse=True)

    items = []
    for entry in entries:
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
