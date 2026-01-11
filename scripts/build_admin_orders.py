#!/usr/bin/env python3
import json
import os
import sys
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import now_cst, isoformat


def _read_jsonl(path):
    entries = []
    if not os.path.exists(path):
        return entries
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def _read_reports_index(path):
    data = {}
    if not os.path.exists(path):
        return data
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            report_id = entry.get("report_id")
            if report_id:
                data[report_id] = entry
    return data


def _render_orders(entries, reports, lang="zh"):
    if not entries:
        empty = "暂无订单" if lang == "zh" else "No orders yet."
        return f'<div class="empty">{empty}</div>'

    blocks = []
    for entry in entries:
        report_id = entry.get("report_id", "")
        order_id = entry.get("order_id", "")
        tier = entry.get("tier", "")
        created_at = entry.get("created_at", "")
        status = entry.get("status", "submitted")
        email = entry.get("email", "-")
        method = entry.get("payment_method", "-")
        token = entry.get("payment_token", "-")

        report = reports.get(report_id, {})
        title = report.get("source_title", report_id)
        preview = report.get("preview_zh") if lang == "zh" else report.get("preview_en")
        if isinstance(preview, dict):
            title = preview.get("title") or title

        prefix = "/cn" if lang == "zh" else "/en"
        pay_link = f"{prefix}/pay/{report_id}.html"
        admin_link = f"{prefix}/admin/premium/{report_id}.html"

        meta = f"{created_at} · {order_id} · {tier} · {status}"
        extra = f"{email} · {method} · {token}"

        blocks.append(
            '<div class="card">'
            f'<div class="card-title">{title}</div>'
            f'<div class="card-meta">{meta}</div>'
            f'<div class="card-meta">{extra}</div>'
            '<div class="card-actions">'
            f'<a href="{pay_link}" target="_blank">Pay Page</a>'
            f'<a href="{admin_link}" target="_blank">Report</a>'
            '</div>'
            '</div>'
        )
    return "".join(blocks)


def _build_page(template_path, output_path, entries, reports, lang="zh"):
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    html = (
        template.replace("{{TOTAL_COUNT}}", str(len(entries)))
        .replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{ORDERS_LIST}}", _render_orders(entries, reports, lang=lang))
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    config = Config.from_env()
    orders_path = os.path.join(config.data_dir, "pay", "orders.jsonl")
    entries = _read_jsonl(orders_path)
    entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)

    reports_index = os.path.join(config.premium_dir, "reports_index.jsonl")
    reports = _read_reports_index(reports_index)

    cn_template = os.path.join(config.templates_dir, "admin_orders.html")
    en_template = os.path.join(config.templates_dir, "admin_orders_en.html")

    cn_output = os.path.join(config.static_dir, "cn", "admin", "orders", "index.html")
    en_output = os.path.join(config.static_dir, "en", "admin", "orders", "index.html")

    _build_page(cn_template, cn_output, entries, reports, lang="zh")
    _build_page(en_template, en_output, entries, reports, lang="en")

    print("Admin orders pages built")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
