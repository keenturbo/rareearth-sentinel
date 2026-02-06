#!/usr/bin/env python3
import html
import json
import os
import re
import sys
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import now_cst, isoformat


SOCIAL_PREFIXES_CN = ["要点：", "要点:", "要点", "今日要点：", "今日要点", "要点摘要：", "要点摘要"]
SOCIAL_PREFIXES_EN = [
    "tl;dr:",
    "tl;dr",
    "key angles:",
    "key angles",
    "key points:",
    "key points",
    "highlights:",
    "highlights",
    "summary:",
    "summary",
]


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

    html_out = (
        template.replace("{{TOTAL_COUNT}}", str(len(entries)))
        .replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{ORDERS_LIST}}", renderer(entries, reports, lang=lang))
        .replace("{{CONFIRM_ENDPOINT}}", confirm_endpoint)
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)


def _build_sources_page(template_path, output_path, lang="zh"):
    if not os.path.exists(template_path):
        return
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    html_out = (
        template.replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{SOURCES_ENDPOINT}}", "/api/admin/sources/list")
        .replace("{{SOURCES_SAVE_ENDPOINT}}", "/api/admin/sources/save")
        .replace("{{SOURCES_DELETE_ENDPOINT}}", "/api/admin/sources/delete")
        .replace("{{SOURCES_RUN_ENDPOINT}}", "/api/admin/sources/run")
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)


def _build_indices_page(template_path, output_path):
    if not os.path.exists(template_path):
        return
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    html_out = (
        template.replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{UPLOAD_ENDPOINT}}", "/api/admin/indices/upload")
        .replace("{{STATUS_ENDPOINT}}", "/api/admin/indices/status")
        .replace("{{MARKET_STATUS_ENDPOINT}}", "/api/admin/indices/market/status")
        .replace("{{MARKET_SAVE_ENDPOINT}}", "/api/admin/indices/market/save")
        .replace("{{MARKET_CLEAR_ENDPOINT}}", "/api/admin/indices/market/clear-image")
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)


def _build_keywords_page(template_path, output_path):
    if not os.path.exists(template_path):
        return
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    html_out = (
        template.replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{KEYWORDS_ENDPOINT}}", "/api/admin/keywords/list")
        .replace("{{CANDIDATES_ENDPOINT}}", "/api/admin/keywords/candidates")
        .replace("{{KEYWORDS_ADD_ENDPOINT}}", "/api/admin/keywords/add")
        .replace("{{KEYWORDS_DELETE_ENDPOINT}}", "/api/admin/keywords/delete")
        .replace("{{KEYWORDS_APPROVE_ENDPOINT}}", "/api/admin/keywords/approve")
        .replace("{{KEYWORDS_REJECT_ENDPOINT}}", "/api/admin/keywords/reject")
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)


def _read_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _strip_prefix(text, prefixes):
    raw = str(text or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    for prefix in prefixes:
        if lowered.startswith(prefix.lower()):
            raw = raw[len(prefix) :].lstrip(" :：-—•*\t")
            break
    raw = re.sub(r"^[\-\*\u2022]+\s*", "", raw)
    return raw.strip()


def _normalize_punct(text):
    if not text:
        return ""
    return (
        text.replace("。;", "。")
        .replace("；;", "；")
        .replace(".;", ".")
        .replace(";;", ";")
    )


def _compact_bullet(text, limit=80):
    raw = str(text or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"\s+", " ", raw)
    raw = _normalize_punct(raw)
    raw = raw.strip(" \t-—:：•")
    if limit and len(raw) > limit:
        raw = raw[:limit].rstrip("，。;；, .") + "…"
    return raw


def _extract_bullets(text, prefixes=None):
    raw = _strip_prefix(text, prefixes or [])
    raw = raw.replace("\r", " ").replace("\n", " ")
    raw = _normalize_punct(raw)
    if not raw:
        return []
    if "•" in raw:
        parts = [p.strip() for p in raw.split("•") if p.strip()]
    else:
        parts = [raw.strip()]
    cleaned = []
    for part in parts:
        part = re.sub(r"^[\-\*\u2022•]+\s*", "", part)
        part = part.strip(" \t-—:：")
        if part:
            cleaned.append(part)
    return cleaned


def _normalize_compare(text):
    raw = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower())
    return re.sub(r"\s+", " ", raw).strip()


