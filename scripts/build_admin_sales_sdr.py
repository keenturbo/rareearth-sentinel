#!/usr/bin/env python3
import html
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SALES_DIR = os.path.join(ROOT, "scripts", "sales")
if SALES_DIR not in sys.path:
    sys.path.insert(0, SALES_DIR)

from src.config import Config
from src.utils import isoformat, now_cst
from _common import read_jsonl, sales_paths, safe_str

_TASK_LIST_ENDPOINT = "/api/admin/sales/sdr/tasks/list"
_TASK_UPDATE_ENDPOINT = "/api/admin/sales/sdr/tasks/update"

_TASK_STATUS_ORDER = ["draft", "approved", "sent", "failed", "cancelled"]


def _load_snapshot(path: str):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _render_lead_rows(rows):
    if not rows:
        return '<tr><td colspan="6" class="muted">暂无线索</td></tr>'
    out = []
    rows = sorted(rows, key=lambda x: float(x.get("score") or 0), reverse=True)[:20]
    for r in rows:
        out.append(
            "<tr>"
            f"<td>{html.escape(safe_str(r.get('id')))}</td>"
            f"<td>{html.escape(safe_str(r.get('display_name') or r.get('name')))}</td>"
            f"<td>{html.escape(safe_str(r.get('company')))}</td>"
            f"<td>{html.escape(safe_str(r.get('role')))}</td>"
            f"<td>{float(r.get('score') or 0):.0f}</td>"
            f"<td>{html.escape(safe_str(r.get('status')))}</td>"
            "</tr>"
        )
    return "".join(out)


def _task_status_options(current: str) -> str:
    c = safe_str(current).lower() or "draft"
    labels = {
        "draft": "draft",
        "approved": "approved",
        "sent": "sent",
        "failed": "failed",
        "cancelled": "cancelled",
    }
    out = []
    for st in _TASK_STATUS_ORDER:
        sel = " selected" if st == c else ""
        out.append(f'<option value="{st}"{sel}>{labels[st]}</option>')
    return "".join(out)


def _render_task_rows(rows):
    if not rows:
        return '<tr><td colspan="9" class="muted">暂无任务</td></tr>'

    active = [x for x in rows if safe_str(x.get("status")) in {"draft", "approved", "sent", "failed", "cancelled"}]
    active = sorted(active, key=lambda x: safe_str(x.get("created_at")), reverse=True)[:20]
    if not active:
        return '<tr><td colspan="9" class="muted">暂无任务</td></tr>'

    out = []
    for r in active:
        task_id = html.escape(safe_str(r.get("id")))
        status = safe_str(r.get("status")).lower() or "draft"
        out.append(
            f'<tr data-task-id="{task_id}" data-task-status="{html.escape(status)}">'
            f"<td>{html.escape(safe_str(r.get('created_at')))}</td>"
            f"<td>{task_id}</td>"
            f"<td>{html.escape(safe_str(r.get('lead_id')))}</td>"
            f"<td>{html.escape(safe_str(r.get('channel')))}</td>"
            f"<td>{html.escape(safe_str(r.get('step')))}</td>"
            f"<td>{html.escape(safe_str(r.get('scheduled_at')))}</td>"
            f"<td>{html.escape(safe_str(r.get('template_version')))}</td>"
            f"<td><select class='task-status-select'>{_task_status_options(status)}</select></td>"
            "<td><button class='task-save-btn'>保存</button> <span class='task-save-msg muted'></span></td>"
            "</tr>"
        )
    return "".join(out)


def _render_reply_rows(rows):
    if not rows:
        return '<tr><td colspan="5" class="muted">暂无回复记录</td></tr>'
    out = []
    rows = sorted(rows, key=lambda x: safe_str(x.get("created_at")), reverse=True)[:20]
    for r in rows:
        out.append(
            "<tr>"
            f"<td>{html.escape(safe_str(r.get('created_at')))}</td>"
            f"<td>{html.escape(safe_str(r.get('lead_id')))}</td>"
            f"<td>{html.escape(safe_str(r.get('channel')))}</td>"
            f"<td>{html.escape(safe_str(r.get('reply_label')))}</td>"
            f"<td>{html.escape(safe_str(r.get('reply_text')))}</td>"
            "</tr>"
        )
    return "".join(out)


