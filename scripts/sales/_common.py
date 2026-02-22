#!/usr/bin/env python3
import hashlib
import json
import os
import sys
from typing import Dict, List

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import isoformat, now_cst


def read_jsonl(path: str) -> List[dict]:
    rows: List[dict] = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def write_jsonl(path: str, rows: List[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def append_jsonl(path: str, rows: List[dict]) -> None:
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def now_iso() -> str:
    return isoformat(now_cst())


def stable_id(prefix: str, text: str, length: int = 12) -> str:
    key = f"{prefix}:{text}"
    return f"{prefix}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:length]}"


def safe_str(v) -> str:
    return str(v or "").strip()


def sales_paths(config: Config) -> Dict[str, str]:
    sales_dir = os.path.join(config.data_dir, "sales")
    analytics_dir = os.path.join(config.data_dir, "analytics")
    return {
        "sales_dir": sales_dir,
        "analytics_dir": analytics_dir,
        "team_brief_leads": os.path.join(config.data_dir, "pay", "team_brief_leads.jsonl"),
        "team_brief_events": os.path.join(config.data_dir, "pay", "team_brief_lead_events.jsonl"),
        "manual_leads": os.path.join(sales_dir, "manual_leads.jsonl"),
        "leads": os.path.join(sales_dir, "leads.jsonl"),
        "tasks": os.path.join(sales_dir, "outreach_tasks.jsonl"),
        "replies": os.path.join(sales_dir, "replies.jsonl"),
        "audit": os.path.join(sales_dir, "audit_logs.jsonl"),
        "snapshot": os.path.join(sales_dir, "sdr_daily_snapshot.json"),
        "snapshot_jsonl": os.path.join(analytics_dir, "sdr_daily_snapshots.jsonl"),
        "brief_txt": os.path.join(sales_dir, "sdr_daily_brief.txt"),
    }


def parse_iso(value: str):
    text = safe_str(value)
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        import datetime as _dt

        return _dt.datetime.fromisoformat(text)
    except Exception:
        return None
