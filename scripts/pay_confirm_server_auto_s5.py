#!/usr/bin/env python3
"""
S5 extension wrapper for team brief leads CRM APIs.

Keeps existing pay_confirm_server_auto behavior, and adds:
- GET  /api/admin/leads/team-brief/list
- POST /api/admin/leads/team-brief/update
- GET  /api/admin/leads/team-brief/export
- GET  /api/admin/leads/team-brief/events
- POST /api/admin/leads/team-brief/insert-test
- POST /api/admin/leads/team-brief/clear-test
"""

import csv
import io
import json
import os
import sys
import threading
from datetime import datetime
from typing import Dict, List, Tuple
from urllib.parse import parse_qs, urlparse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pay_confirm_server_auto as base  # noqa: E402

pcs = base.pcs
_CFG = pcs.Config.from_env()

_LIST_ENDPOINT = "/api/admin/leads/team-brief/list"
_UPDATE_ENDPOINT = "/api/admin/leads/team-brief/update"
_EXPORT_ENDPOINT = "/api/admin/leads/team-brief/export"
_EVENTS_ENDPOINT = "/api/admin/leads/team-brief/events"
_INSERT_TEST_ENDPOINT = "/api/admin/leads/team-brief/insert-test"
_CLEAR_TEST_ENDPOINT = "/api/admin/leads/team-brief/clear-test"

_ALLOWED_STATUSES = {"new", "contacted", "qualified", "proposal_sent", "won", "lost"}
_ALLOWED_QUOTE_CURRENCY = {"USD", "CNY"}
_ALLOWED_QUOTE_PLAN = {"basic", "pro", "custom"}
_LEAD_LOCK = threading.Lock()

_ORIG_DO_GET = pcs.PayConfirmHandler.do_GET
_ORIG_DO_POST = pcs.PayConfirmHandler.do_POST


def _utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _normalize_text(value, max_len=2000):
    text = str(value or "").strip()
    text = " ".join(text.split())
    return text[:max_len]


def _leads_path(config):
    return os.path.join(config.data_dir, "pay", "team_brief_leads.jsonl")


def _lead_events_path(config):
    return os.path.join(config.data_dir, "pay", "team_brief_lead_events.jsonl")


def _read_jsonl_rows(path: str) -> List[Dict]:
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


def _write_jsonl_rows(path: str, rows: List[Dict]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp_path, path)


def _is_test_row(row: Dict) -> bool:
    if bool((row or {}).get("test")):
        return True
    if _normalize_text((row or {}).get("source_channel"), 40).lower() == "admin_test":
        return True
    email = _normalize_text((row or {}).get("email"), 120).lower()
    return email.startswith("admin-test-")


def _normalize_lead_row(row: Dict) -> Dict:
    out = dict(row or {})
    out["lead_id"] = _normalize_text(out.get("lead_id"), 80)
    out["created_at"] = _normalize_text(out.get("created_at"), 40)
    out["name"] = _normalize_text(out.get("name"), 80)
    out["email"] = _normalize_text(out.get("email"), 120)
    out["company"] = _normalize_text(out.get("company"), 120)
    out["role"] = _normalize_text(out.get("role"), 120)
    out["country_region"] = _normalize_text(out.get("country_region"), 80)
    out["brief_need"] = _normalize_text(out.get("brief_need"), 40)
    out["budget_range"] = _normalize_text(out.get("budget_range"), 40)
    out["message"] = _normalize_text(out.get("message"), 1200)
    out["lang"] = _normalize_text(out.get("lang"), 8) or "cn"
    out["source_channel"] = _normalize_text(out.get("source_channel"), 40)
    out["utm_source"] = _normalize_text(out.get("utm_source"), 120)
    out["utm_campaign"] = _normalize_text(out.get("utm_campaign"), 120)
    out["utm_content"] = _normalize_text(out.get("utm_content"), 120)
    out["ip_hash"] = _normalize_text(out.get("ip_hash"), 128)

    status = _normalize_text(out.get("status"), 20).lower() or "new"
    if status not in _ALLOWED_STATUSES:
        status = "new"
    out["status"] = status

    out["owner"] = _normalize_text(out.get("owner"), 80)
    out["next_action_at"] = _normalize_text(out.get("next_action_at"), 40)
    out["updated_at"] = _normalize_text(out.get("updated_at"), 40)

    qc = _normalize_text(out.get("quote_currency"), 8).upper() or "USD"
    if qc not in _ALLOWED_QUOTE_CURRENCY:
        qc = "USD"
    out["quote_currency"] = qc
    qa = _normalize_text(out.get("quote_amount"), 40)
    out["quote_amount"] = qa
    qp = _normalize_text(out.get("quote_plan"), 20).lower() or "basic"
    if qp not in _ALLOWED_QUOTE_PLAN:
        qp = "basic"
    out["quote_plan"] = qp
    out["quote_valid_until"] = _normalize_text(out.get("quote_valid_until"), 20)
    out["last_proposal_at"] = _normalize_text(out.get("last_proposal_at"), 40)

    try:
        out["notes_count"] = int(out.get("notes_count") or 0)
    except (TypeError, ValueError):
        out["notes_count"] = 0

    if bool(out.get("test")):
        out["test"] = True
    else:
        out["test"] = _is_test_row(out)

    return out


