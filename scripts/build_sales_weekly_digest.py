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

from src.config import Config
from src.utils import now_cst, isoformat


_REASON_LABEL = {
    "price_fit": "价格匹配",
    "timely_response": "响应及时",
    "high_relevance": "需求高度匹配",
    "trust_from_sample": "试读建立信任",
    "other_won": "其他成交原因",
    "price_too_high": "价格过高",
    "no_budget": "暂无预算",
    "no_need_now": "当前无需求",
    "slow_followup": "跟进过慢",
    "competitor_chosen": "选择竞品",
    "unknown": "原因未知",
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


def _in_last_days(value, now_utc, days=7):
    dt = _parse_dt(value)
    if not dt:
        return False
    return dt >= now_utc - timedelta(days=days)


def _pct(n, d):
    try:
        n = float(n)
        d = float(d)
    except Exception:
        return "0%"
    if d <= 0:
        return "0%"
    return f"{(n * 100.0 / d):.1f}%"


def _render_group_rows(items):
    if not items:
        return "<tr><td colspan='6' class='muted'>暂无数据</td></tr>"
    out = []
    for key, stats in items[:12]:
        total = int(stats.get("total", 0))
        proposal = int(stats.get("proposal", 0))
        won = int(stats.get("won", 0))
        lost = int(stats.get("lost", 0))
        out.append(
            "<tr>"
            f"<td>{html.escape(key)}</td>"
            f"<td>{total}</td>"
            f"<td>{proposal}</td>"
            f"<td>{won}</td>"
            f"<td>{lost}</td>"
            f"<td>{_pct(won, proposal)}</td>"
            "</tr>"
        )
    return "".join(out)


def _render_reason_rows(counter):
    if not counter:
        return "<tr><td colspan='3' class='muted'>暂无原因数据</td></tr>"
    total = sum(counter.values()) or 1
    out = []
    for code, cnt in counter.most_common(12):
        out.append(
            "<tr>"
            f"<td>{html.escape(_REASON_LABEL.get(code, code))}</td>"
            f"<td>{int(cnt)}</td>"
            f"<td>{_pct(cnt, total)}</td>"
            "</tr>"
        )
    return "".join(out)


def _render_actions(actions):
    if not actions:
        return "<li>本周数据不足，先持续积累线索与报价样本。</li>"
    return "".join(f"<li>{html.escape(x)}</li>" for x in actions)


def build_page(config: Config):
    leads_path = os.path.join(config.data_dir, "pay", "team_brief_leads.jsonl")
    events_path = os.path.join(config.data_dir, "pay", "team_brief_lead_events.jsonl")
    tpl_path = os.path.join(ROOT, "templates", "admin_team_brief_weekly.html")
    out_path = os.path.join(config.static_dir, "cn", "admin", "leads", "team-brief", "weekly", "index.html")

    leads = _read_jsonl(leads_path)
    events = _read_jsonl(events_path)

    now_utc = datetime.now(timezone.utc)
    week_start_utc = now_utc - timedelta(days=7)

    leads_7d = [x for x in leads if _in_last_days(x.get("created_at"), now_utc, 7)]

    proposal_ids = set()
    won_ids = set()
    lost_ids = set()
    upsell_sent = 0
    reason_counter = Counter()

    for ev in events:
        if not _in_last_days(ev.get("created_at"), now_utc, 7):
            continue
        lead_id = str(ev.get("lead_id") or "").strip()
        et = str(ev.get("event_type") or "").strip()
        if et == "proposal_sent" and lead_id:
            proposal_ids.add(lead_id)
        if et == "status_change":
            to_status = str(ev.get("to_status") or "").strip().lower()
            if to_status == "won" and lead_id:
                won_ids.add(lead_id)
            elif to_status == "lost" and lead_id:
                lost_ids.add(lead_id)
        if et == "close_reason_set":
            code = str(ev.get("close_reason_code") or "").strip()
            if code:
                reason_counter[code] += 1
            st = str(ev.get("status") or "").strip().lower()
            if st == "won" and lead_id:
                won_ids.add(lead_id)
            elif st == "lost" and lead_id:
                lost_ids.add(lead_id)
        if et == "note_append":
            note = str(ev.get("note") or "")
            if "[upsell_sent]" in note:
                upsell_sent += 1

    new_7d = len(leads_7d)
    proposal_7d = len(proposal_ids)
    won_7d = len(won_ids)
    lost_7d = len(lost_ids)

    channel_stats = defaultdict(lambda: {"total": 0, "proposal": 0, "won": 0, "lost": 0})
    owner_stats = defaultdict(lambda: {"total": 0, "proposal": 0, "won": 0, "lost": 0})

    for lead in leads_7d:
        lead_id = str(lead.get("lead_id") or "").strip()
        channel = str(lead.get("source_channel") or "").strip() or "unknown"
        owner = str(lead.get("owner") or "").strip() or "unassigned"

        channel_stats[channel]["total"] += 1
        owner_stats[owner]["total"] += 1

        if lead_id in proposal_ids:
            channel_stats[channel]["proposal"] += 1
            owner_stats[owner]["proposal"] += 1
        if lead_id in won_ids:
            channel_stats[channel]["won"] += 1
            owner_stats[owner]["won"] += 1
        if lead_id in lost_ids:
            channel_stats[channel]["lost"] += 1
            owner_stats[owner]["lost"] += 1

    channel_items = sorted(channel_stats.items(), key=lambda kv: (kv[1]["total"], kv[1]["won"]), reverse=True)
    owner_items = sorted(owner_stats.items(), key=lambda kv: (kv[1]["total"], kv[1]["won"]), reverse=True)

    actions = []
    proposal_rate = (proposal_7d / new_7d) if new_7d else 0.0
    win_rate = (won_7d / proposal_7d) if proposal_7d else 0.0
    if proposal_rate < 0.6:
        actions.append("报价率低于60%，优先跟进 qualified 线索并在24小时内发出报价。")
    if win_rate < 0.2:
        actions.append("已报价到成交转化偏低，建议强化试读样本+时效承诺，缩短决策周期。")
    if upsell_sent == 0 and won_7d > 0:
        actions.append("本周已有成交但未记录复购触达，建议对 won 客户发送 Task Sprint 复购推荐。")

    week_range = f"{week_start_utc.astimezone(timezone.utc).date()} ~ {now_utc.astimezone(timezone.utc).date()}"

    with open(tpl_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    html_out = (
        tpl.replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{WEEK_RANGE}}", html.escape(week_range))
        .replace("{{NEW_7D}}", str(new_7d))
        .replace("{{PROPOSAL_7D}}", str(proposal_7d))
        .replace("{{WON_7D}}", str(won_7d))
        .replace("{{LOST_7D}}", str(lost_7d))
        .replace("{{UPSELL_SENT_7D}}", str(upsell_sent))
        .replace("{{PROPOSAL_RATE_7D}}", _pct(proposal_7d, new_7d))
        .replace("{{WON_RATE_7D}}", _pct(won_7d, proposal_7d))
        .replace("{{CHANNEL_ROWS}}", _render_group_rows(channel_items))
        .replace("{{OWNER_ROWS}}", _render_group_rows(owner_items))
        .replace("{{REASON_ROWS}}", _render_reason_rows(reason_counter))
        .replace("{{ACTION_ITEMS}}", _render_actions(actions))
    )

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    return out_path


if __name__ == "__main__":
    cfg = Config.from_env()
    out = build_page(cfg)
    print(f"Admin team brief weekly page built: {out}")
