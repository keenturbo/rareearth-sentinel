#!/usr/bin/env python3
import html
import json
import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import isoformat, now_cst


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
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return rows


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


def _norm_row(row):
    return {
        "lead_id": str((row or {}).get("lead_id") or "").strip(),
        "status": str((row or {}).get("status") or "new").strip().lower(),
        "owner": str((row or {}).get("owner") or "").strip() or "未分配",
        "company": str((row or {}).get("company") or "").strip() or "-",
        "name": str((row or {}).get("name") or "").strip() or "-",
        "email": str((row or {}).get("email") or "").strip(),
        "source_channel": str((row or {}).get("source_channel") or "").strip() or "unknown",
        "next_action_at": str((row or {}).get("next_action_at") or "").strip(),
        "quote_valid_until": str((row or {}).get("quote_valid_until") or "").strip(),
        "created_at": str((row or {}).get("created_at") or "").strip(),
    }


def _classify(rows):
    now = now_cst()
    if getattr(now, "tzinfo", None):
        now = now.replace(tzinfo=None)
    today = now.date()
    exp_end = today + timedelta(days=3)

    active = []
    for row in rows:
        r = _norm_row(row)
        if r["status"] in {"won", "lost"}:
            continue
        active.append(r)

    high = [x for x in active if x["status"] == "qualified"]

    due_today = []
    overdue = []
    expiring = []

    for x in active:
        na = _parse_datetime(x.get("next_action_at"))
        if na:
            na_cmp = na.replace(tzinfo=None) if getattr(na, "tzinfo", None) else na
            if na_cmp.date() == today:
                due_today.append(x)
            if na_cmp < now:
                overdue.append(x)

        qd = _parse_date(x.get("quote_valid_until"))
        if qd and today <= qd <= exp_end:
            expiring.append(x)

    def _sort_key(z):
        d = _parse_datetime(z.get("next_action_at"))
        if not d:
            return datetime(2999, 1, 1)
        return d.replace(tzinfo=None) if getattr(d, "tzinfo", None) else d

    for arr in (high, due_today, overdue, expiring):
        arr.sort(key=_sort_key)

    return {
        "high_priority": high,
        "today": due_today,
        "overdue": overdue,
        "expiring": expiring,
    }


def _summary_block(title, rows):
    lines = [f"【{title}】"]
    if not rows:
        lines.append("暂无")
        return "\n".join(lines)
    for i, x in enumerate(rows, start=1):
        lines.append(
            f"{i}. {x['lead_id'] or '-'} | {x['company']} | 负责人:{x['owner']} | 状态:{x['status']} | 下次:{x['next_action_at'] or '-'} | 到期:{x['quote_valid_until'] or '-'}"
        )
    return "\n".join(lines)


def _master_summary(data):
    today = datetime.now().strftime("%Y-%m-%d")
    parts = [f"【团队简报每日执行摘要】\n日期：{today}"]
    parts.append(_summary_block("高优先", data.get("high_priority") or []))
    parts.append(_summary_block("今日待跟进", data.get("today") or []))
    parts.append(_summary_block("已逾期", data.get("overdue") or []))
    parts.append(_summary_block("报价将到期（3天内）", data.get("expiring") or []))
    return "\n\n".join(parts)


def _to_rows_for_csv(rows):
    out = []
    for x in rows:
        out.append(
            {
                "lead_id": x.get("lead_id", ""),
                "status": x.get("status", ""),
                "owner": x.get("owner", ""),
                "name": x.get("name", ""),
                "email": x.get("email", ""),
                "company": x.get("company", ""),
                "source_channel": x.get("source_channel", ""),
                "next_action_at": x.get("next_action_at", ""),
                "quote_valid_until": x.get("quote_valid_until", ""),
            }
        )
    return out


def build_page(config: Config):
    template_path = os.path.join(ROOT, "templates", "admin_team_brief_daily_brief.html")
    output_path = os.path.join(
        config.static_dir, "cn", "admin", "leads", "team-brief", "daily-brief", "index.html"
    )

    leads_path = os.path.join(config.data_dir, "pay", "team_brief_leads.jsonl")
    rows = _read_jsonl(leads_path)
    data = _classify(rows)

    master_text = _master_summary(data)

    payload = {
        "master": _to_rows_for_csv(
            (data.get("high_priority") or [])
            + (data.get("today") or [])
            + (data.get("overdue") or [])
            + (data.get("expiring") or [])
        ),
        "high": _to_rows_for_csv(data.get("high_priority") or []),
        "today": _to_rows_for_csv(data.get("today") or []),
        "overdue": _to_rows_for_csv(data.get("overdue") or []),
        "expiring": _to_rows_for_csv(data.get("expiring") or []),
    }

    with open(template_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    html_out = (
        tpl.replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{COUNT_HIGH}}", str(len(data.get("high_priority") or [])))
        .replace("{{COUNT_TODAY}}", str(len(data.get("today") or [])))
        .replace("{{COUNT_OVERDUE}}", str(len(data.get("overdue") or [])))
        .replace("{{COUNT_EXPIRING}}", str(len(data.get("expiring") or [])))
        .replace("{{MASTER_TEXT}}", html.escape(master_text))
        .replace("{{HIGH_TEXT}}", html.escape(_summary_block("高优先", data.get("high_priority") or [])))
        .replace("{{TODAY_TEXT}}", html.escape(_summary_block("今日待跟进", data.get("today") or [])))
        .replace("{{OVERDUE_TEXT}}", html.escape(_summary_block("已逾期", data.get("overdue") or [])))
        .replace("{{EXPIRING_TEXT}}", html.escape(_summary_block("报价将到期（3天内）", data.get("expiring") or [])))
        .replace("{{CSV_PAYLOAD_JSON}}", json.dumps(payload, ensure_ascii=False))
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)


def main():
    config = Config.from_env()
    build_page(config)
    print("Admin team brief daily brief page built")


if __name__ == "__main__":
    main()