def _render_warnings(warnings):
    if not warnings:
        return '<div class="ok">状态正常：暂无异常提醒</div>'
    return "".join([f'<div class="warn">{html.escape(str(w))}</div>' for w in warnings])


def build_page(config: Config):
    paths = sales_paths(config)
    tpl_path = os.path.join(ROOT, "templates", "admin_sales_sdr.html")
    out_path = os.path.join(config.static_dir, "cn", "admin", "sales", "sdr", "index.html")

    snapshot = _load_snapshot(paths["snapshot"])
    leads = read_jsonl(paths["leads"])
    tasks = read_jsonl(paths["tasks"])
    replies = read_jsonl(paths["replies"])

    with open(tpl_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    quality = snapshot.get("quality_flags") or {}
    leads_cnt = snapshot.get("leads") or {}
    tasks_cnt = snapshot.get("tasks") or {}
    task_funnel = snapshot.get("task_funnel") or {}
    replies_cnt = snapshot.get("replies") or {}
    funnel = snapshot.get("funnel") or {}

    html_out = (
        tpl.replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{SNAPSHOT_AT}}", html.escape(safe_str(snapshot.get("generated_at") or "-")))
        .replace("{{LEAD_TOTAL}}", str(int(leads_cnt.get("total") or 0)))
        .replace("{{LEAD_NEW}}", str(int(leads_cnt.get("new") or 0)))
        .replace("{{LEAD_QUALIFIED}}", str(int(leads_cnt.get("qualified") or 0)))
        .replace("{{LEAD_PROPOSAL}}", str(int(leads_cnt.get("proposal_sent") or 0)))
        .replace("{{LEAD_WON}}", str(int(leads_cnt.get("won") or 0)))
        .replace("{{TASK_DRAFT}}", str(int(tasks_cnt.get("draft") or 0)))
        .replace("{{TASK_APPROVED}}", str(int(tasks_cnt.get("approved") or 0)))
        .replace("{{TASK_SENT}}", str(int(tasks_cnt.get("sent") or 0)))
        .replace("{{TASK_CANCELLED}}", str(int(tasks_cnt.get("cancelled") or 0)))
        .replace("{{TASK_FUNNEL_APPROVAL}}", html.escape(safe_str(task_funnel.get("approval_rate") or "0%")))
        .replace("{{TASK_FUNNEL_SEND}}", html.escape(safe_str(task_funnel.get("send_rate") or "0%")))
        .replace("{{REPLY_INTERESTED}}", str(int(replies_cnt.get("interested") or 0)))
        .replace("{{REPLY_MAYBE}}", str(int(replies_cnt.get("maybe") or 0)))
        .replace("{{REPLY_NO_FIT}}", str(int(replies_cnt.get("no_fit") or 0)))
        .replace("{{FUNNEL_QUALIFICATION}}", html.escape(safe_str(funnel.get("qualification_rate") or "0%")))
        .replace("{{FUNNEL_PROPOSAL}}", html.escape(safe_str(funnel.get("proposal_rate") or "0%")))
        .replace("{{FUNNEL_WIN}}", html.escape(safe_str(funnel.get("win_rate") or "0%")))
        .replace("{{QUALITY_STATUS}}", "PASS" if quality.get("pass") else "DEGRADED")
        .replace("{{WARNING_ROWS}}", _render_warnings(quality.get("warnings") or []))
        .replace("{{TOP_LEAD_ROWS}}", _render_lead_rows(leads))
        .replace("{{DRAFT_TASK_ROWS}}", _render_task_rows(tasks))
        .replace("{{REPLY_ROWS}}", _render_reply_rows(replies))
        .replace("{{TASK_LIST_ENDPOINT}}", _TASK_LIST_ENDPOINT)
        .replace("{{TASK_UPDATE_ENDPOINT}}", _TASK_UPDATE_ENDPOINT)
    )

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    return out_path


def main():
    cfg = Config.from_env()
    out = build_page(cfg)
    print(f"Admin SDR page built: {out}")


if __name__ == "__main__":
    main()
