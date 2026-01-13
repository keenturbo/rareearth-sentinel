#!/usr/bin/env python3
import argparse
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.config import Config
from src.indices.signals import write_index_signals


def main():
    parser = argparse.ArgumentParser(description="Build index signals")
    parser.add_argument("--once", action="store_true", help="Build signals once")
    args = parser.parse_args()

    config = Config.from_env()
    write_index_signals(
        config.index_normalized_dir,
        daily_threshold=config.index_daily_threshold,
        weekly_threshold=config.index_weekly_threshold,
    )

    if not args.once:
        return


if __name__ == "__main__":
    main()
