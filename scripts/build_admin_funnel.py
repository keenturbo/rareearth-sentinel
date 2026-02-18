#!/usr/bin/env python3
import html
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import now_cst, isoformat


STAGES = ["view_pricing", "click_pay", "order_created", "order_captured", "unlock_success"]


def _read_events(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
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


def _in_window(rows, days, now_utc):
    start = now_utc - timedelta(days=days)
    out = []
    for row in rows:
        dt = _parse_dt(row.get("created_at"))
        if not dt:
            continue
        if dt >= start:
            out.append(row)
    return out


def _count_stages(rows):
    counts = {k: 0 for k in STAGES}
    other = 0
    for row in rows:
        et = str(row.get("event_type") or "").strip()
        if et in counts:
            counts[et] += 1
        elif et:
            other += 1
    return counts, other


def _ratio(a, b):
    if not b:
        return "0%"
    return f"{(a / b) * 100:.1f}%"


def _render_funnel_rows(counts):
    rows = []
    prev = None
    for key in STAGES:
        val = int(counts.get(key, 0))
        conv = "-" if prev is None else _ratio(val, prev)
        rows.append(
            "<tr>"
            f"<td>{key}</td>"
            f"<td>{val}</td>"
            f"<td>{conv}</td>"
            "</tr>"
        )
        prev = val
    return "".join(rows)


def _group_by(rows, key_name):
    bucket = defaultdict(lambda: {k: 0 for k in STAGES})
    for row in rows:
        key = str(row.get(key_name) or "").strip() or "(empty)"
        et = str(row.get("event_type") or "").strip()
        if et in STAGES:
            bucket[key][et] += 1

    items = []
    for key, stats in bucket.items():
        items.append((key, stats))

    items.sort(
        key=lambda x: (
            int(x[1].get("order_created", 0)),
            int(x[1].get("unlock_success", 0)),
            int(x[1].get("click_pay", 0)),
        ),
        reverse=True,
    )
    return items


def _render_group_rows(items):
    if not items:
        return "<tr><td colspan='7' class='muted'>暂无数据</td></tr>"

    rows = []
    for key, stats in items[:20]:
        key_esc = html.escape(key)
        created = int(stats.get("order_created", 0))
        captured = int(stats.get("order_captured", 0))
        unlock = int(stats.get("unlock_success", 0))
        click = int(stats.get("click_pay", 0))
        view = int(stats.get("view_pricing", 0))
        c1 = _ratio(click, view)
        c2 = _ratio(created, click)
        c3 = _ratio(captured, created)
        c4 = _ratio(unlock, captured)
        rows.append(
            "<tr>"
            f"<td>{key_esc}</td>"
            f"<td>{view}</td>"
            f"<td>{click}</td>"
            f"<td>{created}</td>"
            f"<td>{captured}</td>"
            f"<td>{unlock}</td>"
            f"<td>{c1}/{c2}/{c3}/{c4}</td>"
            "</tr>"
        )
    return "".join(rows)


def _summary_html(counts, other=0):
    return (
        f"<span class='pill'>view {int(counts.get('view_pricing', 0))}</span>"
        f"<span class='pill'>click {int(counts.get('click_pay', 0))}</span>"
        f"<span class='pill'>created {int(counts.get('order_created', 0))}</span>"
        f"<span class='pill'>captured {int(counts.get('order_captured', 0))}</span>"
        f"<span class='pill'>unlock {int(counts.get('unlock_success', 0))}</span>"
        f"<span class='pill muted'>other {int(other)}</span>"
    )


def build_admin_funnel_page(config):
    tpl_path = os.path.join(config.templates_dir, "admin_funnel.html")
    out_path = os.path.join(config.static_dir, "cn", "admin", "funnel", "index.html")
    events_path = os.path.join(config.data_dir, "analytics", "funnel_events.jsonl")

    if not os.path.exists(tpl_path):
        raise FileNotFoundError(tpl_path)

    rows = _read_events(events_path)
    now_utc = datetime.now(timezone.utc)
    rows_7d = _in_window(rows, 7, now_utc)
    rows_30d = _in_window(rows, 30, now_utc)

    cnt_7d, other_7d = _count_stages(rows_7d)
    cnt_30d, other_30d = _count_stages(rows_30d)

    sku_items = _group_by(rows_30d, "sku_id")
    channel_items = _group_by(rows_30d, "channel")

    with open(tpl_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    html_out = (
        tpl.replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{PATH_EVENTS}}", html.escape(events_path))
        .replace("{{TOTAL_EVENTS}}", str(len(rows)))
        .replace("{{SUMMARY_7D}}", _summary_html(cnt_7d, other_7d))
        .replace("{{SUMMARY_30D}}", _summary_html(cnt_30d, other_30d))
        .replace("{{FUNNEL_ROWS_7D}}", _render_funnel_rows(cnt_7d))
        .replace("{{FUNNEL_ROWS_30D}}", _render_funnel_rows(cnt_30d))
        .replace("{{SKU_ROWS}}", _render_group_rows(sku_items))
        .replace("{{CHANNEL_ROWS}}", _render_group_rows(channel_items))
    )

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    return out_path


def main():
    config = Config.from_env()
    out = build_admin_funnel_page(config)
    print(f"Admin funnel page built: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
