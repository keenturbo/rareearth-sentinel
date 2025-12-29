import argparse
import time

from src.config import Config
from src.sources.registry import get_sources
from src.fetcher import Fetcher
from src.extractor import GeminiExtractor
from src.storage import Storage
from src.reporter import Reporter
from src.utils import clean_text


def run_pipeline(config):
    storage = Storage(config)
    seen_ids = storage.load_existing_ids()
    sources = get_sources(config)
    fetcher = Fetcher(config)
    extractor = GeminiExtractor(config)
    reporter = Reporter(config)

    stats = {
        "total_items": 0,
        "seen": 0,
        "fetch_fail": 0,
        "too_short": 0,
        "analyze_fail": 0,
        "saved": 0,
        "summary_fallback": 0,
    }

    for source in sources:
        items = source.fetch_items()
        print(f"[source] {source.__class__.__name__}: {len(items)} items")

        for item in items:
            title = (item.get("title") or "").strip()
            url = item.get("url")
            stats["total_items"] += 1
            print(f"[item] {title[:120]}")

            if storage.is_seen(url, seen_ids):
                stats["seen"] += 1
                print("  - skip: seen")
                continue

            fetch_result = fetcher.fetch(url)
            content = (fetch_result.get("content") or "").strip()
            fetch_status = fetch_result.get("status")
            resolved_url = fetch_result.get("resolved_url")
            content_source = "fulltext"

            summary = (item.get("summary") or "").strip()
            fallback_text = f"{title}\n\n{summary}".strip()

            if (not content) or (len(content) < config.min_content_chars):
                if len(fallback_text) >= config.summary_min_chars:
                    content = fallback_text
                    content_source = "rss_summary"
                    stats["summary_fallback"] += 1
                    print("  - fallback: title+summary")
                else:
                    if not content:
                        stats["fetch_fail"] += 1
                        print("  - skip: fetch_fail")
                    else:
                        stats["too_short"] += 1
                        print(f"  - skip: too_short ({len(content)} chars)")
                    continue

            content = clean_text(content)

            if content_source == "rss_summary":
                if len(content) < config.summary_min_chars:
                    stats["too_short"] += 1
                    print(f"  - skip: too_short ({len(content)} chars)")
                    continue
            else:
                if len(content) < config.min_content_chars:
                    stats["too_short"] += 1
                    print(f"  - skip: too_short ({len(content)} chars)")
                    continue

            if config.fetch_delay_seconds > 0:
                time.sleep(config.fetch_delay_seconds)

            analysis = extractor.analyze(content)
            if analysis is None:
                stats["analyze_fail"] += 1
                print("  - skip: analyze_fail")
                continue

            record = storage.build_record(
                item,
                analysis,
                content,
                content_source=content_source,
                fetch_status=fetch_status,
                resolved_url=resolved_url,
            )
            storage.append_record(record)
            seen_ids.add(record["id"])
            stats["saved"] += 1
            print("  + saved")

            if config.analyze_delay_seconds > 0:
                time.sleep(config.analyze_delay_seconds)

    reporter.rebuild(storage)
    return stats


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

    stats = run_pipeline(config)
    print(
        "Run complete. New records: {saved} | "
        "items: {total_items}, seen: {seen}, fetch_fail: {fetch_fail}, "
        "too_short: {too_short}, analyze_fail: {analyze_fail}, "
        "summary_fallback: {summary_fallback}".format(**stats)
    )


if __name__ == "__main__":
    main()
