#!/usr/bin/env python3
import argparse
import html
import json
import os
import sys
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from scripts.pay_confirm_server import _issue_unlock


def _read_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_jsonl(path):
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
            except Exception:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def _parse_memo(memo):
    out = {}
    for part in str(memo or "").split():
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        if not k:
            continue
        out[k] = v.strip()
    return out


def _load_packs(path):
    data = _read_json(path)
    packs = data.get("packs") if isinstance(data, dict) else []
    if not isinstance(packs, list):
        packs = []
    out = []
    for pack in packs:
        if not isinstance(pack, dict):
            continue
        if str(pack.get("status") or "active") != "active":
            continue
        out.append(pack)
    return out


def _read_reports_index(path):
    rows = _read_jsonl(path)
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return rows


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
        week = datetime.utcnow().strftime("%Yw%W")
        return {
            "pack_id": f"{lang_key}-weekly-{week}",
            "lang": lang_key,
            "period_start": "",
            "period_end": "",
            "included_report_ids": ids,
            "primary_report_id": ids[0],
            "price": 29 if lang_key == "en" else 99,
            "currency": "USD" if lang_key == "en" else "CNY",
            "status": "active",
        }

    out = []
    for lang in ("en", "cn"):
        pack = _for_lang(lang)
        if pack:
            out.append(pack)
    return out


def _all_packs(config):
    packs_path = os.path.join(config.data_dir, "products", "packs.json")
    packs = _load_packs(packs_path)
    reports = _read_reports_index(os.path.join(config.premium_dir, "reports_index.jsonl"))
    return _derive_packs_if_needed(packs, reports)


def _load_pack(config, pack_id, report_id="", lang=""):
    packs = _all_packs(config)
    if pack_id:
        for pack in packs:
            if str(pack.get("pack_id") or "").strip() == pack_id:
                return pack

    if report_id:
        report_id = str(report_id).strip()
        lang = str(lang or "").strip().lower()
        for pack in packs:
            pack_lang = str(pack.get("lang") or "").strip().lower()
            if lang in {"cn", "en"} and pack_lang and pack_lang != lang:
                continue
            primary = str(pack.get("primary_report_id") or "").strip()
            included = [str(x).strip() for x in (pack.get("included_report_ids") or [])]
            if report_id == primary or report_id in included:
                return pack

    return {}


def _load_order_entry(data_dir, order_id):
    path = os.path.join(data_dir, "pay", "orders.jsonl")
    if not os.path.exists(path):
        return {}
    latest = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if str(row.get("order_id") or "") == order_id:
                latest = row
    return latest


def _load_report_title(index_path, report_id, lang="cn"):
    rows = _read_jsonl(index_path)
    for row in rows:
        if str(row.get("report_id") or "") != report_id:
            continue
        preview = row.get("preview_zh") if lang == "cn" else row.get("preview_en")
        if isinstance(preview, dict):
            t = str(preview.get("title") or "").strip()
            if t:
                return t
        t = str(row.get("source_title") or "").strip()
        if t:
            return t
    return report_id


