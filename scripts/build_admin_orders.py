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
    entries = {}
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            order_id = entry.get("order_id")
            if not order_id:
                continue
            # Later entries overwrite earlier ones
            entries[order_id] = entry
    return list(entries.values())


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
    confirm_label = "确认收款" if lang == "zh" else "Confirm"
    confirmed_text = "已确认" if lang == "zh" else "Confirmed"

    for entry in entries:
        report_id = entry.get("report_id", "")
        order_id = entry.get("order_id", "")
        tier = entry.get("tier", "")
        created_at = entry.get("created_at", "")
        status = entry.get("status", "submitted")
        email = entry.get("email", "-")
        method = entry.get("payment_method", "-")
        token = entry.get("payment_token", "-")
        unlock_url = entry.get("unlock_url", "")

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

        confirm_btn = (
            f'<button class="confirm-btn" data-order-id="{order_id}" '
            f'data-report-id="{report_id}" data-tier="{tier}" data-lang="{lang}">{confirm_label}</button>'
            if status != "confirmed"
            else f'<span class="confirmed">{confirmed_text}</span>'
        )

        unlock_block = (
            f'<div class="unlock-link"><a href="{unlock_url}" target="_blank">{unlock_url}</a></div>'
            if unlock_url
            else '<div class="unlock-link" data-empty="1"></div>'
        )

        blocks.append(
            '<div class="card">'
            f'<div class="card-title">{title}</div>'
            f'<div class="card-meta">{meta}</div>'
            f'<div class="card-meta">{extra}</div>'
            '<div class="card-actions">'
            f'<a href="{pay_link}" target="_blank">Pay Page</a>'
            f'<a href="{admin_link}" target="_blank">Report</a>'
            '</div>'
            f'<div class="card-confirm">{confirm_btn}</div>'
            f'{unlock_block}'
            '</div>'
        )
    return "".join(blocks)


def _render_task_orders(entries, reports, lang="zh"):
    if not entries:
        empty = "暂无任务订单" if lang == "zh" else "No task orders yet."
        return f'<div class="empty">{empty}</div>'

    blocks = []
    confirm_label = "确认收款" if lang == "zh" else "Confirm"
    confirmed_text = "已确认" if lang == "zh" else "Confirmed"

    for entry in entries:
        report_id = entry.get("report_id", "")
        order_id = entry.get("order_id", "")
        task_id = entry.get("task_id", "")
        tier = entry.get("tier", "")
        created_at = entry.get("created_at", "")
        status = entry.get("status", "submitted")
        task_status = entry.get("task_status", "pending")
        frequency = entry.get("frequency", "-")
        email = entry.get("email", "-")
        method = entry.get("payment_method", "-")
        task_url = entry.get("task_url", "")

        title = entry.get("report_title") or ""
        if not title:
            report = reports.get(report_id, {})
            title = report.get("source_title", report_id)
            preview = report.get("preview_zh") if lang == "zh" else report.get("preview_en")
            if isinstance(preview, dict):
                title = preview.get("title") or title

        suggestion = entry.get("task_suggestion", "")
        prefix = "/cn" if lang == "zh" else "/en"
        pay_link = f"{prefix}/task/pay/{report_id}.html"
        task_link = f"{prefix}/task/{task_id}.html" if task_id else ""

        meta = f"{created_at} · {order_id} · {tier} · {status} · {task_status}"
        extra = f"{frequency} · {email} · {method}"

        confirm_btn = (
            f'<button class="confirm-btn" data-order-id="{order_id}" '
            f'data-report-id="{report_id}" data-task-id="{task_id}" data-tier="{tier}" data-lang="{lang}">{confirm_label}</button>'
            if status != "confirmed"
            else f'<span class="confirmed">{confirmed_text}</span>'
        )

        unlock_block = (
            f'<div class="unlock-link"><a href="{task_url}" target="_blank">{task_url}</a></div>'
            if task_url
            else '<div class="unlock-link" data-empty="1"></div>'
        )

        actions = [f'<a href="{pay_link}" target="_blank">Pay Page</a>']
        if task_link:
            actions.append(f'<a href="{task_link}" target="_blank">Task</a>')

        blocks.append(
            '<div class="card">'
            f'<div class="card-title">{title}</div>'
            f'<div class="card-meta">{meta}</div>'
            f'<div class="card-meta">{suggestion}</div>'
            f'<div class="card-meta">{extra}</div>'
            '<div class="card-actions">'
            f"{' '.join(actions)}"
            '</div>'
            f'<div class="card-confirm">{confirm_btn}</div>'
            f'{unlock_block}'
            '</div>'
        )
    return "".join(blocks)


def _build_page(template_path, output_path, entries, reports, lang="zh", confirm_endpoint="", renderer=_render_orders):
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    html = (
        template.replace("{{TOTAL_COUNT}}", str(len(entries)))
        .replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{ORDERS_LIST}}", renderer(entries, reports, lang=lang))
        .replace("{{CONFIRM_ENDPOINT}}", confirm_endpoint)
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    config = Config.from_env()
    orders_path = os.path.join(config.data_dir, "pay", "orders.jsonl")
    task_orders_path = os.path.join(config.data_dir, "pay", "task_orders.jsonl")
    entries = _read_jsonl(orders_path)
    task_entries = _read_jsonl(task_orders_path)
    entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    task_entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)

    reports_index = os.path.join(config.premium_dir, "reports_index.jsonl")
    reports = _read_reports_index(reports_index)

    cn_template = os.path.join(config.templates_dir, "admin_orders.html")
    en_template = os.path.join(config.templates_dir, "admin_orders_en.html")
    cn_task_template = os.path.join(config.templates_dir, "admin_task_orders.html")
    en_task_template = os.path.join(config.templates_dir, "admin_task_orders_en.html")

    cn_output = os.path.join(config.static_dir, "cn", "admin", "orders", "index.html")
    en_output = os.path.join(config.static_dir, "en", "admin", "orders", "index.html")
    cn_task_output = os.path.join(config.static_dir, "cn", "admin", "task-orders", "index.html")
    en_task_output = os.path.join(config.static_dir, "en", "admin", "task-orders", "index.html")

    _build_page(
        cn_template,
        cn_output,
        entries,
        reports,
        lang="zh",
        confirm_endpoint=config.pay_confirm_admin_endpoint,
        renderer=_render_orders,
    )
    _build_page(
        en_template,
        en_output,
        entries,
        reports,
        lang="en",
        confirm_endpoint=config.pay_confirm_admin_endpoint,
        renderer=_render_orders,
    )

    _build_page(
        cn_task_template,
        cn_task_output,
        task_entries,
        reports,
        lang="zh",
        confirm_endpoint=config.pay_confirm_admin_endpoint,
        renderer=_render_task_orders,
    )
    _build_page(
        en_task_template,
        en_task_output,
        task_entries,
        reports,
        lang="en",
        confirm_endpoint=config.pay_confirm_admin_endpoint,
        renderer=_render_task_orders,
    )

    print("Admin orders pages built")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
