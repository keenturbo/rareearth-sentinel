import json
import os
import random
from datetime import datetime
from email.utils import parsedate_to_datetime

from .input_guard import guard_article_text, clean_model_input
from .storage import Storage
from .utils import clean_text, now_cst


def _parse_date(record):
    for key in ("published_at", "collected_at"):
        text = record.get(key)
        if not text:
            continue
        if isinstance(text, str):
            text = text.strip()
        if not text:
            continue
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            pass
        try:
            return parsedate_to_datetime(text)
        except (TypeError, ValueError):
            continue
    return now_cst()


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _build_event_json(record, relevant):
    normalized = record.get("normalized") or {}
    minerals = normalized.get("minerals")
    if not minerals:
        minerals = record.get("minerals") or []
    entities = normalized.get("entities")
    if not entities:
        entities = record.get("entities") or []
    action_type = normalized.get("action_type") or record.get("action_type") or "Other"
    return {
        "relevant": bool(relevant),
        "minerals": _as_list(minerals),
        "country_primary": normalized.get("country_primary")
        or record.get("country_primary", ""),
        "entities": _as_list(entities),
        "action_type": action_type,
        "capex_usd": normalized.get("capex_usd"),
        "capacity_delta_tpa": normalized.get("capacity_delta_tpa"),
    }


def _build_expert_json(record):
    return {
        "china_impact": record.get("china_impact"),
        "china_risk_trend": record.get("china_risk_trend"),
        "expert_summary": record.get("expert_summary"),
        "recommendations_strategy": record.get("recommendations_strategy", []),
        "recommendations_tactical": record.get("recommendations_tactical", []),
        "supply_risk_score": record.get("supply_risk_score"),
        "policy_risk_score": record.get("policy_risk_score"),
        "price_pressure_score": record.get("price_pressure_score"),
        "risk_score": record.get("risk_score"),
        "forecast_6m": record.get("forecast_6m", {}),
        "forecast_12m": record.get("forecast_12m", {}),
        "forecast_24m": record.get("forecast_24m", {}),
        "forecast_48m": record.get("forecast_48m", {}),
    }


def _write_jsonl(path, entries):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False))
            f.write("\n")


def _split_by_time(samples, train_ratio, valid_ratio):
    if not samples:
        return [], [], []
    samples.sort(key=lambda item: item["ts"])
    total = len(samples)
    train_end = int(total * train_ratio)
    valid_end = int(total * (train_ratio + valid_ratio))
    train = [item["entry"] for item in samples[:train_end]]
    valid = [item["entry"] for item in samples[train_end:valid_end]]
    test = [item["entry"] for item in samples[valid_end:]]
    return train, valid, test


def _content_passes_guard(content, config):
    """Return cleaned content if it passes the quality guard, else None."""
    if not content or not content.strip():
        return None
    guard = guard_article_text(content, config)
    if not guard.get("pass"):
        return None
    cleaned = clean_model_input(content)
    if not cleaned or len(cleaned) < 100:
        return None
    return cleaned


def build_datasets(config):
    storage = Storage(config)
    records = list(storage.iter_records())
    if not records:
        print("No records found. Run the pipeline first.")
        return {}

    min_score = config.dataset_min_credibility
    negative_ratio = config.dataset_negative_ratio
    train_ratio = config.dataset_split_train
    valid_ratio = config.dataset_split_valid
    min_chars = config.dataset_min_chars
    rng = random.Random(config.dataset_random_seed)

    positives = []
    negatives = []
    reason_samples = []
    skipped_dirty = 0

    for record in records:
        raw_content = str(record.get("content", "")).strip()
        if not raw_content:
            continue

        # --- Quality gate: skip dirty content from datasets ---
        cleaned_content = _content_passes_guard(raw_content, config)
        if cleaned_content is None:
            skipped_dirty += 1
            continue

        if len(cleaned_content) < min_chars:
            continue

        credibility = record.get("credibility", {}) or {}
        score = credibility.get("score")
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0

        ts = _parse_date(record)
        normalized = record.get("normalized")
        if not isinstance(normalized, dict):
            normalized = {}

        item = {
            "record": record,
            "content": cleaned_content,
            "ts": ts,
        }

        if score >= min_score and normalized:
            positives.append(item)
        else:
            negatives.append(item)

    negative_target = int(len(positives) * negative_ratio)
    negatives_sample = []
    if negative_target > 0 and negatives:
        negatives_sample = rng.sample(negatives, min(negative_target, len(negatives)))

    extract_samples = []

    for item in positives:
        record = item["record"]
        entry = {
            "instruction": "请从稀土新闻中提取结构化信息",
            "input": item["content"],
            "output": json.dumps(_build_event_json(record, True), ensure_ascii=False),
        }
        extract_samples.append({"entry": entry, "ts": item["ts"]})

        expert_summary = str(record.get("expert_summary", "")).strip()
        if expert_summary:
            event_json = _build_event_json(record, True)
            context_lines = record.get("related_context") or []
            input_payload = {"event": event_json, "context": context_lines}
            entry = {
                "instruction": "基于事件与历史背景，输出对中国的影响与预警",
                "input": json.dumps(input_payload, ensure_ascii=False),
                "output": json.dumps(_build_expert_json(record), ensure_ascii=False),
            }
            reason_samples.append({"entry": entry, "ts": item["ts"]})

    for item in negatives_sample:
        record = item["record"]
        entry = {
            "instruction": "请从稀土新闻中提取结构化信息",
            "input": item["content"],
            "output": json.dumps(_build_event_json(record, False), ensure_ascii=False),
        }
        extract_samples.append({"entry": entry, "ts": item["ts"]})

    extract_train, extract_valid, extract_test = _split_by_time(
        extract_samples, train_ratio, valid_ratio
    )
    reason_train, reason_valid, reason_test = _split_by_time(
        reason_samples, train_ratio, valid_ratio
    )

    datasets_dir = config.datasets_dir
    extract_dir = os.path.join(datasets_dir, "extract")
    reason_dir = os.path.join(datasets_dir, "reason")

    _write_jsonl(os.path.join(extract_dir, "train.jsonl"), extract_train)
    _write_jsonl(os.path.join(extract_dir, "valid.jsonl"), extract_valid)
    _write_jsonl(os.path.join(extract_dir, "test.jsonl"), extract_test)

    _write_jsonl(os.path.join(reason_dir, "train.jsonl"), reason_train)
    _write_jsonl(os.path.join(reason_dir, "valid.jsonl"), reason_valid)
    _write_jsonl(os.path.join(reason_dir, "test.jsonl"), reason_test)

    stats = {
        "extract": {
            "total": len(extract_samples),
            "train": len(extract_train),
            "valid": len(extract_valid),
            "test": len(extract_test),
            "negatives": len(negatives_sample),
        },
        "reason": {
            "total": len(reason_samples),
            "train": len(reason_train),
            "valid": len(reason_valid),
            "test": len(reason_test),
        },
        "threshold": {
            "min_credibility": min_score,
            "negative_ratio": negative_ratio,
            "min_chars": min_chars,
        },
        "quality_gate": {
            "skipped_dirty": skipped_dirty,
        },
    }

    stats_path = os.path.join(datasets_dir, "stats.json")
    os.makedirs(datasets_dir, exist_ok=True)
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    print("Dataset build complete.")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return stats
