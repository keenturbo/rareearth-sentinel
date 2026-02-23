#!/usr/bin/env python3
import argparse
import json
import os
import random
import sys
from datetime import datetime

CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from _common import Config, now_iso, read_jsonl, sales_paths, safe_str, write_jsonl

TEST_TASK_PREFIX = "otask-test-"
TEST_LEAD_PREFIX = "slead-test-"
_ALLOWED_STATUSES = {"draft", "approved", "sent", "failed", "cancelled"}


def _mk_suffix() -> str:
    return datetime.utcnow().strftime("%Y%m%d%H%M%S") + f"-{random.randrange(16**4):04x}"


def _mk_task_id() -> str:
    return f"{TEST_TASK_PREFIX}{_mk_suffix()}"


def _mk_lead_id() -> str:
    return f"{TEST_LEAD_PREFIX}{_mk_suffix()}"


def _ensure_test_lead(leads, lead_id: str, owner: str = "admin_sdr"):
    for row in leads:
        if safe_str((row or {}).get("id")) == lead_id:
            return False

    now = now_iso()
    leads.append(
        {
            "id": lead_id,
            "source": "admin_test",
            "profile_url": "",
            "display_name": "SDR Test Lead",
            "name": "SDR Test Lead",
            "email": f"{lead_id}@example.com",
            "role": "Procurement Manager",
            "company": "SDR Test Metals",
            "country": "US",
            "language": "en",
            "tags": ["admin_test", "sdr"],
            "score": 72.0,
            "score_reason": {"admin_test": 72},
            "status": "new",
            "owner": owner,
            "next_followup_at": "",
            "created_at": now,
            "updated_at": now,
            "test": True,
        }
    )
    return True


def insert_test_task(config: Config, lead_id: str = "", status: str = "draft", step: str = "first_touch", channel: str = "linkedin_dm"):
    paths = sales_paths(config)

    tasks = read_jsonl(paths["tasks"])
    leads = read_jsonl(paths["leads"])

    lid = safe_str(lead_id) or _mk_lead_id()
    if not lid.startswith(TEST_LEAD_PREFIX):
        lid = f"{TEST_LEAD_PREFIX}{lid}"

    st = safe_str(status).lower() or "draft"
    if st not in _ALLOWED_STATUSES:
        st = "draft"

    lead_created = _ensure_test_lead(leads, lid)

    now = now_iso()
    task_id = _mk_task_id()
    task = {
        "id": task_id,
        "lead_id": lid,
        "channel": safe_str(channel) or "linkedin_dm",
        "step": safe_str(step) or "first_touch",
        "template_version": "test_v1",
        "draft_text": "[TEST] RareEarth Sentinel SDR test task. Please mark approved/sent to validate admin workflow.",
        "status": st,
        "scheduled_at": now,
        "sent_at": now if st == "sent" else "",
        "failure_reason": "",
        "created_at": now,
        "updated_at": now,
        "test": True,
    }

    tasks.append(task)
    tasks.sort(key=lambda x: safe_str((x or {}).get("created_at")), reverse=True)

    write_jsonl(paths["tasks"], tasks)
    write_jsonl(paths["leads"], leads)

    return {
        "ok": True,
        "mode": "insert",
        "task_id": task_id,
        "lead_id": lid,
        "status": st,
        "lead_created": lead_created,
        "tasks_path": paths["tasks"],
        "leads_path": paths["leads"],
    }


def clear_test_tasks(config: Config):
    paths = sales_paths(config)
    tasks = read_jsonl(paths["tasks"])
    leads = read_jsonl(paths["leads"])

    before_tasks = len(tasks)
    before_leads = len(leads)

    def _keep_task(row):
        rid = safe_str((row or {}).get("id"))
        lid = safe_str((row or {}).get("lead_id"))
        is_test = bool((row or {}).get("test"))
        if rid.startswith(TEST_TASK_PREFIX):
            return False
        if lid.startswith(TEST_LEAD_PREFIX):
            return False
        if is_test:
            return False
        return True

    def _keep_lead(row):
        rid = safe_str((row or {}).get("id"))
        is_test = bool((row or {}).get("test"))
        if rid.startswith(TEST_LEAD_PREFIX):
            return False
        if is_test:
            return False
        return True

    tasks_new = [r for r in tasks if _keep_task(r)]
    leads_new = [r for r in leads if _keep_lead(r)]

    write_jsonl(paths["tasks"], tasks_new)
    write_jsonl(paths["leads"], leads_new)

    return {
        "ok": True,
        "mode": "clear",
        "removed_tasks": before_tasks - len(tasks_new),
        "removed_leads": before_leads - len(leads_new),
        "tasks_path": paths["tasks"],
        "leads_path": paths["leads"],
    }


def main():
    ap = argparse.ArgumentParser(description="Insert/clear SDR test task for admin workflow acceptance")
    ap.add_argument("--clear", action="store_true", help="Clear SDR test tasks and test leads")
    ap.add_argument("--lead-id", default="", help="Optional lead id (suffix or full) for insertion")
    ap.add_argument("--status", default="draft", help="Task status: draft/approved/sent/failed/cancelled")
    ap.add_argument("--step", default="first_touch", help="Task step, e.g. first_touch/followup_d1")
    ap.add_argument("--channel", default="linkedin_dm", help="Task channel, e.g. linkedin_dm/x_dm/email")
    args = ap.parse_args()

    cfg = Config.from_env()
    if args.clear:
        result = clear_test_tasks(cfg)
    else:
        result = insert_test_task(
            cfg,
            lead_id=args.lead_id,
            status=args.status,
            step=args.step,
            channel=args.channel,
        )

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
