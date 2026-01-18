import argparse
import json
import os

from src.config import Config
from src.telegram import post_telegram_records


def _load_records(path):
    records = []
    if not path or not os.path.exists(path):
        return records
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def main():
    parser = argparse.ArgumentParser(description="Post telegram messages")
    parser.add_argument("--input", required=True, help="JSONL file path")
    args = parser.parse_args()

    records = _load_records(args.input)
    if not records:
        print("No records to post")
        return

    config = Config.from_env()
    posted, total = post_telegram_records(records, config)
    print(f"Posted {posted}/{total}")


if __name__ == "__main__":
    main()