def _status_counts(rows: List[Dict]) -> Dict[str, int]:
    stats = {k: 0 for k in _ALLOWED_STATUSES}
    for row in rows:
        st = str(row.get("status") or "new").lower()
        if st in stats:
            stats[st] += 1
    return stats


def _filter_rows(rows: List[Dict], status: str, owner: str, keyword: str) -> List[Dict]:
    status = str(status or "").strip().lower()
    owner = str(owner or "").strip().lower()
    keyword = str(keyword or "").strip().lower()

    out = []
    for row in rows:
        r = _normalize_lead_row(row)
        if status and r.get("status") != status:
            continue
        if owner and owner not in str(r.get("owner") or "").lower():
            continue
        if keyword:
            hay = " ".join(
                [
                    str(r.get("lead_id") or ""),
                    str(r.get("name") or ""),
                    str(r.get("email") or ""),
                    str(r.get("company") or ""),
                    str(r.get("message") or ""),
                ]
            ).lower()
            if keyword not in hay:
                continue
        out.append(r)

    out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return out


def _events_for_lead(config, lead_id: str, limit: int = 100) -> List[Dict]:
    rows = _read_jsonl_rows(_lead_events_path(config))
    lead_id = _normalize_text(lead_id, 80)
    out = []
    for row in rows:
        if _normalize_text((row or {}).get("lead_id"), 80) == lead_id:
            out.append(dict(row))
    out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return out[: max(1, min(limit, 500))]


def _append_lead_event(config, payload: Dict):
    evt = dict(payload or {})
    evt.setdefault("event_id", f"lgev-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{os.urandom(2).hex()}")
    evt.setdefault("created_at", _utc_now_iso())
    base._ORIGINAL_APPEND_JSONL(_lead_events_path(config), evt)


def _normalize_quote_fields(payload: Dict) -> Tuple[Dict, str]:
    quote_currency = _normalize_text((payload or {}).get("quote_currency"), 8).upper() or "USD"
    if quote_currency not in _ALLOWED_QUOTE_CURRENCY:
        return {}, "invalid_quote_currency"

    quote_plan = _normalize_text((payload or {}).get("quote_plan"), 20).lower() or "basic"
    if quote_plan not in _ALLOWED_QUOTE_PLAN:
        return {}, "invalid_quote_plan"

    quote_amount_raw = _normalize_text((payload or {}).get("quote_amount"), 40)
    quote_amount = ""
    if quote_amount_raw:
        try:
            quote_amount_val = float(quote_amount_raw)
            if quote_amount_val < 0:
                return {}, "invalid_quote_amount"
            quote_amount = f"{quote_amount_val:.2f}".rstrip("0").rstrip(".")
        except (TypeError, ValueError):
            return {}, "invalid_quote_amount"

    quote_valid_until = _normalize_text((payload or {}).get("quote_valid_until"), 20)
    if quote_valid_until:
        try:
            datetime.strptime(quote_valid_until, "%Y-%m-%d")
        except ValueError:
            return {}, "invalid_quote_valid_until"

    return {
        "quote_currency": quote_currency,
        "quote_amount": quote_amount,
        "quote_plan": quote_plan,
        "quote_valid_until": quote_valid_until,
    }, ""


