#!/usr/bin/env python3
import json
import os
import sys

from src.config import Config
from src.utils import isoformat, now_cst

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from scripts.build_weekly_pack import build_weekly_packs
except Exception:
    build_weekly_packs = None


FUNNEL_ENDPOINT = "/api/pay/funnel"


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
        pack = _for_lang(lang)
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
    return f"{value} {currency or ('USD' if lang == 'en' else 'CNY')}"


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


def _find_sku(catalog, sku_type, lang):
    for sku in catalog:
        if str(sku.get("sku_type") or "").lower() != sku_type:
            continue
        sku_lang = str(sku.get("lang") or "").lower()
        if sku_lang in {lang, "all"}:
            return sku
    return {}


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
        sku_id = _escape(sku.get("sku_id") or "")
        channel = _escape(f"pricing_{lang}")
        blocks.append(
            "<article class=\"card\">"
            f"<h3>{title}</h3>"
            f"<div class=\"price\">{_escape(price)}</div>"
            f"<div class=\"desc\">{desc}</div>"
            f"<a class=\"btn funnel-pay-link\" data-sku-id=\"{sku_id}\" data-channel=\"{channel}\" href=\"{_escape(href)}\">{cta}</a>"
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


def _build_weekly_pack_list(packs, reports, lang, weekly_sku_id=""):
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

        if pack_id and primary:
            href = f"/{subdir}/pack/pay/{pack_id}.html"
            cta = "解锁周包" if lang != "en" else "Unlock Weekly Pack"
        elif primary:
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
            + (f"<span class=\"meta\">{_escape(period)}</span>" if period else "")
            + f"<ul>{''.join(li)}</ul>"
            f"<a class=\"btn funnel-pay-link\" data-sku-id=\"{_escape(weekly_sku_id)}\" data-channel=\"weekly_pack_page\" href=\"{_escape(href)}\">{_escape(cta)}</a>"
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
        sku_id = _escape(sku.get("sku_id") or "")
        blocks.append(
            "<section class=\"card\">"
            f"<h3>{title}</h3>"
            f"<div class=\"meta\">{_escape(price)} · 14 days</div>"
            f"<div class=\"meta\" style=\"margin-top:6px\">{desc}</div>"
            f"<a class=\"btn funnel-pay-link\" data-sku-id=\"{sku_id}\" data-channel=\"task_sprint_page\" href=\"{_escape(href)}\">{cta}</a>"
            "</section>"
        )
    if not blocks:
        return '<section class="card"><div class="meta">暂无任务产品配置</div></section>' if lang != "en" else '<section class="card"><div class="meta">No task sprint SKU configured.</div></section>'
    return "".join(blocks)


def _pack_reports_html(pack, rows_by_id, lang):
    report_ids = [str(x).strip() for x in (pack.get("included_report_ids") or []) if str(x).strip()]
    if not report_ids:
        return "<li>暂无报告</li>" if lang != "en" else "<li>No reports in this pack.</li>"
    out = []
    for rid in report_ids[:10]:
        row = rows_by_id.get(rid, {})
        title = _escape(_report_title(row, lang) or rid)
        out.append(f"<li data-report-id=\"{_escape(rid)}\">{title}</li>")
    return "".join(out)


def _build_pack_payment_pages(config, catalog, packs, reports, last_update):
    rows_by_id = {str(r.get("report_id") or ""): r for r in reports if r.get("report_id")}

    for lang in ("cn", "en"):
        sku = _find_sku(catalog, "weekly_pack", lang)
        sku_id = str(sku.get("sku_id") or "").strip()
        channel_default = "weekly_pack_page"

        for pack in packs:
            pack_lang = str(pack.get("lang") or "").lower()
            if pack_lang != lang:
                continue

            pack_id = str(pack.get("pack_id") or "").strip()
            if not pack_id:
                continue

            report_ids = [str(x).strip() for x in (pack.get("included_report_ids") or []) if str(x).strip()]
            primary = str(pack.get("primary_report_id") or "").strip() or (report_ids[0] if report_ids else "")
            if not primary:
                continue

            price_value = pack.get("price")
            currency = str(pack.get("currency") or ("USD" if lang == "en" else "CNY"))
            display_price = _fmt_price(price_value, currency, lang)
            tier = display_price if display_price != "-" else ("$29" if lang == "en" else "¥99")

            template_name = "pack_pay_en.html" if lang == "en" else "pack_pay.html"
            template_path = os.path.join(ROOT, "templates", template_name)
            if not os.path.exists(template_path):
                continue

            report_list_html = _pack_reports_html(pack, rows_by_id, lang)
            title = (
                f"Weekly Pack ({pack_id})"
                if lang == "en"
                else f"周度组合包（{pack_id}）"
            )
            reports_csv = ",".join(report_ids)
            html_out = _render_template(
                template_path,
                {
                    "{{LAST_UPDATE}}": last_update,
                    "{{PACK_ID}}": _escape(pack_id),
                    "{{PACK_TITLE}}": _escape(title),
                    "{{PACK_PRICE}}": _escape(display_price),
                    "{{PACK_TIER}}": _escape(tier),
                    "{{PACK_PRIMARY_REPORT_ID}}": _escape(primary),
                    "{{PACK_REPORT_IDS_CSV}}": _escape(reports_csv),
                    "{{PACK_REPORT_COUNT}}": str(len(report_ids)),
                    "{{PACK_REPORT_LIST}}": report_list_html,
                    "{{PACK_SKU_ID}}": _escape(sku_id),
                    "{{CHANNEL_DEFAULT}}": _escape(channel_default),
                    "{{PAY_CONFIRM_ENDPOINT}}": _escape(config.pay_confirm_endpoint),
                },
            )

            out_path = os.path.join(
                config.static_dir,
                lang,
                "pack",
                "pay",
                f"{pack_id}.html",
            )
            _write(out_path, html_out)


def build_product_pages(config):
    catalog_path = os.path.join(config.data_dir, "products", "catalog.json")
    packs_path = os.path.join(config.data_dir, "products", "packs.json")
    reports_index_path = os.path.join(config.premium_dir, "reports_index.jsonl")

    if callable(build_weekly_packs):
        try:
            build_weekly_packs(config, count=5, langs=("cn", "en"))
        except Exception:
            pass

    catalog = _load_catalog(catalog_path)
    reports = _read_reports_index(reports_index_path)
    packs = _derive_packs_if_needed(_load_packs(packs_path), reports)

    weekly_sku_cn = str(_find_sku(catalog, "weekly_pack", "cn").get("sku_id") or "")
    weekly_sku_en = str(_find_sku(catalog, "weekly_pack", "en").get("sku_id") or "")

    last_update = isoformat(now_cst())

    mappings = [
        ("templates/pricing.html", os.path.join(config.static_dir, "cn", "pricing", "index.html"), "{{PRICING_CARDS}}", _build_pricing_cards(catalog, "cn"), "cn", "pricing_page"),
        ("templates/pricing_en.html", os.path.join(config.static_dir, "en", "pricing", "index.html"), "{{PRICING_CARDS}}", _build_pricing_cards(catalog, "en"), "en", "pricing_page"),
        ("templates/weekly_pack.html", os.path.join(config.static_dir, "cn", "weekly-pack", "index.html"), "{{WEEKLY_PACK_LIST}}", _build_weekly_pack_list(packs, reports, "cn", weekly_sku_id=weekly_sku_cn), "cn", "weekly_pack_page"),
        ("templates/weekly_pack_en.html", os.path.join(config.static_dir, "en", "weekly-pack", "index.html"), "{{WEEKLY_PACK_LIST}}", _build_weekly_pack_list(packs, reports, "en", weekly_sku_id=weekly_sku_en), "en", "weekly_pack_page"),
        ("templates/task_sprint.html", os.path.join(config.static_dir, "cn", "task", "sprint", "index.html"), "{{TASK_SPRINT_CARDS}}", _build_task_cards(catalog, "cn"), "cn", "task_sprint_page"),
        ("templates/task_sprint_en.html", os.path.join(config.static_dir, "en", "task", "sprint", "index.html"), "{{TASK_SPRINT_CARDS}}", _build_task_cards(catalog, "en"), "en", "task_sprint_page"),
    ]

    for tpl_rel, out_path, slot, body, page_lang, page_channel in mappings:
        tpl_path = os.path.join(ROOT, tpl_rel)
        if not os.path.exists(tpl_path):
            continue
        html = _render_template(
            tpl_path,
            {
                "{{LAST_UPDATE}}": last_update,
                slot: body,
                "{{FUNNEL_ENDPOINT}}": FUNNEL_ENDPOINT,
                "{{PAGE_LANG}}": page_lang,
                "{{PAGE_CHANNEL}}": page_channel,
            },
        )
        _write(out_path, html)

    _build_pack_payment_pages(config, catalog, packs, reports, last_update)


def main():
    config = Config.from_env()
    build_product_pages(config)
    print("Product pages built")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
