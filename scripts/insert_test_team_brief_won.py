#!/usr/bin/env python3
import argparse
import json
import os
import random
import sys
from datetime import timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import isoformat, now_cst

TEST_PREFIX = "tb-test-won-"


def _read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def _append_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def _ts():
    return now_cst()


def _mk_id():
    n = _ts()
    return f"{TEST_PREFIX}{n.strftime('%Y%m%d%H%M%S')}-{random.randrange(16**4):04x}"


def insert_test_won(config: Config, lead_id: str = ""):
    leads_path = os.path.join(config.data_dir, "pay", "team_brief_leads.jsonl")
    events_path = os.path.join(config.data_dir, "pay", "team_brief_lead_events.jsonl")

    lead_id = (lead_id or "").strip() or _mk_id()
    if not lead_id.startswith(TEST_PREFIX):
        lead_id = TEST_PREFIX + lead_id

    base = _ts()
    t0 = base - timedelta(minutes=20)
    t1 = base - timedelta(minutes=12)
    t2 = base - timedelta(minutes=8)
    t3 = base - timedelta(minutes=3)
    t4 = base - timedelta(minutes=1)

    lead = {
        "lead_id": lead_id,
        "created_at": isoformat(t0),
        "updated_at": isoformat(t4),
        "name": "S13 Won Test",
        "email": f"admin-test-won-{lead_id[-4:]}@example.com",
        "company": "S13 Test Metals Ltd",
        "role": "Procurement Manager",
        "brief_need": "验证成交复盘卡展示与复制按钮链路",
        "budget_range": "USD 500-1000",
        "source_channel": "admin_test",
        "message": "This is a generated won test lead for S13 acceptance.",
        "status": "won",
        "owner": "admin-test",
        "next_action_at": isoformat(base + timedelta(days=7)),
        "quote_currency": "USD",
        "quote_amount": "499",
        "quote_plan": "pro",
        "proposal_template": "pro",
        "proposal_template_version": "pro_v1",
        "quote_valid_until": (base.date() + timedelta(days=7)).isoformat(),
        "close_reason_code": "high_relevance",
        "close_reason_note": "测试成交：用于验收复盘卡与复制功能。",
        "utm_source": "admin",
        "utm_campaign": "s13_won_review_test",
        "utm_content": "insert_test_team_brief_won",
        "test": True,
    }

    events = [
        {
            "lead_id": lead_id,
            "created_at": isoformat(t0),
            "event_type": "lead_created",
            "status": "new",
            "source_channel": "admin_test",
            "operator": "script",
            "test": True,
        },
        {
            "lead_id": lead_id,
            "created_at": isoformat(t1),
            "event_type": "proposal_template_applied",
            "proposal_template": "pro",
            "proposal_template_version": "pro_v1",
            "operator": "script",
            "test": True,
        },
        {
            "lead_id": lead_id,
            "created_at": isoformat(t2),
            "event_type": "proposal_created",
            "quote_currency": "USD",
            "quote_amount": "499",
            "quote_plan": "pro",
            "proposal_template": "pro",
            "proposal_template_version": "pro_v1",
            "operator": "script",
            "test": True,
        },
        {
            "lead_id": lead_id,
            "created_at": isoformat(t3),
            "event_type": "status_change",
            "from_status": "proposal_sent",
            "to_status": "won",
            "status": "won",
            "operator": "script",
            "test": True,
        },
        {
            "lead_id": lead_id,
            "created_at": isoformat(t4),
            "event_type": "close_reason_set",
            "status": "won",
            "close_reason_code": "high_relevance",
            "close_reason_note": "测试成交：用于验收复盘卡与复制功能。",
            "operator": "script",
            "test": True,
        },
    ]

    _append_jsonl(leads_path, [lead])
    _append_jsonl(events_path, events)
    return {
        "ok": True,
        "mode": "insert",
        "lead_id": lead_id,
        "leads_path": leads_path,
        "events_path": events_path,
        "events": len(events),
    }


def clear_test_won(config: Config):
    leads_path = os.path.join(config.data_dir, "pay", "team_brief_leads.jsonl")
    events_path = os.path.join(config.data_dir, "pay", "team_brief_lead_events.jsonl")

    leads_rows = _read_jsonl(leads_path)
    events_rows = _read_jsonl(events_path)
    before_leads = len(leads_rows)
    before_events = len(events_rows)

    def _keep(row):
        lead_id = str(row.get("lead_id") or "").strip()
        return not lead_id.startswith(TEST_PREFIX)

    leads_new = [r for r in leads_rows if _keep(r)]
    events_new = [r for r in events_rows if _keep(r)]

    if before_leads != len(leads_new):
        _write_jsonl(leads_path, leads_new)
    if before_events != len(events_new):
        _write_jsonl(events_path, events_new)

    return {
        "ok": True,
        "mode": "clear",
        "removed_leads": before_leads - len(leads_new),
        "removed_events": before_events - len(events_new),
        "leads_path": leads_path,
        "events_path": events_path,
    }


def main():
    ap = argparse.ArgumentParser(description="Insert/clear a Team Brief won test lead for S13 won review card acceptance")
    ap.add_argument("--clear", action="store_true", help="Clear all test won leads inserted by this script")
    ap.add_argument("--lead-id", default="", help="Optional custom lead id suffix or full id")
    args = ap.parse_args()

    cfg = Config.from_env()
    if args.clear:
        result = clear_test_won(cfg)
    else:
        result = insert_test_won(cfg, lead_id=args.lead_id)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
