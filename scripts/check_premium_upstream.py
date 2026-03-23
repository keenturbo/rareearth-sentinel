import json
import os
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import isoformat, now_cst


STATUS_PATH = os.path.join("data", "status", "premium_upstream_status.json")
GENERATION_STATUS_PATH = os.path.join("data", "status", "premium_generation_status.json")
REPORTS_RECENT_HOURS = 72
SAMPLE_LIMIT = 5



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



def _date_key_from_dt(dt):
    if not dt:
        return ""
    try:
        dt = dt.astimezone(now_cst().tzinfo)
    except Exception:
        pass
    return dt.strftime("%Y-%m-%d")



def _record_date_key(record):
    return _date_key_from_dt(
        _parse_dt(record.get("collected_at") or record.get("published_at"))
    )



def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default



def _load_today_records(config, today_key):
    path = os.path.join(config.data_dir, "intel.jsonl")
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
            if not isinstance(row, dict):
                continue
            if _record_date_key(row) == today_key:
                rows.append(row)
    return rows



def _load_reports_recent(config, now_dt, recent_hours=REPORTS_RECENT_HOURS):
    path = os.path.join(config.premium_dir, "reports_index.jsonl")
    rows = []
    if not os.path.exists(path):
        return rows
    threshold = now_dt - timedelta(hours=recent_hours)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            dt = _parse_dt(row.get("created_at") or row.get("date"))
            if not dt:
                continue
            try:
                local_dt = dt.astimezone(now_dt.tzinfo)
            except Exception:
                local_dt = dt
            if local_dt >= threshold:
                rows.append(row)
    return rows



def _load_top3_source(config):
    path = os.path.join(config.data_dir, "status", "top3_build_status.json")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("source") or "").strip()



def _load_generation_status():
    if not os.path.exists(GENERATION_STATUS_PATH):
        return {}
    try:
        with open(GENERATION_STATUS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}



def build_premium_upstream_status(config):
    now_dt = now_cst()
    today_key = now_dt.strftime("%Y-%m-%d")
    today_records = _load_today_records(config, today_key)
    reports_recent = _load_reports_recent(config, now_dt)
    top3_source = _load_top3_source(config)
    generation_status = _load_generation_status()

    min_cred = _safe_int(getattr(config, "premium_min_credibility", 60), 60)
    min_imp = _safe_int(getattr(config, "premium_min_importance", 3), 3)

    eligible_records = []
    blocked_low_credibility = []
    blocked_low_importance = []
    blocked_both = []

    for row in today_records:
        cred = _safe_int((row.get("credibility") or {}).get("score"), 0)
        imp = _safe_int(row.get("importance_score"), 0)
        cred_ok = cred >= min_cred
        imp_ok = imp >= min_imp
        if cred_ok and imp_ok:
            eligible_records.append(row)
        elif (not cred_ok) and imp_ok:
            blocked_low_credibility.append(row)
        elif cred_ok and (not imp_ok):
            blocked_low_importance.append(row)
        else:
            blocked_both.append(row)

    if len(today_records) <= 3:
        status = "insufficient_today_records"
    elif today_records and not eligible_records:
        status = "threshold_blocked"
    elif eligible_records and not reports_recent:
        status = "reports_not_recent"
    elif eligible_records and reports_recent:
        status = "healthy"
    else:
        status = "unknown"

    def _sample_titles(rows):
        out = []
        for row in rows[:SAMPLE_LIMIT]:
            title = str(row.get("title") or "").strip()
            if title:
                out.append(title)
        return out

    payload = {
        "date": today_key,
        "generated_at": isoformat(now_dt),
        "today_records": len(today_records),
        "eligible_records": len(eligible_records),
        "blocked_low_credibility": len(blocked_low_credibility),
        "blocked_low_importance": len(blocked_low_importance),
        "blocked_both": len(blocked_both),
        "reports_recent_count": len(reports_recent),
        "top3_source": top3_source,
        "status": status,
        "thresholds": {
            "premium_min_credibility": min_cred,
            "premium_min_importance": min_imp,
            "reports_recent_hours": REPORTS_RECENT_HOURS,
        },
        "premium_generation": {
            "status": str(generation_status.get("status") or ""),
            "brief_generated_count": _safe_int(generation_status.get("brief_generated_count"), 0),
            "index_written_count": _safe_int(generation_status.get("index_written_count"), 0),
            "reports_index_updated": bool(generation_status.get("reports_index_updated")),
            "notes": generation_status.get("notes") or [],
        },
        "sample_titles": {
            "eligible": _sample_titles(eligible_records),
            "blocked_low_credibility": _sample_titles(blocked_low_credibility),
            "blocked_low_importance": _sample_titles(blocked_low_importance),
            "blocked_both": _sample_titles(blocked_both),
        },
    }
    return payload



def write_status(payload):
    os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)



def main():
    config = Config.from_env()
    payload = build_premium_upstream_status(config)
    write_status(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
