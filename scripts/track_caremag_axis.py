#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlparse

import feedparser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import clean_text, hash_url, isoformat, normalize_token, now_cst

RSS_URL_TEMPLATE = (
    "https://news.google.com/rss/search?q={query}&hl={lang}&gl={region}&ceid={edition}"
)
DEFAULT_MAX_ITEMS = 8
WATCH_REL_PATH = os.path.join("data", "trackers", "caremag_axis_watch.jsonl")
STATUS_REL_PATH = os.path.join("data", "status", "caremag_axis_tracker_status.json")
TARGETS_REL_PATH = os.path.join("data", "trackers", "caremag_axis_targets.json")
TRACKER_SCORE_MIN = 75
RECENT_DAYS_LIMIT = 540

OFFICIAL_DOMAINS = {
    "meti.go.jp",
    "jogmec.go.jp",
    "elysee.fr",
    "economie.gouv.fr",
    "tresor.economie.gouv.fr",
}
COMPANY_DOMAINS = {
    "carester.fr",
    "stellantis.com",
    "solvay.com",
    "brazilianrareearths.com",
}
REGIONAL_MEDIA_DOMAINS = {
    "larepubliquedespyrenees.fr",
}

TARGET_PATTERNS = {
    "caremag": re.compile(r"\bcaremag\b", re.I),
    "carester": re.compile(r"\bcarester\b", re.I),
    "lacq": re.compile(r"\blacq\b", re.I),
    "jogmec": re.compile(r"\bjogmec\b", re.I),
    "iwatani": re.compile(r"\biwatani\b", re.I),
    "stellantis": re.compile(r"\bstellantis\b", re.I),
    "solvay": re.compile(r"\bsolvay\b", re.I),
    "bre": re.compile(r"\bbrazilian rare earths\b|\bBRE\b", re.I),
    "critical_minerals": re.compile(r"critical minerals|minerais critiques|重要鉱物", re.I),
    "rare_earth": re.compile(r"rare earth|terres rares|稀土", re.I),
    "heavy_rare_earth": re.compile(r"heavy rare earth|heavy rare earths|重稀土|dysprosium|terbium|dysprosium oxide|terbium oxide", re.I),
    "offtake": re.compile(r"offtake|supply contract|supply agreement|term sheet|binding agreement", re.I),
    "funding": re.compile(r"financing|investment|invest|subsidy|tax credit|equity and debt", re.I),
    "roadmap": re.compile(r"roadmap|feuille de route|共同声明|協力ロードマップ|declaration", re.I),
    "milestone": re.compile(r"launch|construction|commissioning|inauguration|groundbreaking|facility|plant", re.I),
    "delay": re.compile(r"delay|retard|permit|overrun|protest|risk", re.I),
}

NEGATIVE_PATTERNS = [
    re.compile(r"nickel|cobalt|lithium", re.I),
    re.compile(r"image album", re.I),
    re.compile(r"corporate", re.I),
    re.compile(r"village|cérémonie des vœux|bon vivre", re.I),
]


def _abs_path(rel_path):
    if os.path.isabs(rel_path):
        return rel_path
    return os.path.join(ROOT, rel_path)


def _ensure_parent(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)


