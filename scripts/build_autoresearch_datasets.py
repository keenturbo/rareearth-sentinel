#!/usr/bin/env python3
import json
import os
import sys
from typing import Any

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.autoresearch.datasets import validate_dataset_snapshot
from src.autoresearch.registry import load_dataset_definitions
from src.config import Config
from src.storage import Storage
from src.utils import isoformat, now_cst


AUTORESEARCH_STATUS_DIR = os.path.join("data", "status", "autoresearch")


def _read_json(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
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


def _read_reports_index(path: str) -> list[dict[str, Any]]:
    rows = _read_jsonl(path)
    out = []
    for row in rows:
        report_id = str(row.get("report_id") or "").strip()
        if not report_id:
            continue
        out.append(
            {
                "report_id": report_id,
                "title_zh": str((row.get("preview_zh") or {}).get("title") or row.get("source_title") or "").strip(),
                "title_en": str((row.get("preview_en") or {}).get("title") or row.get("source_title") or "").strip(),
                "url_zh": f"/cn/pay/{report_id}.html",
                "url_en": f"/en/pay/{report_id}.html",
            }
        )
    return out


def _clip_text(text: Any, limit: int = 220) -> str:
    s = str(text or "").strip()
    if not limit or len(s) <= limit:
        return s
    return s[:limit].rstrip("，。；;,. ") + "…"


def _safe_list(value: Any, limit: int = 5) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = [str(v).strip() for v in value if str(v).strip()]
    elif isinstance(value, str):
        items = [line.strip("-• ").strip() for line in value.splitlines() if line.strip()]
    else:
        items = []
    return items[:limit]


def _record_map(storage: Storage) -> dict[str, dict[str, Any]]:
    return {
        str(record.get("id") or "").strip(): record
        for record in storage.iter_records() or []
        if str(record.get("id") or "").strip()
    }


def _pick_record_context(record_map: dict[str, dict[str, Any]], top_items: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    rows = []
    for item in top_items[:limit]:
        event_id = str(item.get("event_id") or "").strip()
        record = record_map.get(event_id)
        if not record:
            continue
        rows.append(
            {
                "record_id": event_id,
                "title": str(record.get("title") or "").strip(),
                "summary_zh": _clip_text(record.get("summary_zh"), 220),
                "china_impact": str(record.get("china_impact") or "").strip(),
                "risk_score": record.get("risk_score"),
            }
        )
    return rows


def _synthetic_top_item_from_variant(row: dict[str, Any]) -> dict[str, Any]:
    source_pack = row.get("source_pack") or row.get("fact_pack") or {}
    report_id = str(row.get("report_id") or row.get("record_id") or "").strip()
    record_id = str(row.get("record_id") or report_id).strip()
    title_zh = str(source_pack.get("title") or report_id or record_id).strip()
    title_en = str(source_pack.get("title_en") or source_pack.get("title") or report_id or record_id).strip()
    value_zh = _clip_text(source_pack.get("summary_zh") or source_pack.get("expert_summary") or title_zh, 220)
    value_en = _clip_text(source_pack.get("summary_en") or source_pack.get("expert_summary_en") or title_en, 220)
    report_contains_zh = _clip_text((source_pack.get("premium") or {}).get("conclusion") or value_zh, 220)
    report_contains_en = _clip_text((source_pack.get("premium") or {}).get("conclusion_en") or value_en, 220)
    return {
        "report_id": report_id,
        "event_id": record_id,
        "title": title_zh,
        "title_zh": title_zh,
        "title_en": title_en,
        "value_zh": value_zh,
        "value_en": value_en,
        "report_contains_zh": report_contains_zh,
        "report_contains_en": report_contains_en,
        "risk_score": source_pack.get("risk_score") or 80,
        "impact_score": source_pack.get("importance_score") or 4,
    }


def _synthetic_top_item_from_record(record: dict[str, Any]) -> dict[str, Any]:
    record_id = str(record.get("id") or "").strip()
    title_zh = str(record.get("title") or record_id).strip()
    title_en = str(record.get("title_en") or record.get("title") or record_id).strip()
    value_zh = _clip_text(record.get("summary_zh") or record.get("expert_summary") or title_zh, 220)
    value_en = _clip_text(record.get("summary_en") or record.get("expert_summary_en") or title_en, 220)
    report_contains_zh = _clip_text(record.get("expert_summary") or value_zh, 220)
    report_contains_en = _clip_text(record.get("expert_summary_en") or value_en, 220)
    return {
        "report_id": record_id,
        "event_id": record_id,
        "title": title_zh,
        "title_zh": title_zh,
        "title_en": title_en,
        "value_zh": value_zh,
        "value_en": value_en,
        "report_contains_zh": report_contains_zh,
        "report_contains_en": report_contains_en,
        "risk_score": record.get("risk_score") or 80,
        "impact_score": record.get("importance_score") or 4,
    }


def _fallback_top_items(storage: Storage, limit: int = 3) -> tuple[list[dict[str, Any]], str, str]:
    variant_rows = list(storage.iter_content_variants() or [])
    variant_rows.sort(key=lambda row: str(row.get("generated_at") or ""), reverse=True)
    items: list[dict[str, Any]] = []
    date_value = ""
    for row in variant_rows:
        synthetic = _synthetic_top_item_from_variant(row)
        if not synthetic.get("event_id"):
            continue
        items.append(synthetic)
        if not date_value:
            generated_at = str(row.get("generated_at") or "")
            if len(generated_at) >= 10:
                date_value = generated_at[:10]
        if len(items) >= limit:
            break
    if items:
        return items[:limit], "variants_fallback", date_value or str(now_cst().date())

    _, recent_records = storage.read_recent(200)
    sorted_records = sorted(
        recent_records,
        key=lambda row: (str(row.get("collected_at") or ""), int(row.get("risk_score") or 0), int(row.get("importance_score") or 0)),
        reverse=True,
    )
    items = []
    for record in sorted_records:
        synthetic = _synthetic_top_item_from_record(record)
        if not synthetic.get("event_id"):
            continue
        items.append(synthetic)
        if not date_value:
            collected_at = str(record.get("collected_at") or "")
            if len(collected_at) >= 10:
                date_value = collected_at[:10]
        if len(items) >= limit:
            break
    if items:
        return items[:limit], "intel_fallback", date_value or str(now_cst().date())

    return [], "empty", str(now_cst().date())


def _get_effective_top_items(top3: dict[str, Any], storage: Storage, limit: int = 3) -> tuple[list[dict[str, Any]], str, str]:
    items = top3.get("items") if isinstance(top3, dict) else []
    if isinstance(items, list) and items:
        return items[:limit], "top3", str(top3.get("date") or now_cst().date())
    return _fallback_top_items(storage, limit=limit)


def _build_homepage_focus_gold(config: Config, storage: Storage) -> dict[str, Any]:
    top3_path = os.path.join(config.data_dir, "status", "top3_list.json")
    daily_zh_path = os.path.join(config.data_dir, "status", "daily_report_zh.json")
    daily_en_path = os.path.join(config.data_dir, "status", "daily_report_en.json")

    top3 = _read_json(top3_path)
    items, source_mode, date_value = _get_effective_top_items(top3, storage, limit=3)
    if not items:
        raise ValueError(f"homepage_top3_missing: {top3_path}")

    daily_zh = _read_json(daily_zh_path)
    daily_en = _read_json(daily_en_path)
    record_map = _record_map(storage)
    context_rows = _pick_record_context(record_map, items)

    out_items: list[dict[str, Any]] = []

    if daily_zh:
        out_items.append(
            {
                "case_id": f"homepage-focus-{date_value}-zh",
                "date": date_value,
                "lang": "zh",
                "top_items": items[:3],
                "daily_report": daily_zh,
                "record_context": context_rows,
                "expected_properties": {
                    "should_dedupe": True,
                    "should_have_evidence_anchor": True,
                    "should_avoid_stale_claim": True,
                },
                "baseline_material": {
                    "existing_topic_text": "",
                    "existing_source": source_mode,
                },
            }
        )

    if daily_en:
        out_items.append(
            {
                "case_id": f"homepage-focus-{date_value}-en",
                "date": date_value,
                "lang": "en",
                "top_items": items[:3],
                "daily_report": daily_en,
                "record_context": context_rows,
                "expected_properties": {
                    "should_dedupe": True,
                    "should_have_evidence_anchor": True,
                    "should_avoid_stale_claim": True,
                },
                "baseline_material": {
                    "existing_topic_text": "",
                    "existing_source": source_mode,
                },
            }
        )

    if not out_items:
        raise ValueError("homepage_daily_reports_missing")

    return {
        "dataset_id": "homepage_focus_gold_v1",
        "family": "homepage_topic_policy",
        "generated_at": isoformat(now_cst()),
        "source_refs": {
            "top3_list": top3_path,
            "daily_report_zh": daily_zh_path,
            "daily_report_en": daily_en_path,
            "intel": storage.jsonl_path,
            "top_items_source": source_mode,
        },
        "items": out_items,
    }


def _build_social_copy_gold(config: Config, storage: Storage) -> dict[str, Any]:
    top3_path = os.path.join(config.data_dir, "status", "top3_list.json")
    daily_zh_path = os.path.join(config.data_dir, "status", "daily_report_zh.json")
    daily_en_path = os.path.join(config.data_dir, "status", "daily_report_en.json")
    social_cache_path = os.path.join(config.data_dir, "status", "social_copy_generated.json")
    reports_index_path = os.path.join(config.premium_dir, "reports_index.jsonl")

    top3 = _read_json(top3_path)
    items, source_mode, date_value = _get_effective_top_items(top3, storage, limit=3)
    if not items:
        raise ValueError(f"social_top3_missing: {top3_path}")

    reports_index = _read_reports_index(reports_index_path)
    if not reports_index:
        raise ValueError(f"social_reports_index_missing: {reports_index_path}")

    daily_zh = _read_json(daily_zh_path)
    daily_en = _read_json(daily_en_path)
    if not daily_zh and not daily_en:
        raise ValueError("social_daily_reports_missing")

    current_bundle = _read_json(social_cache_path)

    return {
        "dataset_id": "social_copy_gold_v1",
        "family": "social_copy_policy",
        "generated_at": isoformat(now_cst()),
        "source_refs": {
            "top3_list": top3_path,
            "daily_report_zh": daily_zh_path,
            "daily_report_en": daily_en_path,
            "reports_index": reports_index_path,
            "social_cache": social_cache_path,
            "top_items_source": source_mode,
        },
        "items": [
            {
                "case_id": f"social-copy-{date_value}-001",
                "date": date_value,
                "top_items": items[:3],
                "daily_report_zh": daily_zh,
                "daily_report_en": daily_en,
                "report_index": reports_index[:20],
                "current_bundle": current_bundle if isinstance(current_bundle, dict) else {},
                "expected_properties": {
                    "should_have_clear_subject": True,
                    "should_have_fact_anchor": True,
                    "should_not_repeat_fields": True,
                    "should_support_external_and_dm_split": True,
                },
            }
        ],
    }


def _build_variants_gold(config: Config, storage: Storage) -> dict[str, Any]:
    variants_path = storage.content_variants_jsonl_path
    rows = list(storage.iter_content_variants() or [])
    if not rows:
        raise ValueError(f"variants_source_missing: {variants_path}")

    rows.sort(key=lambda row: str(row.get("generated_at") or ""), reverse=True)
    record_map = _record_map(storage)

    items: list[dict[str, Any]] = []
    for idx, row in enumerate(rows[:5]):
        record_id = str(row.get("record_id") or "").strip()
        source_pack = row.get("source_pack") or row.get("fact_pack") or {}
        record = record_map.get(record_id, {})
        title = str(source_pack.get("title") or record.get("title") or record_id or f"variant-{idx + 1}").strip()
        current_variant = {
            "xueqiu_long": row.get("variants", {}).get("xueqiu_long") if isinstance(row.get("variants"), dict) else {},
            "sentinel_pro": row.get("variants", {}).get("sentinel_pro") if isinstance(row.get("variants"), dict) else {},
            "deep_dive": row.get("variants", {}).get("deep_dive") if isinstance(row.get("variants"), dict) else {},
            "fallback_used": bool((row.get("validator") or {}).get("fallback_used")),
            "reason": str((row.get("validator") or {}).get("reason") or "").strip(),
            "failed_field": str((row.get("validator") or {}).get("failed_field") or "").strip(),
        }
        record_payload = {
            "summary_zh": _clip_text(record.get("summary_zh") or source_pack.get("summary_zh"), 220),
            "summary_en": _clip_text(record.get("summary_en"), 220),
            "expert_summary": _clip_text(record.get("expert_summary") or source_pack.get("expert_summary"), 220),
            "risk_score": record.get("risk_score") if record else source_pack.get("risk_score"),
            "china_impact": str(record.get("china_impact") or "").strip(),
            "entities": _safe_list(record.get("entities") or source_pack.get("entities"), 8),
            "related_context": _safe_list(record.get("related_context"), 6),
        }
        generated_at = str(row.get("generated_at") or "")
        items.append(
            {
                "case_id": f"variants-{generated_at[:10] or 'unknown'}-{idx + 1:03d}",
                "record_id": record_id,
                "date": generated_at[:10] if generated_at else str(now_cst().date()),
                "title": title,
                "source_url": str(source_pack.get("url") or record.get("resolved_url") or record.get("url") or "").strip(),
                "record_payload": record_payload,
                "current_variant": current_variant,
                "expected_properties": {
                    "should_be_today_hit": idx == 0,
                    "should_reduce_fallback": True,
                    "should_separate_fact_inference_unknown": True,
                    "should_soften_deep_dive": True,
                },
            }
        )

    return {
        "dataset_id": "variants_gold_v1",
        "family": "content_variants_policy",
        "generated_at": isoformat(now_cst()),
        "source_refs": {
            "intel": storage.jsonl_path,
            "content_variants": variants_path,
        },
        "items": items,
    }


def _write_snapshot(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def build_autoresearch_datasets(config: Config) -> dict[str, Any]:
    storage = Storage(config)
    dataset_defs = [item for item in load_dataset_definitions(config) if item.enabled]
    if not dataset_defs:
        raise ValueError("no_enabled_autoresearch_datasets")

    results: list[dict[str, Any]] = []
    for definition in dataset_defs:
        if definition.dataset_id == "homepage_focus_gold_v1":
            payload = _build_homepage_focus_gold(config, storage)
        elif definition.dataset_id == "social_copy_gold_v1":
            payload = _build_social_copy_gold(config, storage)
        elif definition.dataset_id == "variants_gold_v1":
            payload = _build_variants_gold(config, storage)
        else:
            raise ValueError(f"unsupported_dataset_builder: {definition.dataset_id}")

        report = validate_dataset_snapshot(definition.dataset_id, payload)
        if not report.get("ok"):
            raise ValueError(
                f"dataset_validation_failed: {definition.dataset_id}: {'; '.join(report.get('errors') or [])}"
            )

        _write_snapshot(definition.source_path, payload)
        results.append(
            {
                "dataset_id": definition.dataset_id,
                "path": definition.source_path,
                "item_count": len(payload.get("items") or []),
                "warnings": report.get("warnings") or [],
            }
        )

    return {
        "ok": True,
        "generated_at": isoformat(now_cst()),
        "dataset_count": len(results),
        "datasets": results,
    }


def main() -> int:
    config = Config.from_env()
    result = build_autoresearch_datasets(config)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
