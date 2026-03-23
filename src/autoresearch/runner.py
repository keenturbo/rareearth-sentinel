from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from .datasets import load_dataset_snapshot
from .decision import compare_batch_results
from .evaluators import evaluate_family_batch
from .models import ExperimentDefinition, RunRecord
from .registry import get_dataset_definition, get_family_definition
from ..utils import isoformat, now_cst


EXPERIMENTS_PATH = os.path.join("data", "status", "autoresearch_experiments.jsonl")
RUNS_PATH = os.path.join("data", "status", "autoresearch_runs.jsonl")


def _read_json(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        raise ValueError(f"input_not_found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"input_invalid_json: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"input_not_dict: {path}")
    return data


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


def _experiments_path(config=None) -> str:
    return EXPERIMENTS_PATH


def _runs_path(config=None) -> str:
    return RUNS_PATH


def _build_run_id(experiment_id: str) -> str:
    ts = now_cst().strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha256(f"{experiment_id}-{isoformat(now_cst())}".encode("utf-8")).hexdigest()[:4]
    return f"run-{ts}-{digest}"


def load_experiment_definition(config, experiment_id: str) -> ExperimentDefinition:
    target = str(experiment_id or "").strip()
    if not target:
        raise ValueError("experiment_id_required")

    rows = _read_jsonl(_experiments_path(config))
    for row in rows:
        if str(row.get("experiment_id") or "").strip() != target:
            continue
        family = str(row.get("family") or "").strip()
        dataset_id = str(row.get("dataset_id") or "").strip()
        family_def = get_family_definition(config, family)
        dataset_def = get_dataset_definition(config, dataset_id)
        if family_def is None:
            raise ValueError(f"experiment_family_not_found: {family}")
        if dataset_def is None:
            raise ValueError(f"experiment_dataset_not_found: {dataset_id}")
        return ExperimentDefinition(
            experiment_id=target,
            family=family,
            target_chain=str(row.get("target_chain") or family_def.target_chain or "").strip(),
            candidate_name=str(row.get("candidate_name") or "").strip(),
            baseline_name=str(row.get("baseline_name") or family_def.baseline_name or "").strip(),
            dataset_id=dataset_id,
            config_patch=row.get("config_patch") if isinstance(row.get("config_patch"), dict) else {},
            enabled=bool(row.get("enabled", True)),
            reviewer=str(row.get("reviewer") or family_def.reviewer or "admin").strip(),
            created_at=str(row.get("created_at") or "").strip(),
        )
    raise ValueError(f"experiment_definition_not_found: {target}")


def load_outputs_input(path_or_payload: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(path_or_payload, dict):
        return path_or_payload
    if not isinstance(path_or_payload, str):
        raise ValueError("outputs_input_invalid")
    return _read_json(path_or_payload)


def _ensure_case_alignment(dataset_payload: dict[str, Any], outputs: dict[str, Any], side: str) -> dict[str, Any]:
    items = (dataset_payload or {}).get("items") or []
    case_ids = [str((item or {}).get("case_id") or "").strip() for item in items if str((item or {}).get("case_id") or "").strip()]
    missing = [case_id for case_id in case_ids if case_id not in outputs]
    if missing:
        preview = ", ".join(missing[:5])
        raise ValueError(f"{side}_missing_case_ids: {preview}")
    return {case_id: outputs.get(case_id) for case_id in case_ids}


def _evaluate_side(family: str, dataset_payload: dict[str, Any], outputs: dict[str, Any]) -> dict[str, Any]:
    return evaluate_family_batch(family, dataset_payload, outputs)


def build_run_record(
    run_id: str,
    experiment: ExperimentDefinition,
    baseline_result: dict[str, Any] | None,
    candidate_result: dict[str, Any] | None,
    decision: dict[str, Any] | None,
    artifacts: dict[str, Any] | None,
    errors: list[str] | None,
    started_at: str,
    finished_at: str,
    status: str,
) -> dict[str, Any]:
    record = RunRecord(
        run_id=run_id,
        experiment_id=experiment.experiment_id,
        family=experiment.family,
        baseline_run_id="",
        status=status,
        decision=decision or {},
        baseline_summary=((baseline_result or {}).get("summary") or {}),
        candidate_summary=((candidate_result or {}).get("summary") or {}),
        case_results={
            "baseline": (baseline_result or {}).get("case_results") or [],
            "candidate": (candidate_result or {}).get("case_results") or [],
        },
        artifacts=artifacts or {},
        errors=errors or [],
        started_at=started_at,
        finished_at=finished_at,
    )
    return record.to_dict()


def _append_jsonl(path: str, row: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False))
        f.write("\n")


def append_run_record(config, run_record: dict[str, Any]) -> None:
    _append_jsonl(_runs_path(config), run_record)


def _build_failed_run_record(run_id: str, experiment_id: str, family: str, errors: list[str], started_at: str, finished_at: str) -> dict[str, Any]:
    experiment = ExperimentDefinition(
        experiment_id=experiment_id,
        family=family,
        target_chain="",
        candidate_name="",
        baseline_name="",
        dataset_id="",
        config_patch={},
        enabled=True,
        reviewer="admin",
        created_at="",
    )
    return build_run_record(
        run_id=run_id,
        experiment=experiment,
        baseline_result=None,
        candidate_result=None,
        decision=None,
        artifacts={},
        errors=errors,
        started_at=started_at,
        finished_at=finished_at,
        status="failed",
    )


def run_experiment(config, experiment_id: str, baseline_input: str | dict[str, Any], candidate_input: str | dict[str, Any]) -> dict[str, Any]:
    started_at = isoformat(now_cst())
    run_id = _build_run_id(experiment_id)
    experiment_family = ""
    try:
        experiment = load_experiment_definition(config, experiment_id)
        experiment_family = experiment.family
        if not experiment.enabled:
            raise ValueError(f"experiment_disabled: {experiment.experiment_id}")

        dataset_payload = load_dataset_snapshot(config, experiment.dataset_id)
        baseline_outputs = load_outputs_input(baseline_input)
        candidate_outputs = load_outputs_input(candidate_input)
        baseline_outputs = _ensure_case_alignment(dataset_payload, baseline_outputs, "baseline")
        candidate_outputs = _ensure_case_alignment(dataset_payload, candidate_outputs, "candidate")

        baseline_result = _evaluate_side(experiment.family, dataset_payload, baseline_outputs)
        candidate_result = _evaluate_side(experiment.family, dataset_payload, candidate_outputs)
        decision = compare_batch_results(
            experiment.family,
            (baseline_result or {}).get("summary") or {},
            (candidate_result or {}).get("summary") or {},
        )

        finished_at = isoformat(now_cst())
        artifacts = {
            "dataset_id": experiment.dataset_id,
            "baseline_input_path": baseline_input if isinstance(baseline_input, str) else "",
            "candidate_input_path": candidate_input if isinstance(candidate_input, str) else "",
        }
        record = build_run_record(
            run_id=run_id,
            experiment=experiment,
            baseline_result=baseline_result,
            candidate_result=candidate_result,
            decision=decision,
            artifacts=artifacts,
            errors=[],
            started_at=started_at,
            finished_at=finished_at,
            status="finished",
        )
        append_run_record(config, record)
        return record
    except Exception as exc:
        finished_at = isoformat(now_cst())
        record = _build_failed_run_record(
            run_id=run_id,
            experiment_id=str(experiment_id or ""),
            family=experiment_family,
            errors=[str(exc)],
            started_at=started_at,
            finished_at=finished_at,
        )
        append_run_record(config, record)
        return record
