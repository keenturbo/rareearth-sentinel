import json
import os
from collections import Counter
from datetime import datetime, timezone

from .utils import isoformat, now_cst

PIPELINE_HEALTH_PATH = os.path.join("data", "status", "pipeline_health.json")
PROMPT_QC_PATH = os.path.join("data", "status", "prompt_qc_logs.jsonl")


def _parse_dt(value):
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
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_today_record(record, today_key):
    for key in ("collected_at", "published_at"):
        dt = _parse_dt(record.get(key))
        if not dt:
            continue
        try:
            dt = dt.astimezone(now_cst().tzinfo)
        except Exception:
            pass
        return dt.strftime("%Y-%m-%d") == today_key
    return False


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_prompt_qc_recent(config, today_key, limit=10):
    path = os.path.join(config.data_dir, "status", "prompt_qc_logs.jsonl")
    counter = Counter()
    total = 0
    if not os.path.exists(path):
        return {"total": 0, "top_reasons": []}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            created_at = str(row.get("created_at") or "")
            if not created_at.startswith(today_key):
                continue
            total += 1
            reason = str(row.get("fallback_reason") or "").strip()
            if not reason:
                reason = "ok" if not row.get("fallback_used") else "fallback"
            counter[reason] += 1
    return {
        "total": total,
        "top_reasons": [
            {"reason": reason, "count": count}
            for reason, count in counter.most_common(limit)
        ],
    }


def build_pipeline_health(config, stats, records, top3_payload=None, error=""):
    today_key = now_cst().strftime("%Y-%m-%d")
    records = list(records or [])
    today_records = [r for r in records if _is_today_record(r, today_key)]
    premium_eligible = [
        r for r in today_records
        if _safe_int((r.get("credibility") or {}).get("score"), 0) >= _safe_int(getattr(config, "premium_min_credibility", 60), 60)
        and _safe_int(r.get("importance_score"), 0) >= _safe_int(getattr(config, "premium_min_importance", 3), 3)
    ]
    prompt_qc_recent = _load_prompt_qc_recent(config, today_key)
    top3_payload = top3_payload or {}
    return {
        "date": today_key,
        "generated_at": isoformat(now_cst()),
        "error": str(error or ""),
        "today_total_records": len(today_records),
        "today_saved_count": _safe_int((stats or {}).get("saved"), 0),
        "premium_eligible_count": len(premium_eligible),
        "top3_item_count": len(top3_payload.get("items") or []),
        "top3_source": str(top3_payload.get("source") or "empty"),
        "top3_fallback_reason": str(top3_payload.get("fallback_reason") or ""),
        "top3_stats": top3_payload.get("stats") or {},
        "fetch_stats": stats or {},
        "prompt_qc_recent": prompt_qc_recent,
    }


def write_pipeline_health(config, payload):
    path = os.path.join(config.data_dir, "status", "pipeline_health.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path
