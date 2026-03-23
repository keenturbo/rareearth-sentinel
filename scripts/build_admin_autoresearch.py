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
HOMEPAGE_COMPARE_EXPERIMENTS = [
    "exp-homepage-provider-v1",
    "exp-homepage-evidence-v1",
]
HOMEPAGE_COMPARE_LIMIT = 5

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



def _sort_run_rows(rows):
    return sorted(rows, key=lambda row: _parse_dt(row.get("finished_at") or row.get("started_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)



def _build_homepage_recent_decisions(rows, limit=HOMEPAGE_COMPARE_LIMIT):
    grouped = defaultdict(list)
    for row in rows:
        experiment_id = str(row.get("experiment_id") or "").strip()
        family = str(row.get("family") or "").strip()
        if family != "homepage_topic_policy":
            continue
        if experiment_id not in HOMEPAGE_COMPARE_EXPERIMENTS:
            continue
        grouped[experiment_id].append(row)

    out = {}
    for experiment_id in HOMEPAGE_COMPARE_EXPERIMENTS:
        recent = _sort_run_rows(grouped.get(experiment_id) or [])[:limit]
        decisions = []
        for row in recent:
            decision = str((row.get("decision") or {}).get("decision") or "").strip()
            if decision:
                decisions.append(decision)
        out[experiment_id] = decisions
    return out



def _build_homepage_experiment_summary(rows, limit=HOMEPAGE_COMPARE_LIMIT):
    grouped = defaultdict(list)
    for row in rows:
        experiment_id = str(row.get("experiment_id") or "").strip()
        family = str(row.get("family") or "").strip()
        if family != "homepage_topic_policy":
            continue
        if experiment_id not in HOMEPAGE_COMPARE_EXPERIMENTS:
            continue
        grouped[experiment_id].append(row)

    result = []
    for experiment_id in HOMEPAGE_COMPARE_EXPERIMENTS:
        recent = _sort_run_rows(grouped.get(experiment_id) or [])[:limit]
        if not recent:
            continue
        keep_count = 0
        review_count = 0
        scores = []
        for row in recent:
            decision = str((row.get("decision") or {}).get("decision") or "").strip()
            if decision == "keep":
                keep_count += 1
            elif decision == "needs_review":
                review_count += 1
            summary = row.get("candidate_summary") or {}
            score_val = summary.get("composite_score")
            if score_val is not None:
                scores.append(_safe_float(score_val))
        latest = recent[0]
        latest_decision = str((latest.get("decision") or {}).get("decision") or "").strip()
        latest_status = str(latest.get("status") or "").strip() or "pending"
        latest_result_kind = "decision" if latest_decision else "status"
        result.append(
            {
                "experiment_id": experiment_id,
                "recent_runs": len(recent),
                "keep_count": keep_count,
                "needs_review_count": review_count,
                "avg_candidate_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
                "latest_decision": latest_decision,
                "latest_status": latest_status,
                "latest_result_kind": latest_result_kind,
                "latest_finished_at": str(latest.get("finished_at") or latest.get("started_at") or "-"),
            }
        )
    return result



def _homepage_experiments_html(rows):
    if not rows:
        return "<tr><td colspan='7' class='muted'>暂无 homepage experiment 对比数据</td></tr>"
    html_rows = []
    for row in rows:
        latest_badge = _decision_badge(row.get("latest_decision")) if row.get("latest_result_kind") == "decision" else _status_badge(row.get("latest_status"))
        html_rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(row['experiment_id']))}</code></td>"
            f"<td>{int(row['recent_runs'])}</td>"
            f"<td>{int(row['keep_count'])}</td>"
            f"<td>{int(row['needs_review_count'])}</td>"
            f"<td>{row['avg_candidate_score']:.4f}</td>"
            f"<td>{latest_badge}</td>"
            f"<td>{html.escape(str(row['latest_finished_at']))}</td>"
            "</tr>"
        )
    return "".join(html_rows)



def _stability_latest_ts(row):
    dt = _parse_dt(row.get("latest_finished_at"))
    return dt.timestamp() if dt else 0.0



def _rows_are_close(left, right):
    if not left or not right:
        return False
    return (
        int(left.get("needs_review_count") or 0) == int(right.get("needs_review_count") or 0)
        and int(left.get("keep_count") or 0) == int(right.get("keep_count") or 0)
        and round(_safe_float(left.get("avg_candidate_score")), 4) == round(_safe_float(right.get("avg_candidate_score")), 4)
        and str(left.get("latest_decision") or "") == str(right.get("latest_decision") or "")
    )



