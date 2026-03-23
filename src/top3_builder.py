import json
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from .utils import isoformat, now_cst

TOP3_LIST_PATH = os.path.join("data", "status", "top3_list.json")
TOP3_BUILD_STATUS_PATH = os.path.join("data", "status", "top3_build_status.json")


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
    dt = _parse_dt(record.get("collected_at") or record.get("published_at"))
    return _date_key_from_dt(dt)


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_title_key(text):
    if not text:
        return ""
    text = re.sub(r"\s+", " ", str(text)).strip().lower()
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", text)
    return text


def _company_key(record):
    if not isinstance(record, dict):
        return ""
    company = str(record.get("company") or "").strip()
    if not company:
        entities = record.get("entities") or []
        if isinstance(entities, str):
            entities = [
                seg.strip()
                for seg in re.split(r"[，,;；\n]+", entities)
                if seg.strip()
            ]
        elif not isinstance(entities, list):
            entities = []
        if entities:
            company = str(entities[0]).strip()
    return _normalize_title_key(company)


def _entry_title(entry, record):
    preview_zh = entry.get("preview_zh") or {}
    preview_en = entry.get("preview_en") or {}
    title = (
        preview_zh.get("title")
        or preview_en.get("title")
        or entry.get("source_title")
        or (record.get("title") if isinstance(record, dict) else "")
    )
    return str(title or "").strip()


def _source_key(record, entry):
    url = ""
    if isinstance(record, dict):
        url = record.get("resolved_url") or record.get("url") or ""
    domain = ""
    if url:
        domain = urlparse(url).netloc.lower().lstrip("www.")
    if not domain:
        domain = str(entry.get("official_source_id") or "").strip().lower()
    return domain


def _entry_impact_score(entry, record_map):
    for key in ("impact_score", "impact_score_zh", "impact_score_en"):
        value = entry.get(key)
        if value not in (None, ""):
            return _safe_int(value, 0)
    event_id = str(entry.get("event_id") or "").strip()
    record = record_map.get(event_id) or {}
    return _safe_int(record.get("importance_score"), 0)


def _entry_risk_score(entry, record_map):
    event_id = str(entry.get("event_id") or "").strip()
    record = record_map.get(event_id) or {}
    return _safe_int(record.get("risk_score"), 0)


def _entry_signature(entry, record_map):
    event_id = str(entry.get("event_id") or "").strip().lower()
    if event_id:
        return f"event:{event_id}"
    report_id = str(entry.get("report_id") or "").strip().lower()
    if report_id:
        report_base = re.sub(r"[-_](policy|industry|capital)$", "", report_id, flags=re.I)
        return f"report:{report_base}"
    record = record_map.get(str(entry.get("event_id") or "").strip()) or {}
    company = _company_key(record)
    if company:
        return f"company:{company}"
    title_key = _normalize_title_key(_entry_title(entry, record))
    if title_key:
        return f"title:{title_key}"
    source = _source_key(record, entry)
    if source:
        return f"source:{source}"
    return ""


def _record_signature(record):
    rid = str(record.get("id") or "").strip().lower()
    if rid:
        return f"event:{rid}"
    company = _company_key(record)
    if company:
        return f"company:{company}"
    title_key = _normalize_title_key(record.get("title") or "")
    if title_key:
        return f"title:{title_key}"
    return ""


def _select_major_event_entries(entries, record_map):
    candidates = []
    for entry in entries:
        event_id = str(entry.get("event_id") or "").strip()
        if not event_id:
            continue
        risk_score = _entry_risk_score(entry, record_map)
        impact_score = _entry_impact_score(entry, record_map)
        if risk_score >= 85 or impact_score >= 9:
            candidates.append((risk_score, impact_score, event_id))
    if not candidates:
        return []
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    major_event_id = candidates[0][2]
    view_entries = [
        entry
        for entry in entries
        if str(entry.get("event_id") or "").strip() == major_event_id
        and entry.get("view") in ("policy", "industry", "capital")
    ]
    if len(view_entries) < 3:
        return []
    order = {"policy": 0, "industry": 1, "capital": 2}
    view_entries.sort(key=lambda e: order.get(e.get("view"), 99))
    return view_entries[:3]


def _dedupe_entries(entries, record_map, limit=3):
    seen = set()
    deduped = []
    for entry in entries:
        sig = _entry_signature(entry, record_map)
        if sig and sig in seen:
            continue
        if sig:
            seen.add(sig)
        deduped.append(entry)
        if limit and len(deduped) >= limit:
            break
    return deduped


def _select_top3_entries(entries, record_map, limit=3):
    major_entries = _select_major_event_entries(entries, record_map)
    if major_entries:
        return major_entries
    return _dedupe_entries(entries, record_map, limit=limit)