def _is_similar_en(a, b):
    na = _normalize_compare(a)
    nb = _normalize_compare(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    a_words = na.split()
    b_words = nb.split()
    if not a_words or not b_words:
        return False
    prefix_len = 8
    ap = " ".join(a_words[:prefix_len])
    bp = " ".join(b_words[:prefix_len])
    return bool(ap and ap == bp)


def _short_social_line(text, limit=80, prefixes=None):
    raw = _strip_prefix(text, prefixes or [])
    if not raw:
        return ""
    raw = re.sub(r"\s+", " ", raw)
    raw = _normalize_punct(raw)
    for sep in ("。", ".", "；", ";", "！", "!", "？", "?"):
        if sep in raw:
            raw = raw.split(sep)[0].strip()
            break
    if limit and len(raw) > limit:
        raw = raw[:limit].rstrip("，。;；, .") + "…"
    return raw


def _first_text(value):
    if isinstance(value, list):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
    if isinstance(value, str):
        return value.strip()
    return ""


def _extract_daily_headline(daily, fallback):
    if not isinstance(daily, dict):
        return fallback
    for key in ("headline", "title", "summary_title", "summary", "headline_zh", "headline_en"):
        value = daily.get(key)
        if value:
            return str(value).strip()
    return fallback


def _extract_opportunity(daily):
    if not isinstance(daily, dict):
        return ""
    for key in (
        "strategy",
        "strategies",
        "tactics",
        "tactical",
        "actionable_strategy",
        "actionable_strategies",
        "actionable_tactics",
    ):
        text = _first_text(daily.get(key))
        if text:
            return text
    return ""


def _angle_meta(report_id, idx, lang="zh"):
    rid = str(report_id or "").lower()
    if re.search(r"(?:-|_)policy$", rid):
        return "📜", "政策" if lang == "zh" else "Policy"
    if re.search(r"(?:-|_)industry$", rid):
        return "🏭", "产业" if lang == "zh" else "Industry"
    if re.search(r"(?:-|_)capital$", rid):
        return "💰", "资本" if lang == "zh" else "Capital"
    fallback_labels = [
        ("📜", "政策" if lang == "zh" else "Policy"),
        ("🏭", "产业" if lang == "zh" else "Industry"),
        ("💰", "资本" if lang == "zh" else "Capital"),
    ]
    if idx < len(fallback_labels):
        return fallback_labels[idx]
    return "🔹", "要点" if lang == "zh" else "Angle"


def _build_pay_link(base_url, subdir, report_id):
    report_id = str(report_id or "").strip()
    if not report_id:
        return ""
    base = str(base_url or "").strip()
    if base:
        base = base.rstrip("/")
        return f"{base}/{subdir}/pay/{report_id}.html"
    return f"/{subdir}/pay/{report_id}.html"


def _build_social_copy_cn(top3, daily, site_base_url):
    items = top3.get("items") if isinstance(top3, dict) else []
    if not items:
        msg = "今日 Top3 尚未生成，请稍后再试。"
        return msg, msg

    first = items[0] if items else {}
    fallback_title = str(first.get("title_zh") or first.get("title_en") or "稀土情报").strip()
    headline = _extract_daily_headline(daily, fallback_title)

    lines = [f"【重磅】{headline}", "", "要点"]

    for idx, item in enumerate(items[:3]):
        raw = (
            item.get("value_zh")
            or item.get("report_contains_zh")
            or item.get("value_en")
            or item.get("report_contains_en")
        )
        bullets = _extract_bullets(raw, prefixes=SOCIAL_PREFIXES_CN)
        angle = _compact_bullet(bullets[0], limit=60) if bullets else ""
        if not angle:
            angle = _compact_bullet(item.get("title_zh") or item.get("title_en"), limit=60)
        emoji, label = _angle_meta(item.get("report_id"), idx, lang="zh")
        if angle:
            lines.append(f"{emoji} {label}：{angle}")
        else:
            lines.append(f"{emoji} {label}")

    opportunity = _extract_opportunity(daily)
    opportunity = _short_social_line(opportunity, limit=80)
    if opportunity:
        lines.extend(["", f"机会窗口：{opportunity}"])

    link_lines = list(lines)
    report_id = first.get("report_id")
    link = _build_pay_link(site_base_url, "cn", report_id)
    if link:
        link_lines.extend(["", "👉 完整报告（含阈值/情景/行动清单）：", link])
    link_lines.extend(["私信回复【稀土】获取完整版", "#稀土 #关键矿产 #供应链"])

    dm_lines = list(lines)
    dm_lines.extend(["", "私信回复【稀土】获取完整版", "#稀土 #关键矿产 #供应链"])

    return "\n".join(link_lines), "\n".join(dm_lines)


def _build_social_copy_en(top3, daily, site_base_url):
    items = top3.get("items") if isinstance(top3, dict) else []
    if not items:
        msg = "Top 3 not ready yet. Please check later."
        return msg, msg

    first = items[0] if items else {}
    fallback_title = str(first.get("title_en") or first.get("title_zh") or "Rare Earth Signals").strip()
    headline = _extract_daily_headline(daily, fallback_title)

    lines = ["Rare Earth Signals | Top 3", f"Lead: {headline}", "", "Key angles"]
    prev_angle = ""

    for idx, item in enumerate(items[:3]):
        raw = (
            item.get("value_en")
            or item.get("report_contains_en")
            or item.get("value_zh")
            or item.get("report_contains_zh")
        )
        bullets = _extract_bullets(raw, prefixes=SOCIAL_PREFIXES_EN)
        candidate = bullets[0] if bullets else ""
        if candidate and prev_angle and _is_similar_en(candidate, prev_angle) and len(bullets) > 1:
            candidate = bullets[1]
        angle = _compact_bullet(candidate, limit=140)
        if not angle:
            angle = _compact_bullet(item.get("title_en") or item.get("title_zh"), limit=140)
        emoji, label = _angle_meta(item.get("report_id"), idx, lang="en")
        if angle:
            lines.append(f"{emoji} {label}: {angle}")
            prev_angle = angle
        else:
            lines.append(f"{emoji} {label}")

    opportunity = _extract_opportunity(daily)
    opportunity = _short_social_line(opportunity, limit=140)
    if opportunity:
        lines.extend(["", f"Opportunity: {opportunity}"])

    link_lines = list(lines)
    report_id = first.get("report_id")
    link = _build_pay_link(site_base_url, "en", report_id)
    if link:
        link_lines.extend(["", "Full report:", link])
    link_lines.extend(["DM for full report", "#RareEarth #CriticalMinerals #SupplyChain"])

    dm_lines = list(lines)
    dm_lines.extend(["", "DM for full report", "#RareEarth #CriticalMinerals #SupplyChain"])

    return "\n".join(link_lines), "\n".join(dm_lines)


def _build_social_page(template_path, output_path, social_cn_link, social_cn_dm, social_en_link, social_en_dm):
    if not os.path.exists(template_path):
        return
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    html_out = (
        template.replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{SOCIAL_COPY_CN_LINK}}", html.escape(social_cn_link))
        .replace("{{SOCIAL_COPY_CN_DM}}", html.escape(social_cn_dm))
        .replace("{{SOCIAL_COPY_EN_LINK}}", html.escape(social_en_link))
        .replace("{{SOCIAL_COPY_EN_DM}}", html.escape(social_en_dm))
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)


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

    cn_sources_template = os.path.join(config.templates_dir, "admin_sources.html")
    en_sources_template = os.path.join(config.templates_dir, "admin_sources_en.html")
    cn_sources_output = os.path.join(config.static_dir, "cn", "admin", "sources", "index.html")
    en_sources_output = os.path.join(config.static_dir, "en", "admin", "sources", "index.html")

    _build_sources_page(cn_sources_template, cn_sources_output, lang="zh")
    _build_sources_page(en_sources_template, en_sources_output, lang="en")

    cn_indices_template = os.path.join(config.templates_dir, "admin_indices.html")
    cn_indices_output = os.path.join(config.static_dir, "cn", "admin", "indices", "index.html")
    _build_indices_page(cn_indices_template, cn_indices_output)

    cn_keywords_template = os.path.join(config.templates_dir, "admin_keywords.html")
    cn_keywords_output = os.path.join(config.static_dir, "cn", "admin", "keywords", "index.html")
    _build_keywords_page(cn_keywords_template, cn_keywords_output)

    social_template = os.path.join(config.templates_dir, "admin_social.html")
    social_output = os.path.join(config.static_dir, "cn", "admin", "social", "index.html")
    top3_path = os.path.join(config.data_dir, "status", "top3_list.json")
    daily_zh_path = os.path.join(config.data_dir, "status", "daily_report_zh.json")
    daily_en_path = os.path.join(config.data_dir, "status", "daily_report_en.json")
    top3_data = _read_json(top3_path)
    daily_zh = _read_json(daily_zh_path)
    daily_en = _read_json(daily_en_path)
    social_cn_link, social_cn_dm = _build_social_copy_cn(top3_data, daily_zh, config.site_base_url)
    social_en_link, social_en_dm = _build_social_copy_en(top3_data, daily_en, config.site_base_url)
    _build_social_page(
        social_template,
        social_output,
        social_cn_link,
        social_cn_dm,
        social_en_link,
        social_en_dm,
    )

    print("Admin orders pages built")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
