#!/usr/bin/env python3
"""
S10 P0 extension wrapper for team-brief today-list API.

Builds on top of pay_confirm_server_auto_s5 and adds:
- GET  /api/admin/leads/team-brief/today-list
- GET  /api/admin/sales/sdr/tasks/list
- POST /api/admin/sales/sdr/tasks/update

This keeps S5 behavior unchanged while providing:
- server-side grouping for today/overdue/expiring/high_priority/upsell_due
- SDR task manual status update (draft -> approved -> sent/cancelled)
- S14 P0: auto-create followup_d1 task when first_touch is marked sent (idempotent)
"""

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List
from urllib.parse import parse_qs, urlparse

import pay_confirm_server_auto_s5 as s5

pcs = s5.pcs

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_TODAY_LIST_ENDPOINT = "/api/admin/leads/team-brief/today-list"
_SDR_TASK_LIST_ENDPOINT = "/api/admin/sales/sdr/tasks/list"
_SDR_TASK_UPDATE_ENDPOINT = "/api/admin/sales/sdr/tasks/update"

_ALLOWED_TASK_STATUSES = {"draft", "approved", "sent", "failed", "cancelled"}
_TASK_LOCK = threading.Lock()

_ORIG_DO_GET = pcs.PayConfirmHandler.do_GET
_ORIG_DO_POST = pcs.PayConfirmHandler.do_POST


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

    high_priority = _sort_created_desc(high_priority)
    due_today = _sort_created_desc(due_today)
    overdue = _sort_created_desc(overdue)
    expiring = _sort_created_desc(expiring)
    upsell_due = _sort_created_desc(upsell_due)

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


def _sdr_tasks_path(config) -> str:
    return os.path.join(config.data_dir, "sales", "outreach_tasks.jsonl")


def _sdr_audit_path(config) -> str:
    return os.path.join(config.data_dir, "sales", "audit_logs.jsonl")


def _sdr_leads_path(config) -> str:
    return os.path.join(config.data_dir, "sales", "leads.jsonl")


def _normalize_task_row(row: Dict) -> Dict:
    out = dict(row or {})
    out["id"] = s5._normalize_text(out.get("id"), 80)
    out["lead_id"] = s5._normalize_text(out.get("lead_id"), 80)
    out["channel"] = s5._normalize_text(out.get("channel"), 40)
    out["step"] = s5._normalize_text(out.get("step"), 40)
    out["template_version"] = s5._normalize_text(out.get("template_version"), 40)
    out["draft_text"] = s5._normalize_text(out.get("draft_text"), 4000)
    st = s5._normalize_text(out.get("status"), 20).lower() or "draft"
    if st not in _ALLOWED_TASK_STATUSES:
        st = "draft"
    out["status"] = st
    out["scheduled_at"] = s5._normalize_text(out.get("scheduled_at"), 40)
    out["sent_at"] = s5._normalize_text(out.get("sent_at"), 40)
    out["failure_reason"] = s5._normalize_text(out.get("failure_reason"), 500)
    out["created_at"] = s5._normalize_text(out.get("created_at"), 40)
    out["updated_at"] = s5._normalize_text(out.get("updated_at"), 40)
    return out


def _read_sdr_tasks(config) -> List[Dict]:
    rows = s5._read_jsonl_rows(_sdr_tasks_path(config))
    out = [_normalize_task_row(r) for r in rows]
    out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return out


