import json
import os
from typing import Any

from .registry import get_dataset_definition


SUPPORTED_FAMILIES = {
    "homepage_topic_policy",
    "social_copy_policy",
    "content_variants_policy",
}


def _read_json_file(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        raise ValueError(f"dataset_snapshot_not_found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"dataset_snapshot_invalid_json: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"dataset_snapshot_not_dict: {path}")
    return data


def _validate_homepage_focus_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    if payload.get("dataset_id") != "homepage_focus_gold_v1":
        errors.append("homepage_dataset_id_mismatch")
    if payload.get("family") != "homepage_topic_policy":
        errors.append("homepage_family_mismatch")

    items = payload.get("items")
    if not isinstance(items, list) or not items:
        errors.append("homepage_items_missing")
        return {"ok": False, "errors": errors, "warnings": warnings}

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"homepage_item_not_dict:{idx}")
            continue
        if not str(item.get("case_id") or "").strip():
            errors.append(f"homepage_case_id_missing:{idx}")
        if str(item.get("lang") or "").strip() not in {"zh", "en"}:
            errors.append(f"homepage_lang_invalid:{idx}")
        top_items = item.get("top_items")
        if not isinstance(top_items, list) or not top_items:
            errors.append(f"homepage_top_items_missing:{idx}")
        expected = item.get("expected_properties")
        if not isinstance(expected, dict):
            errors.append(f"homepage_expected_properties_missing:{idx}")
        if not isinstance(item.get("daily_report"), dict):
            warnings.append(f"homepage_daily_report_missing:{idx}")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def _validate_social_copy_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    if payload.get("dataset_id") != "social_copy_gold_v1":
        errors.append("social_dataset_id_mismatch")
    if payload.get("family") != "social_copy_policy":
        errors.append("social_family_mismatch")

    items = payload.get("items")
    if not isinstance(items, list) or not items:
        errors.append("social_items_missing")
        return {"ok": False, "errors": errors, "warnings": warnings}

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"social_item_not_dict:{idx}")
            continue
        if not str(item.get("case_id") or "").strip():
            errors.append(f"social_case_id_missing:{idx}")
        if not isinstance(item.get("top_items"), list) or not item.get("top_items"):
            errors.append(f"social_top_items_missing:{idx}")
        if not isinstance(item.get("current_bundle"), dict):
            errors.append(f"social_current_bundle_missing:{idx}")
        if not isinstance(item.get("expected_properties"), dict):
            errors.append(f"social_expected_properties_missing:{idx}")
        if not isinstance(item.get("daily_report_zh"), dict):
            warnings.append(f"social_daily_report_zh_missing:{idx}")
        if not isinstance(item.get("daily_report_en"), dict):
            warnings.append(f"social_daily_report_en_missing:{idx}")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def _validate_variants_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    if payload.get("dataset_id") != "variants_gold_v1":
        errors.append("variants_dataset_id_mismatch")
    if payload.get("family") != "content_variants_policy":
        errors.append("variants_family_mismatch")

    items = payload.get("items")
    if not isinstance(items, list) or not items:
        errors.append("variants_items_missing")
        return {"ok": False, "errors": errors, "warnings": warnings}

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"variants_item_not_dict:{idx}")
            continue
        if not str(item.get("case_id") or "").strip():
            errors.append(f"variants_case_id_missing:{idx}")
        if not str(item.get("record_id") or "").strip():
            errors.append(f"variants_record_id_missing:{idx}")
        if not isinstance(item.get("record_payload"), dict):
            errors.append(f"variants_record_payload_missing:{idx}")
        if not isinstance(item.get("current_variant"), dict):
            errors.append(f"variants_current_variant_missing:{idx}")
        if not isinstance(item.get("expected_properties"), dict):
            errors.append(f"variants_expected_properties_missing:{idx}")
        if not str(item.get("title") or "").strip():
            warnings.append(f"variants_title_missing:{idx}")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def validate_dataset_snapshot(dataset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    target = str(dataset_id or "").strip()
    if target == "homepage_focus_gold_v1":
        return _validate_homepage_focus_snapshot(payload)
    if target == "social_copy_gold_v1":
        return _validate_social_copy_snapshot(payload)
    if target == "variants_gold_v1":
        return _validate_variants_snapshot(payload)
    return {"ok": False, "errors": [f"unsupported_dataset_id: {target}"], "warnings": []}


def load_dataset_snapshot(config, dataset_id: str) -> dict[str, Any]:
    definition = get_dataset_definition(config, dataset_id)
    if definition is None:
        raise ValueError(f"dataset_definition_not_found: {dataset_id}")
    if definition.family not in SUPPORTED_FAMILIES:
        raise ValueError(f"dataset_family_not_supported: {definition.family}")

    payload = _read_json_file(definition.source_path)
    report = validate_dataset_snapshot(definition.dataset_id, payload)
    if not report.get("ok"):
        errors = "; ".join(report.get("errors") or [])
        raise ValueError(f"dataset_snapshot_invalid: {definition.dataset_id}: {errors}")
    return payload


def load_homepage_focus_gold(config) -> dict[str, Any]:
    return load_dataset_snapshot(config, "homepage_focus_gold_v1")


def load_social_copy_gold(config) -> dict[str, Any]:
    return load_dataset_snapshot(config, "social_copy_gold_v1")


def load_variants_gold(config) -> dict[str, Any]:
    return load_dataset_snapshot(config, "variants_gold_v1")