def _update_lead(config, payload: Dict) -> Tuple[Dict, str]:
    lead_id = _normalize_text((payload or {}).get("lead_id"), 80)
    if not lead_id:
        return {}, "missing_lead_id"

    new_status = _normalize_text((payload or {}).get("status"), 20).lower()
    if new_status and new_status not in _ALLOWED_STATUSES:
        return {}, "invalid_status"

    owner = _normalize_text((payload or {}).get("owner"), 80)
    next_action_at = _normalize_text((payload or {}).get("next_action_at"), 40)
    note = _normalize_text((payload or {}).get("note"), 1200)
    operator = _normalize_text((payload or {}).get("operator"), 40) or "admin"

    quote_fields, quote_err = _normalize_quote_fields(payload or {})
    if quote_err:
        return {}, quote_err

    path = _leads_path(config)
    with _LEAD_LOCK:
        rows = _read_jsonl_rows(path)
        if not rows:
            return {}, "lead_not_found"

        updated = None
        for idx, row in enumerate(rows):
            cur = _normalize_lead_row(row)
            if cur.get("lead_id") != lead_id:
                continue

            before_status = cur.get("status")
            before_owner = cur.get("owner")
            before_next = cur.get("next_action_at")
            before_quote = {
                "quote_currency": cur.get("quote_currency"),
                "quote_amount": cur.get("quote_amount"),
                "quote_plan": cur.get("quote_plan"),
                "quote_valid_until": cur.get("quote_valid_until"),
            }

            if new_status:
                cur["status"] = new_status
            cur["owner"] = owner
            cur["next_action_at"] = next_action_at
            cur["updated_at"] = _utc_now_iso()

            cur["quote_currency"] = quote_fields.get("quote_currency", cur.get("quote_currency") or "USD")
            cur["quote_amount"] = quote_fields.get("quote_amount", cur.get("quote_amount") or "")
            cur["quote_plan"] = quote_fields.get("quote_plan", cur.get("quote_plan") or "basic")
            cur["quote_valid_until"] = quote_fields.get("quote_valid_until", cur.get("quote_valid_until") or "")

            if new_status == "proposal_sent":
                cur["last_proposal_at"] = _utc_now_iso()

            if note:
                cur["notes_count"] = int(cur.get("notes_count") or 0) + 1

            rows[idx] = cur
            updated = cur

            if before_status != cur.get("status"):
                _append_lead_event(
                    config,
                    {
                        "lead_id": lead_id,
                        "event_type": "status_change",
                        "from_status": before_status,
                        "to_status": cur.get("status"),
                        "operator": operator,
                    },
                )
            if before_owner != cur.get("owner"):
                _append_lead_event(
                    config,
                    {
                        "lead_id": lead_id,
                        "event_type": "owner_change",
                        "owner": cur.get("owner"),
                        "operator": operator,
                    },
                )
            if before_next != cur.get("next_action_at"):
                _append_lead_event(
                    config,
                    {
                        "lead_id": lead_id,
                        "event_type": "next_action_set",
                        "next_action_at": cur.get("next_action_at"),
                        "operator": operator,
                    },
                )

            if before_quote != {
                "quote_currency": cur.get("quote_currency"),
                "quote_amount": cur.get("quote_amount"),
                "quote_plan": cur.get("quote_plan"),
                "quote_valid_until": cur.get("quote_valid_until"),
            }:
                _append_lead_event(
                    config,
                    {
                        "lead_id": lead_id,
                        "event_type": "proposal_created",
                        "quote_currency": cur.get("quote_currency"),
                        "quote_amount": cur.get("quote_amount"),
                        "quote_plan": cur.get("quote_plan"),
                        "quote_valid_until": cur.get("quote_valid_until"),
                        "operator": operator,
                    },
                )

            if new_status == "proposal_sent":
                _append_lead_event(
                    config,
                    {
                        "lead_id": lead_id,
                        "event_type": "proposal_sent",
                        "quote_currency": cur.get("quote_currency"),
                        "quote_amount": cur.get("quote_amount"),
                        "quote_plan": cur.get("quote_plan"),
                        "quote_valid_until": cur.get("quote_valid_until"),
                        "operator": operator,
                    },
                )

            if note:
                _append_lead_event(
                    config,
                    {
                        "lead_id": lead_id,
                        "event_type": "note_append",
                        "note": note,
                        "operator": operator,
                    },
                )
            break

        if not updated:
            return {}, "lead_not_found"

        _write_jsonl_rows(path, rows)
        return updated, ""