def _load_json(path, default):
    abs_path = _abs_path(path)
    if not os.path.exists(abs_path):
        return default
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _iter_jsonl(path):
    abs_path = _abs_path(path)
    if not os.path.exists(abs_path):
        return
    with open(abs_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def _append_jsonl(path, rows):
    abs_path = _abs_path(path)
    _ensure_parent(abs_path)
    with open(abs_path, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def _write_json(path, payload):
    abs_path = _abs_path(path)
    _ensure_parent(abs_path)
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _normalize_title(text):
    raw = str(text or "").strip()
    if not raw:
        return ""
    lowered = normalize_token(raw)
    return lowered[:240]


def _source_tier(domain):
    domain = str(domain or "").lower().lstrip("www.")
    if not domain:
        return "secondary"
    if domain in OFFICIAL_DOMAINS or domain.endswith(".gouv.fr"):
        return "official"
    if domain in COMPANY_DOMAINS:
        return "company"
    if domain in REGIONAL_MEDIA_DOMAINS:
        return "regional_media"
    if domain in {"reuters.com", "asia.nikkei.com", "nikkei.com", "kyodonews.net"}:
        return "media"
    return "secondary"


def _load_targets(path):
    data = _load_json(path, {})
    items = data.get("targets", []) if isinstance(data, dict) else []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if not item.get("enabled", True):
            continue
        if not item.get("id"):
            continue
        source_type = str(item.get("source_type") or "").strip().lower()
        if source_type not in {"rss", "site_search"}:
            continue
        out.append(item)
    return out


def _build_site_search_url(target, config):
    domain = str(target.get("domain") or "").strip()
    query_terms = str(target.get("query_terms") or "").strip()
    if not domain or not query_terms:
        return ""
    query = f"site:{domain} {query_terms}".strip()
    lang = target.get("news_lang") or config.google_news_lang
    region = target.get("news_region") or config.google_news_region
    edition = target.get("news_edition") or config.google_news_edition
    return RSS_URL_TEMPLATE.format(
        query=quote_plus(query),
        lang=lang,
        region=region,
        edition=edition,
    )


def _fetch_target_entries(target, config, limit=0):
    source_type = str(target.get("source_type") or "").strip().lower()
    try:
        max_items = int(limit or target.get("max_items") or DEFAULT_MAX_ITEMS)
    except (TypeError, ValueError):
        max_items = DEFAULT_MAX_ITEMS

    if source_type == "rss":
        url = str(target.get("url") or "").strip()
    else:
        url = _build_site_search_url(target, config)

    if not url:
        return [], "missing_url"

    feed = feedparser.parse(url)
    entries = list(feed.entries or [])[:max_items]
    return entries, ""


def _clean_summary(text):
    raw = str(text or "")
    raw = re.sub(r"(?is)<a[^>]*>(.*?)</a>", r"\1", raw)
    raw = re.sub(r"(?is)<[^>]+>", " ", raw)
    raw = raw.replace("&nbsp;", " ")
    summary = clean_text(raw)
    if len(summary) > 400:
        summary = summary[:400].rstrip() + "…"
    return summary


def _parse_published_at(text):
    value = str(text or "").strip()
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(value)
    except Exception:
        try:
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            dt = datetime.fromisoformat(value)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _days_old(published_at):
    dt = _parse_published_at(published_at)
    if not dt:
        return None
    now = now_cst()
    try:
        dt = dt.astimezone(now.tzinfo)
    except Exception:
        pass
    return max(0, (now - dt).days)


def _text_blob(row):
    parts = [
        str(row.get("title") or ""),
        str(row.get("summary") or ""),
        str(row.get("source_name") or ""),
        str(row.get("source_domain") or ""),
    ]
    return " \n ".join(parts)


def _count_hits(pattern_keys, text):
    count = 0
    matched = []
    for key in pattern_keys:
        pattern = TARGET_PATTERNS[key]
        if pattern.search(text):
            count += 1
            matched.append(key)
    return count, matched


def _classify_signal_type(text):
    if TARGET_PATTERNS["roadmap"].search(text):
        return "policy_release"
    if TARGET_PATTERNS["funding"].search(text):
        return "funding_secured"
    if TARGET_PATTERNS["offtake"].search(text):
        return "supply_contract"
    if TARGET_PATTERNS["delay"].search(text):
        return "delay_or_risk"
    if TARGET_PATTERNS["milestone"].search(text):
        return "project_milestone"
    if TARGET_PATTERNS["heavy_rare_earth"].search(text) or TARGET_PATTERNS["rare_earth"].search(text):
        return "media_analysis"
    return "media_analysis"


def _classify_impact_node(text):
    if TARGET_PATTERNS["roadmap"].search(text) or TARGET_PATTERNS["critical_minerals"].search(text):
        return "policy_framework"
    if TARGET_PATTERNS["offtake"].search(text) or TARGET_PATTERNS["stellantis"].search(text) or TARGET_PATTERNS["iwatani"].search(text):
        return "downstream_offtake"
    if TARGET_PATTERNS["funding"].search(text) or TARGET_PATTERNS["jogmec"].search(text):
        return "midstream_refining"
    if TARGET_PATTERNS["bre"].search(text) or re.search(r"feedstock|concentrate|recycling magnets|raw ores", text, re.I):
        return "upstream_feedstock"
    if TARGET_PATTERNS["caremag"].search(text) or TARGET_PATTERNS["carester"].search(text) or TARGET_PATTERNS["lacq"].search(text):
        return "midstream_refining"
    return "policy_framework"


def _score_row(row):
    text = _text_blob(row)
    source_tier = row.get("source_tier") or "secondary"

    tier_score = {
        "official": 30,
        "company": 22,
        "regional_media": 16,
        "media": 12,
        "secondary": 6,
    }.get(source_tier, 6)

    target_hits, matched = _count_hits(
        [
            "caremag",
            "carester",
            "lacq",
            "jogmec",
            "iwatani",
            "stellantis",
            "solvay",
            "bre",
            "critical_minerals",
            "rare_earth",
            "heavy_rare_earth",
        ],
        text,
    )
    keyword_score = min(20, target_hits * 4)

    signal_hits, signal_matched = _count_hits(
        ["roadmap", "funding", "offtake", "milestone", "delay"],
        text,
    )
    signal_score = min(10, signal_hits * 3)

    recency_days = _days_old(row.get("published_at"))
    if recency_days is None:
        recency_score = 2
    elif recency_days <= 30:
        recency_score = 10
    elif recency_days <= 120:
        recency_score = 7
    elif recency_days <= 365:
        recency_score = 4
    elif recency_days <= RECENT_DAYS_LIMIT:
        recency_score = 1
    else:
        recency_score = -10

    quality_score = 0
    url = str(row.get("url") or "")
    if url.lower().endswith(".pdf"):
        quality_score += 5
    if source_tier in {"official", "company"}:
        quality_score += 5

    penalty = 0
    negative_reason = []
    for pattern in NEGATIVE_PATTERNS:
        if pattern.search(text):
            penalty -= 20
            negative_reason.append(pattern.pattern)

    if target_hits == 0:
        penalty -= 25
        negative_reason.append("no_target_hit")
    if not (TARGET_PATTERNS["caremag"].search(text) or TARGET_PATTERNS["carester"].search(text) or TARGET_PATTERNS["lacq"].search(text) or (TARGET_PATTERNS["jogmec"].search(text) and TARGET_PATTERNS["france"].search(text) if "france" in TARGET_PATTERNS else False)):
        if not (TARGET_PATTERNS["critical_minerals"].search(text) and (TARGET_PATTERNS["japan"].search(text) if "japan" in TARGET_PATTERNS else False)):
            pass

    score = max(0, min(100, tier_score + keyword_score + signal_score + recency_score + quality_score + penalty))
    return score, matched + signal_matched, negative_reason


def _entity_tags_for_row(row, matched_keys):
    target_tags = list(row.get("entity_tags") or [])
    mapped = {
        "caremag": "Caremag",
        "carester": "Carester",
        "lacq": "Lacq",
        "jogmec": "JOGMEC",
        "iwatani": "Iwatani",
        "stellantis": "Stellantis",
        "solvay": "Solvay",
        "bre": "BRE",
        "critical_minerals": "critical minerals",
        "rare_earth": "rare earth",
        "heavy_rare_earth": "heavy rare earth",
        "roadmap": "roadmap",
        "funding": "funding",
        "offtake": "offtake",
    }
    for key in matched_keys:
        value = mapped.get(key)
        if value and value not in target_tags:
            target_tags.append(value)
    return target_tags[:12]


def _is_relevant_candidate(row):
    text = _text_blob(row)
    if any(pattern.search(text) for pattern in NEGATIVE_PATTERNS):
        return False
    target_anchor = any(
        TARGET_PATTERNS[key].search(text)
        for key in ("caremag", "carester", "lacq")
    )
    policy_anchor = TARGET_PATTERNS["critical_minerals"].search(text) and (
        TARGET_PATTERNS["jogmec"].search(text) or re.search(r"japan|japon|日仏|france|français|フランス", text, re.I)
    )
    return bool(target_anchor or policy_anchor)


def _build_candidate(target, entry):
    title = str(entry.get("title") or "").strip()
    url = str(entry.get("link") or "").strip()
    if not title or not url:
        return None

    source_domain = str(target.get("domain") or "").strip().lower()
    if not source_domain:
        try:
            source_domain = urlparse(url).netloc.lower().lstrip("www.")
        except Exception:
            source_domain = ""

    now_text = isoformat(now_cst())
    normalized_title = _normalize_title(title)
    summary = _clean_summary(entry.get("summary") or entry.get("description") or "")
    entity_tags = target.get("entity_tags") or []
    if not isinstance(entity_tags, list):
        entity_tags = []

    row = {
        "id": f"caremag-axis-{now_cst().strftime('%Y%m%d')}-{hash_url(url)}",
        "collected_at": now_text,
        "target_id": str(target.get("id") or "").strip(),
        "channel": str(target.get("channel") or "general").strip(),
        "source_tier": _source_tier(source_domain),
        "source_name": str(target.get("source_name") or target.get("label") or source_domain),
        "source_domain": source_domain,
        "title": title,
        "url": url,
        "published_at": str(entry.get("published") or entry.get("updated") or "").strip(),
        "summary": summary,
        "normalized_title": normalized_title,
        "entity_tags": entity_tags,
        "signal_type": "",
        "impact_node": "",
        "tracker_score": 0,
        "promote_candidate": False,
        "promoted_to_intel": False,
    }

    text = _text_blob(row)
    row["signal_type"] = _classify_signal_type(text)
    row["impact_node"] = _classify_impact_node(text)
    score, matched_keys, negative_reason = _score_row(row)
    row["tracker_score"] = score
    row["promote_candidate"] = bool(score >= TRACKER_SCORE_MIN and _is_relevant_candidate(row))
    row["entity_tags"] = _entity_tags_for_row(row, matched_keys)
    if negative_reason:
        row["tracker_notes"] = negative_reason[:5]
    return row


def _dedupe_keys_for_row(row):
    url = str(row.get("url") or "").strip()
    source_domain = str(row.get("source_domain") or "").strip().lower()
    normalized_title = str(row.get("normalized_title") or "").strip()
    keys = set()
    if url:
        keys.add(f"url::{url}")
    if source_domain and normalized_title:
        keys.add(f"title::{source_domain}::{normalized_title}")
    return keys


def _load_existing_dedupe_keys(path):
    keys = set()
    for row in _iter_jsonl(path):
        keys.update(_dedupe_keys_for_row(row))
    return keys


def _build_status(now_text, targets, fetched, added, deduped, source_breakdown, errors, dry_run, high_score):
    return {
        "generated_at": now_text,
        "targets_enabled": len(targets),
        "items_fetched": fetched,
        "items_new": added,
        "items_deduped": deduped,
        "high_score_candidates": high_score,
        "promoted_count": 0,
        "source_breakdown": dict(source_breakdown),
        "dry_run": bool(dry_run),
        "errors": errors,
    }


def run_tracker(config, dry_run=False, limit=0):
    targets = _load_targets(TARGETS_REL_PATH)
    existing_keys = _load_existing_dedupe_keys(WATCH_REL_PATH)

    fetched_total = 0
    deduped_total = 0
    new_rows = []
    errors = []
    source_breakdown = Counter()
    high_score = 0

    for target in targets:
        target_id = str(target.get("id") or "").strip()
        try:
            entries, err = _fetch_target_entries(target, config, limit=limit)
        except Exception as exc:
            entries, err = [], f"{target_id}:fetch_exception:{type(exc).__name__}"
        if err:
            errors.append(err)
        fetched_total += len(entries)

        for entry in entries:
            row = _build_candidate(target, entry)
            if not row:
                continue
            if not _is_relevant_candidate(row):
                deduped_total += 1
                continue
            if _days_old(row.get("published_at")) not in (None,) and _days_old(row.get("published_at")) > RECENT_DAYS_LIMIT:
                deduped_total += 1
                continue
            row_keys = _dedupe_keys_for_row(row)
            if any(key in existing_keys for key in row_keys):
                deduped_total += 1
                continue
            existing_keys.update(row_keys)
            new_rows.append(row)
            source_breakdown[row.get("source_tier") or "secondary"] += 1
            if row.get("promote_candidate"):
                high_score += 1

    now_text = isoformat(now_cst())
    status = _build_status(
        now_text=now_text,
        targets=targets,
        fetched=fetched_total,
        added=len(new_rows),
        deduped=deduped_total,
        source_breakdown=source_breakdown,
        errors=errors,
        dry_run=dry_run,
        high_score=high_score,
    )

    if not dry_run and new_rows:
        _append_jsonl(WATCH_REL_PATH, new_rows)
    if not dry_run:
        _write_json(STATUS_REL_PATH, status)

    return status


def main():
    parser = argparse.ArgumentParser(description="Track France-Japan Caremag axis")
    parser.add_argument("--run", action="store_true", help="Run tracker (default action)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print status without writing files")
    parser.add_argument("--limit", type=int, default=0, help="Optional max items per target for this run")
    args = parser.parse_args()

    config = Config.from_env()
    status = run_tracker(config, dry_run=args.dry_run, limit=args.limit)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
