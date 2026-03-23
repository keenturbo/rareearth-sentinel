#!/usr/bin/env python3
import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.autoresearch.runner import run_experiment
from src.config import Config


def main() -> int:
    parser = argparse.ArgumentParser(description="Run RareEarth Sentinel autoresearch replay")
    parser.add_argument("--experiment-id", required=True, help="Experiment ID from data/status/autoresearch_experiments.jsonl")
    parser.add_argument("--baseline-input", required=True, help="Path to baseline outputs JSON file")
    parser.add_argument("--candidate-input", required=True, help="Path to candidate outputs JSON file")
    args = parser.parse_args()

    config = Config.from_env()
    result = run_experiment(
        config,
        experiment_id=args.experiment_id,
        baseline_input=args.baseline_input,
        candidate_input=args.candidate_input,
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
