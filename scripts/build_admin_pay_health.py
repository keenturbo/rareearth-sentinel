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


def _render_actionable_rows(items):
    if not items:
        return '<tr><td colspan="6" class="muted">暂无可执行异常单</td></tr>'
    out = []
    for x in items[:200]:
        out.append(
            "<tr>"
            f"<td>{html.escape(str(x.get('order_id') or '-'))}</td>"
            f"<td>{html.escape(str(x.get('product_type') or '-'))}</td>"
            f"<td>{html.escape(str(x.get('status') or '-'))}</td>"
            f"<td>{html.escape(str(x.get('created_at') or '-'))}</td>"
            f"<td>{html.escape(str(x.get('age_minutes') if x.get('age_minutes') is not None else '-'))}</td>"
            f"<td>{html.escape(str(x.get('reason') or '-'))}</td>"
            "</tr>"
        )
    return "".join(out)


def _today_selfcheck_status(today_check: dict):
    if not today_check:
        return "缺失", "warn"
    ok = bool(today_check.get("pass", False))
    if ok:
        return "通过", "ok"
    return "失败", "danger"


def _alert(actionable_count: int, raw_count: int, today_ok: bool):
    if actionable_count > 0:
        return (
            f"存在可执行支付告警 {actionable_count} 单，请优先处理（已支付未交付）。",
            "danger",
        )
    if not today_ok:
        return (
            "today 页自检未通过，请先修复后再执行运营动作。",
            "warn",
        )
    if raw_count > 0:
        return (
            f"原始未交付记录 {raw_count} 单（含历史噪声），当前可执行告警为 0。",
            "ok",
        )
    return ("支付健康正常，未发现可执行未交付异常。", "ok")


def build_page(config: Config):
    tpl_path = os.path.join(ROOT, "templates", "admin_pay_health.html")
    out_path = os.path.join(config.static_dir, "cn", "admin", "pay", "health", "index.html")

    unfulfilled_path = os.path.join(config.data_dir, "status", "unfulfilled_orders.json")
    today_check_path = os.path.join(config.data_dir, "status", "team_brief_today_selfcheck.json")
    events_path = os.path.join(config.data_dir, "pay", "team_brief_lead_events.jsonl")

    unfulfilled = _load_json(unfulfilled_path)
    today_check = _load_json(today_check_path)

    raw_count = int(unfulfilled.get("count") or 0)
    actionable_count = int(unfulfilled.get("actionable_count") or 0)
    threshold_minutes = int(unfulfilled.get("threshold_minutes") or 30)
    legacy_before = html.escape(str(unfulfilled.get("legacy_untyped_before") or "-"))
    noise_breakdown = unfulfilled.get("noise_breakdown") or {}
    actionable_items = unfulfilled.get("actionable_items") or []

    today_status_text, today_status_class = _today_selfcheck_status(today_check)
    today_ok = today_status_text == "通过"
    alert_text, alert_level = _alert(actionable_count, raw_count, today_ok)

    lead_events = _read_jsonl(events_path)
    proposal_events = sum(1 for e in lead_events if str(e.get("event_type") or "") in {"proposal_created", "proposal_sent"})

    with open(tpl_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    html_out = (
        tpl.replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{ALERT_TEXT}}", html.escape(alert_text))
        .replace("{{ALERT_LEVEL}}", alert_level)
        .replace("{{RAW_COUNT}}", str(raw_count))
        .replace("{{ACTIONABLE_COUNT}}", str(actionable_count))
        .replace("{{THRESHOLD_MINUTES}}", str(threshold_minutes))
        .replace("{{LEGACY_BEFORE}}", legacy_before)
        .replace("{{TODAY_CHECK_STATUS}}", today_status_text)
        .replace("{{TODAY_CHECK_CLASS}}", today_status_class)
        .replace("{{NOISE_BREAKDOWN_JSON}}", html.escape(json.dumps(noise_breakdown, ensure_ascii=False, indent=2)))
        .replace("{{ACTIONABLE_ROWS}}", _render_actionable_rows(actionable_items))
        .replace("{{RAW_CLASS}}", "warn" if raw_count > 0 else "ok")
        .replace("{{ACT_CLASS}}", "danger" if actionable_count > 0 else "ok")
    )

    # Include a lightweight hint based on events presence (non-blocking)
    hint = f"（近线索事件数：{proposal_events}）"
    html_out = html_out.replace("管理员 · 支付健康审计", f"管理员 · 支付健康审计{hint}", 1)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    return out_path


if __name__ == "__main__":
    cfg = Config.from_env()
    out = build_page(cfg)
    print(f"Admin pay health page built: {out}")
