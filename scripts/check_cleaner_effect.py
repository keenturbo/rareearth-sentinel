import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.input_guard import guard_article_text
from src.utils import isoformat, now_cst


STATUS_PATH = os.path.join("data", "status", "cleaner_effect_status.json")
EXAMPLE_LIMIT = 5

POLLUTION_PATTERNS = {
    "contains_script_tag": re.compile(r"<script", re.I),
    "contains_function": re.compile(r"\bfunction\s*\(", re.I),
    "contains_window": re.compile(r"\bwindow\.", re.I),
    "contains_document": re.compile(r"\bdocument\.", re.I),
    "contains_datadog": re.compile(r"datadog", re.I),
    "contains_googletagmanager": re.compile(r"googletagmanager", re.I),
    "contains_next_data": re.compile(r"__NEXT_DATA__", re.I),
    "contains_algolia": re.compile(r"algolia", re.I),
    "contains_div_tag": re.compile(r"<div", re.I),
    "contains_style_tag": re.compile(r"<style", re.I),
    "contains_font_face": re.compile(r"@font-face", re.I),
    "contains_freestar": re.compile(r"freestar", re.I),
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


def _source_label(record):
    source = str(record.get("source") or "").strip()
    if source:
        return source
    url = str(record.get("url") or "").strip()
    if url:
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc or "unknown"
        except Exception:
            pass
    return "unknown"


def _load_records(config, date_key=None):
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
            if date_key is not None and _record_date_key(row) != date_key:
                continue
            rows.append(row)
    return rows


def build_cleaner_effect_status(config, scope="today"):
    now_dt = now_cst()
    today_key = now_dt.strftime("%Y-%m-%d")

    if scope == "all":
        records = _load_records(config, date_key=None)
        scope_label = "all"
    else:
        records = _load_records(config, date_key=today_key)
        scope_label = today_key

    signal_counts = {name: 0 for name in POLLUTION_PATTERNS}
    polluted_records = 0
    examples = []

    source_total = Counter()
    source_polluted = Counter()
    guard_reason_counts = Counter()
    guard_signal_counts = Counter()

    for row in records:
        content = str(row.get("content") or "")
        src = _source_label(row)
        source_total[src] += 1

        # Legacy pattern check
        matched = []
        for name, pattern in POLLUTION_PATTERNS.items():
            if pattern.search(content):
                signal_counts[name] += 1
                matched.append(name)
        if matched:
            polluted_records += 1
            source_polluted[src] += 1
            if len(examples) < EXAMPLE_LIMIT:
                examples.append(
                    {
                        "id": str(row.get("id") or ""),
                        "title": str(row.get("title") or "").strip(),
                        "source": src,
                        "matched_signals": matched,
                    }
                )

        # New: guard-based quality check
        guard = guard_article_text(content, config)
        if not guard.get("pass"):
            reason = guard.get("reason", "unknown")
            guard_reason_counts[reason] += 1
            for sig in (guard.get("pollution_signals") or []):
                guard_signal_counts[sig] += 1

    total = len(records)

    # Source pollution ranking (top 15 by polluted count)
    source_ranking = []
    for src in sorted(source_polluted, key=source_polluted.get, reverse=True)[:15]:
        t = source_total[src]
        p = source_polluted[src]
        source_ranking.append({
            "source": src,
            "total": t,
            "polluted": p,
            "pollution_rate": round(p / max(1, t), 4),
        })

    payload = {
        "scope": scope_label,
        "generated_at": isoformat(now_dt),
        "total_records": total,
        "polluted_records": polluted_records,
        "pollution_rate": round(polluted_records / max(1, total), 4),
        "legacy_signals": signal_counts,
        "guard_reason_counts": dict(guard_reason_counts),
        "guard_signal_counts": dict(guard_signal_counts),
        "source_pollution_ranking": source_ranking,
        "unique_sources": len(source_total),
        "examples": examples,
    }
    return payload


def write_status(payload):
    os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", default="today", choices=["today", "all"],
                        help="today: only today's records; all: full intel.jsonl")
    args = parser.parse_args()

    config = Config.from_env()
    payload = build_cleaner_effect_status(config, scope=args.scope)
    write_status(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
