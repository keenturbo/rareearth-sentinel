import argparse
import json

from src.config import Config
from src.storage import Storage
from src.top3_builder import build_top3_payload, write_top3_build_status, write_top3_list


def rebuild_top3(config, limit=3, recent_hours=72):
    storage = Storage(config)
    records = list(storage.iter_records())
    payload = build_top3_payload(config, records, limit=limit, recent_hours=recent_hours)
    path = write_top3_list(config, payload)
    write_top3_build_status(config, payload)
    return path, len(payload.get("items") or []), payload.get("source") or "empty"


def main():
    parser = argparse.ArgumentParser(description="Rebuild Top3 list with resilient fallback")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--recent-hours", type=int, default=72)
    args = parser.parse_args()
    config = Config.from_env()
    path, count, source = rebuild_top3(config, limit=args.limit, recent_hours=args.recent_hours)
    print(json.dumps({"ok": True, "path": path, "count": count, "source": source}, ensure_ascii=False))


if __name__ == "__main__":
    main()
