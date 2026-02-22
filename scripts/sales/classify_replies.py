#!/usr/bin/env python3
import os
import sys

CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from _common import Config, append_jsonl, now_iso, read_jsonl, sales_paths, safe_str, stable_id, write_jsonl


INTERESTED_KEYWORDS = ["interested", "yes", "price", "quote", "trial", "pilot", "task", "sample", "book"]
MAYBE_KEYWORDS = ["maybe", "later", "next month", "revisit", "not now"]
NO_FIT_KEYWORDS = ["not interested", "no thanks", "stop", "unsubscribe", "remove me"]


def _classify(text: str):
    t = safe_str(text).lower()
    if not t:
        return "unknown", 0.0, 0.0
    if any(k in t for k in NO_FIT_KEYWORDS):
        return "no_fit", -0.8, 0.0
    if any(k in t for k in INTERESTED_KEYWORDS):
        return "interested", 0.8, 0.9
    if any(k in t for k in MAYBE_KEYWORDS):
        return "maybe", 0.2, 0.4
    return "unknown", 0.0, 0.2


def classify_replies(config: Config):
    paths = sales_paths(config)
    replies = read_jsonl(paths["replies"])
    leads = read_jsonl(paths["leads"])
    lead_map = {safe_str(x.get("id")): x for x in leads if safe_str(x.get("id"))}

    changed = 0
    lead_updates = 0
    audit_rows = []
    now = now_iso()

    for r in replies:
        label = safe_str(r.get("reply_label"))
        if label and label != "unknown":
            continue

        reply_text = safe_str(r.get("reply_text"))
        new_label, sent, intent = _classify(reply_text)
        r["reply_label"] = new_label
        r["sentiment_score"] = sent
        r["intent_score"] = intent
        if not safe_str(r.get("created_at")):
            r["created_at"] = now
        changed += 1

        lead_id = safe_str(r.get("lead_id"))
        lead = lead_map.get(lead_id)
        if not lead:
            continue

        old_status = safe_str(lead.get("status")).lower() or "new"
        new_status = old_status
        if new_label == "interested":
            new_status = "qualified"
        elif new_label == "maybe" and old_status in {"new", "contacted"}:
            new_status = "replied"
        elif new_label == "no_fit":
            new_status = "lost"

        if new_status != old_status:
            lead["status"] = new_status
            lead["updated_at"] = now
            lead_updates += 1
            audit_rows.append(
                {
                    "id": stable_id("audit", f"reply_classified:{lead_id}:{r.get('id') or ''}:{now}"),
                    "event_type": "reply_classified",
                    "ref_id": safe_str(r.get("id")),
                    "payload": {
                        "lead_id": lead_id,
                        "from_status": old_status,
                        "to_status": new_status,
                        "reply_label": new_label,
                    },
                    "created_at": now,
                }
            )

    out_leads = list(lead_map.values()) + [x for x in leads if safe_str(x.get("id")) not in lead_map]
    out_leads.sort(key=lambda x: safe_str(x.get("updated_at") or x.get("created_at")), reverse=True)

    write_jsonl(paths["replies"], replies)
    write_jsonl(paths["leads"], out_leads)
    append_jsonl(paths["audit"], audit_rows)

    return {
        "ok": True,
        "replies_classified": changed,
        "leads_updated": lead_updates,
        "audit": len(audit_rows),
    }


def main():
    cfg = Config.from_env()
    result = classify_replies(cfg)
    print(result)


if __name__ == "__main__":
    main()
