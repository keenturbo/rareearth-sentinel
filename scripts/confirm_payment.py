#!/usr/bin/env python3
import argparse
import json
import os
import sys
from datetime import timedelta
from uuid import uuid4

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import now_cst


def _load_index(index_path):
    entries = []
    if not os.path.exists(index_path):
        return entries
    with open(index_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def _find_entry(entries, report_id):
    for entry in entries:
        if entry.get("report_id") == report_id:
            return entry
    return None


def _load_brief_md(premium_dir, date_key, report_id, lang):
    if not date_key:
        return ""
    path = os.path.join(premium_dir, date_key, f"brief_{report_id}_{lang}.md")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Confirm payment and issue unlock token")
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--tier", default="9")
    parser.add_argument("--lang", default="cn", choices=["cn", "en"])
    parser.add_argument("--expires-days", type=int, default=7)
    args = parser.parse_args()

    config = Config.from_env()
    index_path = os.path.join(config.premium_dir, "reports_index.jsonl")
    entries = _load_index(index_path)
    entry = _find_entry(entries, args.report_id)
    if not entry:
        print("Report not found in reports_index.jsonl")
        return 2

    date_key = entry.get("date", "")
    lang = args.lang
    brief_md = _load_brief_md(config.premium_dir, date_key, args.report_id, "zh" if lang == "cn" else "en")

    token = uuid4().hex[:16]
    expires_at = now_cst() + timedelta(days=args.expires_days)

    unlock_payload = {
        "report_id": args.report_id,
        "tier": args.tier,
        "lang": lang,
        "token": token,
        "expires_at": expires_at.isoformat(),
        "content_md": brief_md,
    }

    status_payload = {
        "report_id": args.report_id,
        "tier": args.tier,
        "order_id": args.order_id,
        "token": token,
        "unlock_url": f"/{lang}/pay/{args.report_id}.html?unlock={token}",
        "expires_at": expires_at.isoformat(),
    }

    status_path = os.path.join(config.static_dir, "pay", "status", f"{args.order_id}.json")
    unlock_path = os.path.join(config.static_dir, "unlocks", f"{token}.json")

    _write_json(status_path, status_payload)
    _write_json(unlock_path, unlock_payload)

    print("Unlock issued")
    print(f"Unlock URL: {status_payload['unlock_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
