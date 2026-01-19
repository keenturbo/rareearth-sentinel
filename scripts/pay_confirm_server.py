#!/usr/bin/env python3
import threading
import argparse
import cgi
import html
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import requests
import yaml

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


def _normalize_bool(value, default=True):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


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


def _build_task_orders_link(base, order_id):
    if not base:
        return ""
    base = base if base.endswith("/") else base + "/"
    if "/admin/orders/" in base:
        base = base.replace("/admin/orders/", "/admin/task-orders/")
    elif base.endswith("/admin/orders"):
        base = base.replace("/admin/orders", "/admin/task-orders")
    elif "/orders/" in base:
        base = base.replace("/orders/", "/task-orders/")
    elif base.endswith("/orders"):
        base = base.replace("/orders", "/task-orders")
    else:
        base = base.rstrip("/") + "/task-orders/"
    if order_id:
        return f"{base}?order_id={order_id}"
    return base


def _parse_memo_kv(memo):
    if not memo:
        return {}
    data = {}
    for part in str(memo).strip().split():
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if not key:
            continue
        data[key] = value.strip()
    return data


def _load_yaml_sources(path):
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return []
    if isinstance(data, dict):
        return data.get("sources", [])
    if isinstance(data, list):
        return data
    return []


def _load_custom_sources(path):
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        return data.get("sources", [])
    if isinstance(data, list):
        return data
    return []


def _write_custom_sources(path, sources):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sources, f, ensure_ascii=False, indent=2)


def _resolve_path(path):
    if not path:
        return path
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


def _indices_status_path():
    return os.path.join(ROOT, "data", "status", "indices_upload.json")


