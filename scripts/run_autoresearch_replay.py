#!/usr/bin/env python3
import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.autoresearch.datasets import load_homepage_focus_gold
from src.autoresearch.providers import build_homepage_provider_bundle
from src.autoresearch.runner import load_experiment_definition, run_experiment
from src.config import Config


SUPPORTED_PROVIDER = "homepage"
SUPPORTED_PROVIDER_FAMILY = "homepage_topic_policy"


def _print_error(error: str, **extra) -> int:
    payload = {"ok": False, "error": error}
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run RareEarth Sentinel autoresearch replay")
    parser.add_argument("--experiment-id", required=True, help="Experiment ID from data/status/autoresearch_experiments.jsonl")
    parser.add_argument("--baseline-input", default="", help="Path to baseline outputs JSON file")
    parser.add_argument("--candidate-input", default="", help="Path to candidate outputs JSON file")
    parser.add_argument("--provider", default="", help="Provider shortcut, currently supports: homepage")
    args = parser.parse_args()

    experiment_id = str(args.experiment_id or "").strip()
    baseline_input = str(args.baseline_input or "").strip()
    candidate_input = str(args.candidate_input or "").strip()
    provider = str(args.provider or "").strip().lower()

    manual_mode = bool(baseline_input or candidate_input)
    provider_mode = bool(provider)

    if provider_mode and manual_mode:
        return _print_error(
            "provider_mode_conflicts_with_manual_inputs",
            provider=provider,
            baseline_input=baseline_input,
            candidate_input=candidate_input,
        )

    if not provider_mode and not (baseline_input and candidate_input):
        return _print_error(
            "missing_replay_input_mode",
            required="either --provider homepage or both --baseline-input and --candidate-input",
        )

    config = Config.from_env()

    if provider_mode:
        if provider != SUPPORTED_PROVIDER:
            return _print_error(
                f"unsupported_provider: {provider}",
                supported_provider=SUPPORTED_PROVIDER,
            )
        try:
            experiment = load_experiment_definition(config, experiment_id)
        except Exception as exc:
            return _print_error(str(exc), experiment_id=experiment_id)
        if experiment.family != SUPPORTED_PROVIDER_FAMILY:
            return _print_error(
                f"provider_family_mismatch: {experiment.family}",
                provider=provider,
                expected_family=SUPPORTED_PROVIDER_FAMILY,
                experiment_family=experiment.family,
            )
        try:
            dataset_payload = load_homepage_focus_gold(config)
            bundle = build_homepage_provider_bundle(dataset_payload)
            baseline_input = bundle.get("baseline") or {}
            candidate_input = bundle.get("candidate") or {}
        except Exception as exc:
            return _print_error(str(exc), provider=provider, experiment_id=experiment_id)

    result = run_experiment(
        config,
        experiment_id=experiment_id,
        baseline_input=baseline_input,
        candidate_input=candidate_input,
    )

    ok = str(result.get("status") or "") == "finished"
    decision = result.get("decision") or {}
    payload = {
        "ok": ok,
        "run_id": result.get("run_id"),
        "family": result.get("family"),
        "status": result.get("status"),
        "decision": decision.get("decision") if isinstance(decision, dict) else "",
        "score_delta": decision.get("score_delta") if isinstance(decision, dict) else None,
        "baseline_score": decision.get("baseline_score") if isinstance(decision, dict) else None,
        "candidate_score": decision.get("candidate_score") if isinstance(decision, dict) else None,
        "errors": result.get("errors") or [],
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
