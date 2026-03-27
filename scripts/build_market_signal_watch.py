#!/usr/bin/env python3
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import isoformat, now_cst, normalize_token


BASE_NOTES = [
    "commit1_baseline",
    "manual_status_only",
    "commit2_anomaly_scoring",
    "commit2_intel_link",
]
INTEL_LOOKBACK_HOURS = 72
TOP_TAG_LIMIT = 5
GENERIC_SIGNAL_TOKENS = {
    "manual",
    "market",
    "snapshot",
    "analysis",
    "proxy",
    "placeholder",
    "signal",
    "signals",
    "index",
    "indices",
    "today",
    "daily",
    "text",
    "image",
    "fallback",
    "present",
    "url",
    "numeric",
    "rare",
    "earth",
    "rareearth",
    "行情",
    "市场",
    "分析",
    "今日",
    "图片",
    "快照",
    "占位",
    "代理",
    "指数",
    "手工",
    "保底",
}


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


def _to_local_dt(dt, tzinfo):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(tzinfo)
    except Exception:
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


def _classify_severity(score):
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def _score_source_confidence(market, value_num=None, pct_change_1d=None):
    score = 0
    analysis_text = str(market.get("analysis_text") or "").strip()
    image_url = str(market.get("image_url") or "").strip()
    updated_at = str(market.get("updated_at") or "").strip()

    if analysis_text:
        score += 40
    if image_url:
        score += 20
    if updated_at:
        score += 20
    if value_num is not None or pct_change_1d is not None:
        score += 20

    return min(score, 100)


def _score_move(pct_change_1d):
    if pct_change_1d is None:
        return 0
    move = abs(float(pct_change_1d))
    if move >= 10:
        return 100
    if move >= 5:
        return 80
    if move >= 3:
        return 60
    if move >= 1:
        return 35
    if move > 0:
        return 20
    return 0


def _dedupe_list(values):
    out = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _signal_reason_tags(market, record, source_confidence_score):
    tags = []
    analysis_text = str(market.get("analysis_text") or "").strip()
    image_url = str(market.get("image_url") or "").strip()

    pct_change_1d = record.get("pct_change_1d")
    if pct_change_1d is not None and abs(float(pct_change_1d)) >= 1:
        tags.append("price_jump_1d")

    if analysis_text:
        tags.append("analysis_text_present")
    if image_url:
        tags.append("image_snapshot_present")
    if record.get("value_num") is not None:
        tags.append("proxy_numeric_present")
    if source_confidence_score < 50:
        tags.append("source_low_confidence")
    if record.get("volume_num") is None:
        tags.append("missing_volume")

    return _dedupe_list(tags)


def _score_anomaly(item, market, record):
    _ = item
    move_score = _score_move(record.get("pct_change_1d"))
    source_confidence_score = _score_source_confidence(
        market,
        value_num=record.get("value_num"),
        pct_change_1d=record.get("pct_change_1d"),
    )

    analysis_text = str(market.get("analysis_text") or "").strip()
    image_url = str(market.get("image_url") or "").strip()

    if analysis_text:
        proxy_text_score = 50
    elif image_url:
        proxy_text_score = 20
    else:
        proxy_text_score = 0

    anomaly_score = round(
        min(
            100,
            move_score * 0.6
            + source_confidence_score * 0.2
            + proxy_text_score * 0.2,
        )
    )
    severity = _classify_severity(anomaly_score)
    tags = _signal_reason_tags(market, record, source_confidence_score)
    return anomaly_score, severity, tags


def _extract_tokens_from_text(text):
    raw = str(text or "").strip().lower()
    if not raw:
        return set()

    tokens = set()
    for token in re.findall(r"[a-z]{3,}", raw):
        token = normalize_token(token)
        if token and token not in GENERIC_SIGNAL_TOKENS:
            tokens.add(token)

    for token in re.findall(r"[\u4e00-\u9fff]{2,6}", raw):
        token = normalize_token(token)
        if token and token not in GENERIC_SIGNAL_TOKENS:
            tokens.add(token)

    return tokens


def _extract_record_tokens(record):
    tokens = set()
    for key in ("name", "category", "raw_summary", "signal_id"):
        tokens.update(_extract_tokens_from_text(record.get(key)))
    return tokens


def _record_dt_from_intel(row):
    return _parse_dt(row.get("collected_at") or row.get("published_at"))


def _load_intel_records(config, now_dt, lookback_hours=INTEL_LOOKBACK_HOURS):
    path = os.path.join(config.data_dir, "intel.jsonl")
    if not os.path.exists(path):
        return []

    local_tz = now_dt.tzinfo
    threshold = now_dt - timedelta(hours=lookback_hours)
    rows = []

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

            dt = _record_dt_from_intel(row)
            if not dt:
                continue
            dt_local = _to_local_dt(dt, local_tz)
            if not dt_local or dt_local < threshold:
                continue

            title = str(row.get("title") or "").strip()
            summary_zh = str(row.get("summary_zh") or "").strip()
            summary_en = str(row.get("summary_en") or "").strip()
            content = str(row.get("content") or "").strip()[:800]
            search_text = " ".join(
                part for part in (title, summary_zh, summary_en, content) if part
            ).lower()

            rows.append(
                {
                    "id": str(row.get("id") or "").strip(),
                    "title": title,
                    "dt": dt_local,
                    "search_text": search_text,
                }
            )

    return rows