def _insert_test_lead(config, operator: str = "admin") -> Dict:
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    lead = {
        "lead_id": f"tb-test-{ts}-{os.urandom(2).hex()}",
        "created_at": _utc_now_iso(),
        "name": "Admin Test Lead",
        "email": f"admin-test-{ts}@example.com",
        "company": "Test Company",
        "role": "Analyst",
        "country_region": "CN",
        "brief_need": "custom",
        "budget_range": "unknown",
        "message": f"Admin test lead {ts}",
        "lang": "cn",
        "source_channel": "admin_test",
        "utm_source": "admin",
        "utm_campaign": "team_brief_test",
        "utm_content": "empty_state",
        "ip_hash": "admin_test",
        "status": "new",
        "owner": "",
        "next_action_at": "",
        "updated_at": _utc_now_iso(),
        "notes_count": 0,
        "test": True,
        "quote_currency": "USD",
        "quote_amount": "",
        "quote_plan": "basic",
        "quote_valid_until": "",
        "last_proposal_at": "",
    }
    with _LEAD_LOCK:
        rows = _read_jsonl_rows(_leads_path(config))
        rows.append(lead)
        _write_jsonl_rows(_leads_path(config), rows)
    _append_lead_event(
        config,
        {
            "lead_id": lead["lead_id"],
            "event_type": "lead_created",
            "note": "admin insert test lead",
            "operator": operator,
        },
    )
    return lead


def _clear_test_leads(config, operator: str = "admin") -> Dict:
    with _LEAD_LOCK:
        rows = _read_jsonl_rows(_leads_path(config))
        before = len(rows)
        removed_ids = []
        kept = []
        for row in rows:
            n = _normalize_lead_row(row)
            if _is_test_row(n):
                removed_ids.append(n.get("lead_id") or "")
            else:
                kept.append(n)
        _write_jsonl_rows(_leads_path(config), kept)

        ev_rows = _read_jsonl_rows(_lead_events_path(config))
        removed_set = {rid for rid in removed_ids if rid}
        if removed_set and ev_rows:
            ev_kept = [e for e in ev_rows if _normalize_text((e or {}).get("lead_id"), 80) not in removed_set]
            _write_jsonl_rows(_lead_events_path(config), ev_kept)

    _append_lead_event(
        config,
        {
            "lead_id": "_system",
            "event_type": "test_clear",
            "note": f"removed {len(removed_ids)} test leads",
            "operator": operator,
        },
    )

    return {
        "before": before,
        "after": len(kept),
        "removed": len(removed_ids),
        "removed_ids": removed_ids[:20],
    }


def _to_csv(rows: List[Dict]) -> str:
    cols = [
        "lead_id",
        "created_at",
        "status",
        "owner",
        "next_action_at",
        "name",
        "email",
        "company",
        "role",
        "country_region",
        "brief_need",
        "budget_range",
        "source_channel",
        "utm_source",
        "utm_campaign",
        "utm_content",
        "quote_currency",
        "quote_amount",
        "quote_plan",
        "quote_valid_until",
        "last_proposal_at",
        "message",
        "test",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols)
    writer.writeheader()
    for row in rows:
        r = _normalize_lead_row(row)
        writer.writerow({k: r.get(k, "") for k in cols})
    return buf.getvalue()


