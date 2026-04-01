#!/usr/bin/env python3
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.input_guard import clean_model_input, guard_article_text
from src.utils import isoformat, now_cst


DEFAULT_INPUT = os.path.join("data", "intel.jsonl")
DEFAULT_CLEANED = os.path.join("data", "intel.cleaned.jsonl")
DEFAULT_REMOVED = os.path.join("data", "intel.removed.jsonl")
DEFAULT_REPORT = os.path.join("data", "status", "intel_clean_report.json")

# Fields that indicate Gemini has already analysed this record successfully.
# If ANY of these fields contain a non-empty string, we keep the record even
# when the raw content field is dirty / empty.
ANALYSIS_KEEP_FIELDS = (
    "summary_zh",
    "summary_en",
    "expert_summary",
    "expert_summary_en",
)


def _has_valid_analysis(row):
    """Return True if the record already carries useful analysis output."""
    for field in ANALYSIS_KEEP_FIELDS:
        value = row.get(field)
        if isinstance(value, str) and len(value.strip()) >= 20:
            return True
    return False


def _read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                rows.append(
                    {
                        "__line__": idx,
                        "__raw__": raw,
                        "__invalid_json__": True,
                    }
                )
                continue
            if isinstance(row, dict):
                row["__line__"] = idx
                rows.append(row)
    return rows


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            clean_row = {k: v for k, v in row.items() if not k.startswith("__")}
            f.write(json.dumps(clean_row, ensure_ascii=False) + "\n")


def _build_removed_entry(row, reason, stats=None, signals=None):
    payload = {k: v for k, v in row.items() if not k.startswith("__")}
    payload["removed_reason"] = reason
    if stats:
        payload["removed_stats"] = stats
    if signals:
        payload["removed_signals"] = signals
    return payload


def _is_low_value_cleaned(cleaned_text):
    text = str(cleaned_text or "")
    if not text:
        return True
    lower = text.lower()
    bad_fragments = [
        "cookie settings",
        "privacy policy",
        "terms of use",
        "about us",
        "follow us",
        "latest news",
        "all sections",
        "view comments",
        "sign in",
        "subscribe",
        "newsletter",
    ]
    hits = sum(1 for frag in bad_fragments if frag in lower)
    if hits >= 3:
        return True
    words = text.split()
    unique_ratio = (len(set(words)) / max(1, len(words))) if words else 0
    if len(words) >= 20 and unique_ratio < 0.28:
        return True
    return False


def clean_history(config, input_path, cleaned_path, removed_path, report_path):
    rows = _read_jsonl(input_path)
    cleaned_rows = []
    removed_rows = []
    reason_counter = Counter()
    signal_counter = Counter()
    kept_by_analysis = 0
    examples = []

    for row in rows:
        if row.get("__invalid_json__"):
            reason = "invalid_json"
            reason_counter[reason] += 1
            removed_rows.append(
                {
                    "raw_line": row.get("__raw__", ""),
                    "removed_reason": reason,
                    "line": row.get("__line__"),
                }
            )
            if len(examples) < 10:
                examples.append(
                    {
                        "line": row.get("__line__"),
                        "title": "",
                        "source": "",
                        "reason": reason,
                    }
                )
            continue

        content = str(row.get("content") or "")

        # --- SAFE GUARD: keep records that already have valid analysis ---
        if not content.strip() or len(content.strip()) < 50:
            if _has_valid_analysis(row):
                # Content is empty/tiny but Gemini already analysed it.
                # Keep the record as-is (do NOT re-clean content).
                out = {k: v for k, v in row.items() if not k.startswith("__")}
                cleaned_rows.append(out)
                kept_by_analysis += 1
                continue
            reason = "empty_content"
            reason_counter[reason] += 1
            removed_rows.append(_build_removed_entry(row, reason))
            if len(examples) < 10:
                examples.append(
                    {
                        "line": row.get("__line__"),
                        "title": str(row.get("title") or ""),
                        "source": str(row.get("source") or ""),
                        "reason": reason,
                    }
                )
            continue

        guard = guard_article_text(content, config)
        reason = guard.get("reason", "ok")
        stats = guard.get("stats") or {}
        signals = guard.get("pollution_signals") or []

        if not guard.get("pass"):
            # Check if we should still keep it because analysis exists
            if _has_valid_analysis(row):
                out = {k: v for k, v in row.items() if not k.startswith("__")}
                cleaned_rows.append(out)
                kept_by_analysis += 1
                continue

            reason_counter[reason] += 1
            for sig in signals:
                signal_counter[sig] += 1
            removed_rows.append(_build_removed_entry(row, reason, stats=stats, signals=signals))
            if len(examples) < 10:
                examples.append(
                    {
                        "line": row.get("__line__"),
                        "title": str(row.get("title") or ""),
                        "source": str(row.get("source") or ""),
                        "reason": reason,
                    }
                )
            continue

        cleaned_text = clean_model_input(content)
        if _is_low_value_cleaned(cleaned_text):
            if _has_valid_analysis(row):
                out = {k: v for k, v in row.items() if not k.startswith("__")}
                cleaned_rows.append(out)
                kept_by_analysis += 1
                continue

            reason = "low_value_wrapper"
            reason_counter[reason] += 1
            removed_rows.append(_build_removed_entry(row, reason, stats=stats, signals=signals))
            if len(examples) < 10:
                examples.append(
                    {
                        "line": row.get("__line__"),
                        "title": str(row.get("title") or ""),
                        "source": str(row.get("source") or ""),
                        "reason": reason,
                    }
                )
            continue

        out = {k: v for k, v in row.items() if not k.startswith("__")}
        out["content"] = cleaned_text
        cleaned_rows.append(out)

    _write_jsonl(cleaned_path, cleaned_rows)
    _write_jsonl(removed_path, removed_rows)

    report = {
        "generated_at": isoformat(now_cst()),
        "input_path": input_path,
        "cleaned_path": cleaned_path,
        "removed_path": removed_path,
        "input_count": len(rows),
        "cleaned_count": len(cleaned_rows),
        "removed_count": len(removed_rows),
        "removed_rate": round(len(removed_rows) / max(1, len(rows)), 4),
        "kept_by_analysis": kept_by_analysis,
        "reason_breakdown": dict(reason_counter),
        "signal_breakdown": dict(signal_counter),
        "examples": examples,
    }

    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Clean historical intel.jsonl into cleaned + removed outputs without overwriting source by default."
    )
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--cleaned", default=DEFAULT_CLEANED)
    parser.add_argument("--removed", default=DEFAULT_REMOVED)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    args = parser.parse_args()

    config = Config.from_env()
    report = clean_history(
        config=config,
        input_path=args.input,
        cleaned_path=args.cleaned,
        removed_path=args.removed,
        report_path=args.report,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