def _build_homepage_stability_hints(rows):
    if not rows:
        return ["暂无足够的 homepage 实验数据，暂不能生成稳定性提示。"]
    if len(rows) == 1:
        row = rows[0]
        return [
            f"当前活跃实验：{row['experiment_id']}（最近 {int(row['recent_runs'])} 次，KEEP {int(row['keep_count'])}）"
        ]
    if len(rows) == 2 and _rows_are_close(rows[0], rows[1]):
        return ["当前两条 homepage 正式轨道表现接近，建议继续积累样本后再判断。"]

    stable = min(
        rows,
        key=lambda row: (
            int(row.get("needs_review_count") or 0),
            -int(row.get("keep_count") or 0),
            -_safe_float(row.get("avg_candidate_score")),
            -_stability_latest_ts(row),
        ),
    )
    observe = max(
        rows,
        key=lambda row: (
            int(row.get("needs_review_count") or 0),
            -_safe_float(row.get("avg_candidate_score")),
            1 if str(row.get("latest_decision") or "") != "keep" else 0,
            _stability_latest_ts(row),
        ),
    )

    hints = []
    stable_recent = int(stable.get("recent_runs") or 0)
    stable_keep = int(stable.get("keep_count") or 0)
    stable_review = int(stable.get("needs_review_count") or 0)
    stable_avg = _safe_float(stable.get("avg_candidate_score"))
    if stable_recent > 0 and stable_keep == stable_recent and stable_review == 0:
        hints.append(f"当前更稳：{stable['experiment_id']}（最近 {stable_recent} 次均为 KEEP，平均分 {stable_avg:.4f}）")
    else:
        hints.append(f"当前更稳：{stable['experiment_id']}（最近 {stable_recent} 次中 KEEP {stable_keep}，平均分 {stable_avg:.4f}）")

    if observe.get("experiment_id") != stable.get("experiment_id"):
        observe_recent = int(observe.get("recent_runs") or 0)
        observe_review = int(observe.get("needs_review_count") or 0)
        observe_avg = _safe_float(observe.get("avg_candidate_score"))
        latest_decision = str(observe.get("latest_decision") or "").strip()
        if observe_review > 0:
            hints.append(f"需继续观察：{observe['experiment_id']}（最近 {observe_recent} 次含 {observe_review} 次 REVIEW，平均分 {observe_avg:.4f}）")
        elif latest_decision and latest_decision != "keep":
            hints.append(f"需继续观察：{observe['experiment_id']}（最新结果为 {latest_decision.upper()}，平均分 {observe_avg:.4f}）")
    return hints[:2]



def _homepage_stability_hints_html(rows):
    hints = _build_homepage_stability_hints(rows)
    if not hints:
        hints = ["暂无足够的 homepage 实验数据，暂不能生成稳定性提示。"]
    return "".join(f"<li>{html.escape(item)}</li>" for item in hints)



def _build_homepage_trend_hints(compare_rows, recent_decisions_map):
    if not compare_rows:
        return ["暂无足够的 homepage 实验数据，暂不能生成趋势提示。"]

    hints = []
    for row in compare_rows:
        experiment_id = str(row.get("experiment_id") or "").strip()
        decisions = list(recent_decisions_map.get(experiment_id) or [])
        recent_runs = int(row.get("recent_runs") or 0)
        if recent_runs < 2 or len(decisions) < 2:
            hints.append(f"{experiment_id}：样本仍少，趋势判断暂不稳定")
            continue

        latest = decisions[0]
        if latest == "needs_review":
            hints.append(f"{experiment_id}：最近一次为 REVIEW，短期仍需观察")
            continue

        if latest == "keep" and "needs_review" in decisions[1:]:
            hints.append(f"{experiment_id}：近 {len(decisions)} 次中曾出现 REVIEW，当前已恢复 KEEP")
            continue

        streak = 0
        for decision in decisions:
            if decision == "keep":
                streak += 1
            else:
                break
        if streak >= 2:
            hints.append(f"{experiment_id}：最近 {streak} 次连续 KEEP")
            continue

        hints.append(f"{experiment_id}：近期表现平稳，建议继续积累样本")

    return hints[:2] if hints else ["暂无足够的 homepage 实验数据，暂不能生成趋势提示。"]



def _homepage_trend_hints_html(compare_rows, recent_decisions_map):
    hints = _build_homepage_trend_hints(compare_rows, recent_decisions_map)
    return "".join(f"<li>{html.escape(item)}</li>" for item in hints)



def _short_experiment_id(experiment_id):
    text = str(experiment_id or "").strip()
    if text.startswith("exp-homepage-"):
        return text.replace("exp-homepage-", "", 1)
    return text or "homepage"



def _pick_stable_row(rows):
    if not rows:
        return None
    return min(
        rows,
        key=lambda row: (
            int(row.get("needs_review_count") or 0),
            -int(row.get("keep_count") or 0),
            -_safe_float(row.get("avg_candidate_score")),
            -_stability_latest_ts(row),
        ),
    )



