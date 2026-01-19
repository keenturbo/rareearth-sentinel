#!/usr/bin/env python3
import threading
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


def _merge_sources_for_admin(base_sources, custom_sources):
    base_sources = base_sources or []
    custom_sources = custom_sources or []

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


def _spawn_source_run(source_id):
    if not source_id:
        return
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
    subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=log)
    if log is not subprocess.DEVNULL:
        log.close()


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


# ... [rest of file unchanged except new handlers below] ...
