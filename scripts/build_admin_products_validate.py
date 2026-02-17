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


def _read_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _read_reports_index(path):
    reports = {}
    if not os.path.exists(path):
        return reports
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            rid = str(row.get("report_id") or "").strip()
            if rid:
                reports[rid] = row
    return reports


def _mk(level, title, detail, suggestion=""):
    return {
        "level": level,
        "title": title,
        "detail": detail,
        "suggestion": suggestion,
    }


def _is_iso_date(v):
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", str(v or "")))


def _validate(config):
    rows = []

    reports_path = os.path.join(config.premium_dir, "reports_index.jsonl")
    reports = _read_reports_index(reports_path)
    if reports:
        rows.append(_mk("pass", "reports_index 可用", f"读取到 {len(reports)} 条 report_id。"))
    else:
        rows.append(_mk("fail", "reports_index 缺失或为空", reports_path, "先确认 premium 报告索引是否已生成。"))

    products_dir = os.path.join(config.data_dir, "products")
    catalog_path = os.path.join(products_dir, "catalog.json")
    packs_path = os.path.join(products_dir, "packs.json")

    catalog = _read_json(catalog_path)
    if not isinstance(catalog, dict):
        rows.append(_mk("fail", "catalog.json 不可读", catalog_path, "检查 JSON 格式。"))
        skus = []
    else:
        skus = catalog.get("skus") if isinstance(catalog.get("skus"), list) else []
        if skus:
            rows.append(_mk("pass", "catalog.json 可用", f"SKU 数量：{len(skus)}"))
        else:
            rows.append(_mk("fail", "catalog.json 缺少 skus", catalog_path, "至少配置 single/weekly/task SKU。"))

    if skus:
        active_weekly_cn = any(
            s.get("sku_type") == "weekly_pack" and s.get("lang") == "cn" and bool(s.get("active", True))
            for s in skus if isinstance(s, dict)
        )
        active_weekly_en = any(
            s.get("sku_type") == "weekly_pack" and s.get("lang") == "en" and bool(s.get("active", True))
            for s in skus if isinstance(s, dict)
        )
        if active_weekly_cn and active_weekly_en:
            rows.append(_mk("pass", "weekly_pack SKU 完整", "中英文 weekly_pack SKU 均已激活。"))
        else:
            miss = []
            if not active_weekly_cn:
                miss.append("cn")
            if not active_weekly_en:
                miss.append("en")
            rows.append(_mk("warn", "weekly_pack SKU 不完整", f"缺失语言：{', '.join(miss)}", "检查 data/products/catalog.json。"))

    packs_obj = _read_json(packs_path)
    packs = []
    if isinstance(packs_obj, dict) and isinstance(packs_obj.get("packs"), list):
        packs = packs_obj.get("packs")
    elif isinstance(packs_obj, list):
        packs = packs_obj

    if packs_obj is None:
        rows.append(_mk("fail", "packs.json 不存在或不可读", packs_path, "运行 main.py --build-products 自动生成周包。"))
        packs = []
    else:
        rows.append(_mk("pass", "packs.json 可读", f"pack 总数：{len(packs)}"))

    active_packs = [p for p in packs if isinstance(p, dict) and str(p.get("status") or "active") == "active"]
    if not active_packs:
        rows.append(_mk("warn", "无 active 周包", "当前没有 active pack。", "运行 main.py --build-products 重新轮换周包。"))

    for p in active_packs:
        pack_id = str(p.get("pack_id") or "").strip()
        lang = str(p.get("lang") or "").strip()
        start = p.get("period_start")
        end = p.get("period_end")
        included = p.get("included_report_ids") if isinstance(p.get("included_report_ids"), list) else []
        primary = str(p.get("primary_report_id") or "").strip()

        title = f"Pack 校验：{pack_id or '(missing pack_id)'}"

        missing = []
        if not pack_id:
            missing.append("pack_id")
        if not lang:
            missing.append("lang")
        if not _is_iso_date(start):
            missing.append("period_start")
        if not _is_iso_date(end):
            missing.append("period_end")
        if missing:
            rows.append(_mk("fail", title, f"缺失/格式错误字段：{', '.join(missing)}", "修复 packs.json 对应字段。"))
            continue

        if lang not in ("cn", "en"):
            rows.append(_mk("warn", title, f"lang={lang} 非预期值（建议 cn/en）", "修正 lang 字段。"))

        if len(included) != 5:
            rows.append(_mk("fail", title, f"included_report_ids 数量为 {len(included)}（期望 5）", "调整为 5 篇固定交付。"))
            continue

        unknown = [rid for rid in included if rid not in reports]
        if unknown:
            rows.append(_mk("fail", title, f"存在无效 report_id：{', '.join(unknown[:3])}" + (" ..." if len(unknown) > 3 else ""), "先重建 premium/index 或更新 pack 映射。"))
            continue

        if len(set(included)) != len(included):
            rows.append(_mk("warn", title, "included_report_ids 存在重复项。", "去重后再发布。"))

        if primary and primary not in included:
            rows.append(_mk("warn", title, "primary_report_id 不在 included_report_ids 中。", "建议主报告来自交付清单。"))

        rows.append(_mk("pass", title, f"通过：{lang} / {start}~{end} / 5篇均有效。"))

    summary = {"pass": 0, "warn": 0, "fail": 0}
    for r in rows:
        lv = r.get("level")
        if lv in summary:
            summary[lv] += 1

    return {
        "rows": rows,
        "summary": summary,
        "reports_count": len(reports),
        "packs_count": len(packs),
        "active_packs_count": len(active_packs),
        "catalog_path": catalog_path,
        "packs_path": packs_path,
        "reports_path": reports_path,
    }


