import argparse
import json
import os
import time
from dataclasses import replace
from urllib.parse import urlparse

from src.config import Config
from src.sources.registry import get_sources
from src.fetcher import Fetcher
from src.extractor import GeminiExtractor
from src.storage import Storage
from src.reporter import Reporter
from src.context import ContextBuilder, VectorContextBuilder
from src.embeddings import EmbeddingStore
from src.normalizer import Normalizer
from src.utils import clean_text, isoformat, now_cst, is_homepage_url
from src.datasets import build_datasets
from src.premium import generate_daily_briefs, backfill_daily_briefs
from src.telegram import post_telegram_records


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
        "analyze_en_fail": 0,
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
    new_records = []

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

                    source_type = str(item.get("source_type") or "")
                    if source_type.startswith("official:"):
                        resolved_host = urlparse(resolved_url or "").netloc
                        if is_homepage_url(resolved_url) or resolved_host == "news.google.com":
                            stats["fetch_fail"] += 1
                            print("  - skip: official_homepage")
                            continue

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

                    # Chinese analysis (China perspective)
                    analysis = extractor.analyze(content, context_summary=context_summary)
                    if analysis is None:
                        stats["analyze_fail"] += 1
                        print("  - skip: analyze_fail (zh)")
                        continue

                    # English analysis (Western perspective)
                    analysis_en = extractor.analyze_en(content, context_summary=context_summary)
                    if analysis_en is None:
                        stats["analyze_en_fail"] += 1
                        print("  - warn: analyze_en_fail (en), continuing with zh only")
                        analysis_en = {}

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
                        analysis_en=analysis_en,
                    )
                    storage.append_record(record)
                    context_builder.add_record(record)
                    normalizer.add_record(record)
                    seen_ids.add(record["id"])
                    stats["saved"] += 1
                    new_records.append(record)
                    print("  + saved (zh + en)")

                    if config.analyze_delay_seconds > 0:
                        time.sleep(config.analyze_delay_seconds)
                finally:
                    processed_items += 1
                    write_status(running=True)

        if config.premium_enabled:
            generate_daily_briefs(config, list(storage.iter_records()))

        reporter.rebuild(storage)
        try:
            posted, total = post_telegram_records(new_records, config)
            if total:
                print(f"[telegram] posted {posted}/{total}")
        except Exception as exc:
            print(f"[telegram] error: {exc}")
        write_status(running=False, finished=True)
        return stats
    except Exception as exc:
        write_status(running=False, finished=True, error=str(exc))
        raise


def _run_rebuild(config):
    storage = Storage(config)
    reporter = Reporter(config)
    reporter.rebuild(storage)


def _run_build_datasets(config):
    storage = Storage(config)
    records = list(storage.iter_records())
    build_datasets(config, records)


def main():
    parser = argparse.ArgumentParser(description="RareEarth Sentinel")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild static pages")
    parser.add_argument(
        "--build-datasets",
        action="store_true",
        help="Generate training datasets from stored records",
    )
    args = parser.parse_args()

    config = Config.from_env()

    if args.rebuild:
        _run_rebuild(config)
        return

    if args.build_datasets:
        _run_build_datasets(config)
        return

    run_pipeline(config)


if __name__ == "__main__":
    main()
