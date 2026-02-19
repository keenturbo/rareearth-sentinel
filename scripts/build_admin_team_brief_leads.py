#!/usr/bin/env python3
import html
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import isoformat, now_cst


_LIST_ENDPOINT = "/api/admin/leads/team-brief/list"
_UPDATE_ENDPOINT = "/api/admin/leads/team-brief/update"
_EXPORT_ENDPOINT = "/api/admin/leads/team-brief/export"
_EVENTS_ENDPOINT = "/api/admin/leads/team-brief/events"
_INSERT_TEST_ENDPOINT = "/api/admin/leads/team-brief/insert-test"
_CLEAR_TEST_ENDPOINT = "/api/admin/leads/team-brief/clear-test"

_STATUS_ORDER = ["new", "contacted", "qualified", "proposal_sent", "won", "lost"]
_STATUS_LABEL = {
    "new": "新线索",
    "contacted": "已联系",
    "qualified": "已评估",
    "proposal_sent": "已发送报价",
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


def _parse_datetime(value):
    s = str(value or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        pass
    s2 = s.replace("+00:00", "")
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s2[:19], fmt)
        except Exception:
            continue
    return None


def _parse_date(value):
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _is_test_row(row):
    if bool(row.get("test")):
        return True
    if str(row.get("source_channel") or "").strip().lower() == "admin_test":
        return True
    email = str(row.get("email") or "").strip().lower()
    return email.startswith("admin-test-")


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
        next_action_raw = str(r.get("next_action_at") or "")
        next_action = html.escape(_to_dt_local(next_action_raw))
        quote_currency = html.escape(str(r.get("quote_currency") or "USD"))
        quote_amount = html.escape(str(r.get("quote_amount") or ""))
        quote_plan = html.escape(str(r.get("quote_plan") or "basic"))
        quote_valid_until_raw = str(r.get("quote_valid_until") or "")
        quote_valid_until = html.escape(quote_valid_until_raw)
        is_test = _is_test_row(r)
        test_badge = "<div class='muted' style='color:#d97706'>test</div>" if is_test else ""

        out.append(
            f'<tr data-lead-id="{lead_id}" data-status="{html.escape(status)}" data-owner="{owner}" data-channel="{channel}" data-next-action="{html.escape(next_action_raw)}" data-quote-valid="{html.escape(quote_valid_until_raw)}">'
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
            f"<div class='muted' style='margin-top:4px'>报价: {quote_currency} {quote_amount} / {quote_plan} / {quote_valid_until}</div>"
            "</td>"
            "<td>"
            "<div class='quote-box'>"
            "<div class='action-row' style='margin-bottom:6px'>"
            f"<select class='quote-currency' style='max-width:90px'><option value='USD'{' selected' if quote_currency == 'USD' else ''}>USD</option><option value='CNY'{' selected' if quote_currency == 'CNY' else ''}>CNY</option></select>"
            f"<input class='quote-amount' type='number' step='0.01' min='0' value='{quote_amount}' placeholder='报价金额' style='max-width:120px' />"
            f"<select class='quote-plan' style='max-width:110px'><option value='basic'{' selected' if quote_plan == 'basic' else ''}>basic</option><option value='pro'{' selected' if quote_plan == 'pro' else ''}>pro</option><option value='custom'{' selected' if quote_plan == 'custom' else ''}>custom</option></select>"
            f"<input class='quote-valid-until' type='date' value='{quote_valid_until}' style='max-width:150px' />"
            "</div>"
            "<div class='action-row' style='margin-bottom:6px'>"
            "<button class='gen-quote-btn'>生成报价</button>"
            "<button class='copy-quote-cn-btn'>复制中文报价</button>"
            "<button class='copy-quote-en-btn'>Copy EN Quote</button>"
            "<button class='send-proposal-btn'>标记已发送报价</button>"
            "</div>"
            "<div class='quote-preview'></div>"
            "</div>"
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
        "proposal_sent": c.get("proposal_sent", 0),
        "won": c.get("won", 0),
        "lost": c.get("lost", 0),
    }


def _pct(n, d):
    try:
        n = float(n)
        d = float(d)
    except Exception:
        return "0%"
    if d <= 0:
        return "0%"
    return f"{(n * 100.0 / d):.0f}%"


def _extra_metrics(rows):
    now = now_cst()
    if getattr(now, "tzinfo", None):
        now = now.replace(tzinfo=None)
    today = now.date()
    expiring_end = today + timedelta(days=3)

    followup_today = 0
    overdue = 0
    quote_expiring = 0

    owner_map = defaultdict(lambda: {"total": 0, "proposal_sent": 0, "won": 0})
    channel_map = defaultdict(lambda: {"total": 0, "won": 0})

    for r in rows:
        st = str(r.get("status") or "new").strip().lower()

        owner = str(r.get("owner") or "").strip() or "unassigned"
        owner_map[owner]["total"] += 1
        if st == "proposal_sent":
            owner_map[owner]["proposal_sent"] += 1
        if st == "won":
            owner_map[owner]["won"] += 1

        ch = str(r.get("source_channel") or "").strip() or "unknown"
        channel_map[ch]["total"] += 1
        if st == "won":
            channel_map[ch]["won"] += 1

        nxt = _parse_datetime(r.get("next_action_at"))
        if nxt:
            nxt_cmp = nxt.replace(tzinfo=None) if getattr(nxt, "tzinfo", None) else nxt
            if nxt_cmp.date() == today:
                followup_today += 1
            if nxt_cmp < now:
                overdue += 1

        qd = _parse_date(r.get("quote_valid_until"))
        if qd and today <= qd <= expiring_end:
            quote_expiring += 1

    owner_rows = sorted(owner_map.items(), key=lambda kv: kv[1]["total"], reverse=True)[:8]
    channel_rows = sorted(channel_map.items(), key=lambda kv: kv[1]["total"], reverse=True)[:8]

    return {
        "followup_today": followup_today,
        "overdue": overdue,
        "quote_expiring": quote_expiring,
        "owner_rows": owner_rows,
        "channel_rows": channel_rows,
    }


def _render_owner_board(owner_rows):
    if not owner_rows:
        return '<div class="owner-card"><div class="k">负责人看板</div><div class="v">暂无数据</div></div>'
    out = []
    for owner, s in owner_rows:
        out.append(
            "<div class='owner-card'>"
            f"<div class='k'>{html.escape(owner)}</div>"
            f"<div class='v'>{int(s.get('total',0))}</div>"
            f"<div class='muted'>报价 {int(s.get('proposal_sent',0))} · 成交 {int(s.get('won',0))}</div>"
            "</div>"
        )
    return "".join(out)


def _render_channel_board(channel_rows):
    if not channel_rows:
        return '<div class="owner-card"><div class="k">渠道看板</div><div class="v">暂无数据</div></div>'
    out = []
    for ch, s in channel_rows:
        out.append(
            "<div class='owner-card'>"
            f"<div class='k'>{html.escape(ch)}</div>"
            f"<div class='v'>{int(s.get('total',0))}</div>"
            f"<div class='muted'>成交 {int(s.get('won',0))}</div>"
            "</div>"
        )
    return "".join(out)


def build_page(config):
    leads_path = os.path.join(config.data_dir, "pay", "team_brief_leads.jsonl")
    template_path = os.path.join(ROOT, "templates", "admin_team_brief_leads.html")
    output_path = os.path.join(config.static_dir, "cn", "admin", "leads", "team-brief", "index.html")

    rows = _read_jsonl(leads_path)
    cnt = _counts(rows)
    extra = _extra_metrics(rows)

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
        .replace("{{PROPOSAL_SENT_COUNT}}", str(cnt["proposal_sent"]))
        .replace("{{WON_COUNT}}", str(cnt["won"]))
        .replace("{{LOST_COUNT}}", str(cnt["lost"]))
        .replace("{{FOLLOWUP_TODAY_COUNT}}", str(extra["followup_today"]))
        .replace("{{OVERDUE_COUNT}}", str(extra["overdue"]))
        .replace("{{QUOTE_EXPIRING_COUNT}}", str(extra["quote_expiring"]))
        .replace("{{RATE_CONTACTED}}", _pct(cnt["contacted"], cnt["new"] or len(rows)))
        .replace("{{RATE_QUALIFIED}}", _pct(cnt["qualified"], cnt["contacted"]))
        .replace("{{RATE_PROPOSAL}}", _pct(cnt["proposal_sent"], cnt["qualified"]))
        .replace("{{RATE_WON}}", _pct(cnt["won"], cnt["proposal_sent"]))
        .replace("{{OWNER_BOARD_ITEMS}}", _render_owner_board(extra["owner_rows"]))
        .replace("{{CHANNEL_BOARD_ITEMS}}", _render_channel_board(extra["channel_rows"]))
        .replace("{{LEADS_PATH}}", html.escape(leads_path))
        .replace("{{LEADS_ROWS}}", _render_items(rows))
        .replace("{{LEADS_LIST_ENDPOINT}}", _LIST_ENDPOINT)
        .replace("{{LEADS_UPDATE_ENDPOINT}}", _UPDATE_ENDPOINT)
        .replace("{{LEADS_EXPORT_ENDPOINT}}", _EXPORT_ENDPOINT)
        .replace("{{LEADS_EVENTS_ENDPOINT}}", _EVENTS_ENDPOINT)
        .replace("{{LEADS_INSERT_TEST_ENDPOINT}}", _INSERT_TEST_ENDPOINT)
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
