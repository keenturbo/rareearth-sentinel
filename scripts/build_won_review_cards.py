#!/usr/bin/env python3
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import isoformat, now_cst


_REASON_LABEL = {
    "price_fit": "价格匹配",
    "timely_response": "响应及时",
    "high_relevance": "需求高度匹配",
    "trust_from_sample": "试读建立信任",
    "other_won": "其他成交原因",
    "unknown": "原因未知",
}

_TEMPLATE_LABEL = {
    "basic": "Basic",
    "pro": "Pro",
    "custom": "Custom",
    "unknown": "Unknown",
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
    return rows


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def _safe_str(value):
    return str(value or "").strip()


def _parse_dt(value):
    text = _safe_str(value)
    if not text:
        return None
    text = text.replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_or_empty(dt):
    if not dt:
        return ""
    return dt.isoformat(timespec="seconds")


def _norm_status(value):
    return _safe_str(value).lower() or "new"


def _norm_template(value):
    t = _safe_str(value).lower()
    return t if t in {"basic", "pro", "custom"} else "unknown"


def _norm_template_version(value, template_key="unknown"):
    base = template_key if template_key in {"basic", "pro", "custom", "unknown"} else "unknown"
    raw = _safe_str(value).lower()
    if not raw:
        return f"{base}_v1"
    if raw in {"v1", "v2"}:
        return f"{base}_{raw}"
    allowed = {
        "basic_v1", "basic_v2", "pro_v1", "pro_v2",
        "custom_v1", "custom_v2", "unknown_v1", "unknown_v2",
    }
    if raw in allowed:
        return raw
    if "_" in raw:
        a, b = raw.split("_", 1)
        if a in {"basic", "pro", "custom", "unknown"} and b in {"v1", "v2"}:
            return f"{a}_{b}"
    return f"{base}_v1"


def _pick_latest_rows(rows):
    out = {}
    for row in rows:
        lead_id = _safe_str(row.get("lead_id"))
        if not lead_id:
            continue
        cur = out.get(lead_id)
        cur_dt = _parse_dt(cur.get("created_at")) if isinstance(cur, dict) else None
        row_dt = _parse_dt(row.get("created_at"))
        if cur is None:
            out[lead_id] = row
            continue
        if row_dt and (not cur_dt or row_dt >= cur_dt):
            out[lead_id] = row
    return out


def _group_events(rows):
    by_lead = defaultdict(list)
    for row in rows:
        lead_id = _safe_str(row.get("lead_id"))
        if not lead_id:
            continue
        by_lead[lead_id].append(row)
    for lead_id in list(by_lead.keys()):
        by_lead[lead_id].sort(key=lambda x: (_parse_dt(x.get("created_at")) or datetime(1970,1,1,tzinfo=timezone.utc), _safe_str(x.get("event_type"))))
    return by_lead


def _is_won_event(ev):
    et = _norm_status(ev.get("event_type"))
    if et == "status_change" and _norm_status(ev.get("to_status")) == "won":
        return True
    if et == "close_reason_set" and _norm_status(ev.get("status")) == "won":
        return True
    return False


def _pick_won_at(lead, events):
    candidates = []
    for ev in events:
        if _is_won_event(ev):
            dt = _parse_dt(ev.get("created_at"))
            if dt:
                candidates.append(dt)
    if candidates:
        return max(candidates)
    for key in ("updated_at", "created_at"):
        dt = _parse_dt(lead.get(key))
        if dt:
            return dt
    return None


def _pick_close_reason(lead, events):
    code = _safe_str(lead.get("close_reason_code")).lower()
    note = _safe_str(lead.get("close_reason_note"))
    if code:
        return code, note
    for ev in reversed(events):
        if _norm_status(ev.get("event_type")) != "close_reason_set":
            continue
        if _norm_status(ev.get("status")) != "won":
            continue
        code = _safe_str(ev.get("close_reason_code")).lower()
        note = _safe_str(ev.get("close_reason_note") or ev.get("note"))
        if code:
            return code, note
    return "unknown", ""


def _pick_template_fields(lead, events):
    template = _norm_template(lead.get("proposal_template") or lead.get("quote_plan"))
    version = _norm_template_version(lead.get("proposal_template_version"), template)

    for ev in reversed(events):
        ev_template = _norm_template(ev.get("proposal_template") or ev.get("quote_plan"))
        if ev_template != "unknown":
            template = ev_template
            version = _norm_template_version(ev.get("proposal_template_version"), template)
            break
        raw_ver = _safe_str(ev.get("proposal_template_version"))
        if raw_ver:
            version = _norm_template_version(raw_ver, template)
            break
    return template, version


def _short_need(lead):
    for key in ("brief_need", "message"):
        text = _safe_str(lead.get(key))
        if text:
            text = text.replace("\n", " ").strip()
            if len(text) > 36:
                return text[:36].rstrip() + "…"
            return text
    return "关键矿产/供应链风险"


def _why_won(close_reason_code, close_reason_note, template_key, lead):
    parts = []
    reason_map = {
        "price_fit": "价格与预算窗口匹配",
        "timely_response": "响应及时缩短决策周期",
        "high_relevance": "选题与需求高度匹配",
        "trust_from_sample": "试读样本建立信任",
        "other_won": "成交原因来自客户综合判断",
        "unknown": "成交原因未完整记录",
    }
    if close_reason_code:
        parts.append(reason_map.get(close_reason_code, "成交原因未标准化"))
    if template_key == "pro":
        parts.append("交付范围清晰（周清单+专项复盘）")
    elif template_key == "custom":
        parts.append("定制范围承接客户场景")
    elif template_key == "basic":
        parts.append("入门方案决策成本较低")
    need = _short_need(lead)
    if need and need != "关键矿产/供应链风险":
        parts.append(f"问题聚焦：{need}")
    if close_reason_note:
        parts.append(close_reason_note[:60])
    seen = set()
    uniq = []
    for p in parts:
        k = p.strip()
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(k)
    return "；".join(uniq[:3]) or "需求匹配度高，报价与交付预期清晰。"


def _reusable_pitch_cn(lead, template_key):
    company = _safe_str(lead.get("company")) or "客户团队"
    need = _short_need(lead)
    plan = _TEMPLATE_LABEL.get(template_key, "Basic")
    return (
        f"先给结论，再给证据，再给行动清单。围绕{company}当前关注的“{need}”，"
        f"用{plan}方案说明交付频率、样例结构与首轮交付时间，降低决策不确定性。"
    )


def _reusable_pitch_en(lead, template_key):
    company = _safe_str(lead.get("company")) or "the client team"
    need = _short_need(lead)
    plan = _TEMPLATE_LABEL.get(template_key, "Basic")
    return (
        f"Lead with the decision, then evidence, then an action checklist. Frame the {plan} plan around "
        f"{company}'s current priority ({need}) and make the first-delivery timing explicit."
    )


def _next_7d_action(template_key, lead):
    channel = _safe_str(lead.get("source_channel")) or "unknown"
    if template_key == "pro":
        return "D+2确认首轮交付结构；D+4收集反馈；D+7推送同类案例并提出升级模块建议。"
    if template_key == "custom":
        return "D+2确认定制范围与节奏；D+5同步里程碑；D+7输出扩展议题建议清单。"
    if channel == "team_brief_page":
        return "D+3确认试读反馈；D+7发送 Task Sprint/Weekly Pack 复购建议，并附1个近期案例。"
    return "D+3确认使用反馈；D+7推送复购建议与同类案例，记录是否有新增监控主题。"


def _pick_won_ids(leads_map, events_by_lead):
    won_ids = set()
    for lead_id, lead in leads_map.items():
        if _norm_status(lead.get("status")) == "won":
            won_ids.add(lead_id)
    for lead_id, events in events_by_lead.items():
        if any(_is_won_event(ev) for ev in events):
            won_ids.add(lead_id)
    return won_ids


def build_won_review_cards(config: Config):
    leads_path = os.path.join(config.data_dir, "pay", "team_brief_leads.jsonl")
    events_path = os.path.join(config.data_dir, "pay", "team_brief_lead_events.jsonl")
    out_path = os.path.join(config.data_dir, "status", "won_review_cards.jsonl")

    leads_rows = _read_jsonl(leads_path)
    event_rows = _read_jsonl(events_path)

    leads_map = _pick_latest_rows(leads_rows)
    events_by_lead = _group_events(event_rows)
    won_ids = _pick_won_ids(leads_map, events_by_lead)

    generated_at = isoformat(now_cst())
    cards = []

    for lead_id in sorted(won_ids):
        lead = leads_map.get(lead_id, {"lead_id": lead_id})
        events = events_by_lead.get(lead_id, [])
        won_at_dt = _pick_won_at(lead, events)
        close_reason_code, close_reason_note = _pick_close_reason(lead, events)
        template_key, template_ver = _pick_template_fields(lead, events)

        card = {
            "lead_id": lead_id,
            "won_at": _iso_or_empty(won_at_dt),
            "owner": _safe_str(lead.get("owner")),
            "channel": _safe_str(lead.get("source_channel")),
            "proposal_template": template_key,
            "proposal_template_version": template_ver,
            "close_reason_code": close_reason_code or "unknown",
            "close_reason_text": _REASON_LABEL.get(close_reason_code or "unknown", "原因未知"),
            "close_reason_note": close_reason_note,
            "why_won": _why_won(close_reason_code, close_reason_note, template_key, lead),
            "reusable_pitch_cn": _reusable_pitch_cn(lead, template_key),
            "reusable_pitch_en": _reusable_pitch_en(lead, template_key),
            "next_7d_action": _next_7d_action(template_key, lead),
            "company": _safe_str(lead.get("company")),
            "name": _safe_str(lead.get("name")),
            "generated_at": generated_at,
        }
        cards.append(card)

    cards.sort(
        key=lambda x: (
            _parse_dt(x.get("won_at")) or datetime(1970, 1, 1, tzinfo=timezone.utc),
            _safe_str(x.get("lead_id")),
        ),
        reverse=True,
    )

    _write_jsonl(out_path, cards)
    return out_path, len(cards)


def main():
    cfg = Config.from_env()
    out_path, count = build_won_review_cards(cfg)
    print(f"Won review cards built: {out_path} ({count})")


if __name__ == "__main__":
    main()
