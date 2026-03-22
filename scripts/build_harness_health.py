#!/usr/bin/env python3
import json
import os
import sys
from collections import Counter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import now_cst, isoformat


def _read_json(path, default=None):
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return default
    return data if isinstance(data, type(default)) else default


def _read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _safe_ratio(numerator, denominator):
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _record_today_hit(entries):
    today = now_cst().date()
    hits = 0
    for row in entries:
        source_pack = row.get("source_pack") or {}
        meta = source_pack.get("meta") or {}
        generated_at = str(meta.get("generated_at") or row.get("generated_at") or "")
        if len(generated_at) >= 10 and generated_at[:10] == str(today):
            hits += 1
    return hits


def _top_reasons(rows, key_name, top_n=5):
    counter = Counter()
    for row in rows:
        val = row.get(key_name)
        if isinstance(val, str) and val.strip():
            counter[val.strip()] += 1
        elif isinstance(val, list):
            for item in val:
                s = str(item or "").strip()
                if s:
                    counter[s] += 1
    return [{"reason": k, "count": v} for k, v in counter.most_common(top_n)]


def build_harness_health(config):
    status_dir = os.path.join(config.data_dir, "status")
    variants_path = os.path.join(status_dir, "content_variants.jsonl")
    social_path = os.path.join(status_dir, "social_copy_generated.json")
    prompt_qc_path = os.path.join(status_dir, "prompt_qc_logs.jsonl")
    output_path = os.path.join(status_dir, "harness_health.json")

    variant_rows = _read_jsonl(variants_path)
    social = _read_json(social_path, default={})
    prompt_qc_rows = _read_jsonl(prompt_qc_path)

    variant_total = len(variant_rows)
    variant_fallback = sum(1 for row in variant_rows if (row.get("validator") or {}).get("fallback_used"))
    variant_today_hits = _record_today_hit(variant_rows)
    variant_reason_rows = []
    for row in variant_rows:
        validator = row.get("validator") or {}
        variant_reason_rows.append({"reason": validator.get("reason") or "ok"})

    prompt_reason_rows = []
    for row in prompt_qc_rows:
        prompt_reason_rows.append({"reason": row.get("reason") or "ok"})

    health = {
        "updated_at": isoformat(now_cst()),
        "extractor": {
            "prompt_qc_total": len(prompt_qc_rows),
            "prompt_qc_top_reasons": _top_reasons(prompt_reason_rows, "reason", top_n=5),
        },
        "social_copy": {
            "generator_version": str(social.get("generator_version") or ""),
            "fallback_used": bool(social.get("fallback_used")),
            "validator_reason": str(social.get("validator_reason") or ""),
        },
        "content_variants": {
            "total": variant_total,
            "fallback_count": variant_fallback,
            "fallback_rate": _safe_ratio(variant_fallback, variant_total),
            "today_hit_count": variant_today_hits,
            "today_hit_rate": _safe_ratio(variant_today_hits, variant_total),
            "top_validator_reasons": _top_reasons(variant_reason_rows, "reason", top_n=5),
        },
        "homepage_topic": {},
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(health, f, ensure_ascii=False, indent=2)

    return health


def main():
    config = Config.from_env()
    health = build_harness_health(config)
    print(json.dumps({
        "ok": True,
        "updated_at": health.get("updated_at"),
        "variant_total": (health.get("content_variants") or {}).get("total", 0),
        "variant_fallback_rate": (health.get("content_variants") or {}).get("fallback_rate", 0.0),
        "prompt_qc_total": (health.get("extractor") or {}).get("prompt_qc_total", 0),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
