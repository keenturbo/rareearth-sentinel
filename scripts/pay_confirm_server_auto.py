#!/usr/bin/env python3
"""
Auto-fulfillment wrapper for pay_confirm_server.

Purpose:
- Keep upstream scripts/pay_confirm_server.py untouched/minimally risky.
- Monkey-patch unlock flow so weekly_pack confirmations automatically trigger
  scripts/fulfill_pack_order.py.
- Enrich order/status payloads with structured product fields.
- Provide minimal funnel endpoint and event logging.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Dict, Tuple
from urllib.parse import urlparse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# pay_confirm_server lives in the same folder as this wrapper.
import pay_confirm_server as pcs  # type: ignore


_FUNNEL_ENDPOINT = "/api/pay/funnel"
_TEAM_BRIEF_LEAD_ENDPOINT = "/api/lead/team-brief"
_ALLOWED_FUNNEL_EVENTS = {
    "view_pricing",
    "click_pay",
    "order_created",
    "order_captured",
    "unlock_success",
}
_ALLOWED_BRIEF_NEEDS = {"market_risk", "policy_watch", "supplier_mapping", "custom"}
_ALLOWED_BUDGET_RANGES = {"lt_500", "500_2000", "2000_plus", "unknown"}
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_LEAD_WINDOW_SECONDS = 60
_LEAD_MAX_PER_WINDOW = 6
_LEAD_HITS = {}
_LEAD_LOCK = threading.Lock()

_CFG = pcs.Config.from_env()

_ORIGINAL_ISSUE_UNLOCK = pcs._issue_unlock
_ORIGINAL_ISSUE_TASK_LINK = pcs._issue_task_link
_ORIGINAL_WRITE_JSON = pcs._write_json
_ORIGINAL_APPEND_JSONL = pcs._append_jsonl
_ORIGINAL_DO_POST = pcs.PayConfirmHandler.do_POST


def _utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _analytics_events_path(config) -> str:
    return os.path.join(config.data_dir, "analytics", "funnel_events.jsonl")


def _team_brief_leads_path(config) -> str:
    return os.path.join(config.data_dir, "pay", "team_brief_leads.jsonl")


def _refresh_team_brief_leads_page():
    script = os.path.join(ROOT, "scripts", "build_admin_team_brief_leads.py")
    if not os.path.exists(script):
        return
    try:
        subprocess.run(
            [sys.executable, script],
            cwd=ROOT,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _normalize_text(value, max_len=400):
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:max_len]


def _valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(str(email or "").strip()))


def _hash_ip(ip: str) -> str:
    raw = str(ip or "0.0.0.0").encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()


def _lead_rate_ok(ip_hash: str) -> bool:
    now = time.time()
    with _LEAD_LOCK:
        hits = _LEAD_HITS.get(ip_hash, [])
        hits = [t for t in hits if now - t <= _LEAD_WINDOW_SECONDS]
        if len(hits) >= _LEAD_MAX_PER_WINDOW:
            _LEAD_HITS[ip_hash] = hits
            return False
        hits.append(now)
        _LEAD_HITS[ip_hash] = hits
    return True


def _normalize_lang(value: str) -> str:
    v = str(value or "").strip().lower()
    if v.startswith("en"):
        return "en"
    return "cn"


def _normalize_team_brief_lead(data: Dict, client_ip: str) -> Tuple[Dict, str]:
    payload = data if isinstance(data, dict) else {}

    # simple honeypot
    if _normalize_text(payload.get("website"), 120):
        return {}, "bot_detected"

    name = _normalize_text(payload.get("name"), 80)
    email = _normalize_text(payload.get("email"), 120)
    company = _normalize_text(payload.get("company"), 120)
    role = _normalize_text(payload.get("role"), 120)
    country = _normalize_text(payload.get("country_region"), 80)
    message = _normalize_text(payload.get("message"), 1200)

    if not email or not _valid_email(email):
        return {}, "invalid_email"
    if not company:
        return {}, "missing_company"
    if not message:
        return {}, "missing_message"

    brief_need = _normalize_text(payload.get("brief_need"), 40).lower() or "custom"
    if brief_need not in _ALLOWED_BRIEF_NEEDS:
        brief_need = "custom"

    budget = _normalize_text(payload.get("budget_range"), 40).lower() or "unknown"
    if budget not in _ALLOWED_BUDGET_RANGES:
        budget = "unknown"

    lang = _normalize_lang(payload.get("lang"))
    source_channel = _normalize_text(payload.get("source_channel"), 40).lower() or "direct"
    utm_source = _normalize_text(payload.get("utm_source"), 80)
    utm_campaign = _normalize_text(payload.get("utm_campaign"), 80)
    utm_content = _normalize_text(payload.get("utm_content"), 120)

    ip_hash = _hash_ip(client_ip)
    if not _lead_rate_ok(ip_hash):
        return {}, "rate_limited"

    lead_id = f"tb-{datetime.utcnow().strftime('%Y%m%d')}-{os.urandom(3).hex()}"

    lead = {
        "lead_id": lead_id,
        "created_at": _utc_now_iso(),
        "name": name,
        "email": email,
        "company": company,
        "role": role,
        "country_region": country,
        "brief_need": brief_need,
        "budget_range": budget,
        "message": message,
        "lang": lang,
        "source_channel": source_channel,
        "utm_source": utm_source,
        "utm_campaign": utm_campaign,
        "utm_content": utm_content,
        "ip_hash": ip_hash,
        "status": "new",
    }
    return lead, ""


def _append_funnel_event(
    config,
    event_type: str,
    *,
    lang: str = "",
    sku_id: str = "",
    channel: str = "",
    product_type: str = "",
    order_id: str = "",
    report_id: str = "",
    pack_id: str = "",
    meta: Dict = None,
):
    event_type = str(event_type or "").strip()
    if event_type not in _ALLOWED_FUNNEL_EVENTS:
        return

    payload = {
        "event_type": event_type,
        "created_at": _utc_now_iso(),
        "lang": str(lang or "").strip(),
        "sku_id": str(sku_id or "").strip(),
        "channel": str(channel or "").strip(),
        "product_type": str(product_type or "").strip(),
        "order_id": str(order_id or "").strip(),
        "report_id": str(report_id or "").strip(),
        "pack_id": str(pack_id or "").strip(),
        "meta": meta or {},
    }
    _ORIGINAL_APPEND_JSONL(_analytics_events_path(config), payload)


def _is_pay_status_path(path: str) -> bool:
    p = str(path or "").replace("\\", "/")
    return "/pay/status/" in p and p.endswith(".json")


def _is_order_log_path(path: str) -> bool:
    p = str(path or "").replace("\\", "/")
    return p.endswith("/data/pay/orders.jsonl") or p.endswith("/data/pay/task_orders.jsonl")


def _default_sku(product_type: str, lang: str, tier: str, pack_id: str) -> str:
    p = str(product_type or "").strip().lower()
    l = "en" if str(lang or "").strip().lower().startswith("en") else "cn"
    if p == "weekly_pack":
        if l == "en":
            return "en-weekly-pack-29"
        return "cn-weekly-pack-99"
    if p == "task_sprint":
        if l == "en":
            return "en-task-sprint-99"
        return "cn-task-sprint-499"

    amount = re.sub(r"[^0-9.]", "", str(tier or ""))
    if not amount:
        amount = "9"
    if amount.endswith(".0"):
        amount = amount[:-2]
    return f"{l}-single-report-{amount}"


def _extract_structured_fields(payload: Dict) -> Dict:
    payload = dict(payload or {})
    memo_fields = pcs._parse_memo_kv(payload.get("memo", ""))

    order_type = str(
        payload.get("order_type")
        or memo_fields.get("order_type")
        or ("task" if payload.get("task_id") else "report")
    ).strip().lower()

    product_type = str(
        payload.get("product_type")
        or memo_fields.get("product_type")
        or ""
    ).strip().lower()

    if not product_type:
        if order_type == "task" or payload.get("task_id"):
            product_type = "task_sprint"
        else:
            product_type = "single_report"

    sku_id = str(payload.get("sku_id") or memo_fields.get("sku_id") or "").strip()

    channel = str(
        payload.get("channel")
        or payload.get("source_channel")
        or memo_fields.get("channel")
        or memo_fields.get("source_channel")
        or ""
    ).strip()

    if not channel:
        if product_type == "weekly_pack":
            channel = "weekly_pack_page"
        elif order_type == "task":
            channel = "task_pay_page"
        else:
            channel = "pay_page"

    pack_id = str(payload.get("pack_id") or memo_fields.get("pack_id") or "").strip()

    if pack_id and not product_type:
        product_type = "weekly_pack"

    payload["order_type"] = order_type
    payload["product_type"] = product_type
    payload["sku_id"] = sku_id or _default_sku(product_type, payload.get("lang", ""), payload.get("tier", ""), pack_id)
    payload["channel"] = channel
    payload["source_channel"] = channel
    payload["pack_id"] = pack_id

    for k in ("utm_source", "utm_campaign", "utm_content"):
        if not payload.get(k) and memo_fields.get(k):
            payload[k] = memo_fields.get(k)

    return payload


def _normalize_fulfillment_status(payload: Dict) -> Dict:
    data = dict(payload or {})
    p = str(data.get("product_type") or "").strip().lower()
    status = str(data.get("status") or "").strip().lower()
    unlock_url = str(data.get("unlock_url") or "").strip()
    delivery_url = str(data.get("delivery_url") or "").strip()

    delivered = False
    if p == "weekly_pack":
        delivered = bool(delivery_url or "/pack/unlock/" in unlock_url)
    else:
        delivered = bool(status == "confirmed" and unlock_url)

    if delivered:
        data["fulfillment_status"] = "delivered"
        if not data.get("delivered_at"):
            data["delivered_at"] = _utc_now_iso()
    else:
        data.setdefault("fulfillment_status", "pending")
    return data


def _sync_structured_status(config, order_id: str, overrides: Dict = None) -> Dict:
    oid = str(order_id or "").strip()
    if not oid:
        return {}
    status = pcs._load_pay_status(config, oid)
    if not isinstance(status, dict):
        status = {}
    status = _extract_structured_fields(status)
    if isinstance(overrides, dict):
        merged = _extract_structured_fields(overrides)
        status.update(merged)
    status = _normalize_fulfillment_status(status)
    _ORIGINAL_WRITE_JSON(pcs._pay_status_path(config, oid), status)
    return status


def _emit_order_events(payload: Dict, source: str):
    status = str(payload.get("status") or "").strip().lower()
    common = {
        "lang": payload.get("lang", ""),
        "sku_id": payload.get("sku_id", ""),
        "channel": payload.get("channel", ""),
        "product_type": payload.get("product_type", ""),
        "order_id": payload.get("order_id", ""),
        "report_id": payload.get("report_id", ""),
        "pack_id": payload.get("pack_id", ""),
        "meta": {"source": source, "status": status},
    }

    if status == "paypal_created":
        _append_funnel_event(_CFG, "click_pay", **common)
        _append_funnel_event(_CFG, "order_created", **common)
    elif status == "submitted":
        _append_funnel_event(_CFG, "order_created", **common)
    elif status == "confirmed":
        _append_funnel_event(_CFG, "order_captured", **common)


def _patched_write_json(path, payload):
    data = payload
    if isinstance(payload, dict) and _is_pay_status_path(path):
        data = _normalize_fulfillment_status(_extract_structured_fields(payload))
    _ORIGINAL_WRITE_JSON(path, data)


def _patched_append_jsonl(path, payload):
    data = payload
    if isinstance(payload, dict) and _is_order_log_path(path):
        data = _normalize_fulfillment_status(_extract_structured_fields(payload))
        _ORIGINAL_APPEND_JSONL(path, data)
        _emit_order_events(data, source="append_jsonl")
        return
    _ORIGINAL_APPEND_JSONL(path, data)


def _detect_weekly_pack(config, order_id: str) -> bool:
    if not order_id:
        return False
    status = pcs._load_pay_status(config, order_id)
    if not isinstance(status, dict):
        return False

    status = _extract_structured_fields(status)
    product_type = str(status.get("product_type") or "").strip().lower()
    pack_id = str(status.get("pack_id") or "").strip()

    return product_type == "weekly_pack" or bool(pack_id)


def _run_weekly_pack_fulfillment(config, order_id: str, lang: str) -> Tuple[Dict, str]:
    script = os.path.join(ROOT, "scripts", "fulfill_pack_order.py")
    if not os.path.exists(script):
        return {}, "fulfill_script_missing"

    cmd = [sys.executable, script, "--order-id", order_id]
    if lang:
        cmd.extend(["--lang", lang])

    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except Exception as exc:  # pragma: no cover
        return {}, f"fulfill_exception:{exc}"

    if proc.returncode != 0:
        err_tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        err = err_tail[-1] if err_tail else "fulfill_failed"
        return {}, err

    status = pcs._load_pay_status(config, order_id)
    if not isinstance(status, dict):
        return {}, "status_missing_after_fulfill"

    status = _extract_structured_fields(status)
    delivery_url = str(status.get("delivery_url") or status.get("unlock_url") or "").strip()
    if not delivery_url:
        out = (proc.stdout or "").strip().splitlines()
        if out:
            line = out[-1].strip()
            if line.lower().startswith("ok:"):
                delivery_url = line.split(":", 1)[1].strip()
                if delivery_url:
                    status["delivery_url"] = delivery_url
                    status["unlock_url"] = delivery_url
                    _ORIGINAL_WRITE_JSON(pcs._pay_status_path(config, order_id), status)

    payload = {
        "report_id": status.get("report_id", ""),
        "order_id": order_id,
        "tier": status.get("tier", "$29"),
        "lang": status.get("lang", "cn"),
        "status": status.get("status", "confirmed"),
        "unlock_url": status.get("unlock_url") or delivery_url,
        "delivery_url": status.get("delivery_url") or delivery_url,
        "product_type": status.get("product_type") or "weekly_pack",
        "sku_id": status.get("sku_id", ""),
        "channel": status.get("channel", "weekly_pack_page"),
        "pack_id": status.get("pack_id", ""),
    }
    return payload, ""


def _emit_unlock_success(config, order_id: str, result_payload: Dict):
    status = pcs._load_pay_status(config, order_id)
    merged = {}
    if isinstance(status, dict):
        merged.update(status)
    if isinstance(result_payload, dict):
        merged.update(result_payload)

    merged = _normalize_fulfillment_status(_extract_structured_fields(merged))

    _append_funnel_event(
        config,
        "unlock_success",
        lang=merged.get("lang", ""),
        sku_id=merged.get("sku_id", ""),
        channel=merged.get("channel", ""),
        product_type=merged.get("product_type", ""),
        order_id=order_id,
        report_id=merged.get("report_id", ""),
        pack_id=merged.get("pack_id", ""),
        meta={
            "unlock_url": merged.get("unlock_url", ""),
            "delivery_url": merged.get("delivery_url", ""),
        },
    )


def _patched_issue_unlock(config, report_id, order_id, tier, lang):
    oid = str(order_id or "").strip()

    try:
        if _detect_weekly_pack(config, oid):
            payload, err = _run_weekly_pack_fulfillment(
                config,
                oid,
                str(lang or "").strip(),
            )
            if payload:
                _sync_structured_status(config, oid, payload)
                _emit_unlock_success(config, oid, payload)
                return payload
            # fallback to original flow if fulfillment failed
    except Exception:
        pass

    result = _ORIGINAL_ISSUE_UNLOCK(config, report_id, order_id, tier, lang)
    if isinstance(result, dict):
        synced = _sync_structured_status(config, oid, result)
        try:
            _emit_unlock_success(config, oid, synced or result)
        except Exception:
            pass
    return result


def _patched_issue_task_link(config, order_id, report_id, task_id, tier, lang, task_entry=None):
    oid = str(order_id or "").strip()
    result = _ORIGINAL_ISSUE_TASK_LINK(config, order_id, report_id, task_id, tier, lang, task_entry=task_entry)
    if isinstance(result, dict):
        enriched = dict(result)
        enriched.setdefault("product_type", "task_sprint")
        if enriched.get("task_url") and not enriched.get("unlock_url"):
            enriched["unlock_url"] = enriched.get("task_url")
        synced = _sync_structured_status(config, oid, enriched)
        try:
            _emit_unlock_success(config, oid, synced or enriched)
        except Exception:
            pass
    return result


def _patched_do_post(self):
    try:
        parsed = urlparse(self.path)
        normalized_path = pcs._normalize_path(parsed.path)

        if normalized_path == pcs._normalize_path(_TEAM_BRIEF_LEAD_ENDPOINT):
            content_len = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(content_len) if content_len else b"{}"
            try:
                data = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                self._json_response(400, {"ok": False, "error": "bad_json"})
                return

            lead, err = _normalize_team_brief_lead(data or {}, self.client_address[0] if self.client_address else "")
            if err == "bot_detected":
                self._json_response(200, {"ok": True})
                return
            if err:
                code = 429 if err == "rate_limited" else 400
                self._json_response(code, {"ok": False, "error": err})
                return

            _ORIGINAL_APPEND_JSONL(_team_brief_leads_path(self.config), lead)
            try:
                pcs._refresh_admin_orders()
            except Exception:
                pass
            _refresh_team_brief_leads_page()

            self._json_response(200, {"ok": True, "lead_id": lead.get("lead_id")})
            return

        if normalized_path == pcs._normalize_path(_FUNNEL_ENDPOINT):
            content_len = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(content_len) if content_len else b"{}"
            try:
                data = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                self._json_response(400, {"ok": False, "error": "bad_json"})
                return

            event_type = str((data or {}).get("event_type") or "").strip()
            if event_type not in _ALLOWED_FUNNEL_EVENTS:
                self._json_response(400, {"ok": False, "error": "invalid_event_type"})
                return

            _append_funnel_event(
                self.config,
                event_type,
                lang=str((data or {}).get("lang") or "").strip(),
                sku_id=str((data or {}).get("sku_id") or "").strip(),
                channel=str((data or {}).get("channel") or "").strip(),
                product_type=str((data or {}).get("product_type") or "").strip(),
                order_id=str((data or {}).get("order_id") or "").strip(),
                report_id=str((data or {}).get("report_id") or "").strip(),
                pack_id=str((data or {}).get("pack_id") or "").strip(),
                meta=(data or {}).get("meta") if isinstance((data or {}).get("meta"), dict) else {},
            )
            self._json_response(200, {"ok": True})
            return
    except Exception:
        self._json_response(500, {"ok": False, "error": "server_error"})
        return

    return _ORIGINAL_DO_POST(self)


# Apply monkey patches.
pcs._write_json = _patched_write_json
pcs._append_jsonl = _patched_append_jsonl
pcs._issue_unlock = _patched_issue_unlock
pcs._issue_task_link = _patched_issue_task_link
pcs.PayConfirmHandler.do_POST = _patched_do_post


if __name__ == "__main__":
    pcs.main()
