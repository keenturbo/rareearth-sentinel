import os
from typing import Any

import yaml

from .models import DatasetDefinition, FamilyDefinition


DEFAULT_FAMILIES_PATH = os.path.join("config", "autoresearch", "families.yml")
DEFAULT_DATASETS_PATH = os.path.join("config", "autoresearch", "datasets.yml")
ALLOWED_SOURCE_KINDS = {"snapshot_json"}


def _read_yaml_file(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        raise ValueError(f"registry_file_not_found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"registry_yaml_invalid: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"registry_root_not_dict: {path}")
    return data


def _resolve_families_path(_config=None) -> str:
    return DEFAULT_FAMILIES_PATH


def _resolve_datasets_path(_config=None) -> str:
    return DEFAULT_DATASETS_PATH


def load_family_definitions(config=None) -> list[FamilyDefinition]:
    path = _resolve_families_path(config)
    data = _read_yaml_file(path)
    rows = data.get("families")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ValueError(f"families_not_list: {path}")

    out: list[FamilyDefinition] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"family_row_not_dict: {path}#{idx}")
        out.append(
            FamilyDefinition(
                family=str(row.get("family") or "").strip(),
                target_chain=str(row.get("target_chain") or "").strip(),
                enabled=bool(row.get("enabled", True)),
                reviewer=str(row.get("reviewer") or "").strip(),
                runtime_tier=str(row.get("runtime_tier") or "").strip(),
                description=str(row.get("description") or "").strip(),
                baseline_name=str(row.get("baseline_name") or "").strip(),
                allowed_config_keys=[
                    str(item).strip()
                    for item in (row.get("allowed_config_keys") or [])
                    if str(item).strip()
                ],
                metrics_profile=str(row.get("metrics_profile") or "").strip(),
            )
        )
    return out


def load_dataset_definitions(config=None) -> list[DatasetDefinition]:
    path = _resolve_datasets_path(config)
    data = _read_yaml_file(path)
    rows = data.get("datasets")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ValueError(f"datasets_not_list: {path}")

    out: list[DatasetDefinition] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"dataset_row_not_dict: {path}#{idx}")
        out.append(
            DatasetDefinition(
                dataset_id=str(row.get("dataset_id") or "").strip(),
                family=str(row.get("family") or "").strip(),
                enabled=bool(row.get("enabled", True)),
                source_kind=str(row.get("source_kind") or "").strip(),
                source_path=str(row.get("source_path") or "").strip(),
                description=str(row.get("description") or "").strip(),
                owner=str(row.get("owner") or "").strip(),
            )
        )
    return out


def get_family_definition(config, family: str) -> FamilyDefinition | None:
    target = str(family or "").strip()
    if not target:
        return None
    for item in load_family_definitions(config):
        if item.family == target:
            return item
    return None


def get_dataset_definition(config, dataset_id: str) -> DatasetDefinition | None:
    target = str(dataset_id or "").strip()
    if not target:
        return None
    for item in load_dataset_definitions(config):
        if item.dataset_id == target:
            return item
    return None


def validate_registry_consistency(config=None) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    families = load_family_definitions(config)
    datasets = load_dataset_definitions(config)

    family_seen: set[str] = set()
    family_enabled: dict[str, bool] = {}
    for item in families:
        if not item.family:
            errors.append("family_missing_name")
            continue
        if item.family in family_seen:
            errors.append(f"duplicate_family: {item.family}")
        family_seen.add(item.family)
        family_enabled[item.family] = item.enabled

        if not item.target_chain:
            errors.append(f"family_missing_target_chain: {item.family}")
        if not item.baseline_name:
            errors.append(f"family_missing_baseline_name: {item.family}")
        if not item.allowed_config_keys:
            errors.append(f"family_missing_allowed_config_keys: {item.family}")
        if not item.reviewer:
            warnings.append(f"family_missing_reviewer: {item.family}")
        if not item.metrics_profile:
            warnings.append(f"family_missing_metrics_profile: {item.family}")
        if not item.runtime_tier:
            warnings.append(f"family_missing_runtime_tier: {item.family}")

    dataset_seen: set[str] = set()
    for item in datasets:
        if not item.dataset_id:
            errors.append("dataset_missing_id")
            continue
        if item.dataset_id in dataset_seen:
            errors.append(f"duplicate_dataset_id: {item.dataset_id}")
        dataset_seen.add(item.dataset_id)

        if not item.family:
            errors.append(f"dataset_missing_family: {item.dataset_id}")
        elif item.family not in family_seen:
            errors.append(f"dataset_unknown_family: {item.dataset_id} -> {item.family}")
        elif item.enabled and not family_enabled.get(item.family, False):
            errors.append(f"dataset_enabled_but_family_disabled: {item.dataset_id} -> {item.family}")

        if not item.source_path:
            errors.append(f"dataset_missing_source_path: {item.dataset_id}")
        elif not item.source_path.startswith(os.path.join("data", "status", "autoresearch")):
            errors.append(f"dataset_source_path_out_of_scope: {item.dataset_id} -> {item.source_path}")

        if not item.source_kind:
            errors.append(f"dataset_missing_source_kind: {item.dataset_id}")
        elif item.source_kind not in ALLOWED_SOURCE_KINDS:
            errors.append(f"dataset_invalid_source_kind: {item.dataset_id} -> {item.source_kind}")

        if not item.owner:
            warnings.append(f"dataset_missing_owner: {item.dataset_id}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "family_count": len(families),
        "dataset_count": len(datasets),
    }
