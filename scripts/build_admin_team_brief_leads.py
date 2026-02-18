#!/usr/bin/env python3
import html
import json
import os
import sys
from collections import Counter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import isoformat, now_cst


_LIST_ENDPOINT = "/api/admin/leads/team-brief/list"
_UPDATE_ENDPOINT = "/api/admin/leads/team-brief/update"
_EXPORT_ENDPOINT = "/api/admin/leads/team-brief/export"
_EVENTS_ENDPOINT = "/api/admin/leads/team-brief/events"
_CLEAR_TEST_ENDPOINT = "/api/admin/leads/team-brief/clear-test"

_STATUS_ORDER = ["new", "contacted", "qualified", "won", "lost"]
_STATUS_LABEL = {
    "new": "新线索",
    "contacted": "已联系",
    "qualified": "已评估",
    "won": "已成交",
    "lost": "已丢失",
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
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return rows


def _status_options(current):
    current = str(current or "new").strip().lower()
    out = []
    for s in _STATUS_ORDER:
        selected = " selected" if s == current else ""
        out.append(f'<option value="{s}"{selected}>{_STATUS_LABEL[s]}</option>')
    return "".join(out)


def _to_dt_local(value):
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("Z", "")
    if len(text) >= 16:
        return text[:16]
    return text


def _render_items(rows):
    if not rows:
        return '<tr><td colspan="10" class="muted">暂无线索</td></tr>'

    out = []
    for r in rows[:300]:
        lead_id = html.escape(str(r.get("lead_id") or ""))
        created_at = html.escape(str(r.get("created_at") or ""))
        name = html.escape(str(r.get("name") or ""))
        email = html.escape(str(r.get("email") or ""))
        company = html.escape(str(r.get("company") or ""))
        role = html.escape(str(r.get("role") or ""))
        need = html.escape(str(r.get("brief_need") or ""))
        budget = html.escape(str(r.get("budget_range") or ""))
        channel = html.escape(str(r.get("source_channel") or ""))
        message = html.escape(str(r.get("message") or ""))
        utm = html.escape(
            " / ".join(
                [
                    str(r.get("utm_source") or ""),
                    str(r.get("utm_campaign") or ""),
                    str(r.get("utm_content") or ""),
                ]
            ).strip(" /")
        )
        status = str(r.get("status") or "new").strip().lower()
        owner = html.escape(str(r.get("owner") or ""))
        next_action = html.escape(_to_dt_local(r.get("next_action_at")))
        is_test = bool(r.get("test"))
        test_badge = "<div class='muted' style='color:#d97706'>test</div>" if is_test else ""

        out.append(
            f'<tr data-lead-id="{lead_id}">'
            f"<td>{created_at}</td>"
            f"<td><div>{lead_id}</div><div class='muted'>{status}</div>{test_badge}</td>"
            f"<td><div>{name}</div><div class='muted'>{email}</div></td>"
            f"<td><div>{company}</div><div class='muted'>{role}</div></td>"
            f"<td><div>{need}</div><div class='muted'>{budget}</div></td>"
            f"<td><div>{channel}</div><div class='muted'>{utm}</div></td>"
            f"<td class='message-cell'>{message}</td>"
            "<td>"
            f"<select class='status-select'>{_status_options(status)}</select>"
            f"<input class='owner-input' type='text' value='{owner}' placeholder='负责人' />"
            f"<input class='next-action-input' type='datetime-local' value='{next_action}' />"
            "</td>"
            "<td>"
            "<textarea class='note-input' rows='2' placeholder='跟进备注（可选）'></textarea>"
            "<div class='action-row'>"
            "<button class='save-btn'>保存</button>"
            "<span class='save-msg muted'></span>"
            "</div>"
            "</td>"
            "<td class='timeline-cell'>"
            "<button class='timeline-btn'>查看时间线</button>"
            "<div class='timeline-msg muted'></div>"
            "</td>"
            "</tr>"
        )
    return "".join(out)


def _counts(rows):
    c = Counter(str(r.get("status") or "new").strip().lower() for r in rows)
    return {
        "new": c.get("new", 0),
        "contacted": c.get("contacted", 0),
        "qualified": c.get("qualified", 0),
        "won": c.get("won", 0),
        "lost": c.get("lost", 0),
    }


def build_page(config):
    leads_path = os.path.join(config.data_dir, "pay", "team_brief_leads.jsonl")
    template_path = os.path.join(ROOT, "templates", "admin_team_brief_leads.html")
    output_path = os.path.join(config.static_dir, "cn", "admin", "leads", "team-brief", "index.html")

    rows = _read_jsonl(leads_path)
    cnt = _counts(rows)

    if not os.path.exists(template_path):
        return
    with open(template_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    html_out = (
        tpl.replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{TOTAL_COUNT}}", str(len(rows)))
        .replace("{{NEW_COUNT}}", str(cnt["new"]))
        .replace("{{CONTACTED_COUNT}}", str(cnt["contacted"]))
        .replace("{{QUALIFIED_COUNT}}", str(cnt["qualified"]))
        .replace("{{WON_COUNT}}", str(cnt["won"]))
        .replace("{{LOST_COUNT}}", str(cnt["lost"]))
        .replace("{{LEADS_PATH}}", html.escape(leads_path))
        .replace("{{LEADS_ROWS}}", _render_items(rows))
        .replace("{{LEADS_LIST_ENDPOINT}}", _LIST_ENDPOINT)
        .replace("{{LEADS_UPDATE_ENDPOINT}}", _UPDATE_ENDPOINT)
        .replace("{{LEADS_EXPORT_ENDPOINT}}", _EXPORT_ENDPOINT)
        .replace("{{LEADS_EVENTS_ENDPOINT}}", _EVENTS_ENDPOINT)
        .replace("{{LEADS_CLEAR_TEST_ENDPOINT}}", _CLEAR_TEST_ENDPOINT)
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)


def main():
    config = Config.from_env()
    build_page(config)
    print("Admin team brief leads page built")


if __name__ == "__main__":
    main()
