import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def _append_jsonl(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False))
        f.write("\n")


def _normalize_base(url):
    if not url:
        return ""
    return url.rstrip("/")


def _build_admin_link(base, report_id):
    if not base or not report_id:
        return ""
    base = base if base.endswith("/") else base + "/"
    return f"{base}{report_id}.html"


def _send_bark(config, payload):
    bark_base = _normalize_base(config.pay_bark_url)
    if not bark_base:
        return

    title = "支付确认"
    method = payload.get("payment_method", "")
    token = payload.get("payment_token", "")
    body = (
        f"report_id={payload.get('report_id')} | tier={payload.get('tier')} | "
        f"order_id={payload.get('order_id')} | email={payload.get('email')}"
    )
    if method:
        body += f" | method={method}"
    if token:
        body += f" | token={token}"
    admin_link = _build_admin_link(config.pay_admin_base_url, payload.get("report_id"))

    from urllib.parse import quote

    url = f"{bark_base}/{quote(title)}/{quote(body)}"
    if admin_link:
        url = f"{url}?url={quote(admin_link)}"
    try:
        requests.get(url, timeout=6)
    except Exception:
        pass


class PayConfirmHandler(BaseHTTPRequestHandler):
    config = Config.from_env()

    def _json_response(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/pay/confirm":
            self._json_response(404, {"ok": False, "error": "not_found"})
            return

        content_len = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_len) if content_len else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._json_response(400, {"ok": False, "error": "bad_json"})
            return

        report_id = str(data.get("report_id", "")).strip()
        order_id = str(data.get("order_id", "")).strip()
        tier = str(data.get("tier", "")).strip()
        email = str(data.get("email", "")).strip()
        lang = str(data.get("lang", "")).strip() or "cn"
        method = str(data.get("payment_method", "")).strip()
        memo = str(data.get("memo", "")).strip()
        payment_token = str(data.get("payment_token", "")).strip()

        if not report_id or not order_id:
            self._json_response(400, {"ok": False, "error": "missing_fields"})
            return

        now = datetime.utcnow().isoformat() + "Z"
        expires_at = (
            datetime.utcnow() + timedelta(days=7)
        ).isoformat() + "Z"

        status_payload = {
            "report_id": report_id,
            "order_id": order_id,
            "tier": tier or "$9",
            "lang": lang,
            "status": "submitted",
            "email": email,
            "payment_method": method,
            "payment_token": payment_token,
            "memo": memo,
            "created_at": now,
            "expires_at": expires_at,
        }

        static_dir = self.config.static_dir
        status_path = os.path.join(static_dir, "pay", "status", f"{order_id}.json")
        _write_json(status_path, status_payload)

        log_payload = dict(status_payload)
        log_payload["ip"] = self.client_address[0]
        _append_jsonl(os.path.join(self.config.data_dir, "pay", "orders.jsonl"), log_payload)

        _send_bark(self.config, status_payload)

        self._json_response(200, {"ok": True})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8112)
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), PayConfirmHandler)
    print(f"Pay confirm server listening on {args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
