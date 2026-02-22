#!/usr/bin/env python3
import datetime as dt
import os
import sys

CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from _common import Config, append_jsonl, now_iso, parse_iso, read_jsonl, sales_paths, safe_str, stable_id, write_jsonl


FOLLOW_STEPS = [(1, "followup_d1"), (3, "followup_d3"), (7, "followup_d7")]


def _follow_text(lead: dict, step: str) -> str:
    name = safe_str(lead.get("display_name") or lead.get("name")) or "there"
    if step == "followup_d1":
        return f"Hi {name}, quick follow-up — would a 24h rare-earth action memo be useful for your current watchlist?"
    if step == "followup_d3":
        return f"Hi {name}, I still have pilot slots for a $9 mini task sprint this week. Happy to share one sample structure."
    return f"Hi {name}, final ping this week — if timing works, I can deliver a 24h memo for one live event."


def _has_task(tasks, lead_id: str, step: str) -> bool:
    for t in tasks:
        if safe_str(t.get("lead_id")) != lead_id:
            continue
        if safe_str(t.get("step")) != step:
            continue
        return True
    return False


def _has_reply(replies, lead_id: str) -> bool:
    for r in replies:
        if safe_str(r.get("lead_id")) == lead_id:
            return True
    return False


def schedule_followups(config: Config):
    paths = sales_paths(config)
    leads = read_jsonl(paths["leads"])
    lead_map = {safe_str(x.get("id")): x for x in leads if safe_str(x.get("id"))}
    tasks = read_jsonl(paths["tasks"])
    replies = read_jsonl(paths["replies"])

    now = dt.datetime.now(dt.timezone.utc)
    created = []
    audit_rows = []

    sent_tasks = [x for x in tasks if safe_str(x.get("status")) == "sent"]
    for sent in sent_tasks:
        lead_id = safe_str(sent.get("lead_id"))
        if not lead_id or lead_id not in lead_map:
            continue
        if _has_reply(replies, lead_id):
            continue

        sent_at = parse_iso(safe_str(sent.get("sent_at")) or safe_str(sent.get("updated_at")) or safe_str(sent.get("created_at")))
        if not sent_at:
            continue
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=dt.timezone.utc)

        for days, step in FOLLOW_STEPS:
            if _has_task(tasks + created, lead_id, step):
                continue
            due = sent_at + dt.timedelta(days=days)
            if due > now:
                continue
            lead = lead_map[lead_id]
            now_s = now_iso()
            task_id = stable_id("otask", f"{lead_id}:{step}:{now_s}")
            created.append(
                {
                    "id": task_id,
                    "lead_id": lead_id,
                    "channel": safe_str(sent.get("channel")) or "linkedin_dm",
                    "step": step,
                    "template_version": f"{step}_v1",
                    "draft_text": _follow_text(lead, step),
                    "status": "draft",
                    "scheduled_at": now_s,
                    "sent_at": "",
                    "failure_reason": "",
                    "created_at": now_s,
                    "updated_at": now_s,
                }
            )
            audit_rows.append(
                {
                    "id": stable_id("audit", f"followup_scheduled:{task_id}"),
                    "event_type": "followup_scheduled",
                    "ref_id": task_id,
                    "payload": {"lead_id": lead_id, "step": step, "days_after_sent": days},
                    "created_at": now_s,
                }
            )

    out = tasks + created
    out.sort(key=lambda x: safe_str(x.get("created_at")), reverse=True)
    write_jsonl(paths["tasks"], out)
    append_jsonl(paths["audit"], audit_rows)

    return {"ok": True, "created": len(created), "out": paths["tasks"], "audit": len(audit_rows)}


def main():
    cfg = Config.from_env()
    result = schedule_followups(cfg)
    print(result)


if __name__ == "__main__":
    main()
