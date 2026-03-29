#!/usr/bin/env python3
import html
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import isoformat, now_cst


def _abs_path(rel_path):
    if os.path.isabs(rel_path):
        return rel_path
    return os.path.join(ROOT, rel_path)


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _load_jsonl(path):
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


def _severity_class(severity):
    s = str(severity or "low").lower()
    if s == "high":
        return "high"
    if s == "medium":
        return "medium"
    return "low"


def _fmt_score_bar(score, severity):
    score = int(score or 0)
    cls = _severity_class(severity)
    width = max(1, min(100, score))
    return (
        f'<span class="score-bar">'
        f'<span class="score-fill {cls}" style="width:{width}%"></span>'
        f'</span> {score}'
    )


def _render_status_pills(status):
    if not isinstance(status, dict):
        return '<span class="pill">无状态数据</span>'

    build = str(status.get("build_status") or "unknown")
    build_cls = "ok" if build == "ok" else "degraded"

    pills = [
        f'<span class="pill {build_cls}">{html.escape(build)}</span>',
        f'<span class="pill">watchlist {status.get("watchlist_count", 0)}</span>',
        f'<span class="pill">成功 {status.get("success_count", 0)}</span>',
        f'<span class="pill">失败 {status.get("failed_count", 0)}</span>',
    ]

    high = int(status.get("high_severity_count") or 0)
    medium = int(status.get("medium_severity_count") or 0)
    low = int(status.get("low_severity_count") or 0)
    intel_hit = int(status.get("related_intel_hit_count") or 0)

    if high:
        pills.append(f'<span class="pill high">高 {high}</span>')
    if medium:
        pills.append(f'<span class="pill medium">中 {medium}</span>')
    if low:
        pills.append(f'<span class="pill low">低 {low}</span>')
    if intel_hit:
        pills.append(f'<span class="pill ok">情报关联 {intel_hit}</span>')

    top_tags = status.get("signal_reason_top_tags") or []
    for tag in top_tags[:5]:
        pills.append(f'<span class="pill">{html.escape(str(tag))}</span>')

    return "".join(pills)


def _render_status_notes(status):
    if not isinstance(status, dict):
        return ""
    notes = status.get("notes") or []
    date = status.get("date") or ""
    generated = status.get("generated_at") or ""
    stale = status.get("stale_sources") or []

    parts = []
    if date:
        parts.append(f"日期: {html.escape(date)}")
    if generated:
        parts.append(f"生成: {html.escape(generated)}")
    if stale:
        parts.append(f"陈旧源: {html.escape(', '.join(stale))}")
    if notes:
        parts.append(f"备注: {html.escape(', '.join(str(n) for n in notes[:8]))}")
    return " · ".join(parts)


def _render_signal_rows(signals):
    if not signals:
        return '<tr><td colspan="8" class="empty">暂无市场信号</td></tr>'

    rows = []
    for sig in signals:
        signal_id = html.escape(str(sig.get("signal_id") or ""))
        name = html.escape(str(sig.get("name") or ""))
        category = html.escape(str(sig.get("category") or ""))
        score = int(sig.get("anomaly_score") or 0)
        severity = str(sig.get("severity") or "low")
        sev_cls = _severity_class(severity)
        tags = sig.get("signal_reason_tags") or []
        related = sig.get("related_intel_ids") or []
        observed = html.escape(str(sig.get("observed_at") or "")[:16])

        tags_html = "".join(
            f'<span class="tag">{html.escape(str(t))}</span>' for t in tags[:4]
        )
        related_count = len(related)
        related_text = f"{related_count}条" if related_count else "-"

        rows.append(
            "<tr>"
            f"<td>{signal_id}</td>"
            f"<td>{name}</td>"
            f"<td>{category}</td>"
            f"<td>{_fmt_score_bar(score, severity)}</td>"
            f'<td><span class="pill {sev_cls}">{html.escape(severity)}</span></td>'
            f"<td>{tags_html}</td>"
            f"<td>{related_text}</td>"
            f"<td>{observed}</td>"
            "</tr>"
        )
    return "".join(rows)


