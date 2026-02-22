#!/usr/bin/env python3
import os
import sys

CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from _common import Config, now_iso, read_jsonl, sales_paths, safe_str, stable_id, write_jsonl


STATUS_MAP = {
    "new": "new",
    "contacted": "contacted",
    "qualified": "qualified",
    "proposal_sent": "proposal_sent",
    "won": "won",
    "lost": "lost",
}


def _norm_status(value: str) -> str:
    key = safe_str(value).lower()
    return STATUS_MAP.get(key, "new")


def _lead_key(lead: dict) -> str:
    profile = safe_str(lead.get("profile_url")).lower()
    if profile:
        return f"p:{profile}"
    email = safe_str(lead.get("email")).lower()
    if email:
        return f"e:{email}"
    name = safe_str(lead.get("display_name") or lead.get("name")).lower()
    company = safe_str(lead.get("company")).lower()
    return f"n:{name}|c:{company}"


def _from_team_brief(row: dict) -> dict:
    source = safe_str(row.get("source_channel") or "team_brief")
    display_name = safe_str(row.get("name"))
    company = safe_str(row.get("company"))
    role = safe_str(row.get("role"))
    profile_url = safe_str(row.get("profile_url"))
    email = safe_str(row.get("email"))

    lead_id = safe_str(row.get("lead_id"))
    sid = stable_id("slead", lead_id or f"{display_name}|{company}|{email}")

    tags = []
    if source:
        tags.append(source)
    budget = safe_str(row.get("budget_range"))
    if budget:
        tags.append(budget)

    created_at = safe_str(row.get("created_at")) or now_iso()
    updated_at = safe_str(row.get("updated_at")) or created_at

    return {
        "id": sid,
        "source": source,
        "profile_url": profile_url,
        "display_name": display_name,
        "name": display_name,
        "email": email,
        "role": role,
        "company": company,
        "country": safe_str(row.get("country")),
        "language": safe_str(row.get("language") or "en"),
        "tags": tags,
        "score": float(row.get("score") or 0),
        "score_reason": row.get("score_reason") or {},
        "status": _norm_status(row.get("status")),
        "owner": safe_str(row.get("owner") or "keenturbo"),
        "next_followup_at": safe_str(row.get("next_action_at")),
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _from_manual(row: dict) -> dict:
    source = safe_str(row.get("source") or "manual")
    display_name = safe_str(row.get("display_name") or row.get("name"))
    company = safe_str(row.get("company"))
    email = safe_str(row.get("email"))
    sid = safe_str(row.get("id")) or stable_id("slead", f"{display_name}|{company}|{email}")

    tags = row.get("tags") if isinstance(row.get("tags"), list) else []
    created_at = safe_str(row.get("created_at")) or now_iso()
    updated_at = safe_str(row.get("updated_at")) or created_at

    return {
        "id": sid,
        "source": source,
        "profile_url": safe_str(row.get("profile_url")),
        "display_name": display_name,
        "name": display_name,
        "email": email,
        "role": safe_str(row.get("role")),
        "company": company,
        "country": safe_str(row.get("country")),
        "language": safe_str(row.get("language") or "en"),
        "tags": tags,
        "score": float(row.get("score") or 0),
        "score_reason": row.get("score_reason") or {},
        "status": _norm_status(row.get("status")),
        "owner": safe_str(row.get("owner") or "keenturbo"),
        "next_followup_at": safe_str(row.get("next_followup_at")),
        "created_at": created_at,
        "updated_at": updated_at,
    }


def build_lead_pool(config: Config):
    paths = sales_paths(config)

    existing = read_jsonl(paths["leads"])
    merged = {}

    for row in existing:
        k = _lead_key(row)
        if not k:
            continue
        merged[k] = row

    source_rows = read_jsonl(paths["team_brief_leads"])
    for row in source_rows:
        lead = _from_team_brief(row)
        k = _lead_key(lead)
        if not k:
            continue
        cur = merged.get(k)
        if cur:
            lead["id"] = safe_str(cur.get("id")) or lead["id"]
            lead["score"] = float(cur.get("score") or lead["score"])
            lead["score_reason"] = cur.get("score_reason") or lead["score_reason"]
            lead["status"] = _norm_status(cur.get("status") or lead["status"])
            lead["owner"] = safe_str(cur.get("owner") or lead["owner"])
            lead["created_at"] = safe_str(cur.get("created_at") or lead["created_at"])
        merged[k] = lead

    manual_rows = read_jsonl(paths["manual_leads"])
    for row in manual_rows:
        lead = _from_manual(row)
        k = _lead_key(lead)
        if not k:
            continue
        cur = merged.get(k)
        if cur:
            cur.update({k2: v2 for k2, v2 in lead.items() if safe_str(v2)})
            cur["updated_at"] = now_iso()
            merged[k] = cur
        else:
            merged[k] = lead

    out_rows = list(merged.values())
    out_rows.sort(key=lambda x: safe_str(x.get("updated_at") or x.get("created_at")), reverse=True)
    write_jsonl(paths["leads"], out_rows)

    return {
        "ok": True,
        "out": paths["leads"],
        "count": len(out_rows),
        "from_team_brief": len(source_rows),
        "from_manual": len(manual_rows),
    }


def main():
    cfg = Config.from_env()
    result = build_lead_pool(cfg)
    print(result)


if __name__ == "__main__":
    main()
