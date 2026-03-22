#!/usr/bin/env python3
import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import now_cst, isoformat


REQUIRED_ARGS = [
    "chain",
    "severity",
    "error_type",
    "symptom",
    "root_cause",
    "fix_type",
    "status",
]


def _build_parser():
    parser = argparse.ArgumentParser(description="Append a harness feedback record")
    parser.add_argument("--chain", required=True)
    parser.add_argument("--severity", required=True)
    parser.add_argument("--error-type", dest="error_type", required=True)
    parser.add_argument("--sample-record-id", dest="sample_record_id", default="")
    parser.add_argument("--symptom", required=True)
    parser.add_argument("--root-cause", dest="root_cause", required=True)
    parser.add_argument("--fix-type", dest="fix_type", required=True)
    parser.add_argument("--fix-ref", dest="fix_ref", default="")
    parser.add_argument("--status", required=True)
    return parser


def _validate_args(args):
    missing = []
    for key in REQUIRED_ARGS:
        value = str(getattr(args, key, "") or "").strip()
        if not value:
            missing.append(key)
    return missing


def append_feedback(config, args):
    path = os.path.join(config.data_dir, "status", "harness_feedback.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record = {
        "created_at": isoformat(now_cst()),
        "chain": str(args.chain).strip(),
        "severity": str(args.severity).strip(),
        "error_type": str(args.error_type).strip(),
        "sample_record_id": str(args.sample_record_id or "").strip(),
        "symptom": str(args.symptom).strip(),
        "root_cause": str(args.root_cause).strip(),
        "fix_type": str(args.fix_type).strip(),
        "fix_ref": str(args.fix_ref or "").strip(),
        "status": str(args.status).strip(),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def main():
    parser = _build_parser()
    args = parser.parse_args()
    missing = _validate_args(args)
    if missing:
        raise SystemExit("missing required args: " + ", ".join(missing))

    config = Config.from_env()
    record = append_feedback(config, args)
    print(json.dumps({"ok": True, "record": record}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
