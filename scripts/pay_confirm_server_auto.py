#!/usr/bin/env python3
"""
Auto-fulfillment wrapper for pay_confirm_server.

Purpose:
- Keep upstream scripts/pay_confirm_server.py untouched/minimally risky.
- Monkey-patch _issue_unlock so weekly_pack confirmations automatically trigger
  scripts/fulfill_pack_order.py.

How to use:
- Run this file instead of scripts/pay_confirm_server.py in systemd ExecStart.
"""

import json
import os
import subprocess
import sys
from typing import Dict, Tuple

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# pay_confirm_server lives in the same folder as this wrapper.
import pay_confirm_server as pcs  # type: ignore


_ORIGINAL_ISSUE_UNLOCK = pcs._issue_unlock


def _detect_weekly_pack(config, order_id: str) -> bool:
    if not order_id:
        return False
    status = pcs._load_pay_status(config, order_id)
    if not isinstance(status, dict):
        return False
    memo_fields = pcs._parse_memo_kv(status.get("memo", ""))

    product_type = str(
        status.get("product_type")
        or memo_fields.get("product_type")
        or ""
    ).strip().lower()
    pack_id = str(status.get("pack_id") or memo_fields.get("pack_id") or "").strip()

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

    delivery_url = str(status.get("delivery_url") or status.get("unlock_url") or "").strip()
    if not delivery_url:
        # Try parse from stdout: "ok: /cn/pack/unlock/XXX.html"
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
    }
    return payload, ""


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
                return payload
            # Fallback: continue original flow if fulfillment failed.
            # Keep service resilient and non-blocking.
    except Exception:
        pass

    return _ORIGINAL_ISSUE_UNLOCK(config, report_id, order_id, tier, lang)


pcs._issue_unlock = _patched_issue_unlock


if __name__ == "__main__":
    pcs.main()
