#!/usr/bin/env python3
import argparse
import html
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

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


def _normalize_path(path):
    if not path:
        return "/"
    path = path.strip()
    if path == "/":
        return "/"
    return path.rstrip("/")


def _normalize_lang(lang):
    if not lang:
        return "cn"
    key = str(lang).strip().lower()
    if key in {"zh", "zh-cn", "zh-hans", "cn", "china"}:
        return "cn"
    if key.startswith("en"):
        return "en"
    return "cn"


def _url_prefix(lang):
    return "en" if lang == "en" else "cn"


def _refresh_admin_orders():
    script = os.path.join(ROOT, "scripts", "build_admin_orders.py")
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


def _build_admin_link(base, report_id):
    if not base or not report_id:
        return ""
    base = base if base.endswith("/") else base + "/"
    return f"{base}{report_id}.html"


def _build_orders_link(base, order_id):
    if not base:
        return ""
    base = base if base.endswith("/") else base + "/"
    if order_id:
        return f"{base}?order_id={order_id}"
    return base


def _send_bark(config, payload):
    bark_base = _normalize_base(config.pay_bark_url)
    if not bark_base:
        return

    is_task = bool(payload.get("task_id")) or str(payload.get("order_type", "")).lower() == "task"
    title = "任务支付确认" if is_task else "支付确认"
    method = payload.get("payment_method", "")
    token = payload.get("payment_token", "")

    if is_task:
        body = (
            f"task_id={payload.get('task_id')} | report_id={payload.get('report_id')} | "
            f"freq={payload.get('frequency')} | tier={payload.get('tier')} | "
            f"order_id={payload.get('order_id')} | email={payload.get('email')}"
        )
    else:
        body = (
            f"report_id={payload.get('report_id')} | tier={payload.get('tier')} | "
            f"order_id={payload.get('order_id')} | email={payload.get('email')}"
        )

    if method:
        body += f" | method={method}"
    if token:
        body += f" | token={token}"

    orders_link = _build_orders_link(config.pay_admin_orders_url, payload.get("order_id"))
    if not orders_link:
        orders_link = _build_orders_link(config.pay_admin_orders_url_en, payload.get("order_id"))
    if not orders_link:
        orders_link = _build_admin_link(config.pay_admin_base_url, payload.get("report_id"))

    from urllib.parse import quote

    url = f"{bark_base}/{quote(title)}/{quote(body)}"
    if orders_link:
        url = f"{url}?url={quote(orders_link)}"
    try:
        requests.get(url, timeout=6)
    except Exception:
        pass


