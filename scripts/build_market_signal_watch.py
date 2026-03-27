#!/usr/bin/env python3
import json
import os
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import isoformat, now_cst


BASE_NOTES = ["commit1_baseline", "manual_status_only"]


def _abs_path(rel_path):
    if os.path.isabs(rel_path):
        return rel_path
    return os.path.join(ROOT, rel_path)


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _parse_dt(value):
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _safe_float(value):
    if value in (None, "", "--", "N/A", "n/a"):
        return None
    text = str(value).strip().replace(",", "")
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _load_watchlist(watchlist_path):
    data = _load_json(_abs_path(watchlist_path), [])
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if not item.get("enabled", True):
            continue
        if not item.get("signal_id"):
            continue
        if not item.get("name"):
            continue
        if not item.get("source_type"):
            continue
        if not item.get("source_ref"):
            continue
        out.append(item)
    return out


def _load_indices_market(source_ref):
    path = _abs_path(source_ref)
    data = _load_json(path, {})
    return data if isinstance(data, dict) else {}


def _extract_numeric_proxy(market):
    value_num = None
    pct_change_1d = None

    for key in ("value", "index", "close", "price", "latest"):
        value_num = _safe_float(market.get(key))
        if value_num is not None:
            break

    for key in ("pct_change", "change_pct", "daily_change", "change"):
        pct_change_1d = _safe_float(market.get(key))
        if pct_change_1d is not None:
            break

    return value_num, pct_change_1d


def _build_signal_record(
    item,
    observed_at,
    value_num,
    pct_change_1d,
    raw_summary,
    source_health,
):
    now_text = isoformat(now_cst())
    event_id = f"ms-{now_cst().strftime('%Y%m%d')}-{item['signal_id']}"
    return {
        "event_id": event_id,
        "signal_id": item.get("signal_id", ""),
        "name": item.get("name", ""),
        "category": item.get("category", ""),
        "region": item.get("region", ""),
        "observed_at": observed_at,
        "value_num": value_num,
        "pct_change_1d": pct_change_1d,
        "pct_change_5d": None,
        "volume_num": None,
        "source_type": item.get("source_type", ""),
        "source_ref": item.get("source_ref", ""),
        "source_health": source_health,
        "raw_summary": raw_summary,
        "anomaly_score": 0,
        "severity": "low",
        "signal_reason_tags": [],
        "related_intel_ids": [],
        "related_topics": [],
        "pre_event_flag": False,
        "post_event_confirmation_flag": False,
        "note": "commit1_baseline",
        "created_at": now_text,
    }


def _raw_summary_from_hint(field_hint, analysis_text, image_url, value_num):
    hint = str(field_hint or "").strip().lower()
    if hint == "analysis_text" and analysis_text:
        return analysis_text[:160].strip()
    if hint == "image_url" and image_url:
        return "image_url_present"
    if hint in {"value", "index", "close", "price", "latest"} and value_num is not None:
        return "numeric_proxy_present"
    if analysis_text:
        return analysis_text[:160].strip()
    if image_url:
        return "image_url_present"
    if value_num is not None:
        return "numeric_proxy_present"
    return "manual_status_present"


def _extract_manual_status_signal(item, market):
    if not market:
        return None, "source_empty"

    dt = _parse_dt(market.get("updated_at"))
    local_tz = now_cst().tzinfo
    observed_at = isoformat(dt.astimezone(local_tz)) if dt else isoformat(now_cst())

    analysis_text = str(market.get("analysis_text") or "").strip()
    image_url = str(market.get("image_url") or "").strip()
    value_num, pct_change_1d = _extract_numeric_proxy(market)

    raw_summary = _raw_summary_from_hint(
        item.get("field_hint"), analysis_text, image_url, value_num
    )

    record = _build_signal_record(
        item=item,
        observed_at=observed_at,
        value_num=value_num,
        pct_change_1d=pct_change_1d,
        raw_summary=raw_summary,
        source_health="ok",
    )
    return record, ""


def _write_jsonl(path, rows):
    abs_path = _abs_path(path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def _write_status(path, payload):
    abs_path = _abs_path(path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main():
    config = Config.from_env()
    data_dir = config.data_dir or "data"
    watchlist_path = os.path.join(data_dir, "market", "watchlist.json")
    signals_path = os.path.join(data_dir, "market", "market_signals.jsonl")
    status_path = os.path.join(data_dir, "status", "market_signal_status.json")

    now_dt = now_cst()
    watchlist = _load_watchlist(watchlist_path)

    rows = []
    failed_count = 0
    stale_sources = []
    notes = list(BASE_NOTES)

    if not watchlist:
        notes.append("watchlist_empty")

    for item in watchlist:
        source_type = str(item.get("source_type") or "").strip()
        source_ref = str(item.get("source_ref") or "").strip()

        if source_type != "manual_status":
            failed_count += 1
            notes.append(f"unsupported_source_type:{source_type}")
            continue

        market = _load_indices_market(source_ref)
        if not market:
            failed_count += 1
            stale_sources.append(source_ref)
            continue

        record, err = _extract_manual_status_signal(item, market)
        if record:
            rows.append(record)
        else:
            failed_count += 1
            if err:
                notes.append(f"{item.get('signal_id')}:{err}")

    _write_jsonl(signals_path, rows)

    payload = {
        "date": now_dt.strftime("%Y-%m-%d"),
        "generated_at": isoformat(now_dt),
        "watchlist_count": len(watchlist),
        "enabled_count": len(watchlist),
        "success_count": len(rows),
        "failed_count": failed_count,
        "high_severity_count": 0,
        "stale_sources": sorted(set(stale_sources)),
        "build_status": "ok" if rows else "degraded",
        "notes": notes,
    }

    _write_status(status_path, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
