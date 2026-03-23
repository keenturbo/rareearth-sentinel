#!/usr/bin/env python3
import html
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.autoresearch.registry import load_family_definitions
from src.config import Config
from src.utils import isoformat, now_cst


EXPERIMENTS_PATH = os.path.join("data", "status", "autoresearch_experiments.jsonl")
RUNS_PATH = os.path.join("data", "status", "autoresearch_runs.jsonl")

FAMILY_LABELS = {
    "homepage_topic_policy": "首页专题",
    "social_copy_policy": "社媒文案",
    "content_variants_policy": "三版本输出",
}

DECISION_LABELS = {
    "keep": ("KEEP", "ok"),
    "discard": ("DISCARD", "danger"),
    "needs_review": ("REVIEW", "warn"),
    "unknown": ("UNKNOWN", "muted"),
}

STATUS_LABELS = {
    "finished": ("FINISHED", "ok"),
    "failed": ("FAILED", "danger"),
    "pending": ("PENDING", "muted"),
    "running": ("RUNNING", "info"),
}


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


def _parse_dt(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        norm = text.replace(" ", "T")
        if norm.endswith("Z"):
            norm = norm[:-1] + "+00:00"
        dt = datetime.fromisoformat(norm)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _family_label(family):
    raw = str(family or "").strip()
    return FAMILY_LABELS.get(raw, raw or "-")


def _build_experiment_map():
    rows = _read_jsonl(EXPERIMENTS_PATH)
    out = {}
    for row in rows:
        experiment_id = str(row.get("experiment_id") or "").strip()
        if not experiment_id:
            continue
        out[experiment_id] = row
    return out


def _build_family_meta(config):
    out = {}
    for item in load_family_definitions(config):
        out[item.family] = item
    return out


def _recent_7d_rows(rows):
    now_utc = datetime.now(timezone.utc)
    start = now_utc - timedelta(days=7)
    out = []
    for row in rows:
        dt = _parse_dt(row.get("finished_at") or row.get("started_at"))
        if dt and dt >= start:
            out.append(row)
    return out


def _summary_cards(rows):
    total_runs = len(rows)
    recent_runs = len(_recent_7d_rows(rows))
    keep_count = sum(1 for row in rows if ((row.get("decision") or {}).get("decision") == "keep"))
    discard_count = sum(1 for row in rows if ((row.get("decision") or {}).get("decision") == "discard"))
    review_count = sum(1 for row in rows if ((row.get("decision") or {}).get("decision") == "needs_review"))

    family_scores = defaultdict(list)
    for row in rows:
        family = str(row.get("family") or "").strip()
        score = _safe_float((row.get("candidate_summary") or {}).get("composite_score"))
        if family:
            family_scores[family].append(score)

    best_family = "-"
    best_avg = -1.0
    for family, scores in family_scores.items():
        if not scores:
            continue
        avg = sum(scores) / len(scores)
        if avg > best_avg:
            best_avg = avg
            best_family = _family_label(family)

    return {
        "total_runs": total_runs,
        "recent_7d_runs": recent_runs,
        "keep_count": keep_count,
        "discard_count": discard_count,
        "review_count": review_count,
        "best_family_label": best_family,
    }


def _family_rows(rows):
    bucket = defaultdict(lambda: {"runs": 0, "keep": 0, "discard": 0, "review": 0, "scores": [], "latest": ""})
    for row in rows:
        family = str(row.get("family") or "").strip() or "(empty)"
        item = bucket[family]
        item["runs"] += 1
        decision = str((row.get("decision") or {}).get("decision") or "")
        if decision == "keep":
            item["keep"] += 1
        elif decision == "discard":
            item["discard"] += 1
        elif decision == "needs_review":
            item["review"] += 1
        item["scores"].append(_safe_float((row.get("candidate_summary") or {}).get("composite_score")))
        latest = str(row.get("finished_at") or row.get("started_at") or "")
        if latest > item["latest"]:
            item["latest"] = latest

    result = []
    for family, data in bucket.items():
        scores = data.get("scores") or []
        avg_score = round(sum(scores) / len(scores), 4) if scores else 0.0
        result.append(
            {
                "family": family,
                "family_label": _family_label(family),
                "runs": data["runs"],
                "keep": data["keep"],
                "discard": data["discard"],
                "review": data["review"],
                "avg_candidate_score": avg_score,
                "latest_run": data["latest"] or "-",
            }
        )
    result.sort(key=lambda row: (row["runs"], row["avg_candidate_score"]), reverse=True)
    return result


def _reason_rows(rows):
    decision_counter = Counter()
    validator_counter = Counter()
    failed_field_counter = Counter()

    for row in rows:
        decision_reason = str((row.get("decision") or {}).get("reason") or "").strip()
        if decision_reason:
            decision_counter[decision_reason] += 1

        summary = row.get("candidate_summary") or {}
        for item in summary.get("top_validator_reasons") or []:
            reason = str(item.get("reason") or "").strip()
            count = int(item.get("count") or 0)
            if reason and count > 0:
                validator_counter[reason] += count

        for item in summary.get("top_failed_fields") or []:
            field = str(item.get("field") or "").strip()
            count = int(item.get("count") or 0)
            if field and count > 0:
                failed_field_counter[field] += count

    decision_rows = [{"reason": key, "count": val} for key, val in decision_counter.most_common(8)]
    validator_rows = [{"reason": key, "count": val} for key, val in validator_counter.most_common(8)]
    failed_field_rows = [{"field": key, "count": val} for key, val in failed_field_counter.most_common(8)]
    return decision_rows, validator_rows, failed_field_rows


def _badge(label, tone):
    return f'<span class="badge {html.escape(tone)}">{html.escape(label)}</span>'


def _decision_badge(decision):
    label, tone = DECISION_LABELS.get(str(decision or "unknown"), DECISION_LABELS["unknown"])
    return _badge(label, tone)


def _status_badge(status):
    label, tone = STATUS_LABELS.get(str(status or "pending"), STATUS_LABELS["pending"])
    return _badge(label, tone)


def _family_stats_html(rows):
    if not rows:
        return "<tr><td colspan='7' class='muted'>暂无实验记录</td></tr>"
    html_rows = []
    for row in rows:
        html_rows.append(
            "<tr>"
            f"<td>{html.escape(row['family_label'])}</td>"
            f"<td>{row['runs']}</td>"
            f"<td>{row['keep']}</td>"
            f"<td>{row['discard']}</td>"
            f"<td>{row['review']}</td>"
            f"<td>{row['avg_candidate_score']:.4f}</td>"
            f"<td>{html.escape(str(row['latest_run']))}</td>"
            "</tr>"
        )
    return "".join(html_rows)


def _simple_reason_table(rows, key_name="reason", empty_text="暂无数据"):
    if not rows:
        return f"<tr><td colspan='2' class='muted'>{html.escape(empty_text)}</td></tr>"
    html_rows = []
    for row in rows:
        key = str(row.get(key_name) or "-")
        count = int(row.get("count") or 0)
        html_rows.append(
            "<tr>"
            f"<td>{html.escape(key)}</td>"
            f"<td>{count}</td>"
            "</tr>"
        )
    return "".join(html_rows)


def _recent_runs_html(rows, experiment_map, family_meta):
    if not rows:
        return '<div class="empty">暂无实验记录。<br/>请先运行 <code>scripts/run_autoresearch_replay.py</code> 生成首条 run。</div>'

    def _sort_key(row):
        return str(row.get("finished_at") or row.get("started_at") or "")

    html_rows = []
    for row in sorted(rows, key=_sort_key, reverse=True)[:50]:
        experiment_id = str(row.get("experiment_id") or "").strip()
        experiment = experiment_map.get(experiment_id, {})
        family = str(row.get("family") or experiment.get("family") or "").strip()
        family_label = _family_label(family)
        decision = row.get("decision") or {}
        baseline_score = _safe_float(decision.get("baseline_score")) if decision else None
        candidate_score = _safe_float(decision.get("candidate_score")) if decision else None
        score_delta = _safe_float(decision.get("score_delta")) if decision else None
        reason = str(decision.get("reason") or "").strip()
        if not reason:
            summary = row.get("candidate_summary") or {}
            top_validator_reasons = summary.get("top_validator_reasons") or []
            if top_validator_reasons:
                reason = str(top_validator_reasons[0].get("reason") or "").strip()
        finished_at = str(row.get("finished_at") or row.get("started_at") or "-")
        status = str(row.get("status") or "pending")

        html_rows.append(
            "<tr>"
            f"<td>{html.escape(finished_at)}</td>"
            f"<td>{html.escape(family_label)}</td>"
            f"<td><code>{html.escape(experiment_id or '-')}</code></td>"
            f"<td>{_decision_badge(decision.get('decision'))}</td>"
            f"<td>{('-' if baseline_score is None else f'{baseline_score:.4f}')}</td>"
            f"<td>{('-' if candidate_score is None else f'{candidate_score:.4f}')}</td>"
            f"<td>{('-' if score_delta is None else f'{score_delta:.4f}')}</td>"
            f"<td>{html.escape(reason or '-')}</td>"
            f"<td>{_status_badge(status)}</td>"
            "</tr>"
        )
    return (
        '<table><thead><tr>'
        '<th>时间</th><th>Family</th><th>Experiment ID</th><th>Decision</th>'
        '<th>Baseline 分</th><th>Candidate 分</th><th>Delta</th><th>主原因</th><th>状态</th>'
        '</tr></thead><tbody>'
        + "".join(html_rows)
        + '</tbody></table>'
    )


def build_admin_autoresearch_page(config):
    template_path = os.path.join(config.templates_dir, "admin_autoresearch.html")
    output_path = os.path.join(config.static_dir, "cn", "admin", "autoresearch", "index.html")
    runs = _read_jsonl(RUNS_PATH)
    experiment_map = _build_experiment_map()
    family_meta = _build_family_meta(config)

    summary = _summary_cards(runs)
    family_rows = _family_rows(runs)
    decision_reason_rows, validator_reason_rows, failed_field_rows = _reason_rows(runs)

    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    html_out = (
        template.replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{TOTAL_RUNS}}", str(summary["total_runs"]))
        .replace("{{RECENT_7D_RUNS}}", str(summary["recent_7d_runs"]))
        .replace("{{KEEP_COUNT}}", str(summary["keep_count"]))
        .replace("{{DISCARD_COUNT}}", str(summary["discard_count"]))
        .replace("{{REVIEW_COUNT}}", str(summary["review_count"]))
        .replace("{{BEST_FAMILY}}", html.escape(summary["best_family_label"]))
        .replace("{{FAMILY_ROWS}}", _family_stats_html(family_rows))
        .replace("{{DECISION_REASON_ROWS}}", _simple_reason_table(decision_reason_rows, key_name="reason", empty_text="暂无决策原因统计"))
        .replace("{{VALIDATOR_REASON_ROWS}}", _simple_reason_table(validator_reason_rows, key_name="reason", empty_text="暂无质量失败原因统计"))
        .replace("{{FAILED_FIELD_ROWS}}", _simple_reason_table(failed_field_rows, key_name="field", empty_text="暂无 failed_field 统计"))
        .replace("{{RECENT_RUNS_TABLE}}", _recent_runs_html(runs, experiment_map, family_meta))
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    return output_path


def main():
    config = Config.from_env()
    output_path = build_admin_autoresearch_page(config)
    print(json.dumps({"ok": True, "output_path": output_path, "updated_at": isoformat(now_cst())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
