#!/usr/bin/env python3
"""
Build weekly pack mappings from latest premium reports.

Usage:
  PYTHONPATH=/root/rareearth-sentinel python3 scripts/build_weekly_pack.py
  PYTHONPATH=/root/rareearth-sentinel python3 scripts/build_weekly_pack.py --count 5 --langs cn,en
"""

import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import now_cst


def _read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return default
    return data


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


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
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return rows


def _pick_reports(rows, lang, count):
    title_key = "preview_en" if lang == "en" else "preview_zh"
    selected = []
    for row in rows:
        report_id = str(row.get("report_id") or "").strip()
        if not report_id:
            continue
        preview = row.get(title_key) or {}
        title = str(preview.get("title") or "").strip()
        if not title:
            continue
        selected.append(
            {
                "report_id": report_id,
                "date": str(row.get("date") or "").strip(),
                "created_at": str(row.get("created_at") or "").strip(),
            }
        )
        if len(selected) >= count:
            break
    return selected


def _period_bounds(items):
    dates = [x.get("date") for x in items if x.get("date")]
    if not dates:
        return "", ""
    return min(dates), max(dates)


def _build_pack(rows, lang, count):
    picked = _pick_reports(rows, lang, count)
    if not picked:
        return {}

    week = now_cst().strftime("%Yw%W")
    start, end = _period_bounds(picked)
    report_ids = [x["report_id"] for x in picked]

    return {
        "pack_id": f"{lang}-weekly-{week}",
        "lang": lang,
        "period_start": start,
        "period_end": end,
        "included_report_ids": report_ids,
        "primary_report_id": report_ids[0],
        "price": 29 if lang == "en" else 99,
        "currency": "USD" if lang == "en" else "CNY",
        "status": "active",
        "updated_at": now_cst().isoformat(timespec="seconds"),
    }


def build_weekly_packs(config, count=5, langs=("cn", "en")):
    reports_index_path = os.path.join(config.premium_dir, "reports_index.jsonl")
    packs_path = os.path.join(config.data_dir, "products", "packs.json")

    rows = _read_reports_index(reports_index_path)
    existing = _read_json(packs_path, {})
    existing_packs = existing.get("packs", []) if isinstance(existing, dict) else []
    if not isinstance(existing_packs, list):
        existing_packs = []

    target_langs = {str(x).strip().lower() for x in langs if str(x).strip()}
    target_langs = {"cn" if x.startswith("zh") else x for x in target_langs}

    kept = []
    for pack in existing_packs:
        if not isinstance(pack, dict):
            continue
        lang = str(pack.get("lang") or "").strip().lower()
        if lang in target_langs and str(pack.get("pack_id") or "").startswith(f"{lang}-weekly-"):
            continue
        kept.append(pack)

    built = []
    for lang in ("cn", "en"):
        if lang not in target_langs:
            continue
        pack = _build_pack(rows, lang, count)
        if pack:
            built.append(pack)

    payload = {"packs": kept + built}
    _write_json(packs_path, payload)
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=5, help="reports per pack")
    parser.add_argument("--langs", default="cn,en", help="comma-separated langs, e.g. cn,en")
    args = parser.parse_args()

    langs = [x.strip() for x in str(args.langs or "").split(",") if x.strip()]
    if not langs:
        langs = ["cn", "en"]

    config = Config.from_env()
    payload = build_weekly_packs(config, count=max(1, int(args.count or 5)), langs=langs)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
