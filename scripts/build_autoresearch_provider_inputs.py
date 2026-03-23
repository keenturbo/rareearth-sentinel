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
from src.config import Config
from src.utils import isoformat, now_cst


SUPPORTED_FAMILY = "homepage_topic_policy"
BASELINE_PATH = os.path.join("data", "status", "autoresearch", "inputs", "homepage-baseline-provider.json")
CANDIDATE_PATH = os.path.join("data", "status", "autoresearch", "inputs", "homepage-candidate-provider.json")


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build homepage autoresearch provider inputs")
    parser.add_argument("--family", default=SUPPORTED_FAMILY, help="Currently only homepage_topic_policy is supported")
    args = parser.parse_args()

    family = str(args.family or "").strip()
    if family != SUPPORTED_FAMILY:
        print(json.dumps({
            "ok": False,
            "error": f"unsupported_family: {family}",
            "supported_family": SUPPORTED_FAMILY,
        }, ensure_ascii=False))
        return 1

    config = Config.from_env()
    dataset_payload = load_homepage_focus_gold(config)
    bundle = build_homepage_provider_bundle(dataset_payload)
    baseline = bundle.get("baseline") or {}
    candidate = bundle.get("candidate") or {}

    _write_json(BASELINE_PATH, baseline)
    _write_json(CANDIDATE_PATH, candidate)

    print(json.dumps({
        "ok": True,
        "family": family,
        "baseline_path": BASELINE_PATH,
        "candidate_path": CANDIDATE_PATH,
        "case_count": len(candidate),
        "generated_at": isoformat(now_cst()),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
