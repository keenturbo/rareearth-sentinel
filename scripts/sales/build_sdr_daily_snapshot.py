#!/usr/bin/env python3
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from _common import Config, append_jsonl, now_iso, parse_iso, read_jsonl, sales_paths, safe_str


def _count_status(rows, key):
    c = Counter(safe_str(x.get(key)).lower() for x in rows)
    return dict(c)


def _within_24h(ts: str) -> bool:
    dt = parse_iso(ts)
    if not dt:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= datetime.now(timezone.utc) - timedelta(hours=24)


def build_snapshot(config: Config):
    paths = sales_paths(config)
    leads = read_jsonl(paths["leads"])
    tasks = read_jsonl(paths["tasks"])
    replies = read_jsonl(paths["replies"])

    lead_status = _count_status(leads, "status")
    task_status = _count_status(tasks, "status")
    reply_label = _count_status(replies, "reply_label")

    draft_ready = sum(1 for x in tasks if safe_str(x.get("status")) == "draft")
    sent_24h = sum(1 for x in tasks if safe_str(x.get("status")) == "sent" and _within_24h(safe_str(x.get("sent_at") or x.get("updated_at") or x.get("created_at"))))
    replies_24h = sum(1 for x in replies if _within_24h(safe_str(x.get("created_at"))))

    qualified = int(lead_status.get("qualified", 0))
    proposal_sent = int(lead_status.get("proposal_sent", 0))
    won = int(lead_status.get("won", 0))

    warnings = []
    if int(lead_status.get("new", 0)) > 0 and draft_ready == 0:
        warnings.append("new_leads_without_drafts")
    if qualified > 0 and proposal_sent == 0:
        warnings.append("qualified_without_proposal")

    quality_pass = len(warnings) == 0

    snapshot = {
        "generated_at": now_iso(),
        "leads": {
            "total": len(leads),
            "new": int(lead_status.get("new", 0)),
            "contacted": int(lead_status.get("contacted", 0)),
            "replied": int(lead_status.get("replied", 0)),
            "qualified": qualified,
            "proposal_sent": proposal_sent,
            "won": won,
            "lost": int(lead_status.get("lost", 0)),
        },
        "tasks": {
            "total": len(tasks),
            "draft": int(task_status.get("draft", 0)),
            "approved": int(task_status.get("approved", 0)),
            "sent": int(task_status.get("sent", 0)),
            "failed": int(task_status.get("failed", 0)),
        },
        "replies": {
            "total": len(replies),
            "interested": int(reply_label.get("interested", 0)),
            "maybe": int(reply_label.get("maybe", 0)),
            "no_fit": int(reply_label.get("no_fit", 0)),
            "unknown": int(reply_label.get("unknown", 0)),
        },
        "activity_24h": {
            "sent": sent_24h,
            "replies": replies_24h,
        },
        "funnel": {
            "qualification_rate": f"{round((qualified / max(1, int(lead_status.get('contacted', 0)))) * 100)}%",
            "proposal_rate": f"{round((proposal_sent / max(1, qualified)) * 100)}%",
            "win_rate": f"{round((won / max(1, proposal_sent)) * 100)}%",
        },
        "quality_flags": {
            "pass": quality_pass,
            "warnings": warnings,
        },
    }

    os.makedirs(os.path.dirname(paths["snapshot"]), exist_ok=True)
    with open(paths["snapshot"], "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    append_jsonl(paths["snapshot_jsonl"], [{"date": now_iso()[:10], **snapshot}])

    lines = [
        "【AI SDR Daily Brief】",
        f"Leads: {snapshot['leads']['total']} (new {snapshot['leads']['new']}, qualified {qualified}, won {won})",
        f"Tasks: draft {snapshot['tasks']['draft']}, sent {snapshot['tasks']['sent']}",
        f"Replies(24h): {replies_24h}, Sent(24h): {sent_24h}",
        f"Funnel: Q {snapshot['funnel']['qualification_rate']} / P {snapshot['funnel']['proposal_rate']} / W {snapshot['funnel']['win_rate']}",
        f"Quality: {'PASS' if quality_pass else 'DEGRADED'}",
    ]
    if warnings:
        lines.append("Warnings: " + ", ".join(warnings))

    os.makedirs(os.path.dirname(paths["brief_txt"]), exist_ok=True)
    with open(paths["brief_txt"], "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return {"ok": True, "snapshot": paths["snapshot"], "brief": paths["brief_txt"]}


def main():
    cfg = Config.from_env()
    result = build_snapshot(cfg)
    print(result)


if __name__ == "__main__":
    main()