def _render_summary(meta):
    s = meta.get("summary") or {}
    return (
        f"<span class='pill pass'>通过 {int(s.get('pass', 0))}</span>"
        f"<span class='pill warn'>警告 {int(s.get('warn', 0))}</span>"
        f"<span class='pill fail'>失败 {int(s.get('fail', 0))}</span>"
        f"<span class='pill'>reports {int(meta.get('reports_count', 0))}</span>"
        f"<span class='pill'>packs {int(meta.get('packs_count', 0))}</span>"
        f"<span class='pill'>active {int(meta.get('active_packs_count', 0))}</span>"
    )


def _render_rows(meta):
    rows = meta.get("rows") or []
    if not rows:
        return "<div class='empty'>暂无校验项</div>"

    out = []
    for r in rows:
        lv = html.escape(str(r.get("level") or "warn"))
        title = html.escape(str(r.get("title") or ""))
        detail = html.escape(str(r.get("detail") or ""))
        suggestion = html.escape(str(r.get("suggestion") or ""))

        suggest_html = f"<div class='suggest'>建议：{suggestion}</div>" if suggestion else ""
        out.append(
            "<div class='item {lv}'>"
            f"<div class='item-title'>{title}</div>"
            f"<div class='item-detail'>{detail}</div>"
            f"{suggest_html}"
            "</div>".format(lv=lv)
        )
    return "".join(out)


def _build_page(config, meta):
    template_path = os.path.join(config.templates_dir, "admin_products_validate.html")
    out_path = os.path.join(config.static_dir, "cn", "admin", "products", "validate", "index.html")
    if not os.path.exists(template_path):
        raise FileNotFoundError(template_path)

    with open(template_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    html_out = (
        tpl.replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{VALIDATION_SUMMARY}}", _render_summary(meta))
        .replace("{{VALIDATION_LIST}}", _render_rows(meta))
        .replace("{{PATH_CATALOG}}", html.escape(meta.get("catalog_path") or ""))
        .replace("{{PATH_PACKS}}", html.escape(meta.get("packs_path") or ""))
        .replace("{{PATH_REPORTS}}", html.escape(meta.get("reports_path") or ""))
    )

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    return out_path


def main():
    config = Config.from_env()
    meta = _validate(config)
    out = _build_page(config, meta)
    print(f"Admin products validation page built: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