def _build_homepage_summary_hint(compare_rows, recent_decisions_map):
    if not compare_rows:
        return "当前样本仍少，homepage 实验结论暂不稳定。"
    if len(compare_rows) == 1:
        return "当前样本仍少，homepage 实验结论暂不稳定。"
    if len(compare_rows) >= 2 and _rows_are_close(compare_rows[0], compare_rows[1]):
        return "当前两条 homepage 正式轨道表现接近，建议继续积累样本。"

    stable = _pick_stable_row(compare_rows)
    if not stable:
        return "当前样本仍少，homepage 实验结论暂不稳定。"

    others = [row for row in compare_rows if str(row.get("experiment_id") or "") != str(stable.get("experiment_id") or "")]
    if not others:
        return f"当前 {_short_experiment_id(stable.get('experiment_id'))} 表现更稳，建议继续积累样本。"

    other = others[0]
    stable_name = _short_experiment_id(stable.get("experiment_id"))
    other_name = _short_experiment_id(other.get("experiment_id"))
    other_decisions = list(recent_decisions_map.get(str(other.get("experiment_id") or "")) or [])
    other_recent = int(other.get("recent_runs") or 0)

    if other_recent < 2 or len(other_decisions) < 2:
        return f"当前 {stable_name} 更稳，{other_name} 样本仍少，建议继续积累。"

    latest = other_decisions[0]
    if latest == "needs_review":
        return f"当前 {stable_name} 更稳，{other_name} 短期仍需观察。"
    if latest == "keep" and "needs_review" in other_decisions[1:]:
        return f"当前 {stable_name} 更稳，{other_name} 已从 REVIEW 恢复为 KEEP。"

    streak = 0
    for decision in other_decisions:
        if decision == "keep":
            streak += 1
        else:
            break
    if streak >= 2:
        return f"当前 {stable_name} 更稳，{other_name} 近期也保持稳定。"

    return f"当前 {stable_name} 更稳，{other_name} 建议继续积累样本。"



def _build_homepage_recommend_hint(compare_rows, recent_decisions_map):
    if not compare_rows:
        return "当前样本仍少，建议先继续积累实验结果。"
    if len(compare_rows) == 1:
        return "当前样本仍少，建议先继续积累实验结果。"
    if len(compare_rows) >= 2 and _rows_are_close(compare_rows[0], compare_rows[1]):
        return "当前两条轨道表现接近，建议先继续积累样本。"

    stable = _pick_stable_row(compare_rows)
    if not stable:
        return "当前样本仍少，建议先继续积累实验结果。"

    stable_name = _short_experiment_id(stable.get("experiment_id"))
    others = [row for row in compare_rows if str(row.get("experiment_id") or "") != str(stable.get("experiment_id") or "")]
    if not others:
        return f"本轮可优先关注 {stable_name}，其余实验结果仍需继续积累。"

    other = others[0]
    other_name = _short_experiment_id(other.get("experiment_id"))
    other_decisions = list(recent_decisions_map.get(str(other.get("experiment_id") or "")) or [])
    other_recent = int(other.get("recent_runs") or 0)

    if other_recent < 2 or len(other_decisions) < 2:
        return f"本轮可优先关注 {stable_name}，{other_name} 先继续积累样本。"

    latest = other_decisions[0]
    if latest == "needs_review":
        return f"本轮可优先关注 {stable_name}，{other_name} 暂继续观察。"
    if latest == "keep" and "needs_review" in other_decisions[1:]:
        return f"本轮可优先关注 {stable_name}，{other_name} 已恢复稳定，可继续跟踪。"

    streak = 0
    for decision in other_decisions:
        if decision == "keep":
            streak += 1
        else:
            break
    if streak >= 2:
        return f"本轮可优先关注 {stable_name}，{other_name} 继续跟踪即可。"

    return f"本轮可优先关注 {stable_name}，{other_name} 建议继续积累样本。"



def _recent_runs_html(rows, experiment_map, family_meta):
    if not rows:
        return '<div class="empty">暂无实验记录。<br/>请先运行 <code>scripts/run_autoresearch_replay.py</code> 生成首条 run。</div>'

    html_rows = []
    for row in _sort_run_rows(rows)[:50]:
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
    homepage_experiment_rows = _build_homepage_experiment_summary(runs)
    homepage_recent_decisions = _build_homepage_recent_decisions(runs)
    homepage_summary_hint = _build_homepage_summary_hint(homepage_experiment_rows, homepage_recent_decisions)
    homepage_recommend_hint = _build_homepage_recommend_hint(homepage_experiment_rows, homepage_recent_decisions)
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
        .replace("{{HOMEPAGE_RECOMMEND_HINT}}", html.escape(homepage_recommend_hint))
        .replace("{{HOMEPAGE_SUMMARY_HINT}}", html.escape(homepage_summary_hint))
        .replace("{{HOMEPAGE_STABILITY_HINTS}}", _homepage_stability_hints_html(homepage_experiment_rows))
        .replace("{{HOMEPAGE_TREND_HINTS}}", _homepage_trend_hints_html(homepage_experiment_rows, homepage_recent_decisions))
        .replace("{{HOMEPAGE_EXPERIMENT_ROWS}}", _homepage_experiments_html(homepage_experiment_rows))
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
