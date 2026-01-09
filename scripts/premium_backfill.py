#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.config import Config
from src.storage import Storage
from src.premium import backfill_briefs_by_date


def main():
    parser = argparse.ArgumentParser(
        description="Backfill premium brief reports by record date"
    )
    parser.add_argument(
        "--backfill-premium",
        action="store_true",
        help="Generate premium briefs for eligible records",
    )
    parser.add_argument(
        "--backfill-premium-missing",
        action="store_true",
        help="Only generate briefs that do not already exist",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of records to backfill",
    )
    parser.add_argument(
        "--rebuild",
        dest="rebuild",
        action="store_true",
        help="Rebuild static pages after backfill",
    )
    parser.add_argument(
        "--no-rebuild",
        dest="rebuild",
        action="store_false",
        help="Skip static page rebuild",
    )
    parser.set_defaults(rebuild=True)
    args = parser.parse_args()

    if not args.backfill_premium and not args.backfill_premium_missing:
        args.backfill_premium = True

    config = Config.from_env()
    storage = Storage(config)
    records = list(storage.iter_records())

    limit = args.limit or None
    backfill_briefs_by_date(
        config,
        records,
        limit=limit,
        backfill_missing=args.backfill_premium_missing,
    )

    if args.rebuild:
        subprocess.run(
            [sys.executable, os.path.join(ROOT_DIR, "main.py"), "--rebuild"],
            check=False,
        )


if __name__ == "__main__":
    main()
