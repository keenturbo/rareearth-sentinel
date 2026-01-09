#!/usr/bin/env python3
import argparse

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


if __name__ == "__main__":
    main()
