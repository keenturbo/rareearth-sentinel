import argparse
import json
import os
import time

from src.config import Config
from src.sources.registry import get_sources
from src.fetcher import Fetcher
from src.extractor import GeminiExtractor
from src.storage import Storage
from src.reporter import Reporter
from src.context import ContextBuilder
from src.utils import clean_text, isoformat, now_cst


def run_pipeline(config):
    storage = Storage(config)
    seen_ids = storage.load_existing_ids()
    sources = get_sources(config)
    fetcher = Fetcher(config)
    extractor = GeminiExtractor(config)
    reporter = Reporter(config)

    history_records = list(storage.iter_records())
    context_builder = ContextBuilder(
        history_records,
        max_items=config.context_max_items,
        lookback_days=config.context_window_days,
    )

    stats = {
        "total_items": 0,
        "seen": 0,
        "fetch_fail": 0,
        "too_short": 0,
        "analyze_fail": 0,
        "saved": 0,
        "summary_fallback": 0,
    }

    items_by_source = []
    total_items = 0
    for source in sources:
        items = source.fetch_items()
        items_by_source.append((source, items))
        total_items += len(items)

    stats["total_items"] = total_items
    started_at = isoformat(now_cst())
    processed_items = 0
    current_info = {}

    def write_status(running, finished=False, error=""):
        if running and processed_items < total_items:
            current_index = processed_items + 1
        else:
            current_index = processed_items

        status = {
            "running": running,
            "started_at": started_at,
            "updated_at": isoformat(now_cst()),
            "finished_at": isoformat(now_cst()) if finished else "",
            "total_items": total_items,
            "processed_items": processed_items,
            "current_index": current_index,
            "current": current_info or {},
            "stats": stats,
            "error": error,
        }
        storage.write_status_json(status)

    write_status(running=True)

    try:
        for source, items in items_by_source:
            print(f"[source] {source.__class__.__name__}: {len(items)} items")

            for item in items:
                title = (item.get("title") or "").strip()
                url = item.get("url")
                current_info = {
                    "title": title,
                    "url": url,
                    "source": source.__class__.__name__,
                }
                print(f"[item] {title[:120]}")
                write_status(running=True)

                try:
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

                    context_summary = context_builder.build_context(
                        title, summary or content[:600], exclude_id=""
                    )

                    analysis = extractor.analyze(content, context_summary=context_summary)
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
                    context_builder.add_record(record)
                    seen_ids.add(record["id"])
                    stats["saved"] += 1
                    print("  + saved")

                    if config.analyze_delay_seconds > 0:
                        time.sleep(config.analyze_delay_seconds)
                finally:
                    processed_items += 1
                    write_status(running=True)

        reporter.rebuild(storage)
        write_status(running=False, finished=True)
        return stats
    except Exception as exc:
        write_status(running=False, finished=True, error=str(exc))
        raise


# Remaining functions (backfill, main) unchanged below


def _needs_backfill(record):
    expert_summary = str(record.get("expert_summary", "")).strip()
    return not expert_summary


def _merge_analysis(record, analysis):
    for key, value in analysis.items():
        record[key] = value
    return record


def _analyze_with_retry(extractor, content, retry_attempts, retry_delay):
    attempts = max(0, int(retry_attempts))
    for attempt in range(attempts + 1):
        analysis = extractor.analyze(content)
        if analysis is not None:
            return analysis, attempt
        if attempt < attempts and retry_delay > 0:
            time.sleep(retry_delay)
    return None, attempts


def backfill_records(config, limit=0, retry_attempts=0):
    storage = Storage(config)
    extractor = GeminiExtractor(config)
    reporter = Reporter(config)

    input_path = storage.jsonl_path
    if not os.path.exists(input_path):
        print("No history file found for backfill.")
        return 0

    tmp_path = input_path + ".tmp"
    updated = 0
    processed = 0
    skipped = 0
    analyze_fail = 0

    with open(input_path, "r", encoding="utf-8") as src, open(
        tmp_path, "w", encoding="utf-8"
    ) as dst:
        for line in src:
            processed += 1
            raw = line.rstrip("\n")
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                dst.write(raw + "\n")
                continue

            if not _needs_backfill(record):
                dst.write(json.dumps(record, ensure_ascii=False) + "\n")
                continue

            if limit and updated >= limit:
                dst.write(json.dumps(record, ensure_ascii=False) + "\n")
                continue

            content = str(record.get("content", "")).strip()
            if not content:
                skipped += 1
                dst.write(json.dumps(record, ensure_ascii=False) + "\n")
                continue

            content = clean_text(content)
            if len(content) < config.summary_min_chars:
                skipped += 1
                dst.write(json.dumps(record, ensure_ascii=False) + "\n")
                continue

            analysis, _attempt = _analyze_with_retry(
                extractor, content, retry_attempts, config.analyze_delay_seconds
            )
            if analysis is None:
                analyze_fail += 1
                if retry_attempts:
                    title = str(record.get("title", ""))
                    print(
                        "[backfill] analyze_fail after {attempts} attempts: {title}".format(
                            attempts=retry_attempts + 1, title=title[:120]
                        )
                    )
                dst.write(json.dumps(record, ensure_ascii=False) + "\n")
                continue

            record = _merge_analysis(record, analysis)
            dst.write(json.dumps(record, ensure_ascii=False) + "\n")
            updated += 1
            title = str(record.get("title", ""))
            print(f"[backfill] updated: {title[:120]}")

            if config.analyze_delay_seconds > 0:
                time.sleep(config.analyze_delay_seconds)

    if updated > 0:
        os.replace(tmp_path, input_path)
        reporter.rebuild(storage)
    else:
        os.remove(tmp_path)

    print(
        "Backfill complete. Updated: {updated} | processed: {processed} | "
        "skipped: {skipped} | analyze_fail: {analyze_fail}".format(
            updated=updated,
            processed=processed,
            skipped=skipped,
            analyze_fail=analyze_fail,
        )
    )
    return updated



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild index.json and index.html from history",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Backfill expert fields for historical records",
    )
    parser.add_argument(
        "--backfill-missing",
        action="store_true",
        help="Backfill only missing expert fields (alias of --backfill)",
    )
    parser.add_argument(
        "--backfill-retry",
        nargs="?",
        const=2,
        type=int,
        default=0,
        help="Retry analyze failures during backfill (default 2)",
    )
    parser.add_argument(
        "--backfill-limit",
        type=int,
        default=0,
        help="Max records to backfill (0 = no limit)",
    )
    args = parser.parse_args()

    config = Config.from_env()

    if args.backfill or args.backfill_missing:
        backfill_records(
            config, limit=args.backfill_limit, retry_attempts=args.backfill_retry
        )
        return

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
