#!/usr/bin/env python3
import argparse
import json
import os
import sys
from urllib.parse import urlparse

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.config import Config
from src.storage import Storage
from src.utils import is_homepage_url


def _is_invalid_official(record):
    source_type = str(record.get("source_type", "")).lower().strip()
    if not source_type.startswith("official"):
        return False

    resolved = record.get("resolved_url") or ""
    url = record.get("url") or ""

    candidate = resolved or url
    if not candidate:
        return True

    netloc = urlparse(candidate).netloc.lower().strip()
    if netloc == "news.google.com":
        return True

    return is_homepage_url(candidate)


def main():
    parser = argparse.ArgumentParser(
        description="Remove official-source records with invalid homepage links"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many records would be removed",
    )
    args = parser.parse_args()

    config = Config.from_env()
    storage = Storage(config)
    records = list(storage.iter_records())
    if not records:
        print("No records found.")
        return

    kept = []
    removed = 0
    for record in records:
        if _is_invalid_official(record):
            removed += 1
            continue
        kept.append(record)

    if args.dry_run:
        print(f"Would remove {removed} of {len(records)} records.")
        return

    jsonl_path = storage.jsonl_path
    tmp_path = jsonl_path + ".tmp"
    bak_path = jsonl_path + ".bak"

    with open(tmp_path, "w", encoding="utf-8") as f:
        for record in kept:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")

    if os.path.exists(bak_path):
        os.remove(bak_path)
    if os.path.exists(jsonl_path):
        os.replace(jsonl_path, bak_path)
    os.replace(tmp_path, jsonl_path)

    print(f"Removed {removed} records. Backup at {bak_path}.")


if __name__ == "__main__":
    main()
