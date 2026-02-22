#!/usr/bin/env python3
import os
import sys

CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from _common import Config, now_iso, read_jsonl, sales_paths, safe_str, write_jsonl


ROLE_SIGNALS = {
    "supply_chain": ["supply chain", "procurement", "sourcing", "operations", "buyer", "supply"],
    "investor": ["investor", "analyst", "fund", "portfolio", "capital", "pm"],
    "policy": ["policy", "government", "regulation", "trade", "compliance"],
}

COMPANY_SIGNALS = ["magnet", "battery", "ev", "mining", "materials", "rare earth", "critical mineral"]
COUNTRY_SIGNALS = {"us", "usa", "united states", "uk", "germany", "france", "japan", "korea", "india", "canada", "australia"}


def _contains_any(text: str, words):
    t = safe_str(text).lower()
    return any(w in t for w in words)


def _score_one(lead: dict):
    score = 0
    reason = {}

    role = safe_str(lead.get("role")).lower()
    company = safe_str(lead.get("company")).lower()
    source = safe_str(lead.get("source")).lower()
    country = safe_str(lead.get("country")).lower()
    status = safe_str(lead.get("status")).lower()

    if _contains_any(role, ROLE_SIGNALS["supply_chain"]):
        score += 30
        reason["role_supply_chain"] = 30
    if _contains_any(role, ROLE_SIGNALS["investor"]):
        score += 25
        reason["role_investor"] = 25
    if _contains_any(role, ROLE_SIGNALS["policy"]):
        score += 15
        reason["role_policy"] = 15

    if _contains_any(company, COMPANY_SIGNALS):
        score += 15
        reason["company_signal"] = 15

    if country and country in COUNTRY_SIGNALS:
        score += 10
        reason["country_signal"] = 10

    if any(x in source for x in ["linkedin", "x", "twitter", "manual"]):
        score += 10
        reason["source_priority"] = 10

    if status in {"replied", "qualified", "proposal_sent"}:
        score += 10
        reason["status_intent"] = 10

    score = min(100, max(0, score))
    return float(score), reason


def score_leads(config: Config):
    paths = sales_paths(config)
    leads = read_jsonl(paths["leads"])

    updated = []
    for lead in leads:
        score, reason = _score_one(lead)
        lead["score"] = score
        lead["score_reason"] = reason
        lead["updated_at"] = now_iso()
        updated.append(lead)

    updated.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
    write_jsonl(paths["leads"], updated)

    return {"ok": True, "count": len(updated), "out": paths["leads"]}


def main():
    cfg = Config.from_env()
    result = score_leads(cfg)
    print(result)


if __name__ == "__main__":
    main()
