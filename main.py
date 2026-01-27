import argparse
import json
import os
import re
import signal
import time
import traceback
from dataclasses import replace
from urllib.parse import urlparse

from src.config import Config
from src.sources.registry import get_sources
from src.fetcher import Fetcher
from src.extractor import GeminiExtractor
from src.storage import Storage
from src.reporter import Reporter
from src.context import ContextBuilder, VectorContextBuilder
from src.embeddings import EmbeddingStore
from src.normalizer import Normalizer
from src.utils import clean_text, isoformat, now_cst, is_homepage_url
from src.datasets import build_datasets
from src.premium import generate_daily_briefs, backfill_daily_briefs
from src.telegram import post_telegram_records, post_telegram_top3_due

CANDIDATE_CONFIDENCE_MIN = 60
CANDIDATE_LEN_MIN = 2
CANDIDATE_LEN_MAX = 80


def _context_lines(context_summary, limit=0):
    if not context_summary:
        return []
    lines = []
    for raw in context_summary.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("-"):
            line = line[1:].strip()
        if line:
            lines.append(line)
        if limit and len(lines) >= limit:
            break
    return lines


def _load_candidate_seen(path):
    seen = set()
    if not os.path.exists(path):
        return seen
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            keyword = str(data.get("keyword", "")).strip().lower()
            if keyword:
                seen.add(keyword)
    return seen


def _has_keyword_chars(text):
    return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", text))


def _guess_keyword_type(name):
    lower = name.lower()
    if any(
        token in lower
        for token in (
            "project",
            "mine",
            "deposit",
            "basin",
            "plant",
            "refinery",
            "smelter",
            "processing site",
        )
    ):
        return "project"
    if any(token in name for token in ("项目", "矿区", "矿床", "矿山")):
        return "project"
    if any(
        token in lower
        for token in (
            "ministry",
            "government",
            "agency",
            "bank",
            "fund",
            "commission",
            "authority",
            "institute",
            "university",
            "bureau",
            "administration",
            "council",
            "department",
        )
    ):
        return "institution"
    if any(
        token in name
        for token in ("研究院", "大学", "学院", "基金", "银行", "政府", "委员会")
    ):
        return "institution"
    return "company"


def _append_keyword_candidates(
    record,
    config,
    seen,
    min_confidence=CANDIDATE_CONFIDENCE_MIN,
):
    try:
        confidence = int(record.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        confidence = 0
    if confidence < min_confidence:
        return 0

    entities = record.get("entities") or []
    if isinstance(entities, str):
        entities = [
            seg.strip()
            for seg in re.split(r"[\n,;]+", entities)
            if seg.strip()
        ]
    elif not isinstance(entities, list):
        entities = []

    cleaned = []
    for item in entities:
        text = str(item).strip()
        if not text:
            continue
        if len(text) < CANDIDATE_LEN_MIN or len(text) > CANDIDATE_LEN_MAX:
            continue
        if not _has_keyword_chars(text):
            continue
        cleaned.append(text)

    if not cleaned:
        return 0

    candidates = []
    created_at = isoformat(now_cst())
    for keyword in cleaned:
        key = keyword.lower()
        if key in seen:
            continue
        candidates.append(
            {
                "keyword": keyword,
                "type": _guess_keyword_type(keyword),
                "record_id": record.get("id", ""),
                "title": record.get("title", ""),
                "confidence": confidence,
                "created_at": created_at,
            }
        )
        seen.add(key)

    if not candidates:
        return 0

    path = os.path.join(
        config.data_dir, "keywords", "keyword_candidates.jsonl"
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for entry in candidates:
            f.write(json.dumps(entry, ensure_ascii=False))
            f.write("\n")
    return len(candidates)


def _build_top3_items(entries, record_map):
    items = []
    for entry in entries[:3]:
        event_id = entry.get("event_id")
        report_id = entry.get("report_id")
        if not event_id or not report_id:
            continue
        preview_zh = entry.get("preview_zh") or {}
        preview_en = entry.get("preview_en") or {}
        value_zh = preview_zh.get("value") or ""
        report_contains_zh = preview_zh.get("preview") or ""
        value_en = preview_en.get("value") or ""
        report_contains_en = preview_en.get("preview") or ""
        title_zh = str(preview_zh.get("title") or "").strip()
        title_en = str(preview_en.get("title") or "").strip()
        record = record_map.get(event_id)
        if record:
            if not title_zh:
                title_zh = str(record.get("title", "")).strip()
            title = (
                title_zh
                or title_en
                or str(record.get("title", "")).strip()
                or str(entry.get("source_title", "")).strip()
            )
            summary_zh = record.get("summary_zh", "")
            summary_en = record.get("summary_en", "")
            expert_summary = record.get("expert_summary", "")
            expert_summary_en = record.get("expert_summary_en", "")
            country = record.get("country_primary", "")
            action_type = record.get("action_type", "")
            risk_level = record.get("risk_level", "")
            risk_score = record.get("risk_score", "")
            published_at = record.get("published_at", "")
            collected_at = record.get("collected_at", "")
        else:
            if not title_zh:
                title_zh = str(entry.get("source_title", "")).strip()
            title = (
                title_zh
                or title_en
                or str(entry.get("source_title", "")).strip()
            )
            summary_zh = (
                preview_zh.get("preview")
                or preview_zh.get("value")
                or ""
            )
            summary_en = (
                preview_en.get("preview")
                or preview_en.get("value")
                or ""
            )
            expert_summary = preview_zh.get("value") or ""
            expert_summary_en = preview_en.get("value") or ""
            country = entry.get("country", "")
            action_type = entry.get("action_type", "")
            risk_level = entry.get("risk_level", "")
            risk_score = entry.get("risk_score", "") or entry.get(
                "importance_score", ""
            )
            published_at = entry.get("published_at", "")
            collected_at = entry.get("created_at", "")
        items.append(
            {
                "report_id": report_id,
                "event_id": event_id,
                "title": title,
                "title_zh": title_zh,
                "title_en": title_en,
                "summary_zh": summary_zh,
                "summary_en": summary_en,
                "expert_summary": expert_summary,
                "expert_summary_en": expert_summary_en,
                "value_zh": value_zh,
                "report_contains_zh": report_contains_zh,
                "value_en": value_en,
                "report_contains_en": report_contains_en,
                "country": country,
                "action_type": action_type,
                "risk_level": risk_level,
                "risk_score": risk_score,
                "published_at": published_at,
                "collected_at": collected_at,
            }
        )
    return items


def _write_top3_list(config, items, date_key):
    path = os.path.join(config.data_dir, "status", "top3_list.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "date": date_key,
        "generated_at": isoformat(now_cst()),
        "items": items,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


# ... remainder of file unchanged
