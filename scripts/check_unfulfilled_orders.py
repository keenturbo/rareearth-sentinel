#!/usr/bin/env python3
import glob
import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import isoformat, now_cst

PAID_STATUSES = {
    "confirmed",
    "captured",
    "paypal_captured",
    "completed",
    "paid",
}

DELIVERED_STATUSES = {"delivered", "completed", "complete"}
PACK_PART_RE = re.compile(r"-p\d{2}$", re.IGNORECASE)


def _read_jsonl(path: str):
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


def _load_json(path: str):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_dt(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return dt.astimezone(ZoneInfo("Asia/Shanghai"))
    except Exception:
        return None


def _is_paid(row: dict) -> bool:
    status = str(row.get("status") or "").strip().lower()
    if status in PAID_STATUSES:
        return True
    if row.get("paypal_capture_id") or row.get("capture_id"):
        return True
    return False


def _is_delivered(row: dict) -> bool:
    fulfillment = str(row.get("fulfillment_status") or "").strip().lower()
    if fulfillment in DELIVERED_STATUSES:
        return True
    if row.get("delivery_url"):
        return True
    return False


def _pick_time(row: dict):
    for k in ("updated_at", "confirmed_at", "paid_at", "created_at"):
        dt = _parse_dt(row.get(k))
        if dt:
            return dt
    return None


def _is_test_order(order_id: str, row: dict) -> bool:
    oid = str(order_id or "").strip().upper()
    if oid.startswith("PP-TEST"):
        return True
    channel = str(row.get("channel") or row.get("source_channel") or "").strip().lower()
    if channel in {"admin_test", "test", "qa_test"}:
        return True
    return False


def _is_pack_part_order(order_id: str) -> bool:
    return bool(PACK_PART_RE.search(str(order_id or "").strip()))


def build_unfulfilled(config: Config, threshold_minutes: int = 30):
    orders_path = os.path.join(config.data_dir, "pay", "orders.jsonl")
    status_dir = os.path.join(config.static_dir, "pay", "status")
    out_path = os.path.join(config.data_dir, "status", "unfulfilled_orders.json")

    by_order = {}

    for row in _read_jsonl(orders_path):
        oid = str(row.get("order_id") or "").strip()
        if not oid:
            continue
        by_order[oid] = row

    if os.path.isdir(status_dir):
        for fp in sorted(glob.glob(os.path.join(status_dir, "*.json"))):
            data = _load_json(fp)
            oid = str(data.get("order_id") or os.path.splitext(os.path.basename(fp))[0]).strip()
            if not oid:
                continue
            merged = dict(by_order.get(oid, {}))
            merged.update(data)
            by_order[oid] = merged

    now = now_cst()
    items = []
    actionable_items = []
    noise = {
        "missing_created_at": 0,
        "test_order": 0,
        "pack_part_order": 0,
    }

    for oid, row in by_order.items():
        if not _is_paid(row):
            continue
        if _is_delivered(row):
            continue

        ts = _pick_time(row)
        if ts is None:
            age_minutes = -1
        else:
            age_minutes = int(max(0, (now - ts).total_seconds() // 60))

        if age_minutes >= 0 and age_minutes < threshold_minutes:
            continue

        noise_reasons = []
        if age_minutes < 0:
            noise_reasons.append("missing_created_at")
            noise["missing_created_at"] += 1
        if _is_test_order(oid, row):
            noise_reasons.append("test_order")
            noise["test_order"] += 1
        if _is_pack_part_order(oid):
            noise_reasons.append("pack_part_order")
            noise["pack_part_order"] += 1

        item = {
            "order_id": oid,
            "status": str(row.get("status") or ""),
            "product_type": str(row.get("product_type") or ""),
            "sku_id": str(row.get("sku_id") or ""),
            "channel": str(row.get("channel") or row.get("source_channel") or ""),
            "fulfillment_status": str(row.get("fulfillment_status") or "pending"),
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
            "age_minutes": age_minutes,
            "noise_reasons": noise_reasons,
        }
        items.append(item)
        if not noise_reasons:
            actionable_items.append(item)

    items.sort(key=lambda x: int(x.get("age_minutes") or -1), reverse=True)
    actionable_items.sort(key=lambda x: int(x.get("age_minutes") or -1), reverse=True)

    payload = {
        "generated_at": isoformat(now),
        "threshold_minutes": int(threshold_minutes),
        "count": len(items),
        "actionable_count": len(actionable_items),
        "noise_breakdown": {
            **noise,
            "non_actionable_total": len(items) - len(actionable_items),
        },
        "items": items,
        "actionable_items": actionable_items,
    }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return out_path, payload


if __name__ == "__main__":
    cfg = Config.from_env()
    threshold = int(os.getenv("UNFULFILLED_THRESHOLD_MINUTES", "30") or "30")
    out, payload = build_unfulfilled(cfg, threshold)
    print(
        "unfulfilled orders written: "
        f"{out} (count={payload.get('count')}, actionable={payload.get('actionable_count')})"
    )
