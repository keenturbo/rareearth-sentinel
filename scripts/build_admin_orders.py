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

try:
    from src.social_copy_ai import generate_social_copy_bundle
except Exception:
    generate_social_copy_bundle = None


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

ANGLE_KEYWORDS = {
    "zh": {
        "政策": ["政策", "监管", "出口", "管制", "关税", "法规", "政府", "配额", "许可", "禁令", "储备"],
        "产业": ["供应链", "产能", "生产", "加工", "分离", "冶炼", "库存", "物流", "制造", "瓶颈", "产线"],
        "资本": ["估值", "资金", "融资", "流动性", "资本", "投资", "市值", "溢价", "并购", "股价", "现金流"],
    },
    "en": {
        "Policy": [
            "policy",
            "regulation",
            "export",
            "control",
            "tariff",
            "sanction",
            "government",
            "rule",
            "mandate",
            "ban",
            "license",
            "compliance",
            "stockpile",
        ],
        "Industry": [
            "supply chain",
            "capacity",
            "production",
            "processing",
            "separation",
            "inventory",
            "logistics",
            "refining",
            "manufacturing",
            "bottleneck",
            "reserves",
            "reserve",
        ],
        "Capital": [
            "valuation",
            "liquidity",
            "funding",
            "capital",
            "financing",
            "equity",
            "multiple",
            "market cap",
            "investor",
            "premium",
            "cash flow",
            "cashflow",
        ],
    },
}


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


def _normalize_compare_any(text):
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text or "").lower())


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