def _render_signal_details(signals):
    if not signals:
        return '<div class="empty">暂无信号详情</div>'

    blocks = []
    for sig in signals:
        signal_id = html.escape(str(sig.get("signal_id") or ""))
        name = html.escape(str(sig.get("name") or ""))
        event_id = html.escape(str(sig.get("event_id") or ""))
        category = html.escape(str(sig.get("category") or ""))
        region = html.escape(str(sig.get("region") or ""))
        source_type = html.escape(str(sig.get("source_type") or ""))
        source_ref = html.escape(str(sig.get("source_ref") or ""))
        source_health = html.escape(str(sig.get("source_health") or ""))
        observed = html.escape(str(sig.get("observed_at") or ""))
        created = html.escape(str(sig.get("created_at") or ""))
        score = int(sig.get("anomaly_score") or 0)
        severity = str(sig.get("severity") or "low")
        sev_cls = _severity_class(severity)

        value_num = sig.get("value_num")
        pct_1d = sig.get("pct_change_1d")
        value_text = str(value_num) if value_num is not None else "-"
        pct_text = f"{pct_1d}%" if pct_1d is not None else "-"

        raw_summary = html.escape(str(sig.get("raw_summary") or "")[:200])
        note = html.escape(str(sig.get("note") or ""))

        tags = sig.get("signal_reason_tags") or []
        tags_html = "".join(
            f'<span class="tag {sev_cls}">{html.escape(str(t))}</span>' for t in tags
        )

        related_ids = sig.get("related_intel_ids") or []
        related_topics = sig.get("related_topics") or []
        pre_flag = "是" if sig.get("pre_event_flag") else "否"
        post_flag = "是" if sig.get("post_event_confirmation_flag") else "否"

        related_html = ""
        if related_ids or related_topics:
            items = []
            for i, rid in enumerate(related_ids[:3]):
                topic = related_topics[i] if i < len(related_topics) else ""
                label = html.escape(topic or rid)
                items.append(f"<li>{label}</li>")
            related_html = (
                '<div class="related-list">'
                f'<div>关联情报（{len(related_ids)}条）· 先于事件: {pre_flag} · 事后确认: {post_flag}</div>'
                f'<ul>{"" .join(items)}</ul>'
                '</div>'
            )

        blocks.append(
            '<div class="card">'
            f'<div class="card-title">{name} <span class="pill {sev_cls}">{html.escape(severity)} {score}</span></div>'
            f'<div class="card-meta">{signal_id} · {event_id} · {category} · {region}</div>'
            f'<div class="card-meta">来源: {source_type} · {source_ref} · 健康: {source_health}</div>'
            f'<div class="card-meta">观测: {observed} · 创建: {created}</div>'
            f'<div class="card-meta">数值: {value_text} · 日变动: {pct_text}</div>'
            f'<div class="card-body">摘要: {raw_summary}</div>'
            f'<div class="card-meta">备注: {note}</div>'
            f'<div>{tags_html}</div>'
            f'{related_html}'
            '</div>'
        )
    return "".join(blocks)


def build_admin_market_signals_page(config):
    data_dir = config.data_dir or "data"
    tpl_path = os.path.join(config.templates_dir, "admin_market_signals.html")
    out_path = os.path.join(
        config.static_dir, "cn", "admin", "market-signals", "index.html"
    )

    signals_path = _abs_path(os.path.join(data_dir, "market", "market_signals.jsonl"))
    status_path = _abs_path(os.path.join(data_dir, "status", "market_signal_status.json"))

    if not os.path.exists(tpl_path):
        raise FileNotFoundError(tpl_path)

    signals = _load_jsonl(signals_path)
    status = _load_json(status_path, {})

    with open(tpl_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    html_out = (
        tpl.replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{STATUS_PILLS}}", _render_status_pills(status))
        .replace("{{STATUS_NOTES}}", _render_status_notes(status))
        .replace("{{SIGNAL_ROWS}}", _render_signal_rows(signals))
        .replace("{{SIGNAL_DETAILS}}", _render_signal_details(signals))
    )

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    return out_path


def main():
    config = Config.from_env()
    out = build_admin_market_signals_page(config)
    print(f"Admin market signals page built: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
