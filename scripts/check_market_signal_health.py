#!/usr/bin/env python3
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import isoformat, now_cst


def _abs_path(rel_path):
    if os.path.isabs(rel_path):
        return rel_path
    return os.path.join(ROOT, rel_path)


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _load_jsonl(path):
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
            if isinstance(row, dict):
                rows.append(row)
    return rows


def main():
    config = Config.from_env()
    data_dir = config.data_dir or "data"
    signals_path = _abs_path(os.path.join(data_dir, "market", "market_signals.jsonl"))
    status_path = _abs_path(os.path.join(data_dir, "status", "market_signal_status.json"))

    rows = _load_jsonl(signals_path)
    status = _load_json(status_path, {})

    severity_counts = Counter(str(row.get("severity") or "low") for row in rows)
    tag_counts = Counter()
    related_intel_hit_count = 0

    for row in rows:
        for tag in row.get("signal_reason_tags") or []:
            tag_counts[str(tag)] += 1
        if row.get("related_intel_ids"):
            related_intel_hit_count += 1

    payload = {
        "date": str(status.get("date") or now_cst().strftime("%Y-%m-%d")),
        "generated_at": isoformat(now_cst()),
        "signals_total": len(rows),
        "high_severity_count": severity_counts.get("high", 0),
        "medium_severity_count": severity_counts.get("medium", 0),
        "low_severity_count": severity_counts.get("low", 0),
        "related_intel_hit_count": related_intel_hit_count,
        "stale_source_count": int(status.get("stale_source_count", 0) or 0),
        "build_status": str(status.get("build_status") or "unknown"),
        "top_tags": [tag for tag, _ in tag_counts.most_common(5)],
        "notes": status.get("notes") or [],
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
