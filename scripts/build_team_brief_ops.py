#!/usr/bin/env python3
import html
import json
import os
import sys

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
        badge = '<span class="badge ok">通过</span>' if q.get('pass') else '<span class="badge warn">降级</span>'
        out.append(
            "<tr>"
            f"<td>{html.escape(str(r.get('date') or '-'))}</td>"
            f"<td>{int(r.get('master') or 0)}</td>"
            f"<td>{int(r.get('today') or 0)}</td>"
            f"<td>{int(r.get('overdue') or 0)}</td>"
            f"<td>{int(r.get('expiring') or 0)}</td>"
            f"<td>{int(r.get('upsell_due') or 0)}</td>"
            f"<td>{badge}</td>"
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


def _compute_ops_signals(leads_rows, counts: dict, quality_flags: dict, unfulfilled: dict):
    qualified = 0
    proposal_sent = 0
    for r in leads_rows or []:
        st = str((r or {}).get("status") or "").strip().lower()
        if st == "qualified":
            qualified += 1
        elif st == "proposal_sent":
            proposal_sent += 1

    overdue = int(counts.get("overdue") or 0)
    quality_pass = bool((quality_flags or {}).get("pass", True))
    unfulfilled_count = int((unfulfilled or {}).get("count") or 0)
    actionable_unfulfilled_count = int((unfulfilled or {}).get("actionable_count") or 0)
    unfulfilled_threshold = int((unfulfilled or {}).get("threshold_minutes") or 30)

    alerts = []
    if actionable_unfulfilled_count > 0:
        alerts.append(("danger", f"存在已支付未交付订单 {actionable_unfulfilled_count} 单（>{unfulfilled_threshold}分钟，可执行告警），请优先处理。"))
    elif unfulfilled_count > 0:
        alerts.append(("info", f"存在历史未交付记录 {unfulfilled_count} 单（含测试/子单/缺时间），当前可执行告警为 0。"))
    if not quality_pass:
        alerts.append(("warn", "摘要质量降级：建议优先核对今日清单明细后再执行。"))
    if overdue > 0:
        alerts.append(("danger", f"存在逾期线索 {overdue} 条：请优先处理，避免成交窗口流失。"))
    if qualified > 0 and proposal_sent == 0:
        alerts.append(("warn", "当前 qualified 线索尚未发送报价：建议今日先完成报价推进。"))
    elif qualified > 0 and proposal_sent < qualified:
        gap = qualified - proposal_sent
        alerts.append(("info", f"仍有 {gap} 条 qualified 线索未报价：建议补齐报价动作。"))

    if not alerts:
        alerts.append(("ok", "今日无异常提醒，可按执行清单推进。"))

    coverage = "-"
    if qualified > 0:
        coverage = f"{int(round((proposal_sent / qualified) * 100))}%"

    return {
        "qualified": qualified,
        "proposal_sent": proposal_sent,
        "coverage": coverage,
        "alerts": alerts,
        "unfulfilled_count": unfulfilled_count,
        "actionable_unfulfilled_count": actionable_unfulfilled_count,
    }


def _render_alert_rows(alerts):
    out = []
    for level, text in alerts or []:
        cls = "info"
        if level == "danger":
            cls = "danger"
        elif level == "warn":
            cls = "warn"
        elif level == "ok":
            cls = "ok"
        out.append(f'<div class="alert {cls}">{html.escape(str(text or ""))}</div>')
    return "".join(out)


def build_page(config: Config):
    tpl_path = os.path.join(ROOT, "templates", "admin_team_brief_ops.html")
    out_path = os.path.join(config.static_dir, "cn", "admin", "leads", "team-brief", "ops", "index.html")

    digest_path = os.path.join(config.data_dir, "status", "team_brief_daily_digest.json")
    snapshots_path = os.path.join(config.data_dir, "analytics", "sales_daily_snapshots.jsonl")
    leads_path = os.path.join(config.data_dir, "pay", "team_brief_leads.jsonl")
    unfulfilled_path = os.path.join(config.data_dir, "status", "unfulfilled_orders.json")

    digest = _load_json(digest_path)
    counts = digest.get("counts") or {}
    quality_flags = digest.get("quality_flags") or {}
    quality_notes = digest.get("quality_notes") or []
    master_text = str(digest.get("master_text") or "").strip()

    snapshots = _read_jsonl(snapshots_path)
    leads_rows = _read_jsonl(leads_path)
    unfulfilled = _load_json(unfulfilled_path)
    signals = _compute_ops_signals(leads_rows, counts, quality_flags, unfulfilled)

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
        .replace("{{COUNT_QUALIFIED}}", str(int(signals.get("qualified") or 0)))
        .replace("{{COUNT_PROPOSAL_SENT}}", str(int(signals.get("proposal_sent") or 0)))
        .replace("{{QUOTE_COVERAGE}}", html.escape(str(signals.get("coverage") or "-")))
        .replace("{{ALERT_ROWS}}", _render_alert_rows(signals.get("alerts") or []))
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