def _patched_do_POST(self):
    try:
        parsed = urlparse(self.path)
        normalized_path = pcs._normalize_path(parsed.path)

        if normalized_path == pcs._normalize_path(_UPDATE_ENDPOINT):
            content_len = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(content_len) if content_len else b"{}"
            try:
                data = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                self._json_response(400, {"ok": False, "error": "bad_json"})
                return

            lead, err = _update_lead(self.config, data or {})
            if err:
                code = 404 if err == "lead_not_found" else 400
                self._json_response(code, {"ok": False, "error": err})
                return

            try:
                base._refresh_team_brief_leads_page()
            except Exception:
                pass

            self._json_response(200, {"ok": True, "lead": lead})
            return

        if normalized_path == pcs._normalize_path(_INSERT_TEST_ENDPOINT):
            lead = _insert_test_lead(self.config, operator="admin")
            try:
                base._refresh_team_brief_leads_page()
            except Exception:
                pass
            self._json_response(200, {"ok": True, "lead": lead})
            return

        if normalized_path == pcs._normalize_path(_CLEAR_TEST_ENDPOINT):
            content_len = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(content_len) if content_len else b"{}"
            operator = "admin"
            try:
                data = json.loads(raw.decode("utf-8")) if raw else {}
                if isinstance(data, dict):
                    operator = _normalize_text(data.get("operator"), 40) or "admin"
            except Exception:
                pass
            result = _clear_test_leads(self.config, operator=operator)
            try:
                base._refresh_team_brief_leads_page()
            except Exception:
                pass
            self._json_response(200, {"ok": True, **result})
            return

    except Exception:
        self._json_response(500, {"ok": False, "error": "server_error"})
        return

    return _ORIG_DO_POST(self)


def _patched_do_GET(self):
    try:
        parsed = urlparse(self.path)
        normalized_path = pcs._normalize_path(parsed.path)

        if normalized_path in {
            pcs._normalize_path(_LIST_ENDPOINT),
            pcs._normalize_path(_EXPORT_ENDPOINT),
            pcs._normalize_path(_EVENTS_ENDPOINT),
        }:
            q = parse_qs(parsed.query)

            if normalized_path == pcs._normalize_path(_EVENTS_ENDPOINT):
                lead_id = _normalize_text((q.get("lead_id", [""])[0] or ""), 80)
                if not lead_id:
                    self._json_response(400, {"ok": False, "error": "missing_lead_id"})
                    return
                try:
                    limit = int((q.get("limit", ["100"])[0] or "100"))
                except ValueError:
                    limit = 100
                evts = _events_for_lead(self.config, lead_id=lead_id, limit=limit)
                self._json_response(200, {"ok": True, "lead_id": lead_id, "total": len(evts), "events": evts})
                return

            status = str((q.get("status", [""])[0] or "")).strip().lower()
            owner = str((q.get("owner", [""])[0] or "")).strip()
            keyword = str((q.get("q", [""])[0] or "")).strip()
            try:
                limit = int((q.get("limit", ["500"])[0] or "500"))
            except ValueError:
                limit = 500
            limit = max(1, min(limit, 2000))

            all_rows = _read_jsonl_rows(_leads_path(self.config))
            rows = _filter_rows(all_rows, status=status, owner=owner, keyword=keyword)

            if normalized_path == pcs._normalize_path(_LIST_ENDPOINT):
                page_rows = rows[:limit]
                self._json_response(
                    200,
                    {
                        "ok": True,
                        "total": len(rows),
                        "stats": _status_counts(rows),
                        "leads": page_rows,
                    },
                )
                return

            csv_text = _to_csv(rows)
            data = csv_text.encode("utf-8-sig")
            filename = f"team_brief_leads_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
    except Exception:
        self._json_response(500, {"ok": False, "error": "server_error"})
        return

    return _ORIG_DO_GET(self)


pcs.PayConfirmHandler.do_POST = _patched_do_POST
pcs.PayConfirmHandler.do_GET = _patched_do_GET


if __name__ == "__main__":
    pcs.main()
