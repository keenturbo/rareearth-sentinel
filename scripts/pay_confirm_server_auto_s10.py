#!/usr/bin/env python3
"""
S10 P0 extension wrapper for team-brief today-list API.

Builds on top of pay_confirm_server_auto_s5 and adds:
- GET /api/admin/leads/team-brief/today-list

This keeps S5 behavior unchanged while providing server-side grouping
for today/overdue/expiring/high_priority/upsell_due.
"""

import os
from datetime import datetime, timedelta
from typing import Dict, List
from urllib.parse import parse_qs, urlparse

import pay_confirm_server_auto_s5 as s5

pcs = s5.pcs

_TODAY_LIST_ENDPOINT = "/api/admin/leads/team-brief/today-list"
_ORIG_DO_GET = pcs.PayConfirmHandler.do_GET


def _utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _parse_iso_dt(value: str):
    text = s5._normalize_text(value, 64)
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text[:16], fmt)
        except Exception:
            continue
    return None


def _parse_iso_date(value: str):
    text = s5._normalize_text(value, 32)
    if not text:
        return None
    text = text[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except Exception:
        return None


def _derive_upsell_due(row: Dict):
    direct = _parse_iso_date(row.get("upsell_due_at"))
    if direct:
        return direct
    base = (
        _parse_iso_dt(row.get("won_at"))
        or _parse_iso_dt(row.get("updated_at"))
        or _parse_iso_dt(row.get("created_at"))
    )
    if not base:
        return None
    return (base + timedelta(days=7)).date()


def _sort_created_desc(rows: List[Dict]) -> List[Dict]:
    out = list(rows or [])
    out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return out


def _dedupe_by_lead(rows: List[Dict]) -> List[Dict]:
    out = []
    seen = set()
    for r in rows or []:
        lid = s5._normalize_text((r or {}).get("lead_id"), 80)
        if not lid or lid in seen:
            continue
        seen.add(lid)
        out.append(r)
    return out


def _build_today_groups(rows: List[Dict], limit: int = 800) -> Dict:
    normalized = [s5._normalize_lead_row(r) for r in (rows or [])]
    stats = s5._status_counts(normalized)

    now = datetime.utcnow() + timedelta(hours=8)
    today = now.date()
    exp_end = today + timedelta(days=3)

    active = [r for r in normalized if str(r.get("status") or "").lower() not in {"won", "lost"}]

    high_priority = [r for r in active if str(r.get("status") or "").lower() == "qualified"]

    due_today = []
    overdue = []
    expiring = []

    for r in active:
        next_dt = _parse_iso_dt(r.get("next_action_at"))
        if next_dt:
            if next_dt.date() == today:
                due_today.append(r)
            elif next_dt < now:
                overdue.append(r)

        qvu = _parse_iso_date(r.get("quote_valid_until"))
        if qvu and today <= qvu <= exp_end:
            expiring.append(r)

    upsell_due = []
    for r in normalized:
        if str(r.get("status") or "").lower() != "won":
            continue
        if s5._normalize_text(r.get("upsell_status"), 20).lower() == "done":
            continue
        due = _derive_upsell_due(r)
        if due and due <= today:
            rr = dict(r)
            rr["_display_due"] = due.isoformat()
            upsell_due.append(rr)

    # Sort for operational readability
    high_priority = _sort_created_desc(high_priority)
    due_today = _sort_created_desc(due_today)
    overdue = _sort_created_desc(overdue)
    expiring = _sort_created_desc(expiring)
    upsell_due = _sort_created_desc(upsell_due)

    # Dedupe by lead_id
    high_priority = _dedupe_by_lead(high_priority)
    due_today = _dedupe_by_lead(due_today)
    overdue = _dedupe_by_lead(overdue)
    expiring = _dedupe_by_lead(expiring)
    upsell_due = _dedupe_by_lead(upsell_due)

    lim = max(1, min(int(limit or 800), 5000))

    return {
        "generated_at": _utc_now_iso(),
        "total": len(normalized),
        "stats": stats,
        "high_priority": high_priority[:lim],
        "today": due_today[:lim],
        "overdue": overdue[:lim],
        "expiring": expiring[:lim],
        "upsell_due": upsell_due[:lim],
    }


def _patched_do_GET(self):
    try:
        parsed = urlparse(self.path)
        normalized_path = pcs._normalize_path(parsed.path)

        if normalized_path == pcs._normalize_path(_TODAY_LIST_ENDPOINT):
            q = parse_qs(parsed.query)
            try:
                limit = int((q.get("limit", ["800"])[0] or "800"))
            except Exception:
                limit = 800

            rows = s5._read_jsonl_rows(s5._leads_path(self.config))
            payload = _build_today_groups(rows, limit=limit)
            self._json_response(200, {"ok": True, **payload})
            return
    except Exception:
        self._json_response(500, {"ok": False, "error": "server_error"})
        return

    return _ORIG_DO_GET(self)


pcs.PayConfirmHandler.do_GET = _patched_do_GET


if __name__ == "__main__":
    pcs.main()