def _load_report_entry(premium_dir, report_id):
    index_path = os.path.join(premium_dir, "reports_index.jsonl")
    if not os.path.exists(index_path):
        return None
    with open(index_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("report_id") == report_id:
                return entry
    return None


def _load_brief_md(premium_dir, date_key, report_id, lang):
    if not date_key:
        return ""
    path = os.path.join(premium_dir, date_key, f"brief_{report_id}_{lang}.md")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _mark_order_confirmed(data_dir, order_id, unlock_url):
    log_path = os.path.join(data_dir, "pay", "orders.jsonl")
    if not os.path.exists(log_path):
        return
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(log_path, "w", encoding="utf-8") as f:
            for line in lines:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    f.write(line)
                    continue
                if entry.get("order_id") == order_id:
                    entry["status"] = "confirmed"
                    if unlock_url:
                        entry["unlock_url"] = unlock_url
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                else:
                    f.write(line)
    except OSError:
        return


def _task_orders_path(data_dir):
    return os.path.join(data_dir, "pay", "task_orders.jsonl")


def _load_task_order(data_dir, order_id):
    path = _task_orders_path(data_dir)
    if not os.path.exists(path):
        return None
    last = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("order_id") == order_id:
                last = entry
    return last


def _mark_task_order_confirmed(data_dir, order_id, task_url, task_entry=None):
    log_path = _task_orders_path(data_dir)
    now = datetime.utcnow().isoformat() + "Z"
    if not os.path.exists(log_path):
        if task_entry:
            payload = dict(task_entry)
            payload.update(
                {
                    "status": "confirmed",
                    "task_status": "active",
                    "task_url": task_url,
                    "confirmed_at": now,
                }
            )
            _append_jsonl(log_path, payload)
        return

    found = False
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(log_path, "w", encoding="utf-8") as f:
            for line in lines:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    f.write(line)
                    continue
                if entry.get("order_id") == order_id:
                    found = True
                    entry["status"] = "confirmed"
                    entry["task_status"] = "active"
                    entry["task_url"] = task_url
                    entry["confirmed_at"] = now
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                else:
                    f.write(line)
    except OSError:
        return

    if not found and task_entry:
        payload = dict(task_entry)
        payload.update(
            {
                "status": "confirmed",
                "task_status": "active",
                "task_url": task_url,
                "confirmed_at": now,
            }
        )
        _append_jsonl(log_path, payload)


def _task_status_label(status, lang="cn"):
    mapping_zh = {
        "pending": "待执行",
        "active": "执行中",
        "paused": "已暂停",
        "completed": "已完成",
    }
    mapping_en = {
        "pending": "Pending",
        "active": "Active",
        "paused": "Paused",
        "completed": "Completed",
    }
    status = str(status or "pending")
    return mapping_en.get(status, status) if lang == "en" else mapping_zh.get(status, status)


def _frequency_label(freq, lang="cn"):
    mapping_zh = {
        "weekly": "周报",
        "daily": "日报",
        "event": "事件触发",
    }
    mapping_en = {
        "weekly": "Weekly",
        "daily": "Daily",
        "event": "Event",
    }
    freq = str(freq or "weekly")
    return mapping_en.get(freq, freq) if lang == "en" else mapping_zh.get(freq, freq)


def _write_task_page(config, entry, lang="cn"):
    template_name = "task_en.html" if lang == "en" else "task.html"
    template_path = os.path.join(config.templates_dir, template_name)
    if not os.path.exists(template_path):
        return
    task_id = str(entry.get("task_id", "")).strip()
    if not task_id:
        return

    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    task_status = entry.get("task_status") or "active"
    empty_summary = "暂无执行结果" if lang != "en" else "No results yet."
    empty_table = "暂无关键信息" if lang != "en" else "No key signals yet."
    empty_history = "暂无历史报告" if lang != "en" else "No reports yet."

    summary_html = f'<div class="empty">{empty_summary}</div>'
    table_html = f'<div class="empty">{empty_table}</div>'
    history_html = f'<div class="empty">{empty_history}</div>'

    html_out = (
        template.replace("{{TASK_ID}}", html.escape(task_id))
        .replace("{{REPORT_TITLE}}", html.escape(str(entry.get("report_title", ""))))
        .replace("{{REPORT_VALUE}}", html.escape(str(entry.get("report_value", ""))))
        .replace("{{TASK_SUGGESTION}}", html.escape(str(entry.get("task_suggestion", ""))))
        .replace("{{TASK_FREQUENCY}}", html.escape(_frequency_label(entry.get("frequency"), lang)))
        .replace("{{TASK_STATUS}}", html.escape(_task_status_label(task_status, lang)))
        .replace("{{LAST_UPDATE}}", html.escape(entry.get("confirmed_at", "") or ""))
        .replace("{{TASK_SUMMARY}}", summary_html)
        .replace("{{ACTION_TABLE}}", table_html)
        .replace("{{REPORT_LIST}}", history_html)
    )

    prefix = _url_prefix(lang)
    target_dir = os.path.join(config.static_dir, prefix, "task")
    os.makedirs(target_dir, exist_ok=True)
    with open(os.path.join(target_dir, f"{task_id}.html"), "w", encoding="utf-8") as f:
        f.write(html_out)


def _issue_unlock(config, report_id, order_id, tier, lang):
    entry = _load_report_entry(config.premium_dir, report_id)
    if not entry:
        return None

    date_key = entry.get("date", "")
    lang = _normalize_lang(lang)
    lang_key = "zh" if lang == "cn" else "en"
    content_md = _load_brief_md(config.premium_dir, date_key, report_id, lang_key)

    token = uuid4().hex[:16]
    expires_at = (datetime.utcnow() + timedelta(days=config.unlock_expires_days)).isoformat() + "Z"

    prefix = _url_prefix(lang)
    unlock_payload = {
        "report_id": report_id,
        "tier": tier,
        "lang": lang,
        "token": token,
        "expires_at": expires_at,
        "content_md": content_md,
    }

    status_payload = {
        "report_id": report_id,
        "tier": tier,
        "order_id": order_id,
        "token": token,
        "unlock_url": f"/{prefix}/pay/{report_id}.html?unlock={token}",
        "expires_at": expires_at,
        "status": "confirmed",
    }

    status_path = os.path.join(config.static_dir, "pay", "status", f"{order_id}.json")
    unlock_path = os.path.join(config.static_dir, "unlocks", f"{token}.json")

    _write_json(status_path, status_payload)
    _write_json(unlock_path, unlock_payload)

    return status_payload


def _issue_task_link(config, order_id, report_id, task_id, tier, lang, task_entry=None):
    lang = _normalize_lang(lang)
    prefix = _url_prefix(lang)
    task_url = f"/{prefix}/task/{task_id}.html"
    now = datetime.utcnow().isoformat() + "Z"

    status_payload = {
        "task_id": task_id,
        "report_id": report_id,
        "order_id": order_id,
        "tier": tier,
        "lang": lang,
        "status": "confirmed",
        "task_url": task_url,
        "confirmed_at": now,
    }

    status_path = os.path.join(config.static_dir, "pay", "status", f"{order_id}.json")
    _write_json(status_path, status_payload)

    _mark_task_order_confirmed(config.data_dir, order_id, task_url, task_entry=task_entry)

    entry = dict(task_entry or {})
    entry.update(
        {
            "task_id": task_id,
            "report_id": report_id,
            "order_id": order_id,
            "tier": tier,
            "lang": lang,
            "task_url": task_url,
            "confirmed_at": now,
            "task_status": "active",
        }
    )
    _write_task_page(config, entry, lang=lang)

    return status_payload


class PayConfirmHandler(BaseHTTPRequestHandler):
    config = Config.from_env()

    def log_message(self, format, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), format % args))

    def _json_response(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            normalized_path = _normalize_path(parsed.path)
            admin_path = _normalize_path(self.config.pay_confirm_admin_endpoint)
            confirm_path = _normalize_path(self.config.pay_confirm_endpoint)

            content_len = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(content_len) if content_len else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._json_response(400, {"ok": False, "error": "bad_json"})
                return

            if normalized_path == admin_path:
                order_id = str(data.get("order_id", "")).strip()
                report_id = str(data.get("report_id", "")).strip()
                tier = str(data.get("tier", "") or "$9").strip()
                lang = _normalize_lang(data.get("lang", "cn"))
                task_id = str(data.get("task_id", "")).strip()
                is_task = bool(task_id) or str(data.get("task", "")).strip() in {"1", "true", "yes"}

                if not order_id or not report_id:
                    self._json_response(400, {"ok": False, "error": "missing_fields"})
                    return

                if is_task:
                    task_entry = _load_task_order(self.config.data_dir, order_id) or {}
                    if not task_id:
                        task_id = str(task_entry.get("task_id", "")).strip()
                    if not task_id:
                        self._json_response(400, {"ok": False, "error": "missing_task_id"})
                        return

                    status_payload = _issue_task_link(
                        self.config,
                        order_id,
                        report_id,
                        task_id,
                        tier,
                        lang,
                        task_entry=task_entry,
                    )
                    _refresh_admin_orders()
                    self._json_response(
                        200,
                        {
                            "ok": True,
                            "task_url": status_payload.get("task_url"),
                            "unlock_url": status_payload.get("task_url"),
                        },
                    )
                    return

                status_payload = _issue_unlock(self.config, report_id, order_id, tier, lang)
                if not status_payload:
                    self._json_response(404, {"ok": False, "error": "report_not_found"})
                    return

                _mark_order_confirmed(self.config.data_dir, order_id, status_payload.get("unlock_url"))
                _refresh_admin_orders()
                self._json_response(200, {"ok": True, "unlock_url": status_payload.get("unlock_url")})
                return

            allowed_confirm = {confirm_path, _normalize_path("/api/pay/confirm")}
            if normalized_path not in allowed_confirm:
                self._json_response(404, {"ok": False, "error": "not_found"})
                return

            report_id = str(data.get("report_id", "")).strip()
            order_id = str(data.get("order_id", "")).strip()
            tier = str(data.get("tier", "")).strip()
            email = str(data.get("email", "")).strip()
            lang = _normalize_lang(data.get("lang", "cn"))
            method = str(data.get("payment_method", "")).strip()
            memo = str(data.get("memo", "")).strip()
            payment_token = str(data.get("payment_token", "")).strip()
            task_id = str(data.get("task_id", "")).strip()
            order_type = str(data.get("order_type", "")).strip().lower()
            is_task = order_type == "task" or bool(task_id)

            if not report_id or not order_id:
                self._json_response(400, {"ok": False, "error": "missing_fields"})
                return

            now = datetime.utcnow().isoformat() + "Z"
            expires_at = (
                datetime.utcnow() + timedelta(days=self.config.unlock_expires_days)
            ).isoformat() + "Z"

            if is_task:
                frequency = str(data.get("frequency", "")).strip() or "weekly"
                plan = str(data.get("plan", "")).strip() or frequency
                task_suggestion = str(data.get("task_suggestion", "")).strip()
                report_title = str(data.get("report_title", "")).strip()
                report_value = str(data.get("report_value", "")).strip()

                status_payload = {
                    "task_id": task_id,
                    "report_id": report_id,
                    "order_id": order_id,
                    "tier": tier or "$9",
                    "lang": lang,
                    "status": "submitted",
                    "task_status": "pending",
                    "frequency": frequency,
                    "plan": plan,
                    "task_suggestion": task_suggestion,
                    "report_title": report_title,
                    "report_value": report_value,
                    "email": email,
                    "payment_method": method,
                    "payment_token": payment_token,
                    "memo": memo,
                    "created_at": now,
                    "expires_at": expires_at,
                    "order_type": "task",
                }

                status_path = os.path.join(self.config.static_dir, "pay", "status", f"{order_id}.json")
                _write_json(status_path, status_payload)

                log_payload = dict(status_payload)
                log_payload["ip"] = self.client_address[0]
                _append_jsonl(_task_orders_path(self.config.data_dir), log_payload)

                _send_bark(self.config, status_payload)
                _refresh_admin_orders()

                self._json_response(200, {"ok": True})
                return

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

            status_path = os.path.join(self.config.static_dir, "pay", "status", f"{order_id}.json")
            _write_json(status_path, status_payload)

            log_payload = dict(status_payload)
            log_payload["ip"] = self.client_address[0]
            _append_jsonl(os.path.join(self.config.data_dir, "pay", "orders.jsonl"), log_payload)

            _send_bark(self.config, status_payload)
            _refresh_admin_orders()

            self._json_response(200, {"ok": True})
        except Exception:
            self._json_response(500, {"ok": False, "error": "server_error"})

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            normalized_path = _normalize_path(parsed.path)
            admin_path = _normalize_path(self.config.pay_confirm_admin_endpoint)
            if normalized_path != admin_path:
                self._json_response(404, {"ok": False, "error": "not_found"})
                return

            query = parse_qs(parsed.query)
            order_id = (query.get("order_id", [""])[0] or "").strip()
            report_id = (query.get("report_id", [""])[0] or "").strip()
            tier = (query.get("tier", ["$9"])[0] or "$9").strip()
            lang = _normalize_lang(query.get("lang", ["cn"])[0])
            task_id = (query.get("task_id", [""])[0] or "").strip()
            task_flag = (query.get("task", [""])[0] or "").strip().lower()
            is_task = bool(task_id) or task_flag in {"1", "true", "yes"}

            if not order_id or not report_id:
                self._json_response(400, {"ok": False, "error": "missing_fields"})
                return

            if is_task:
                task_entry = _load_task_order(self.config.data_dir, order_id) or {}
                if not task_id:
                    task_id = str(task_entry.get("task_id", "")).strip()
                if not task_id:
                    self._json_response(400, {"ok": False, "error": "missing_task_id"})
                    return

                status_payload = _issue_task_link(
                    self.config,
                    order_id,
                    report_id,
                    task_id,
                    tier,
                    lang,
                    task_entry=task_entry,
                )
                _refresh_admin_orders()
                self._json_response(
                    200,
                    {
                        "ok": True,
                        "task_url": status_payload.get("task_url"),
                        "unlock_url": status_payload.get("task_url"),
                    },
                )
                return

            status_payload = _issue_unlock(self.config, report_id, order_id, tier, lang)
            if not status_payload:
                self._json_response(404, {"ok": False, "error": "report_not_found"})
                return

            _mark_order_confirmed(self.config.data_dir, order_id, status_payload.get("unlock_url"))
            _refresh_admin_orders()

            self._json_response(200, {"ok": True, "unlock_url": status_payload.get("unlock_url")})
        except Exception:
            self._json_response(500, {"ok": False, "error": "server_error"})


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
