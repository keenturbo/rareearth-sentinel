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

import json
import os
import subprocess
import sys
from datetime import datetime
from typing import Dict, Tuple
from urllib.parse import urlparse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# pay_confirm_server lives in the same folder as this wrapper.
import pay_confirm_server as pcs  # type: ignore


_FUNNEL_ENDPOINT = "/api/pay/funnel"
_ALLOWED_FUNNEL_EVENTS = {
    "view_pricing",
    "click_pay",
    "order_created",
    "order_captured",
    "unlock_success",
}

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
    payload["sku_id"] = sku_id
    payload["channel"] = channel
    payload["source_channel"] = channel
    payload["pack_id"] = pack_id

    # Keep UTM fields if provided.
    for k in ("utm_source", "utm_campaign", "utm_content"):
        if not payload.get(k) and memo_fields.get(k):
            payload[k] = memo_fields.get(k)

    return payload


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
        data = _extract_structured_fields(payload)
    _ORIGINAL_WRITE_JSON(path, data)


def _patched_append_jsonl(path, payload):
    data = payload
    if isinstance(payload, dict) and _is_order_log_path(path):
        data = _extract_structured_fields(payload)
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
                    pcs._write_json(pcs._pay_status_path(config, order_id), status)

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

    merged = _extract_structured_fields(merged)

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
    # Weekly pack: auto trigger fulfillment script instead of single-report unlock.
    try:
        if _detect_weekly_pack(config, str(order_id or "")):
            payload, err = _run_weekly_pack_fulfillment(
                config,
                str(order_id or "").strip(),
                str(lang or "").strip(),
            )
            if payload:
                _emit_unlock_success(config, str(order_id or "").strip(), payload)
                return payload
            # Fallback: continue original flow if fulfillment failed.
            # Keep service resilient and non-blocking.
    except Exception:
        pass

    result = _ORIGINAL_ISSUE_UNLOCK(config, report_id, order_id, tier, lang)
    if isinstance(result, dict):
        try:
            _emit_unlock_success(config, str(order_id or "").strip(), result)
        except Exception:
            pass
    return result


def _patched_issue_task_link(config, order_id, report_id, task_id, tier, lang, task_entry=None):
    result = _ORIGINAL_ISSUE_TASK_LINK(config, order_id, report_id, task_id, tier, lang, task_entry=task_entry)
    if isinstance(result, dict):
        try:
            enriched = dict(result)
            enriched.setdefault("product_type", "task_sprint")
            _emit_unlock_success(config, str(order_id or "").strip(), enriched)
        except Exception:
            pass
    return result


def _patched_do_post(self):
    try:
        parsed = urlparse(self.path)
        normalized_path = pcs._normalize_path(parsed.path)
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
