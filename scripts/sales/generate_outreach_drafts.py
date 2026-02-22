#!/usr/bin/env python3
import os
import sys

CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from _common import Config, append_jsonl, now_iso, read_jsonl, sales_paths, safe_str, stable_id, write_jsonl


ACTIVE_TASK_STATUSES = {"draft", "approved", "sent"}


def _channel_for(lead: dict) -> str:
    src = safe_str(lead.get("source")).lower()
    if "linkedin" in src:
        return "linkedin_dm"
    if "twitter" in src or src == "x":
        return "x_dm"
    if safe_str(lead.get("email")):
        return "email"
    return "linkedin_dm"


def _persona(lead: dict) -> str:
    role = safe_str(lead.get("role")).lower()
    if any(k in role for k in ["invest", "analyst", "fund", "capital"]):
        return "investor"
    if any(k in role for k in ["supply", "procure", "operations", "buyer", "sourcing"]):
        return "supply"
    return "general"


def _first_touch_text(lead: dict) -> str:
    persona = _persona(lead)
    name = safe_str(lead.get("display_name") or lead.get("name")) or "there"
    company = safe_str(lead.get("company")) or "your team"

    if persona == "investor":
        return (
            f"Hi {name}, I track rare-earth/critical-minerals events with execution-focused memos. "
            f"If helpful, I can send a 24h mini task sprint for {company} (action + risk path + evidence). "
            "Pilot price is $9."
        )
    if persona == "supply":
        return (
            f"Hi {name}, if {company} is exposed to critical-minerals volatility, I can provide a 24h action memo "
            "for one live event (what to do now, what to watch next 7 days). Pilot is $9."
        )
    return (
        f"Hi {name}, I run a rare-earth sentinel focused on decision-ready outputs. "
        f"If useful for {company}, I can deliver a 24h mini task sprint memo. Pilot price: $9."
    )


def _has_active_step(tasks, lead_id: str, step: str) -> bool:
    for t in tasks:
        if safe_str(t.get("lead_id")) != lead_id:
            continue
        if safe_str(t.get("step")) != step:
            continue
        if safe_str(t.get("status")) in ACTIVE_TASK_STATUSES:
            return True
    return False


def generate_drafts(config: Config, min_score: float = 40.0):
    paths = sales_paths(config)
    leads = read_jsonl(paths["leads"])
    tasks = read_jsonl(paths["tasks"])

    created = []
    audit_rows = []
    now = now_iso()

    for lead in leads:
        lead_id = safe_str(lead.get("id"))
        if not lead_id:
            continue
        status = safe_str(lead.get("status")).lower() or "new"
        score = float(lead.get("score") or 0)
        if status not in {"new", "contacted", "replied", "qualified"}:
            continue
        if score < min_score:
            continue
        step = "first_touch"
        if _has_active_step(tasks + created, lead_id, step):
            continue

        task_id = stable_id("otask", f"{lead_id}:{step}:{now}")
        draft = {
            "id": task_id,
            "lead_id": lead_id,
            "channel": _channel_for(lead),
            "step": step,
            "template_version": "ft_v1",
            "draft_text": _first_touch_text(lead),
            "status": "draft",
            "scheduled_at": now,
            "sent_at": "",
            "failure_reason": "",
            "created_at": now,
            "updated_at": now,
        }
        created.append(draft)
        audit_rows.append(
            {
                "id": stable_id("audit", f"draft_generated:{task_id}"),
                "event_type": "draft_generated",
                "ref_id": task_id,
                "payload": {"lead_id": lead_id, "step": step, "template_version": "ft_v1"},
                "created_at": now,
            }
        )

    out = tasks + created
    out.sort(key=lambda x: safe_str(x.get("created_at")), reverse=True)
    write_jsonl(paths["tasks"], out)
    append_jsonl(paths["audit"], audit_rows)

    return {
        "ok": True,
        "created": len(created),
        "out": paths["tasks"],
        "audit": len(audit_rows),
    }


def main():
    cfg = Config.from_env()
    result = generate_drafts(cfg)
    print(result)


if __name__ == "__main__":
    main()