def _find_related_intel(record, intel_rows):
    tokens = _extract_record_tokens(record)
    if not tokens or not intel_rows:
        return [], [], False, False

    matches = []
    for row in intel_rows:
        score = 0
        haystack = row.get("search_text") or ""
        for token in tokens:
            if token and token in haystack:
                score += 1
        if score >= 2 or (score >= 1 and len(tokens) <= 2):
            matches.append((score, row.get("dt"), row))

    if not matches:
        return [], [], False, False

    matches.sort(key=lambda x: (x[0], x[1]), reverse=True)
    top_rows = [row for _, _, row in matches[:3]]

    related_ids = [row.get("id") for row in top_rows if row.get("id")]
    related_topics = [row.get("title") for row in top_rows if row.get("title")]

    signal_dt = _parse_dt(record.get("observed_at"))
    signal_dt = _to_local_dt(signal_dt, now_cst().tzinfo) if signal_dt else None
    intel_dts = [row.get("dt") for row in top_rows if row.get("dt")]

    pre_event_flag = False
    post_event_confirmation_flag = False

    if signal_dt and intel_dts:
        earliest = min(intel_dts)
        latest = max(intel_dts)
        if signal_dt < earliest:
            pre_event_flag = True
        elif signal_dt > latest:
            post_event_confirmation_flag = True

    return related_ids, related_topics, pre_event_flag, post_event_confirmation_flag


def _build_signal_record(
    item,
    observed_at,
    value_num,
    pct_change_1d,
    raw_summary,
    source_health,
    anomaly_score=0,
    severity="low",
    signal_reason_tags=None,
    related_intel_ids=None,
    related_topics=None,
    pre_event_flag=False,
    post_event_confirmation_flag=False,
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
        "anomaly_score": anomaly_score,
        "severity": severity,
        "signal_reason_tags": signal_reason_tags or [],
        "related_intel_ids": related_intel_ids or [],
        "related_topics": related_topics or [],
        "pre_event_flag": pre_event_flag,
        "post_event_confirmation_flag": post_event_confirmation_flag,
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


def _extract_manual_status_signal(item, market, intel_rows):
    if not market:
        return None, "source_empty"

    dt = _parse_dt(market.get("updated_at"))
    local_tz = now_cst().tzinfo
    observed_at = isoformat(_to_local_dt(dt, local_tz)) if dt else isoformat(now_cst())

    analysis_text = str(market.get("analysis_text") or "").strip()
    image_url = str(market.get("image_url") or "").strip()
    value_num, pct_change_1d = _extract_numeric_proxy(market)

    raw_summary = _raw_summary_from_hint(
        item.get("field_hint"), analysis_text, image_url, value_num
    )

    base_record = _build_signal_record(
        item=item,
        observed_at=observed_at,
        value_num=value_num,
        pct_change_1d=pct_change_1d,
        raw_summary=raw_summary,
        source_health="ok",
    )

    anomaly_score, severity, tags = _score_anomaly(item, market, base_record)
    related_intel_ids, related_topics, pre_flag, post_flag = _find_related_intel(
        base_record, intel_rows
    )

    record = _build_signal_record(
        item=item,
        observed_at=observed_at,
        value_num=value_num,
        pct_change_1d=pct_change_1d,
        raw_summary=raw_summary,
        source_health="ok",
        anomaly_score=anomaly_score,
        severity=severity,
        signal_reason_tags=tags,
        related_intel_ids=related_intel_ids,
        related_topics=related_topics,
        pre_event_flag=pre_flag,
        post_event_confirmation_flag=post_flag,
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


def _aggregate_status(now_dt, watchlist, rows, failed_count, stale_sources, notes):
    severity_counts = Counter(str(row.get("severity") or "low") for row in rows)
    tag_counts = Counter()
    related_intel_hit_count = 0

    for row in rows:
        for tag in row.get("signal_reason_tags") or []:
            tag_counts[str(tag)] += 1
        if row.get("related_intel_ids"):
            related_intel_hit_count += 1

    payload = {
        "date": now_dt.strftime("%Y-%m-%d"),
        "generated_at": isoformat(now_dt),
        "watchlist_count": len(watchlist),
        "enabled_count": len(watchlist),
        "success_count": len(rows),
        "failed_count": failed_count,
        "high_severity_count": severity_counts.get("high", 0),
        "medium_severity_count": severity_counts.get("medium", 0),
        "low_severity_count": severity_counts.get("low", 0),
        "related_intel_hit_count": related_intel_hit_count,
        "stale_sources": sorted(set(stale_sources)),
        "stale_source_count": len(set(stale_sources)),
        "signal_reason_top_tags": [
            tag for tag, _ in tag_counts.most_common(TOP_TAG_LIMIT)
        ],
        "build_status": "ok" if rows else "degraded",
        "notes": notes,
    }
    return payload


def main():
    config = Config.from_env()
    data_dir = config.data_dir or "data"
    watchlist_path = os.path.join(data_dir, "market", "watchlist.json")
    signals_path = os.path.join(data_dir, "market", "market_signals.jsonl")
    status_path = os.path.join(data_dir, "status", "market_signal_status.json")

    now_dt = now_cst()
    watchlist = _load_watchlist(watchlist_path)
    intel_rows = _load_intel_records(config, now_dt)

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

        record, err = _extract_manual_status_signal(item, market, intel_rows)
        if record:
            rows.append(record)
        else:
            failed_count += 1
            if err:
                notes.append(f"{item.get('signal_id')}:{err}")

    _write_jsonl(signals_path, rows)
    payload = _aggregate_status(
        now_dt=now_dt,
        watchlist=watchlist,
        rows=rows,
        failed_count=failed_count,
        stale_sources=stale_sources,
        notes=notes,
    )
    _write_status(status_path, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