def _write_sdr_tasks(config, rows: List[Dict]):
    path = _sdr_tasks_path(config)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows or []:
            f.write(json.dumps(_normalize_task_row(row), ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _append_sdr_audit(config, event_type: str, ref_id: str, payload: Dict):
    row = {
        "id": f"audit-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{os.urandom(2).hex()}",
        "event_type": event_type,
        "ref_id": ref_id,
        "payload": payload or {},
        "created_at": _utc_now_iso(),
    }
    try:
        s5.base._ORIGINAL_APPEND_JSONL(_sdr_audit_path(config), row)
    except Exception:
        os.makedirs(os.path.dirname(_sdr_audit_path(config)), exist_ok=True)
        with open(_sdr_audit_path(config), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _task_stats(rows: List[Dict]) -> Dict[str, int]:
    stats = {k: 0 for k in _ALLOWED_TASK_STATUSES}
    for r in rows or []:
        st = s5._normalize_text((r or {}).get("status"), 20).lower()
        if st in stats:
            stats[st] += 1
    return stats


def _read_sdr_leads_map(config) -> Dict[str, Dict]:
    rows = s5._read_jsonl_rows(_sdr_leads_path(config))
    out: Dict[str, Dict] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        lid = s5._normalize_text(r.get("id"), 80)
        if lid:
            out[lid] = dict(r)
    return out


def _dt_to_utc_iso(dt_obj) -> str:
    if not dt_obj:
        return _utc_now_iso()
    try:
        if getattr(dt_obj, "tzinfo", None) is not None:
            dt_obj = dt_obj.astimezone(timezone.utc)
            return dt_obj.replace(tzinfo=None, microsecond=0).isoformat() + "Z"
        return dt_obj.replace(microsecond=0).isoformat() + "Z"
    except Exception:
        return _utc_now_iso()


def _build_followup_d1_text(lead: Dict) -> str:
    name = s5._normalize_text((lead or {}).get("display_name"), 80) or s5._normalize_text((lead or {}).get("name"), 80) or "there"
    return f"Hi {name}, quick follow-up — would a 24h rare-earth action memo be useful for your current watchlist?"


def _has_sdr_task(rows: List[Dict], lead_id: str, channel: str, step: str) -> bool:
    lid = s5._normalize_text(lead_id, 80)
    ch = s5._normalize_text(channel, 40)
    stp = s5._normalize_text(step, 40)
    for row in rows or []:
        cur = _normalize_task_row(row)
        if cur.get("lead_id") != lid:
            continue
        if cur.get("channel") != ch:
            continue
        if cur.get("step") != stp:
            continue
        return True
    return False


def _make_sdr_task_id() -> str:
    return f"otask-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{os.urandom(2).hex()}"


def _maybe_create_followup_d1_locked(config, rows: List[Dict], trigger_task: Dict, operator: str) -> Dict:
    cur = _normalize_task_row(trigger_task)
    trigger_task_id = cur.get("id") or ""
    lead_id = cur.get("lead_id") or ""
    channel = cur.get("channel") or ""
    step = cur.get("step") or ""

    def _skip(reason: str, extra: Dict = None) -> Dict:
        payload = {
            "reason": reason,
            "trigger_task_id": trigger_task_id,
            "lead_id": lead_id,
            "channel": channel,
            "step": step,
            "operator": operator,
        }
        if extra:
            payload.update(extra)
        _append_sdr_audit(config, "sdr_task_auto_followup_skipped", trigger_task_id, payload)
        return {"action": "skipped", "reason": reason}

    if cur.get("status") != "sent":
        return {"action": "none"}
    if step != "first_touch":
        return _skip("not_first_touch")
    if not lead_id or not channel:
        return _skip("missing_lead_or_channel")
    if _has_sdr_task(rows, lead_id, channel, "followup_d1"):
        return _skip("already_exists")

    lead = _read_sdr_leads_map(config).get(lead_id, {})
    sent_dt = _parse_iso_dt(cur.get("sent_at") or cur.get("updated_at") or cur.get("created_at"))
    if not sent_dt:
        sent_dt = datetime.utcnow().replace(tzinfo=timezone.utc)
    elif sent_dt.tzinfo is None:
        sent_dt = sent_dt.replace(tzinfo=timezone.utc)

    now_iso = _utc_now_iso()
    scheduled_at = _dt_to_utc_iso(sent_dt + timedelta(days=1))
    task_id = _make_sdr_task_id()
    new_task = {
        "id": task_id,
        "lead_id": lead_id,
        "channel": channel,
        "step": "followup_d1",
        "template_version": "followup_d1_v1",
        "draft_text": _build_followup_d1_text(lead),
        "status": "draft",
        "scheduled_at": scheduled_at,
        "sent_at": "",
        "failure_reason": "",
        "created_at": now_iso,
        "updated_at": now_iso,
        "auto_generated": True,
        "trigger_task_id": trigger_task_id,
        "trigger_rule": "sent_to_followup_d1",
    }
    rows.append(new_task)

    _append_sdr_audit(
        config,
        "sdr_task_auto_followup_created",
        task_id,
        {
            "trigger_task_id": trigger_task_id,
            "lead_id": lead_id,
            "channel": channel,
            "step": "followup_d1",
            "operator": operator,
            "scheduled_at": scheduled_at,
        },
    )
    return {
        "action": "created",
        "task_id": task_id,
        "step": "followup_d1",
        "scheduled_at": scheduled_at,
        "trigger_task_id": trigger_task_id,
    }


def _list_sdr_tasks(config, status: str = "", limit: int = 200) -> Dict:
    rows = _read_sdr_tasks(config)
    st = s5._normalize_text(status, 20).lower()
    if st in _ALLOWED_TASK_STATUSES:
        rows = [r for r in rows if s5._normalize_text(r.get("status"), 20).lower() == st]
    lim = max(1, min(int(limit or 200), 2000))
    return {
        "total": len(rows),
        "stats": _task_stats(rows),
        "tasks": rows[:lim],
    }


def _update_sdr_task(config, payload: Dict):
    no_auto = {"action": "none"}
    task_id = s5._normalize_text((payload or {}).get("task_id"), 80)
    if not task_id:
        return {}, "missing_task_id", no_auto

    new_status = s5._normalize_text((payload or {}).get("status"), 20).lower()
    if new_status not in _ALLOWED_TASK_STATUSES:
        return {}, "invalid_status", no_auto

    operator = s5._normalize_text((payload or {}).get("operator"), 40) or "admin_sdr"
    note = s5._normalize_text((payload or {}).get("note"), 500)
    sent_at_in = s5._normalize_text((payload or {}).get("sent_at"), 40)

    auto_followup = no_auto

    with _TASK_LOCK:
        rows = _read_sdr_tasks(config)
        updated = None

        for idx, row in enumerate(rows):
            cur = _normalize_task_row(row)
            if cur.get("id") != task_id:
                continue

            before = cur.get("status")
            cur["status"] = new_status
            cur["updated_at"] = _utc_now_iso()

            if new_status == "sent":
                cur["sent_at"] = sent_at_in or cur.get("sent_at") or _utc_now_iso()
            if note:
                if new_status in {"failed", "cancelled"}:
                    cur["failure_reason"] = note
                cur["operator_note"] = note

            rows[idx] = cur
            updated = cur

            _append_sdr_audit(
                config,
                "sdr_task_status_change",
                task_id,
                {
                    "from_status": before,
                    "to_status": new_status,
                    "operator": operator,
                    "note": note,
                    "lead_id": cur.get("lead_id"),
                    "step": cur.get("step"),
                },
            )

            if new_status == "sent" and before != "sent":
                try:
                    auto_followup = _maybe_create_followup_d1_locked(config, rows, cur, operator)
                except Exception as e:
                    auto_followup = {"action": "skipped", "reason": "exception"}
                    _append_sdr_audit(
                        config,
                        "sdr_task_auto_followup_skipped",
                        task_id,
                        {
                            "reason": "exception",
                            "operator": operator,
                            "lead_id": cur.get("lead_id"),
                            "channel": cur.get("channel"),
                            "step": cur.get("step"),
                            "error": s5._normalize_text(str(e), 200),
                        },
                    )
            break

        if not updated:
            return {}, "task_not_found", no_auto

        rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        _write_sdr_tasks(config, rows)

    return updated, "", auto_followup


def _refresh_admin_sdr_views() -> None:
    py = sys.executable or "python3"
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT

    scripts = [
        os.path.join(ROOT, "scripts", "sales", "build_sdr_daily_snapshot.py"),
        os.path.join(ROOT, "scripts", "build_admin_sales_sdr.py"),
    ]
    for script in scripts:
        try:
            subprocess.run([py, script], cwd=ROOT, env=env, timeout=120, check=False)
        except Exception:
            continue


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

        if normalized_path == pcs._normalize_path(_SDR_TASK_LIST_ENDPOINT):
            q = parse_qs(parsed.query)
            status = (q.get("status", [""])[0] or "").strip().lower()
            try:
                limit = int((q.get("limit", ["200"])[0] or "200"))
            except Exception:
                limit = 200

            payload = _list_sdr_tasks(self.config, status=status, limit=limit)
            self._json_response(200, {"ok": True, **payload})
            return

    except Exception:
        self._json_response(500, {"ok": False, "error": "server_error"})
        return

    return _ORIG_DO_GET(self)


def _patched_do_POST(self):
    try:
        parsed = urlparse(self.path)
        normalized_path = pcs._normalize_path(parsed.path)

        if normalized_path == pcs._normalize_path(_SDR_TASK_UPDATE_ENDPOINT):
            content_len = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(content_len) if content_len else b"{}"
            try:
                data = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                self._json_response(400, {"ok": False, "error": "bad_json"})
                return

            task, err, auto_followup = _update_sdr_task(self.config, data or {})
            if err:
                code = 404 if err == "task_not_found" else 400
                self._json_response(code, {"ok": False, "error": err})
                return

            try:
                _refresh_admin_sdr_views()
            except Exception:
                pass

            self._json_response(200, {"ok": True, "task": task, "auto_followup": auto_followup})
            return

    except Exception:
        self._json_response(500, {"ok": False, "error": "server_error"})
        return

    return _ORIG_DO_POST(self)


pcs.PayConfirmHandler.do_GET = _patched_do_GET
pcs.PayConfirmHandler.do_POST = _patched_do_POST


if __name__ == "__main__":
    pcs.main()
