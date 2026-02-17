#!/usr/bin/env python3
import json
import os
from datetime import datetime

from src.config import Config
from src.utils import isoformat, now_cst


def _read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return default
    return data


def _load_catalog(path):
    data = _read_json(path, {})
    if isinstance(data, dict):
        rows = data.get("skus", [])
    elif isinstance(data, list):
        rows = data
    else:
        rows = []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not row.get("active", True) or not row.get("visible", True):
            continue
        out.append(dict(row))
    return out


def _read_reports_index(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    rows.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
    return rows


def _load_packs(path):
    data = _read_json(path, {})
    if isinstance(data, dict):
        rows = data.get("packs", [])
    elif isinstance(data, list):
        rows = data
    else:
        rows = []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("status", "active") != "active":
            continue
        out.append(dict(row))
    return out


def _derive_packs_if_needed(packs, reports):
    if packs:
        return packs

    def _for_lang(lang_key):
        lang_field = "preview_en" if lang_key == "en" else "preview_zh"
        ids = []
        for row in reports:
            report_id = str(row.get("report_id") or "").strip()
            if not report_id:
                continue
            preview = row.get(lang_field) or {}
            title = str(preview.get("title") or "").strip()
            if not title:
                continue
            ids.append(report_id)
            if len(ids) >= 5:
                break
        if not ids:
            return None
        week = now_cst().strftime("%Yw%W")
        price = 29 if lang_key == "en" else 99
        currency = "USD" if lang_key == "en" else "CNY"
        return {
            "pack_id": f"{lang_key}-weekly-{week}",
            "lang": lang_key,
            "period_start": "",
            "period_end": "",
            "included_report_ids": ids,
            "primary_report_id": ids[0],
            "price": price,
            "currency": currency,
            "status": "active",
        }

    derived = []
    for lang in ("en", "cn"):
        pack = _for_lang("en" if lang == "en" else "cn")
        if pack:
            derived.append(pack)
    return derived


def _fmt_price(price, currency, lang):
    try:
        value = float(price)
    except (TypeError, ValueError):
        return "-"
    if currency == "USD":
        return f"${int(value) if value.is_integer() else value}"
    if currency == "CNY":
        return f"¥{int(value) if value.is_integer() else value}"
    if lang == "en":
        return f"{value} {currency}"
    return f"{value} {currency}"


def _escape(text):
    import html
    return html.escape(str(text or ""))


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _render_template(template_path, replacements):
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()
    for key, value in replacements.items():
        html = html.replace(key, value)
    return html


def _build_pricing_cards(catalog, lang):
    subdir = "en" if lang == "en" else "cn"
    blocks = []
    for sku in catalog:
        sku_lang = str(sku.get("lang") or "").lower()
        if sku_lang not in {lang, "all"}:
            continue
        sku_type = str(sku.get("sku_type") or "").strip().lower()
        title = _escape(sku.get("title"))
        desc = _escape(sku.get("description"))
        price = _fmt_price(sku.get("price"), sku.get("currency"), lang)

        if sku_type == "weekly_pack":
            href = f"/{subdir}/weekly-pack/"
        elif sku_type == "task_sprint":
            href = f"/{subdir}/task/sprint/"
        else:
            href = f"/{subdir}/premium/"

        cta = _escape(sku.get("cta_text") or ("Open" if lang == "en" else "查看"))
        blocks.append(
            "<article class=\"card\">"
            f"<h3>{title}</h3>"
            f"<div class=\"price\">{_escape(price)}</div>"
            f"<div class=\"desc\">{desc}</div>"
            f"<a class=\"btn\" href=\"{_escape(href)}\">{cta}</a>"
            "</article>"
        )
    if not blocks:
        return '<div class="card"><div class="desc">No products configured.</div></div>'
    return "".join(blocks)


def _report_title(row, lang):
    preview = row.get("preview_en") if lang == "en" else row.get("preview_zh")
    if isinstance(preview, dict):
        title = str(preview.get("title") or "").strip()
        if title:
            return title
    return str(row.get("source_title") or row.get("report_id") or "").strip()


def _build_weekly_pack_list(packs, reports, lang):
    rows_by_id = {str(r.get("report_id") or ""): r for r in reports if r.get("report_id")}
    blocks = []
    subdir = "en" if lang == "en" else "cn"

    for pack in packs:
        pack_lang = str(pack.get("lang") or "").lower()
        if pack_lang != lang:
            continue
        pack_id = str(pack.get("pack_id") or "").strip()
        report_ids = [str(x).strip() for x in (pack.get("included_report_ids") or []) if str(x).strip()]
        primary = str(pack.get("primary_report_id") or "").strip() or (report_ids[0] if report_ids else "")
        price = _fmt_price(pack.get("price"), pack.get("currency"), lang)

        li = []
        for rid in report_ids[:5]:
            row = rows_by_id.get(rid, {})
            title = _escape(_report_title(row, lang) or rid)
            li.append(f"<li>{title}</li>")

        if not li:
            li.append("<li>暂无报告内容</li>" if lang != "en" else "<li>No reports mapped yet</li>")

        if primary:
            href = f"/{subdir}/pay/{primary}.html"
            cta = "先解锁首篇报告" if lang != "en" else "Unlock first report"
        else:
            href = f"/{subdir}/premium/"
            cta = "查看报告库" if lang != "en" else "Open premium archive"

        period = ""
        start = str(pack.get("period_start") or "").strip()
        end = str(pack.get("period_end") or "").strip()
        if start and end:
            period = f"{start} ~ {end}"

        blocks.append(
            f"<article class=\"card\" id=\"pack-{_escape(pack_id)}\">"
            f"<h3>{_escape(pack_id)}</h3>"
            f"<span class=\"badge\">{_escape(price)}</span>"
            + (f"<span class=\"meta\">{_escape(period)}</span>" if period else "") +
            f"<ul>{''.join(li)}</ul>"
            f"<a class=\"btn\" href=\"{_escape(href)}\">{_escape(cta)}</a>"
            "</article>"
        )

    if not blocks:
        return '<article class="card"><div class="meta">暂无可用周包</div></article>' if lang != "en" else '<article class="card"><div class="meta">No weekly pack available yet.</div></article>'
    return "".join(blocks)


def _build_task_cards(catalog, lang):
    blocks = []
    subdir = "en" if lang == "en" else "cn"
    for sku in catalog:
        if str(sku.get("sku_type") or "").lower() != "task_sprint":
            continue
        sku_lang = str(sku.get("lang") or "").lower()
        if sku_lang not in {lang, "all"}:
            continue
        title = _escape(sku.get("title"))
        desc = _escape(sku.get("description"))
        price = _fmt_price(sku.get("price"), sku.get("currency"), lang)
        cta = _escape(sku.get("cta_text") or ("Start" if lang == "en" else "启动"))
        href = f"/{subdir}/premium/"
        blocks.append(
            "<section class=\"card\">"
            f"<h3>{title}</h3>"
            f"<div class=\"meta\">{_escape(price)} · 14 days</div>"
            f"<div class=\"meta\" style=\"margin-top:6px\">{desc}</div>"
            f"<a class=\"btn\" href=\"{_escape(href)}\">{cta}</a>"
            "</section>"
        )
    if not blocks:
        return '<section class="card"><div class="meta">暂无任务产品配置</div></section>' if lang != "en" else '<section class="card"><div class="meta">No task sprint SKU configured.</div></section>'
    return "".join(blocks)


def build_product_pages(config):
    catalog_path = os.path.join(config.data_dir, "products", "catalog.json")
    packs_path = os.path.join(config.data_dir, "products", "packs.json")
    reports_index_path = os.path.join(config.premium_dir, "reports_index.jsonl")

    catalog = _load_catalog(catalog_path)
    reports = _read_reports_index(reports_index_path)
    packs = _derive_packs_if_needed(_load_packs(packs_path), reports)

    last_update = isoformat(now_cst())

    mappings = [
        ("cn", "templates/pricing.html", "static/cn/pricing/index.html", "{{PRICING_CARDS}}", _build_pricing_cards(catalog, "cn")),
        ("en", "templates/pricing_en.html", "static/en/pricing/index.html", "{{PRICING_CARDS}}", _build_pricing_cards(catalog, "en")),
        ("cn", "templates/weekly_pack.html", "static/cn/weekly-pack/index.html", "{{WEEKLY_PACK_LIST}}", _build_weekly_pack_list(packs, reports, "cn")),
        ("en", "templates/weekly_pack_en.html", "static/en/weekly-pack/index.html", "{{WEEKLY_PACK_LIST}}", _build_weekly_pack_list(packs, reports, "en")),
        ("cn", "templates/task_sprint.html", "static/cn/task/sprint/index.html", "{{TASK_SPRINT_CARDS}}", _build_task_cards(catalog, "cn")),
        ("en", "templates/task_sprint_en.html", "static/en/task/sprint/index.html", "{{TASK_SPRINT_CARDS}}", _build_task_cards(catalog, "en")),
    ]

    for _, tpl_rel, out_rel, slot, body in mappings:
        tpl = os.path.join(ROOT, tpl_rel)
        out = os.path.join(ROOT, out_rel)
        html = _render_template(tpl, {
            "{{LAST_UPDATE}}": last_update,
            slot: body,
        })
        _write(out, html)


def main():
    config = Config.from_env()
    build_product_pages(config)
    print("Product pages built")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
