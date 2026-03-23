import json
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from src.config import Config
from src.utils import isoformat, now_cst


STATUS_PATH = os.path.join("data", "status", "cleaner_effect_status.json")
EXAMPLE_LIMIT = 3

POLLUTION_PATTERNS = {
    "contains_script_tag": re.compile(r"<script", re.I),
    "contains_function": re.compile(r"\bfunction\s*\(", re.I),
    "contains_window": re.compile(r"\bwindow\.", re.I),
    "contains_document": re.compile(r"\bdocument\.", re.I),
    "contains_datadog": re.compile(r"datadog", re.I),
    "contains_googletagmanager": re.compile(r"googletagmanager", re.I),
    "contains_next_data": re.compile(r"__NEXT_DATA__", re.I),
    "contains_algolia": re.compile(r"algolia", re.I),
}



def _parse_dt(value):
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt



def _date_key_from_dt(dt):
    if not dt:
        return ""
    try:
        dt = dt.astimezone(now_cst().tzinfo)
    except Exception:
        pass
    return dt.strftime("%Y-%m-%d")



def _record_date_key(record):
    return _date_key_from_dt(
        _parse_dt(record.get("collected_at") or record.get("published_at"))
    )



def _load_today_records(config, today_key):
    path = os.path.join(config.data_dir, "intel.jsonl")
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            if _record_date_key(row) == today_key:
                rows.append(row)
    return rows



def build_cleaner_effect_status(config):
    now_dt = now_cst()
    today_key = now_dt.strftime("%Y-%m-%d")
    today_records = _load_today_records(config, today_key)

    signal_counts = {name: 0 for name in POLLUTION_PATTERNS}
    polluted_records = 0
    examples = []

    for row in today_records:
        content = str(row.get("content") or "")
        matched = []
        for name, pattern in POLLUTION_PATTERNS.items():
            if pattern.search(content):
                signal_counts[name] += 1
                matched.append(name)
        if matched:
            polluted_records += 1
            if len(examples) < EXAMPLE_LIMIT:
                examples.append(
                    {
                        "id": str(row.get("id") or ""),
                        "title": str(row.get("title") or "").strip(),
                        "matched_signals": matched,
                    }
                )

    total = len(today_records)
    payload = {
        "date": today_key,
        "generated_at": isoformat(now_dt),
        "today_records": total,
        "polluted_records": polluted_records,
        "pollution_rate": round((polluted_records / total), 4) if total else 0.0,
        "signals": signal_counts,
        "examples": examples,
    }
    return payload



def write_status(payload):
    os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)



def main():
    config = Config.from_env()
    payload = build_cleaner_effect_status(config)
    write_status(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