def _load_indices_status(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_indices_status(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _last_non_empty(text):
    if not text:
        return ""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line:
            return line
    return ""


def _run_command(cmd, cwd):
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    except Exception as exc:
        return 1, str(exc)
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    return result.returncode, _last_non_empty(output)


def _parse_date_value(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) >= 10:
        text = text[:10]
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _max_date_in_jsonl(path, key):
    if not os.path.exists(path):
        return ""
    latest = None
    latest_str = ""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            dt = _parse_date_value(entry.get(key))
            if not dt:
                continue
            if latest is None or dt > latest:
                latest = dt
                latest_str = dt.strftime("%Y-%m-%d")
    return latest_str


def _latest_trade_date(normalized_dir):
    normalized_dir = _resolve_path(normalized_dir)
    signals_path = os.path.join(normalized_dir, "index_signals.jsonl")
    date = _max_date_in_jsonl(signals_path, "date")
    if date:
        return date
    timeseries_path = os.path.join(normalized_dir, "index_timeseries.jsonl")
    return _max_date_in_jsonl(timeseries_path, "published_at")


def _source_runs_path():
    return os.path.join(ROOT, "data", "status", "source_runs.json")


def _load_source_runs(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_source_runs(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _update_source_run(path, source_id, updates):
    status = _load_source_runs(path)
    entry = status.get(source_id, {})
    entry.update(updates)
    entry["source_id"] = source_id
    status[source_id] = entry
    _write_source_runs(path, status)
    return entry


def _is_pid_alive(pid):
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    try:
        os.kill(pid_int, 0)
        return True
    except OSError:
        return False


def _normalize_source_runs(status):
    if not status:
        return {}
    updated = False
    now = datetime.utcnow().isoformat() + "Z"
    for source_id, entry in list(status.items()):
        if not isinstance(entry, dict):
            continue
        pid = entry.get("pid")
        if entry.get("running") and pid and not _is_pid_alive(pid):
            entry["running"] = False
            entry.setdefault("last_update", now)
            status[source_id] = entry
            updated = True
    if updated:
        _write_source_runs(_source_runs_path(), status)
    return status


def _count_source_entries(data_dir, source_id):
    if not source_id:
        return 0
    path = os.path.join(data_dir, "intel.jsonl")
    if not os.path.exists(path):
        return 0
    count = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("source_id") == source_id:
                count += 1
    return count


def _run_source_job(source_id, config):
    status_path = _source_runs_path()
    start_time = datetime.utcnow().isoformat() + "Z"
    before_count = _count_source_entries(config.data_dir, source_id)

    env = os.environ.copy()
    env["SOURCES"] = "official"
    env["OFFICIAL_SOURCE_IDS"] = source_id
    cmd = [sys.executable, os.path.join(ROOT, "main.py")]

    log_path = os.path.join(ROOT, "logs", "source_run.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    try:
        log = open(log_path, "a", encoding="utf-8")
    except OSError:
        log = subprocess.DEVNULL

    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=log)
    _update_source_run(
        status_path,
        source_id,
        {
            "running": True,
            "start_time": start_time,
            "pid": proc.pid,
        },
    )

    exit_code = proc.wait()
    if log is not subprocess.DEVNULL:
        log.close()

    after_count = _count_source_entries(config.data_dir, source_id)
    new_count = max(after_count - before_count, 0)
    done_time = datetime.utcnow().isoformat() + "Z"

    _update_source_run(
        status_path,
        source_id,
        {
            "running": False,
            "pid": proc.pid,
            "last_update": done_time,
            "last_new": new_count,
            "last_count": after_count,
            "exit_code": exit_code,
        },
    )

    env_rebuild = os.environ.copy()
    env_rebuild.pop("SOURCES", None)
    env_rebuild.pop("OFFICIAL_SOURCE_IDS", None)
    rebuild_cmd = [sys.executable, os.path.join(ROOT, "main.py"), "--rebuild"]
    try:
        subprocess.run(
            rebuild_cmd,
            cwd=ROOT,
            env=env_rebuild,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _spawn_source_run(source_id, config):
    if not source_id:
        return
    threading.Thread(target=_run_source_job, args=(source_id, config), daemon=True).start()


def _normalize_source_payload(data):
    payload = {
        "id": str(data.get("id", "")).strip(),
        "name": str(data.get("name", "")).strip(),
        "mode": str(data.get("mode") or data.get("type") or "site_search").strip().lower(),
        "domain": str(data.get("domain", "")).strip(),
        "query_terms": str(data.get("query_terms", "")).strip(),
        "news_lang": str(data.get("news_lang", "")).strip(),
        "news_region": str(data.get("news_region", "")).strip(),
        "news_edition": str(data.get("news_edition", "")).strip(),
        "enabled": _normalize_bool(data.get("enabled"), True),
    }

    max_items = data.get("max_items")
    if max_items is not None and str(max_items).strip() != "":
        try:
            payload["max_items"] = int(max_items)
        except (TypeError, ValueError):
            pass

    return payload


def _merge_sources_for_admin(base_sources, custom_sources, run_status=None):
    base_sources = base_sources or []
    custom_sources = custom_sources or []
    run_status = run_status or {}

    custom_map = {
        entry.get("id"): entry
        for entry in custom_sources
        if isinstance(entry, dict) and entry.get("id")
    }
    merged = []
    seen = set()

    for entry in base_sources:
        if not isinstance(entry, dict):
            continue
        source_id = entry.get("id")
        merged_entry = dict(entry)
        origin = "base"
        if source_id and source_id in custom_map:
            merged_entry.update(custom_map[source_id])
            origin = "override"
        merged_entry["origin"] = origin
        merged_entry["enabled"] = _normalize_bool(merged_entry.get("enabled"), True)

        run_info = run_status.get(source_id, {}) if source_id else {}
        merged_entry["run_running"] = bool(run_info.get("running"))
        merged_entry["run_start_time"] = run_info.get("start_time", "")
        merged_entry["run_pid"] = run_info.get("pid", "")
        merged_entry["run_last_update"] = run_info.get("last_update", "")
        merged_entry["run_last_new"] = run_info.get("last_new", "")
        merged_entry["run_last_count"] = run_info.get("last_count", "")

        merged.append(merged_entry)
        if source_id:
            seen.add(source_id)

    for entry in custom_sources:
        if not isinstance(entry, dict):
            continue
        source_id = entry.get("id")
        if not source_id or source_id in seen:
            continue
        merged_entry = dict(entry)
        merged_entry["origin"] = "custom"
        merged_entry["enabled"] = _normalize_bool(merged_entry.get("enabled"), True)

        run_info = run_status.get(source_id, {}) if source_id else {}
        merged_entry["run_running"] = bool(run_info.get("running"))
        merged_entry["run_start_time"] = run_info.get("start_time", "")
        merged_entry["run_pid"] = run_info.get("pid", "")
        merged_entry["run_last_update"] = run_info.get("last_update", "")
        merged_entry["run_last_new"] = run_info.get("last_new", "")
        merged_entry["run_last_count"] = run_info.get("last_count", "")

        merged.append(merged_entry)

    return merged


def _upsert_custom_source(sources, payload):
    now = datetime.utcnow().isoformat() + "Z"
    source_id = payload.get("id")
    for idx, entry in enumerate(sources):
        if entry.get("id") == source_id:
            created_at = entry.get("created_at")
            merged = dict(entry)
            merged.update(payload)
            if created_at:
                merged["created_at"] = created_at
            merged["updated_at"] = now
            sources[idx] = merged
            return sources

    payload = dict(payload)
    payload["created_at"] = now
    payload["updated_at"] = now
    sources.append(payload)
    return sources


def _disable_source(source_id, base_sources, custom_sources):
    if not source_id:
        return custom_sources
    base_entry = next((s for s in base_sources if s.get("id") == source_id), {})
    custom_entry = next((s for s in custom_sources if s.get("id") == source_id), {})
    payload = dict(base_entry or custom_entry or {"id": source_id})
    payload["id"] = source_id
    payload["enabled"] = False
    return _upsert_custom_source(custom_sources, payload)


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

    orders_link = ""
    if is_task:
        orders_link = _build_task_orders_link(config.pay_admin_orders_url, payload.get("order_id"))
        if not orders_link:
            orders_link = _build_task_orders_link(config.pay_admin_orders_url_en, payload.get("order_id"))
    if not orders_link:
        orders_link = _build_orders_link(config.pay_admin_orders_url, payload.get("order_id"))
    if not orders_link:
        orders_link = _build_orders_link(config.pay_admin_orders_url_en, payload.get("order_id"))
    if not orders_link:
        orders_link = _build_admin_link(config.pay_admin_base_url, payload.get("report_id"))

    from urllib.parse import quote

    url = f"{bark_base}/{quote(title)}/{quote(body)}"
    if orders_link:
        url = f"{url}?url={quote(orders_link)}"

    def _fire(u):
        try:
            requests.get(u, timeout=(3, 5))
        except Exception:
            pass

    threading.Thread(target=_fire, args=(url,), daemon=True).start()


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

    def _handle_indices_upload(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._json_response(400, {"ok": False, "error": "invalid_content_type"})
            return

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": content_type},
        )
        file_item = form["file"] if "file" in form else None
        if not file_item or not getattr(file_item, "filename", ""):
            self._json_response(400, {"ok": False, "error": "missing_file"})
            return

        filename = os.path.basename(file_item.filename)
        if not filename.lower().endswith(".xls"):
            self._json_response(400, {"ok": False, "error": "invalid_file_type"})
            return

        try:
            content = file_item.file.read()
        except Exception:
            content = b""
        if not content:
            self._json_response(400, {"ok": False, "error": "empty_file"})
            return

        raw_dir = _resolve_path(self.config.index_raw_dir)
        manual_dir = os.path.join(raw_dir, "manual")
        os.makedirs(manual_dir, exist_ok=True)
        target_path = os.path.join(manual_dir, "repe.xls")
        with open(target_path, "wb") as f:
            f.write(content)

        now = datetime.utcnow().isoformat() + "Z"
        status = {
            "last_upload_at": now,
            "last_parse_at": "",
            "last_trade_date": "",
            "last_result": "failed",
            "last_log": "",
            "last_filename": filename,
            "last_filesize_bytes": len(content),
        }
        log_parts = []
        start = time.time()

        normalize_cmd = [
            sys.executable,
            os.path.join(ROOT, "scripts", "normalize_indices.py"),
            "--once",
            "--manual",
        ]
        code, line = _run_command(normalize_cmd, ROOT)
        if code != 0:
            log_parts.append("normalize failed" + (f": {line}" if line else ""))
            status["last_log"] = "; ".join(log_parts)[:200]
            _write_indices_status(_indices_status_path(), status)
            self._json_response(500, {"ok": False, "error": "normalize_failed", "log": status["last_log"]})
            return
        status["last_parse_at"] = datetime.utcnow().isoformat() + "Z"
        log_parts.append("normalize ok")

        signals_cmd = [
            sys.executable,
            os.path.join(ROOT, "scripts", "build_index_signals.py"),
            "--once",
        ]
        code, line = _run_command(signals_cmd, ROOT)
        if code != 0:
            log_parts.append("signals failed" + (f": {line}" if line else ""))
            status["last_log"] = "; ".join(log_parts)[:200]
            _write_indices_status(_indices_status_path(), status)
            self._json_response(500, {"ok": False, "error": "signals_failed", "log": status["last_log"]})
            return
        log_parts.append("signals ok")

        rebuild_cmd = [
            sys.executable,
            os.path.join(ROOT, "main.py"),
            "--rebuild",
        ]
        code, line = _run_command(rebuild_cmd, ROOT)
        if code != 0:
            log_parts.append("rebuild failed" + (f": {line}" if line else ""))
            status["last_log"] = "; ".join(log_parts)[:200]
            _write_indices_status(_indices_status_path(), status)
            self._json_response(500, {"ok": False, "error": "rebuild_failed", "log": status["last_log"]})
            return
        log_parts.append("rebuild ok")

        status["last_trade_date"] = _latest_trade_date(self.config.index_normalized_dir)
        status["last_result"] = "success"
        status["last_log"] = "; ".join(log_parts)[:200]
        status["last_duration_sec"] = int(time.time() - start)

        _write_indices_status(_indices_status_path(), status)
        self._json_response(200, {"ok": True, "status": status})

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            normalized_path = _normalize_path(parsed.path)
            admin_path = _normalize_path(self.config.pay_confirm_admin_endpoint)
            confirm_path = _normalize_path(self.config.pay_confirm_endpoint)

            sources_save_path = _normalize_path("/api/admin/sources/save")
            sources_delete_path = _normalize_path("/api/admin/sources/delete")
            sources_run_path = _normalize_path("/api/admin/sources/run")
            indices_upload_path = _normalize_path("/api/admin/indices/upload")

            if normalized_path == indices_upload_path:
                self._handle_indices_upload()
                return

            content_len = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(content_len) if content_len else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._json_response(400, {"ok": False, "error": "bad_json"})
                return

            if normalized_path == sources_save_path:
                payload = _normalize_source_payload(data or {})
                if not payload.get("id") or not payload.get("domain"):
                    self._json_response(400, {"ok": False, "error": "missing_fields"})
                    return

                sources = _load_custom_sources(self.config.custom_sources_file)
                sources = _upsert_custom_source(sources, payload)
                _write_custom_sources(self.config.custom_sources_file, sources)
                _refresh_admin_orders()
                self._json_response(200, {"ok": True})
                return

            if normalized_path == sources_delete_path:
                source_id = str(data.get("id", "")).strip()
                if not source_id:
                    self._json_response(400, {"ok": False, "error": "missing_fields"})
                    return

                base_sources = _load_yaml_sources(self.config.official_sources_file)
                base_ids = {s.get("id") for s in base_sources if isinstance(s, dict)}
                custom_sources = _load_custom_sources(self.config.custom_sources_file)

                if source_id in base_ids:
                    custom_sources = _disable_source(source_id, base_sources, custom_sources)
                else:
                    custom_sources = [s for s in custom_sources if s.get("id") != source_id]

                _write_custom_sources(self.config.custom_sources_file, custom_sources)
                _refresh_admin_orders()
                self._json_response(200, {"ok": True})
                return

            if normalized_path == sources_run_path:
                source_id = str(data.get("id", "")).strip()
                if not source_id:
                    self._json_response(400, {"ok": False, "error": "missing_fields"})
                    return

                status_path = _source_runs_path()
                run_status = _normalize_source_runs(_load_source_runs(status_path))
                run_info = run_status.get(source_id, {})
                if run_info.get("running") and _is_pid_alive(run_info.get("pid")):
                    self._json_response(200, {"ok": True, "running": True})
                    return

                start_time = datetime.utcnow().isoformat() + "Z"
                _update_source_run(
                    status_path,
                    source_id,
                    {"running": True, "start_time": start_time, "pid": None},
                )
                _spawn_source_run(source_id, self.config)
                self._json_response(200, {"ok": True, "running": False})
                return

            if normalized_path == admin_path:
                order_id = str(data.get("order_id", "")).strip()
                report_id = str(data.get("report_id", "")).strip()
                tier = str(data.get("tier", "") or "$9").strip()
                lang = _normalize_lang(data.get("lang", "cn"))
                task_id = str(data.get("task_id", "")).strip()
                task_flag = str(data.get("task", "")).strip().lower()
                is_task = bool(task_id) or task_flag in {"1", "true", "yes"}

                task_entry = _load_task_order(self.config.data_dir, order_id) if order_id else None
                if task_entry:
                    if not task_id:
                        task_id = str(task_entry.get("task_id", "")).strip()
                    if not report_id:
                        report_id = str(task_entry.get("report_id", "")).strip()
                    is_task = True

                if not order_id or not report_id:
                    self._json_response(400, {"ok": False, "error": "missing_fields"})
                    return

                if is_task:
                    if task_entry is None:
                        task_entry = _load_task_order(self.config.data_dir, order_id)
                    task_entry = task_entry or {}
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
            memo_fields = _parse_memo_kv(memo)
            if not task_id:
                task_id = memo_fields.get("task_id", "").strip()
            if not order_type:
                order_type = memo_fields.get("order_type", "").strip().lower()
            if not report_id:
                report_id = memo_fields.get("report_id", "").strip() or report_id
            if not order_id:
                order_id = memo_fields.get("order_id", "").strip() or order_id
            if not tier:
                tier = memo_fields.get("tier", "").strip()
            if not email:
                email = memo_fields.get("email", "").strip()
            is_task = order_type == "task" or bool(task_id)

            if not report_id or not order_id:
                self._json_response(400, {"ok": False, "error": "missing_fields"})
                return

            now = datetime.utcnow().isoformat() + "Z"
            expires_at = (
                datetime.utcnow() + timedelta(days=self.config.unlock_expires_days)
            ).isoformat() + "Z"

            if is_task:
                frequency = str(data.get("frequency", "")).strip() or memo_fields.get("frequency", "") or "weekly"
                plan = str(data.get("plan", "")).strip() or memo_fields.get("plan", "") or frequency
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
            sources_list_path = _normalize_path("/api/admin/sources/list")
            indices_status_path = _normalize_path("/api/admin/indices/status")

            if normalized_path == sources_list_path:
                base_sources = _load_yaml_sources(self.config.official_sources_file)
                custom_sources = _load_custom_sources(self.config.custom_sources_file)
                run_status = _normalize_source_runs(_load_source_runs(_source_runs_path()))
                merged = _merge_sources_for_admin(base_sources, custom_sources, run_status=run_status)
                self._json_response(200, {"ok": True, "sources": merged})
                return

            if normalized_path == indices_status_path:
                status = _load_indices_status(_indices_status_path())
                self._json_response(200, {"ok": True, "status": status})
                return

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

            task_entry = _load_task_order(self.config.data_dir, order_id) if order_id else None
            if task_entry:
                if not task_id:
                    task_id = str(task_entry.get("task_id", "")).strip()
                if not report_id:
                    report_id = str(task_entry.get("report_id", "")).strip()
                is_task = True

            if not order_id or not report_id:
                self._json_response(400, {"ok": False, "error": "missing_fields"})
                return

            if is_task:
                if task_entry is None:
                    task_entry = _load_task_order(self.config.data_dir, order_id)
                task_entry = task_entry or {}
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
