#!/usr/bin/env python3
import json
import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.content_variants_ai import generate_content_variants_bundle
from src.storage import Storage
from src.utils import now_cst


def _read_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
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
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_record_dt(value):
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        try:
            dt = dt.replace(tzinfo=now_cst().tzinfo)
        except Exception:
            return dt
    try:
        dt = dt.astimezone(now_cst().tzinfo)
    except Exception:
        pass
    return dt


def _record_primary_dt(record):
    for key in ("collected_at", "published_at"):
        dt = _parse_record_dt(record.get(key))
        if dt:
            return dt
    return None


def _is_today_record(record):
    dt = _record_primary_dt(record)
    if not dt:
        return False
    return dt.date() == now_cst().date()


def _is_recent_record(record, hours=48):
    dt = _record_primary_dt(record)
    if not dt:
        return False
    return dt >= (now_cst() - timedelta(hours=hours))


def _record_date_candidates(record):
    candidates = []
    today = now_cst().strftime("%Y-%m-%d")
    candidates.append(today)
    for key in ("published_at", "collected_at"):
        raw = str(record.get(key) or "").strip()
        if len(raw) >= 10:
            date_key = raw[:10]
            if date_key not in candidates:
                candidates.append(date_key)
    return candidates


def _load_premium_payload(config, record):
    if not getattr(config, "premium_enabled", False):
        return {}
    record_id = str(record.get("id") or "").strip()
    if not record_id:
        return {}

    tried = []
    for date_key in _record_date_candidates(record):
        path = os.path.join(config.premium_dir, date_key, f"brief_{record_id}_zh.json")
        tried.append(path)
        data = _read_json(path)
        if data:
            return data

    if not os.path.isdir(config.premium_dir):
        return {}

    date_dirs = []
    for name in os.listdir(config.premium_dir):
        path = os.path.join(config.premium_dir, name)
        if os.path.isdir(path) and len(name) == 10 and name[4] == "-" and name[7] == "-":
            date_dirs.append(name)
    date_dirs.sort(reverse=True)

    for date_key in date_dirs[:30]:
        path = os.path.join(config.premium_dir, date_key, f"brief_{record_id}_zh.json")
        if path in tried:
            continue
        data = _read_json(path)
        if data:
            return data
    return {}


def _variant_sort_key(record):
    dt = _record_primary_dt(record)
    dt_key = dt.isoformat() if dt else ""
    return (
        _safe_int(record.get("risk_score"), 0),
        _safe_int(record.get("importance_score"), 0),
        _safe_int(record.get("confidence"), 0),
        dt_key,
    )


def _record_is_high_value(record, config):
    content = str(record.get("content") or "").strip()
    if not content or len(content) < 300:
        return False

    risk_score = _safe_int(record.get("risk_score"), 0)
    importance = _safe_int(record.get("importance_score"), 0)
    audience_fit = str(record.get("audience_fit") or "").strip().lower()
    has_evidence = len(record.get("key_evidence") or []) >= 2

    return (
        risk_score >= getattr(config, "content_variants_min_risk", 70)
        or importance >= getattr(config, "content_variants_min_importance", 4)
        or (has_evidence and audience_fit in {"investor", "mixed"})
    )


def _extend_unique(target, candidates, seen_ids, limit):
    for record in candidates:
        record_id = str(record.get("id") or "").strip()
        if not record_id or record_id in seen_ids:
            continue
        target.append(record)
        seen_ids.add(record_id)
        if len(target) >= limit:
            break


def _eligible_variant_records(records, config):
    top_n = max(1, int(getattr(config, "content_variants_top_n", 5) or 5))
    high_value = [record for record in records if _record_is_high_value(record, config)]
    high_value.sort(key=_variant_sort_key, reverse=True)

    today_pool = [record for record in high_value if _is_today_record(record)]
    recent_pool = [
        record for record in high_value
        if (not _is_today_record(record)) and _is_recent_record(record, hours=48)
    ]
    historical_pool = [
        record for record in high_value
        if (not _is_today_record(record)) and (not _is_recent_record(record, hours=48))
    ]

    picked = []
    seen_ids = set()
    _extend_unique(picked, today_pool, seen_ids, top_n)
    if len(picked) < top_n:
        _extend_unique(picked, recent_pool, seen_ids, top_n)
    if len(picked) < top_n:
        _extend_unique(picked, historical_pool, seen_ids, top_n)
    return picked[:top_n]


def _load_existing_by_record_id(path):
    merged = {}
    for row in _read_jsonl(path):
        record_id = str(row.get("record_id") or "").strip()
        if record_id:
            merged[record_id] = row
    return merged


def _build_variant_payload(config, record):
    premium_payload = _load_premium_payload(config, record)
    bundle = generate_content_variants_bundle(
        config,
        record,
        premium_payload=premium_payload,
        timeout_seconds=getattr(config, "content_variants_timeout_seconds", 20),
    )
    generated_at = bundle.get("generated_at")
    validator = bundle.get("validator") or {}
    variants = bundle.get("variants") or {}

    return {
        "variant_id": f"var-{record.get('id')}",
        "record_id": record.get("id"),
        "report_id": record.get("id"),
        "generated_at": generated_at,
        "lang": "zh",
        "source_pack": bundle.get("fact_pack") or {},
        "variants": {
            "xueqiu_long": {
                "title": ((variants.get("xueqiu_long") or {}).get("title") or ""),
                "content": ((variants.get("xueqiu_long") or {}).get("content") or ""),
                "quality_score": 85 if validator.get("passed") else 58,
            },
            "sentinel_pro": {
                "title": ((variants.get("sentinel_pro") or {}).get("title") or ""),
                "content": ((variants.get("sentinel_pro") or {}).get("content") or ""),
                "quality_score": 88 if validator.get("passed") else 62,
            },
            "deep_dive": {
                "title": ((variants.get("deep_dive") or {}).get("title") or ""),
                "content": ((variants.get("deep_dive") or {}).get("content") or ""),
                "quality_score": 76 if validator.get("passed") else 55,
            },
        },
        "validator": validator,
        "model": bundle.get("model") or getattr(config, "gemini_model", ""),
        "generator_version": bundle.get("generator_version") or "variants-v1",
    }


def _write_jsonl(path, entries):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main():
    config = Config.from_env()
    if not getattr(config, "content_variants_enabled", True):
        print("content variants disabled")
        return 0

    storage = Storage(config)
    records = list(storage.iter_records())
    eligible = _eligible_variant_records(records, config)
    existing = _load_existing_by_record_id(storage.content_variants_jsonl_path)

    generated = []
    for record in eligible:
        payload = _build_variant_payload(config, record)
        record_id = str(payload.get("record_id") or "").strip()
        if record_id:
            existing[record_id] = payload
        generated.append(payload)

    merged = list(existing.values())
    merged.sort(key=lambda x: str(x.get("generated_at") or ""), reverse=True)
    _write_jsonl(storage.content_variants_jsonl_path, merged)
    print(f"content variants generated: {len(generated)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
