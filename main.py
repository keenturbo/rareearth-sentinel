import argparse
import time

from src.config import Config
from src.sources.registry import get_sources
from src.fetcher import Fetcher
from src.extractor import GeminiExtractor
from src.storage import Storage
from src.reporter import Reporter
from src.utils import is_china_primary


def run_pipeline(config):
    storage = Storage(config)
    seen_ids = storage.load_existing_ids()
    sources = get_sources(config)
    fetcher = Fetcher(config)
    extractor = GeminiExtractor(config)
    reporter = Reporter(config)

    new_count = 0

    for source in sources:
        for item in source.fetch_items():
            if storage.is_seen(item["url"], seen_ids):
                continue

            content = fetcher.fetch(item["url"])
            if not content or len(content) < config.min_content_chars:
                continue

            if config.fetch_delay_seconds > 0:
                time.sleep(config.fetch_delay_seconds)

            analysis = extractor.analyze(content)
            if analysis is None:
                continue

            if is_china_primary(analysis.get("country_primary", "")):
                continue

            record = storage.build_record(item, analysis, content)
            storage.append_record(record)
            seen_ids.add(record["id"])
            new_count += 1

            if config.analyze_delay_seconds > 0:
                time.sleep(config.analyze_delay_seconds)

    reporter.rebuild(storage)
    return new_count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild index.json and index.html from history",
    )
    args = parser.parse_args()

    config = Config.from_env()

    if args.rebuild:
        storage = Storage(config)
        reporter = Reporter(config)
        reporter.rebuild(storage)
        print("Rebuild complete.")
        return

    new_count = run_pipeline(config)
    print(f"Run complete. New records: {new_count}")


if __name__ == "__main__":
    main()