def _pick_record_link(record_id, record, lang="zh"):
    subdir = "cn" if lang == "zh" else "en"
    tactical = record.get("recommendations_tactical") or record.get("recommendations_tactical_en") or []
    if tactical:
        return f"/{subdir}/task/pay/{record_id}.html"
    return f"/{subdir}/news/"


def _build_item_from_entry(entry, record_map):
    record = record_map.get(str(entry.get("event_id") or "").strip()) or {}
    preview_zh = entry.get("preview_zh") or {}
    preview_en = entry.get("preview_en") or {}
    title_zh = str(preview_zh.get("title") or record.get("title") or entry.get("source_title") or "").strip()
    title_en = str(preview_en.get("title") or record.get("title") or entry.get("source_title") or "").strip()
    title = title_zh or title_en or str(entry.get("source_title") or record.get("title") or "").strip()
    value_zh = str(preview_zh.get("value") or record.get("expert_summary") or record.get("summary_zh") or "").strip()
    report_contains_zh = str(preview_zh.get("preview") or record.get("summary_zh") or "").strip()
    value_en = str(preview_en.get("value") or record.get("expert_summary_en") or record.get("summary_en") or "").strip()
    report_contains_en = str(preview_en.get("preview") or record.get("summary_en") or "").strip()
    report_id = str(entry.get("report_id") or "").strip()
    if not report_id:
        return None
    if not title or not (value_zh or report_contains_zh or value_en or report_contains_en):
        return None
    return {
        "report_id": report_id,
        "event_id": str(entry.get("event_id") or "").strip(),
        "title": title,
        "title_zh": title_zh,
        "title_en": title_en,
        "summary_zh": str(record.get("summary_zh") or "").strip(),
        "summary_en": str(record.get("summary_en") or "").strip(),
        "expert_summary": str(record.get("expert_summary") or "").strip(),
        "expert_summary_en": str(record.get("expert_summary_en") or "").strip(),
        "value_zh": value_zh,
        "report_contains_zh": report_contains_zh,
        "value_en": value_en,
        "report_contains_en": report_contains_en,
        "country": str(record.get("country_primary") or "").strip(),
        "action_type": str(record.get("action_type") or entry.get("action_type") or "").strip(),
        "risk_level": str(record.get("risk_level") or entry.get("risk_level") or "").strip(),
        "risk_score": _entry_risk_score(entry, record_map),
        "impact_score": _entry_impact_score(entry, record_map),
        "published_at": str(record.get("published_at") or entry.get("published_at") or "").strip(),
        "collected_at": str(record.get("collected_at") or entry.get("created_at") or "").strip(),
    }


def _build_item_from_record(record):
    if not isinstance(record, dict):
        return None
    record_id = str(record.get("id") or "").strip()
    title = str(record.get("title") or "").strip()
    if not title:
        return None
    value_zh = str(record.get("expert_summary") or record.get("summary_zh") or "").strip()
    report_contains_zh = str(record.get("summary_zh") or "").strip()
    value_en = str(record.get("expert_summary_en") or record.get("summary_en") or "").strip()
    report_contains_en = str(record.get("summary_en") or "").strip()
    if not (value_zh or report_contains_zh or value_en or report_contains_en):
        return None
    return {
        "report_id": "",
        "event_id": record_id,
        "title": title,
        "title_zh": title,
        "title_en": title,
        "summary_zh": str(record.get("summary_zh") or "").strip(),
        "summary_en": str(record.get("summary_en") or "").strip(),
        "expert_summary": str(record.get("expert_summary") or "").strip(),
        "expert_summary_en": str(record.get("expert_summary_en") or "").strip(),
        "value_zh": value_zh,
        "report_contains_zh": report_contains_zh,
        "value_en": value_en,
        "report_contains_en": report_contains_en,
        "country": str(record.get("country_primary") or "").strip(),
        "action_type": str(record.get("action_type") or "").strip(),
        "risk_level": str(record.get("risk_level") or "").strip(),
        "risk_score": _safe_int(record.get("risk_score"), 0),
        "impact_score": _safe_int(record.get("importance_score"), 0),
        "published_at": str(record.get("published_at") or "").strip(),
        "collected_at": str(record.get("collected_at") or "").strip(),
        "link_zh": _pick_record_link(record_id, record, lang="zh"),
        "link_en": _pick_record_link(record_id, record, lang="en"),
    }


def _load_reports_index(config):
    path = os.path.join(config.premium_dir, "reports_index.jsonl")
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
            if isinstance(row, dict) and row.get("report_id"):
                rows.append(row)
    return rows


def _entry_date_key(entry):
    return _date_key_from_dt(_parse_dt(entry.get("created_at") or entry.get("date")))