def _is_similar_any(a, b):
    na = _normalize_compare_any(a)
    nb = _normalize_compare_any(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    sa, sb = set(na), set(nb)
    union = sa | sb
    if not union:
        return False
    return (len(sa & sb) / len(union)) >= 0.72


def _pick_angle_bullet(bullets, label, lang="zh"):
    if not bullets:
        return ""
    if len(bullets) == 1:
        return bullets[0]

    keywords = ANGLE_KEYWORDS.get(lang, {}).get(label, [])
    if not keywords:
        return bullets[0]

    for bullet in bullets:
        haystack = str(bullet or "")
        target = haystack.lower() if lang == "en" else haystack
        for kw in keywords:
            if kw in target:
                return bullet
    return bullets[0]


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


def _clip_words(text, max_words=22):
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return ""
    words = raw.split(" ")
    if len(words) <= max_words:
        return raw
    return " ".join(words[:max_words]).rstrip(" ,;:.") + "..."


def _word_count(text):
    return len(re.findall(r"\S+", str(text or "")))


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


def _view_from_report_id(report_id):
    rid = str(report_id or "").lower()
    if rid.endswith("-policy") or rid.endswith("_policy"):
        return "policy"
    if rid.endswith("-industry") or rid.endswith("_industry"):
        return "industry"
    if rid.endswith("-capital") or rid.endswith("_capital"):
        return "capital"
    return ""


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _build_pay_link(base_url, subdir, report_id):
    report_id = str(report_id or "").strip()
    if not report_id:
        return ""
    base = str(base_url or "").strip()
    if base:
        base = base.rstrip("/")
        return f"{base}/{subdir}/pay/{report_id}.html"
    return f"/{subdir}/pay/{report_id}.html"


def _pick_primary_report_id(items, reports_index):
    if not isinstance(items, list):
        return ""
    candidates = []
    for item in items:
        rid = str((item or {}).get("report_id") or "").strip()
        if rid:
            candidates.append(rid)
    if not candidates:
        return ""
    if isinstance(reports_index, dict) and reports_index:
        for rid in candidates:
            if rid in reports_index:
                return rid
    return candidates[0]


def _strip_ai_tail(text):
    lines = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.search(r"https?://", line):
            continue
        low = line.lower()
        if low.startswith("dm for full report"):
            continue
        if line.startswith("私信回复【稀土】获取完整版"):
            continue
        if line.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _format_ai_social(base_text, link, lang="zh", with_link=True):
    base = _strip_ai_tail(base_text)
    if not base:
        return ""

    out = [base]
    if with_link and link:
        if lang == "zh":
            out.extend(["", "👉 解锁深度报告：", link])
        else:
            out.extend(["", "Unlock full report:", link])

    if lang == "zh":
        out.extend(["私信回复【稀土】获取完整版", "#稀土 #关键矿产 #供应链"])
    else:
        out.extend(["DM for full report", "#RareEarth #CriticalMinerals #SupplyChain"])

    return "\n".join(out).strip()


def _select_top1_item(items):
    if not items:
        return None
    pref = {"policy": 2, "industry": 1, "capital": 0}

    def key_fn(item):
        risk = _to_int((item or {}).get("risk_score"))
        impact = _to_int((item or {}).get("impact_score"))
        view = _view_from_report_id((item or {}).get("report_id"))
        return (risk, impact, pref.get(view, -1))

    return sorted(items, key=key_fn, reverse=True)[0]


def _collect_angles(items, lang="zh"):
    mapping = {
        "zh": ["政策", "产业", "资本"],
        "en": ["Policy", "Industry", "Capital"],
    }
    labels = mapping[lang]
    by_label = {label: "" for label in labels}
    pool = []

    for idx, item in enumerate(items[:3]):
        view = _view_from_report_id(item.get("report_id"))
        if lang == "zh":
            label = {"policy": "政策", "industry": "产业", "capital": "资本"}.get(view)
            raw = item.get("value_zh") or item.get("report_contains_zh") or item.get("title_zh") or item.get("title")
            prefixes = SOCIAL_PREFIXES_CN
            limit = 62
        else:
            label = {"policy": "Policy", "industry": "Industry", "capital": "Capital"}.get(view)
            raw = item.get("value_en") or item.get("report_contains_en") or item.get("title_en") or item.get("title")
            prefixes = SOCIAL_PREFIXES_EN
            limit = 180

        if not label:
            label = labels[min(idx, len(labels) - 1)]

        bullets = _extract_bullets(raw, prefixes=prefixes)
        candidate = _pick_angle_bullet(bullets, label, lang=lang)
        if lang == "zh":
            line = _compact_bullet(candidate, limit=limit)
        else:
            line = _clip_words(_compact_bullet(candidate, limit=220), max_words=20)

        if line:
            if not by_label[label]:
                by_label[label] = line
            pool.append(line)

    used = []
    for label in labels:
        line = by_label[label]
        if line and not any(_is_similar_any(line, u) for u in used):
            used.append(line)
            continue
        by_label[label] = ""

    for label in labels:
        if by_label[label]:
            continue
        for candidate in pool:
            if not any(_is_similar_any(candidate, u) for u in used):
                by_label[label] = candidate
                used.append(candidate)
                break

    return by_label


def _build_evidence(items, lang="zh"):
    out = []
    if lang == "zh":
        keys = ("title_zh", "title")
        clip = 28
        sep = "；"
    else:
        keys = ("title_en", "title")
        clip = 64
        sep = "; "

    for item in items[:3]:
        text = ""
        for key in keys:
            v = str(item.get(key) or "").strip()
            if v:
                text = v
                break
        text = _compact_bullet(text, limit=clip)
        if text and not any(_is_similar_any(text, p) for p in out):
            out.append(text)
        if len(out) >= 2:
            break
    return sep.join(out)


def _build_report_includes(items, lang="zh"):
    if not items:
        return ""
    item = items[0]
    if lang == "zh":
        raw = item.get("report_contains_zh") or item.get("value_zh") or ""
        parts = _extract_bullets(raw, prefixes=SOCIAL_PREFIXES_CN)
        picked = parts[0] if parts else raw
        return _compact_bullet(picked, limit=120)
    raw = item.get("report_contains_en") or item.get("value_en") or ""
    parts = _extract_bullets(raw, prefixes=SOCIAL_PREFIXES_EN)
    picked = parts[0] if parts else raw
    return _clip_words(_compact_bullet(picked, limit=320), max_words=28)


def _build_structured_payload(items, daily, lang="zh"):
    if not items:
        return None

    first = items[0]
    fallback_title = str(first.get("title_zh") or first.get("title_en") or first.get("title") or "").strip()
    headline = _extract_daily_headline(daily, fallback_title)

    if lang == "zh":
        core = _short_social_line(headline, limit=76)
    else:
        core = _clip_words(_compact_bullet(headline, limit=260), max_words=18)

    angles = _collect_angles(items, lang=lang)
    filled_angles = [v for v in angles.values() if v]
    if len(filled_angles) < 2:
        return None

    opportunity = _extract_opportunity(daily)
    if lang == "zh":
        opportunity = _short_social_line(opportunity, limit=84)
    else:
        opportunity = _clip_words(_compact_bullet(opportunity, limit=260), max_words=22)

    evidence = _build_evidence(items, lang=lang)
    includes = _build_report_includes(items, lang=lang)

    return {
        "core": core,
        "angles": angles,
        "opportunity": opportunity,
        "evidence": evidence,
        "includes": includes,
    }


def _build_social_fallback_cn(items, daily, site_base_url, reports_index, with_link=True):
    top1 = _select_top1_item(items)
    if not top1:
        msg = "今日重点专题暂不可用，请稍后再试。"
        return msg

    report_id = _pick_primary_report_id([top1], reports_index or {})
    link = _build_pay_link(site_base_url, "cn", report_id)
    title = _extract_daily_headline(daily, str(top1.get("title_zh") or top1.get("title") or "稀土情报").strip())
    signal = _compact_bullet(top1.get("value_zh") or top1.get("report_contains_zh") or top1.get("title_zh") or top1.get("title"), limit=88)
    includes = _build_report_includes([top1], lang="zh")

    lines = [
        f"【今日重点专题】{title}",
        f"核心结论：{_short_social_line(title, limit=72)}",
        "三视角",
        f"📜 政策：{signal}",
        f"🏭 产业：{signal}",
        f"💰 资本：{signal}",
    ]
    if includes:
        lines.append(f"报告包含：{includes}")
    if with_link and link:
        lines.extend(["", "👉 解锁深度报告：", link])
    lines.extend(["私信回复【稀土】获取完整版", "#稀土 #关键矿产 #供应链"])
    return "\n".join(lines)


def _build_social_fallback_en(items, daily, site_base_url, reports_index, with_link=True):
    top1 = _select_top1_item(items)
    if not top1:
        msg = "Today's focus is unavailable right now. Please check later."
        return msg

    report_id = _pick_primary_report_id([top1], reports_index or {})
    link = _build_pay_link(site_base_url, "en", report_id)
    title = _extract_daily_headline(daily, str(top1.get("title_en") or top1.get("title") or "Rare Earth Signals").strip())
    signal = _clip_words(_compact_bullet(top1.get("value_en") or top1.get("report_contains_en") or top1.get("title_en") or top1.get("title"), limit=260), max_words=18)
    includes = _build_report_includes([top1], lang="en")

    lines = [
        "Today's Focus",
        f"Conclusion: {_clip_words(title, max_words=18)}",
        "Three angles",
        f"📜 Policy: {signal}",
        f"🏭 Industry: {signal}",
        f"💰 Capital: {signal}",
    ]
    if includes:
        lines.append(f"Report includes: {includes}")
    if with_link and link:
        lines.extend(["", "Unlock full report:", link])
    lines.extend(["DM for full report", "#RareEarth #CriticalMinerals #SupplyChain"])
    return "\n".join(lines)


def _build_social_copy_cn(top3, daily, site_base_url, reports_index=None):
    items = top3.get("items") if isinstance(top3, dict) else []
    if not items:
        msg = "今日 Top3 尚未生成，请稍后再试。"
        return msg, msg

    payload = _build_structured_payload(items, daily, lang="zh")
    if not payload:
        return (
            _build_social_fallback_cn(items, daily, site_base_url, reports_index, with_link=True),
            _build_social_fallback_cn(items, daily, site_base_url, reports_index, with_link=False),
        )

    report_id = _pick_primary_report_id(items, reports_index or {})
    link = _build_pay_link(site_base_url, "cn", report_id)

    core = payload.get("core") or ""
    policy = payload.get("angles", {}).get("政策", "")
    industry = payload.get("angles", {}).get("产业", "")
    capital = payload.get("angles", {}).get("资本", "")
    opportunity = payload.get("opportunity") or ""
    evidence = payload.get("evidence") or ""
    includes = payload.get("includes") or ""

    base_lines = [
        "【今日重点专题】",
        f"核心结论：{core}",
        "三视角",
        f"📜 政策：{policy}",
        f"🏭 产业：{industry}",
        f"💰 资本：{capital}",
    ]
    if opportunity:
        base_lines.append(f"机会窗口：{opportunity}")
    if evidence:
        base_lines.append(f"证据锚点：{evidence}")
    if includes:
        base_lines.append(f"报告包含：{includes}")

    narrative = "\n".join(base_lines)
    narrative_len = len(narrative)
    if narrative_len > 420 and includes:
        includes = _compact_bullet(includes, limit=90)
        base_lines[-1] = f"报告包含：{includes}"
        narrative = "\n".join(base_lines)
    if len(narrative) < 260 and items:
        extend = _compact_bullet(items[0].get("report_contains_zh") or items[0].get("value_zh"), limit=120)
        if extend and (not includes or not _is_similar_any(extend, includes)):
            base_lines.append(f"补充：{extend}")

    link_lines = list(base_lines)
    if link:
        link_lines.extend(["", "👉 解锁深度报告：", link])
    link_lines.extend(["私信回复【稀土】获取完整版", "#稀土 #关键矿产 #供应链"])

    dm_lines = list(base_lines)
    dm_lines.extend(["私信回复【稀土】获取完整版", "#稀土 #关键矿产 #供应链"])

    return "\n".join(link_lines), "\n".join(dm_lines)


def _build_social_copy_en(top3, daily, site_base_url, reports_index=None):
    items = top3.get("items") if isinstance(top3, dict) else []
    if not items:
        msg = "Top 3 not ready yet. Please check later."
        return msg, msg

    payload = _build_structured_payload(items, daily, lang="en")
    if not payload:
        return (
            _build_social_fallback_en(items, daily, site_base_url, reports_index, with_link=True),
            _build_social_fallback_en(items, daily, site_base_url, reports_index, with_link=False),
        )

    report_id = _pick_primary_report_id(items, reports_index or {})
    link = _build_pay_link(site_base_url, "en", report_id)

    core = payload.get("core") or ""
    policy = payload.get("angles", {}).get("Policy", "")
    industry = payload.get("angles", {}).get("Industry", "")
    capital = payload.get("angles", {}).get("Capital", "")
    opportunity = payload.get("opportunity") or ""
    evidence = payload.get("evidence") or ""
    includes = payload.get("includes") or ""

    base_lines = [
        "Today's Focus",
        f"Conclusion: {core}",
        "Three angles",
        f"📜 Policy: {policy}",
        f"🏭 Industry: {industry}",
        f"💰 Capital: {capital}",
    ]
    if opportunity:
        base_lines.append(f"Opportunity window: {opportunity}")
    if evidence:
        base_lines.append(f"Evidence anchors: {evidence}")
    if includes:
        base_lines.append(f"Report includes: {includes}")

    wc = _word_count(" ".join(base_lines))
    if wc > 220 and includes:
        includes = _clip_words(includes, max_words=18)
        for i, line in enumerate(base_lines):
            if line.startswith("Report includes:"):
                base_lines[i] = f"Report includes: {includes}"
                break
    wc = _word_count(" ".join(base_lines))
    if wc < 120 and items:
        extra = _clip_words(items[0].get("report_contains_en") or items[0].get("value_en") or "", max_words=20)
        if extra and (not includes or not _is_similar_en(extra, includes)):
            base_lines.append(f"Extra context: {extra}")

    link_lines = list(base_lines)
    if link:
        link_lines.extend(["", "Unlock full report:", link])
    link_lines.extend(["DM for full report", "#RareEarth #CriticalMinerals #SupplyChain"])

    dm_lines = list(base_lines)
    dm_lines.extend(["DM for full report", "#RareEarth #CriticalMinerals #SupplyChain"])

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

    social_cn_link = social_cn_dm = social_en_link = social_en_dm = ""
    social_bundle = None
    if callable(generate_social_copy_bundle):
        try:
            social_bundle = generate_social_copy_bundle(
                config,
                top3_data,
                daily_zh,
                daily_en,
                set(reports.keys()),
                timeout_seconds=8,
            )
        except Exception:
            social_bundle = None

    if social_bundle:
        top_items = top3_data.get("items") if isinstance(top3_data, dict) else []
        primary_id = _pick_primary_report_id(top_items or [], reports)
        cn_link = _build_pay_link(config.site_base_url, "cn", primary_id)
        en_link = _build_pay_link(config.site_base_url, "en", primary_id)

        cn_base = social_bundle.get("cn_topic") or social_bundle.get("cn_social", "")
        en_base = social_bundle.get("en_topic") or social_bundle.get("en_social", "")

        social_cn_link = _format_ai_social(cn_base, cn_link, lang="zh", with_link=True)
        social_cn_dm = _format_ai_social(cn_base, cn_link, lang="zh", with_link=False)
        social_en_link = _format_ai_social(en_base, en_link, lang="en", with_link=True)
        social_en_dm = _format_ai_social(en_base, en_link, lang="en", with_link=False)

    if not all([social_cn_link, social_cn_dm, social_en_link, social_en_dm]):
        social_cn_link, social_cn_dm = _build_social_copy_cn(top3_data, daily_zh, config.site_base_url, reports)
        social_en_link, social_en_dm = _build_social_copy_en(top3_data, daily_en, config.site_base_url, reports)

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