def _render_unlock_page(config, lang, order_id, pack_id, report_links):
    prefix = "en" if lang == "en" else "cn"
    tpl_name = "pack_unlock_en.html" if lang == "en" else "pack_unlock.html"
    tpl_path = os.path.join(config.templates_dir, tpl_name)
    if not os.path.exists(tpl_path):
        items = "".join(
            f'<li><a href="{html.escape(it["url"])}" target="_blank">{html.escape(it["title"])}</a></li>'
            for it in report_links
        )
        return (
            "<!doctype html><html><body><h1>Weekly Pack Delivery</h1>"
            f"<p>Order: {html.escape(order_id)}</p><ul>{items}</ul></body></html>"
        )

    with open(tpl_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    pack_title = f"周包交付（{pack_id}）" if lang != "en" else f"Weekly Pack Delivery ({pack_id})"
    delivered_at = datetime.utcnow().isoformat() + "Z"
    items_html = "".join(
        f'<li><a href="{html.escape(it["url"])}" target="_blank">{html.escape(it["title"])}</a></li>'
        for it in report_links
    )

    return (
        tpl.replace("{{PACK_TITLE}}", html.escape(pack_title))
        .replace("{{ORDER_ID}}", html.escape(order_id))
        .replace("{{DELIVERED_AT}}", html.escape(delivered_at))
        .replace("{{REPORT_ITEMS}}", items_html)
    )


def _update_order_rows(orders_path, order_id, updates):
    rows = _read_jsonl(orders_path)
    changed = False
    for row in rows:
        if str(row.get("order_id") or "") == order_id:
            row.update(updates)
            changed = True
    if changed:
        _write_jsonl(orders_path, rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--lang", default="")
    args = parser.parse_args()

    config = Config.from_env()
    order_id = str(args.order_id).strip()

    status_path = os.path.join(config.static_dir, "pay", "status", f"{order_id}.json")
    status = _read_json(status_path)
    if not status:
        print("error: order status not found")
        return 1

    order_entry = _load_order_entry(config.data_dir, order_id)

    status_memo_fields = _parse_memo(status.get("memo", ""))
    order_memo_fields = _parse_memo(order_entry.get("memo", ""))

    pack_id = str(
        status.get("pack_id")
        or status_memo_fields.get("pack_id")
        or order_entry.get("pack_id")
        or order_memo_fields.get("pack_id")
        or ""
    ).strip()
    sku_id = str(
        status.get("sku_id")
        or status_memo_fields.get("sku_id")
        or order_entry.get("sku_id")
        or order_memo_fields.get("sku_id")
        or ""
    ).strip()
    channel = str(
        status.get("channel")
        or status_memo_fields.get("channel")
        or order_entry.get("channel")
        or order_memo_fields.get("channel")
        or ""
    ).strip()
    product_type = str(
        status.get("product_type")
        or status_memo_fields.get("product_type")
        or order_entry.get("product_type")
        or order_memo_fields.get("product_type")
        or "weekly_pack"
    ).strip()

    report_id = str(
        status.get("report_id")
        or order_entry.get("report_id")
        or status_memo_fields.get("report_id")
        or order_memo_fields.get("report_id")
        or ""
    ).strip()

    lang = str(
        args.lang
        or status.get("lang")
        or order_entry.get("lang")
        or status_memo_fields.get("lang")
        or order_memo_fields.get("lang")
        or "en"
    ).strip().lower()
    if lang.startswith("zh") or lang == "cn":
        lang = "cn"
    else:
        lang = "en"

    pack = _load_pack(config, pack_id, report_id=report_id, lang=lang)
    if not pack:
        print("error: pack not found in configured/derived packs")
        return 1

    pack_id = str(pack.get("pack_id") or pack_id).strip()

    report_ids = pack.get("included_report_ids") or []
    if not isinstance(report_ids, list):
        report_ids = []
    report_ids = [str(x).strip() for x in report_ids if str(x).strip()]
    if not report_ids:
        primary = str(pack.get("primary_report_id") or report_id).strip()
        if primary:
            report_ids = [primary]

    if not report_ids:
        print("error: no reports in pack")
        return 1

    tier = str(status.get("tier") or order_entry.get("tier") or "$29")
    report_links = []
    for idx, rid in enumerate(report_ids, start=1):
        child_order = f"{order_id}-p{idx:02d}"
        unlock = _issue_unlock(config, rid, child_order, tier, lang)
        if not unlock:
            continue
        title = _load_report_title(os.path.join(config.premium_dir, "reports_index.jsonl"), rid, lang=lang)
        report_links.append({"report_id": rid, "url": unlock.get("unlock_url", ""), "title": title})

    if not report_links:
        print("error: failed to issue report unlock links")
        return 1

    html_out = _render_unlock_page(config, lang, order_id, pack_id, report_links)
    prefix = "en" if lang == "en" else "cn"
    delivery_rel = f"/{prefix}/pack/unlock/{order_id}.html"
    delivery_path = os.path.join(config.static_dir, prefix, "pack", "unlock", f"{order_id}.html")
    os.makedirs(os.path.dirname(delivery_path), exist_ok=True)
    with open(delivery_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    now = datetime.utcnow().isoformat() + "Z"
    status.update(
        {
            "status": "confirmed",
            "product_type": product_type or "weekly_pack",
            "sku_id": sku_id,
            "channel": channel,
            "pack_id": pack_id,
            "fulfillment_status": "delivered",
            "delivered_at": now,
            "delivery_url": delivery_rel,
            "unlock_url": delivery_rel,
            "delivered_report_ids": [x["report_id"] for x in report_links],
            "report_id": report_id or str(pack.get("primary_report_id") or ""),
        }
    )
    if not status.get("memo") and order_entry.get("memo"):
        status["memo"] = order_entry.get("memo")
    _write_json(status_path, status)

    orders_path = os.path.join(config.data_dir, "pay", "orders.jsonl")
    _update_order_rows(
        orders_path,
        order_id,
        {
            "status": "confirmed",
            "product_type": product_type or "weekly_pack",
            "sku_id": sku_id,
            "channel": channel,
            "pack_id": pack_id,
            "fulfillment_status": "delivered",
            "delivered_at": now,
            "delivery_url": delivery_rel,
            "unlock_url": delivery_rel,
        },
    )

    print("ok:", delivery_rel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
