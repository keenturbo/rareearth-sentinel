#!/usr/bin/env python3
import html
import json
import os
import sys
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import isoformat, now_cst


def _load_json(path: str):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_jsonl(path: str):
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
            except Exception:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _render_snapshot_rows(rows):
    if not rows:
        return '<tr><td colspan="7" class="muted">暂无快照数据</td></tr>'

    rows_sorted = sorted(rows, key=lambda x: str(x.get("date") or ""), reverse=True)[:7]
    out = []
    for r in rows_sorted:
        q = r.get("quality_flags") or {}
        out.append(
            "<tr>"
            f"<td>{html.escape(str(r.get('date') or '-'))}</td>"
            f"<td>{int(r.get('master') or 0)}</td>"
            f"<td>{int(r.get('today') or 0)}</td>"
            f"<td>{int(r.get('overdue') or 0)}</td>"
            f"<td>{int(r.get('expiring') or 0)}</td>"
            f"<td>{int(r.get('upsell_due') or 0)}</td>"
            f"<td>{'通过' if q.get('pass') else '降级'}</td>"
            "</tr>"
        )
    return "".join(out)


def _build_exec_text(counts: dict, quality_flags: dict):
    high = int(counts.get("high_priority") or 0)
    today = int(counts.get("today") or 0)
    overdue = int(counts.get("overdue") or 0)
    expiring = int(counts.get("expiring") or 0)
    upsell = int(counts.get("upsell_due") or 0)

    lines = ["【今日执行指令】"]
    lines.append(f"1) 今日待跟进 {today} 条；已逾期 {overdue} 条；3天内到期 {expiring} 条。")

    if overdue > 0:
        lines.append("2) 优先处理逾期线索：先联系已评估/已报价客户，避免窗口流失。")
    elif today > 0:
        lines.append("2) 先处理今日待跟进清单，确保 24 小时内完成触达。")
    else:
        lines.append("2) 今日无强制跟进行动，建议补充高潜在客户触达。")

    if expiring > 0:
        lines.append("3) 对报价将到期客户执行二次确认，必要时给出范围缩减版方案。")
    if high > 0:
        lines.append("4) qualified 高优先线索优先转报价，避免卡在评估状态。")
    if upsell > 0:
        lines.append("5) 对复购到期客户发送 Task Sprint 复购话术。")

    if not quality_flags.get("pass", True):
        lines.append("6) 今日摘要为降级生成，请结合明细列表复核关键线索。")

    return "\n".join(lines)


def build_page(config: Config):
    tpl_path = os.path.join(ROOT, "templates", "admin_team_brief_ops.html")
    out_path = os.path.join(config.static_dir, "cn", "admin", "leads", "team-brief", "ops", "index.html")

    digest_path = os.path.join(config.data_dir, "status", "team_brief_daily_digest.json")
    snapshots_path = os.path.join(config.data_dir, "analytics", "sales_daily_snapshots.jsonl")

    digest = _load_json(digest_path)
    counts = digest.get("counts") or {}
    quality_flags = digest.get("quality_flags") or {}
    quality_notes = digest.get("quality_notes") or []
    master_text = str(digest.get("master_text") or "").strip()

    snapshots = _read_jsonl(snapshots_path)

    if not master_text:
        master_text = "【团队简报每日执行摘要】\n今日无待办"

    exec_text = _build_exec_text(counts, quality_flags)

    with open(tpl_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    html_out = (
        tpl.replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{DIGEST_GENERATED_AT}}", html.escape(str(digest.get("generated_at") or "暂无")))
        .replace("{{COUNT_MASTER}}", str(int(counts.get("master") or 0)))
        .replace("{{COUNT_HIGH}}", str(int(counts.get("high_priority") or 0)))
        .replace("{{COUNT_TODAY}}", str(int(counts.get("today") or 0)))
        .replace("{{COUNT_OVERDUE}}", str(int(counts.get("overdue") or 0)))
        .replace("{{COUNT_EXPIRING}}", str(int(counts.get("expiring") or 0)))
        .replace("{{COUNT_UPSELL}}", str(int(counts.get("upsell_due") or 0)))
        .replace("{{QUALITY_PASS}}", "通过" if quality_flags.get("pass") else "降级")
        .replace("{{QUALITY_NOTES}}", html.escape("；".join([str(x) for x in quality_notes]) if quality_notes else "-"))
        .replace("{{MASTER_TEXT}}", html.escape(master_text))
        .replace("{{EXEC_TEXT}}", html.escape(exec_text))
        .replace("{{SNAPSHOT_ROWS}}", _render_snapshot_rows(snapshots))
        .replace("{{DIGEST_JSON_TEXT}}", html.escape(json.dumps(digest, ensure_ascii=False, indent=2)))
    )

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    return out_path


if __name__ == "__main__":
    cfg = Config.from_env()
    out = build_page(cfg)
    print(f"Admin team brief ops page built: {out}")
