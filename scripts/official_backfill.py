#!/usr/bin/env python3
import argparse
import json
import os
import sys
from urllib.parse import urlparse

import yaml

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.config import Config
from src.storage import Storage
from src.normalizer import Normalizer


def _domain_from_url(url):
    if not url:
        return ""
    domain = urlparse(url).netloc.lower().strip()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _load_domain_map(path):
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        return {}
    entries = []
    if isinstance(data, dict):
        entries = data.get("sources", [])
    elif isinstance(data, list):
        entries = data
    domain_map = {}
    for entry in entries:
        domain = entry.get("domain")
        if not domain and entry.get("url"):
            domain = _domain_from_url(entry.get("url"))
        if not domain:
            continue
        domain = _domain_from_url(domain)
        source_id = entry.get("id") or domain
        domain_map[domain] = source_id
    return domain_map


def _should_skip(record):
    if record.get("official_source_id"):
        return True
    source_type = str(record.get("source_type", "")).lower().strip()
    return source_type.startswith("official")


def main():
    parser = argparse.ArgumentParser(
        description="Backfill official source markers in intel.jsonl"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of updates (0 = no limit)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many records would be updated",
    )
    args = parser.parse_args()

    config = Config.from_env()
    storage = Storage(config)
    records = list(storage.iter_records())
    if not records:
        print("No records found.")
        return

    domain_map = _load_domain_map(config.official_sources_file)
    if not domain_map:
        print("No official domains found. Check OFFICIAL_SOURCES_FILE.")
        return

    normalizer = Normalizer(records, config)

    updated = 0
    total = 0
    output = []
    limit = args.limit or 0

    for record in records:
        total += 1
        if _should_skip(record):
            output.append(record)
            continue

        if limit and updated >= limit:
            output.append(record)
            continue

        url = record.get("resolved_url") or record.get("url") or ""
        domain = _domain_from_url(url)
        source_id = domain_map.get(domain)
        if not source_id:
            output.append(record)
            continue

        record["official_source_id"] = source_id
        record["source_type"] = f"official:{source_id}"
        normalized, credibility = normalizer.normalize_and_score_from_record(record)
        record["normalized"] = normalized
        record["credibility"] = credibility
        updated += 1
        output.append(record)

    if args.dry_run:
        print(f"Would update {updated} of {total} records.")
        return

    jsonl_path = storage.jsonl_path
    if not jsonl_path:
        print("Missing jsonl path.")
        return

    tmp_path = jsonl_path + ".tmp"
    bak_path = jsonl_path + ".bak"

    with open(tmp_path, "w", encoding="utf-8") as f:
        for record in output:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")

    if os.path.exists(bak_path):
        os.remove(bak_path)
    if os.path.exists(jsonl_path):
        os.replace(jsonl_path, bak_path)
    os.replace(tmp_path, jsonl_path)

    print(f"Updated {updated} of {total} records. Backup at {bak_path}.")


if __name__ == "__main__":
    main()