def _entry_dt(entry):
    return _parse_dt(entry.get("created_at") or entry.get("date"))


def _append_unique_items(target, seen, candidates, limit=3):
    for item in candidates:
        if not item:
            continue
        sig = _normalize_title_key(item.get("event_id") or item.get("report_id") or item.get("title_zh") or item.get("title") or "")
        if sig and sig in seen:
            continue
        if sig:
            seen.add(sig)
        target.append(item)
        if limit and len(target) >= limit:
            break


def build_top3_payload(config, records, limit=3, recent_hours=72):
    records = list(records or [])
    record_map = {str(r.get("id") or "").strip(): r for r in records if isinstance(r, dict) and r.get("id")}
    today_key = now_cst().strftime("%Y-%m-%d")
    now_dt = now_cst()

    reports_index = _load_reports_index(config)
    today_entries = [e for e in reports_index if _entry_date_key(e) == today_key]
    recent_entries = []
    for entry in reports_index:
        dt = _entry_dt(entry)
        if not dt:
            continue
        try:
            local_dt = dt.astimezone(now_dt.tzinfo)
        except Exception:
            local_dt = dt
        if local_dt.strftime("%Y-%m-%d") == today_key:
            continue
        if local_dt >= now_dt - timedelta(hours=recent_hours):
            recent_entries.append(entry)

    items = []
    seen = set()
    source_chain = []
    fallback_reason = []

    today_selected = _select_top3_entries(today_entries, record_map, limit=limit)
    today_items = [_build_item_from_entry(entry, record_map) for entry in today_selected]
    _append_unique_items(items, seen, today_items, limit=limit)
    if today_items:
        source_chain.append("premium_today")
    else:
        fallback_reason.append("premium_today_empty")

    if len(items) < limit:
        recent_selected = _select_top3_entries(recent_entries, record_map, limit=limit)
        recent_items = [_build_item_from_entry(entry, record_map) for entry in recent_selected]
        before = len(items)
        _append_unique_items(items, seen, recent_items, limit=limit)
        if len(items) > before:
            source_chain.append("reports_index_recent")
        else:
            fallback_reason.append("reports_recent_empty")

    today_records = [r for r in records if _record_date_key(r) == today_key]
    eligible_today = [
        r for r in today_records
        if _safe_int((r.get("credibility") or {}).get("score"), 0) >= _safe_int(getattr(config, "premium_min_credibility", 60), 60)
        and _safe_int(r.get("importance_score"), 0) >= _safe_int(getattr(config, "premium_min_importance", 3), 3)
    ]
    today_records_sorted = sorted(
        today_records,
        key=lambda r: (
            _safe_int(r.get("risk_score"), 0),
            _safe_int(r.get("importance_score"), 0),
            _safe_int((r.get("credibility") or {}).get("score"), 0),
        ),
        reverse=True,
    )
    if len(items) < limit:
        intel_items = [_build_item_from_record(record) for record in today_records_sorted]
        before = len(items)
        _append_unique_items(items, seen, intel_items, limit=limit)
        if len(items) > before:
            source_chain.append("intel_today_fallback")
        else:
            fallback_reason.append("intel_today_empty")

    source = "+".join(source_chain) if source_chain else "empty"
    payload = {
        "date": today_key,
        "generated_at": isoformat(now_cst()),
        "items": items[:limit],
        "source": source,
        "fallback_reason": ",".join(fallback_reason) if fallback_reason else "",
        "stats": {
            "premium_entries_count": len(today_entries),
            "reports_recent_count": len(recent_entries),
            "intel_today_count": len(today_records),
            "eligible_today_count": len(eligible_today),
        },
    }
    return payload


def _top3_list_abspath(config):
    return os.path.join(config.data_dir, "status", "top3_list.json")


def _top3_build_status_abspath(config):
    return os.path.join(config.data_dir, "status", "top3_build_status.json")


def write_top3_list(config, payload):
    path = _top3_list_abspath(config)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {
        "date": payload.get("date") or now_cst().strftime("%Y-%m-%d"),
        "generated_at": payload.get("generated_at") or isoformat(now_cst()),
        "items": payload.get("items") or [],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def write_top3_build_status(config, payload):
    path = _top3_build_status_abspath(config)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {
        "date": payload.get("date") or now_cst().strftime("%Y-%m-%d"),
        "generated_at": payload.get("generated_at") or isoformat(now_cst()),
        "item_count": len(payload.get("items") or []),
        "source": payload.get("source") or "empty",
        "fallback_reason": payload.get("fallback_reason") or "",
        "stats": payload.get("stats") or {},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def load_top3_build_status(config):
    path = _top3_build_status_abspath(config)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
