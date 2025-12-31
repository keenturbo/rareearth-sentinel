import argparse
import json
import os
import time
from dataclasses import replace

from src.config import Config
from src.sources.registry import get_sources
from src.fetcher import Fetcher
from src.extractor import GeminiExtractor
from src.storage import Storage
from src.reporter import Reporter
from src.context import ContextBuilder, VectorContextBuilder
from src.embeddings import EmbeddingStore
from src.normalizer import Normalizer
from src.utils import clean_text, isoformat, now_cst
from src.datasets import build_datasets


def _context_lines(context_summary, limit=0):
    if not context_summary:
        return []
    lines = []
    for raw in context_summary.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("-"):
            line = line[1:].strip()
        if line:
            lines.append(line)
        if limit and len(lines) >= limit:
            break
    return lines


def _build_context_builder(config, records):
    mode = (config.context_mode or "token").strip().lower()
    if mode == "vector":
        embedding_store = EmbeddingStore(config)
        embedding_store.warmup(records, limit=config.embedding_warmup_limit)
        return (
            VectorContextBuilder(
                records,
                embedding_store,
                max_items=config.context_max_items,
                lookback_days=config.context_window_days,
            ),
            embedding_store,
        )
    return (
        ContextBuilder(
            records,
            max_items=config.context_max_items,
            lookback_days=config.context_window_days,
        ),
        None,
    )


def run_pipeline(config):
    storage = Storage(config)
    seen_ids = storage.load_existing_ids()
    sources = get_sources(config)
    fetcher = Fetcher(config)
    extractor = GeminiExtractor(config)
    reporter = Reporter(config)

    history_records = list(storage.iter_records())
    context_builder, _embedding_store = _build_context_builder(config, history_records)
    normalizer = Normalizer(history_records, config)

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
                    context_lines = _context_lines(
                        context_summary, limit=config.context_max_items
                    )

                    analysis = extractor.analyze(content, context_summary=context_summary)
                    if analysis is None:
                        stats["analyze_fail"] += 1
                        print("  - skip: analyze_fail")
                        continue

                    normalized, credibility = normalizer.normalize_and_score(
                        item, analysis, content, content_source
                    )

                    record = storage.build_record(
                        item,
                        analysis,
                        content,
                        content_source=content_source,
                        fetch_status=fetch_status,
                        resolved_url=resolved_url,
                        related_context=context_lines,
                        analysis_with_context=bool(context_summary),
                        normalized=normalized,
                        credibility=credibility,
                    )
                    storage.append_record(record)
                    context_builder.add_record(record)
                    normalizer.add_record(record)
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


def _needs_backfill(record, force_context=False):
    expert_summary = str(record.get("expert_summary", "")).strip()
    if not expert_summary:
        return True
    if force_context:
        related_context = record.get("related_context")
        context_flag = record.get("analysis_with_context")
        if not related_context or not context_flag:
            return True
    return False


def _merge_analysis(record, analysis):
    for key, value in analysis.items():
        record[key] = value
    return record


def _analyze_with_retry(extractor, content, retry_attempts, retry_delay, context_summary=""):
    attempts = max(0, int(retry_attempts))
    for attempt in range(attempts + 1):
        analysis = extractor.analyze(content, context_summary=context_summary)
        if analysis is not None:
            return analysis, attempt
        if attempt < attempts and retry_delay > 0:
            time.sleep(retry_delay)
    return None, attempts


def backfill_records(config, limit=0, retry_attempts=0, use_context=False):
    storage = Storage(config)
    extractor = GeminiExtractor(config)
    reporter = Reporter(config)

    input_path = storage.jsonl_path
    if not os.path.exists(input_path):
        print("No history file found for backfill.")
        return 0

    history_records = []
    with open(input_path, "r", encoding="utf-8") as src:
        for line in src:
            raw = line.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            history_records.append(record)

    normalizer = Normalizer(history_records, config)

    context_builder = None
    if use_context:
        context_builder, _embedding_store = _build_context_builder(
            config, history_records
        )
        print(f"[backfill] context loaded: {len(history_records)} records")

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

            needs_backfill = _needs_backfill(record, force_context=use_context)
            needs_norm = not record.get("normalized") or not record.get("credibility")

            if not needs_backfill and not needs_norm:
                dst.write(json.dumps(record, ensure_ascii=False) + "\n")
                continue

            if limit and updated >= limit:
                dst.write(json.dumps(record, ensure_ascii=False) + "\n")
                continue

            analysis_updated = False

            if needs_backfill:
                content = str(record.get("content", "")).strip()
                if not content:
                    skipped += 1
                else:
                    content = clean_text(content)
                    if len(content) < config.summary_min_chars:
                        skipped += 1
                    else:
                        title = str(record.get("title", ""))
                        summary = str(record.get("summary_zh", "")).strip()
                        if not summary:
                            summary = content[:600]

                        context_summary = ""
                        context_lines = []
                        if use_context and context_builder:
                            context_summary = context_builder.build_context(
                                title, summary, exclude_id=record.get("id", "")
                            )
                            context_lines = _context_lines(
                                context_summary, limit=config.context_max_items
                            )

                        analysis, _attempt = _analyze_with_retry(
                            extractor,
                            content,
                            retry_attempts,
                            config.analyze_delay_seconds,
                            context_summary=context_summary,
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
                        else:
                            record = _merge_analysis(record, analysis)
                            analysis_updated = True
                            if use_context:
                                record["analysis_with_context"] = bool(context_summary)
                                record["related_context"] = context_lines

            if analysis_updated or needs_norm:
                normalized, credibility = normalizer.normalize_and_score_from_record(
                    record
                )
                record["normalized"] = normalized
                record["credibility"] = credibility
                updated += 1
                title = str(record.get("title", ""))
                print(f"[backfill] updated: {title[:120]}")
                if analysis_updated and config.analyze_delay_seconds > 0:
                    time.sleep(config.analyze_delay_seconds)

            dst.write(json.dumps(record, ensure_ascii=False) + "\n")

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
        "--backfill-with-context",
        action="store_true",
        help="Backfill expert fields using historical context",
    )
    parser.add_argument(
        "--backfill-with-vector-context",
        action="store_true",
        help="Backfill expert fields using vector context",
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
    parser.add_argument(
        "--build-datasets",
        action="store_true",
        help="Build training datasets (extract/reason) from history",
    )
    args = parser.parse_args()

    config = Config.from_env()
    if args.backfill_with_vector_context:
        config = replace(config, context_mode="vector")

    if args.build_datasets:
        build_datasets(config)
        return

    if (
        args.backfill
        or args.backfill_missing
        or args.backfill_with_context
        or args.backfill_with_vector_context
    ):
        backfill_records(
            config,
            limit=args.backfill_limit,
            retry_attempts=args.backfill_retry,
            use_context=args.backfill_with_context
            or args.backfill_with_vector_context,
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
