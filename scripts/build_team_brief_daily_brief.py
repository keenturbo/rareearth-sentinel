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
        "won_at": str((row or {}).get("won_at") or "").strip(),
        "updated_at": str((row or {}).get("updated_at") or "").strip(),
        "upsell_due_at": str((row or {}).get("upsell_due_at") or "").strip(),
        "upsell_status": str((row or {}).get("upsell_status") or "").strip().lower(),
    }


def _derive_upsell_due(x):
    due = _parse_date(x.get("upsell_due_at"))
    if due:
        return due
    base = _parse_datetime(x.get("won_at")) or _parse_datetime(x.get("updated_at")) or _parse_datetime(x.get("created_at"))
    if not base:
        return None
    base_cmp = base.replace(tzinfo=None) if getattr(base, "tzinfo", None) else base
    return (base_cmp + timedelta(days=7)).date()


def _classify(rows):
    now = now_cst()
    if getattr(now, "tzinfo", None):
        now = now.replace(tzinfo=None)
    today = now.date()
    exp_end = today + timedelta(days=3)

    normalized = [_norm_row(r) for r in rows]

    active = []
    for r in normalized:
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

    upsell_due = []
    for x in normalized:
        if x.get("status") != "won":
            continue
        if x.get("upsell_status") == "done":
            continue
        due = _derive_upsell_due(x)
        if due and due <= today:
            xx = dict(x)
            xx["_display_due"] = due.isoformat()
            upsell_due.append(xx)

    def _sort_key(z):
        d = _parse_datetime(z.get("next_action_at"))
        if not d:
            return datetime(2999, 1, 1)
        return d.replace(tzinfo=None) if getattr(d, "tzinfo", None) else d

    for arr in (high, due_today, overdue, expiring, upsell_due):
        arr.sort(key=_sort_key)

    return {
        "high_priority": high,
        "today": due_today,
        "overdue": overdue,
        "expiring": expiring,
        "upsell_due": upsell_due,
    }


def _summary_block(title, rows):
    lines = [f"【{title}】"]
    if not rows:
        lines.append("暂无")
        return "\n".join(lines)
    for i, x in enumerate(rows, start=1):
        lines.append(
            f"{i}. {x['lead_id'] or '-'} | {x['company']} | 负责人:{x['owner']} | 状态:{x['status']} | 下次:{x['next_action_at'] or '-'} | 到期:{x.get('_display_due') or x['quote_valid_until'] or '-'}"
        )
    return "\n".join(lines)


def _master_summary(data):
    today = datetime.now().strftime("%Y-%m-%d")
    parts = [f"【团队简报每日执行摘要】\n日期：{today}"]
    parts.append(_summary_block("高优先", data.get("high_priority") or []))
    parts.append(_summary_block("今日待跟进", data.get("today") or []))
    parts.append(_summary_block("已逾期", data.get("overdue") or []))
    parts.append(_summary_block("报价将到期（3天内）", data.get("expiring") or []))
    parts.append(_summary_block("复购跟进到期（成交后+7天）", data.get("upsell_due") or []))
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
                "quote_valid_until": x.get("_display_due") or x.get("quote_valid_until", ""),
            }
        )
    return out


def _write_text(path: str, content: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _write_json(path: str, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _upsert_daily_snapshot(path: str, row: dict):
    rows = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    day = str(row.get("date") or "")
    rows = [x for x in rows if str(x.get("date") or "") != day]
    rows.append(row)
    rows.sort(key=lambda x: str(x.get("date") or ""))

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for x in rows:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")


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
            + (data.get("upsell_due") or [])
        ),
        "high": _to_rows_for_csv(data.get("high_priority") or []),
        "today": _to_rows_for_csv(data.get("today") or []),
        "overdue": _to_rows_for_csv(data.get("overdue") or []),
        "expiring": _to_rows_for_csv(data.get("expiring") or []),
        "upsell": _to_rows_for_csv(data.get("upsell_due") or []),
    }

    generated_at = isoformat(now_cst())
    date_str = datetime.now().strftime("%Y-%m-%d")
    digest = {
        "generated_at": generated_at,
        "date": date_str,
        "counts": {
            "high_priority": len(data.get("high_priority") or []),
            "today": len(data.get("today") or []),
            "overdue": len(data.get("overdue") or []),
            "expiring": len(data.get("expiring") or []),
            "upsell_due": len(data.get("upsell_due") or []),
            "master": len(payload.get("master") or []),
        },
        "master_text": master_text,
        "sections": {
            "high": _summary_block("高优先", data.get("high_priority") or []),
            "today": _summary_block("今日待跟进", data.get("today") or []),
            "overdue": _summary_block("已逾期", data.get("overdue") or []),
            "expiring": _summary_block("报价将到期（3天内）", data.get("expiring") or []),
            "upsell": _summary_block("复购跟进到期（成交后+7天）", data.get("upsell_due") or []),
        },
        "lead_ids": {
            "high": [x.get("lead_id", "") for x in payload.get("high") or []],
            "today": [x.get("lead_id", "") for x in payload.get("today") or []],
            "overdue": [x.get("lead_id", "") for x in payload.get("overdue") or []],
            "expiring": [x.get("lead_id", "") for x in payload.get("expiring") or []],
            "upsell": [x.get("lead_id", "") for x in payload.get("upsell") or []],
        },
    }

    digest_json = os.path.join(config.data_dir, "status", "team_brief_daily_digest.json")
    digest_txt = os.path.join(config.data_dir, "status", "team_brief_daily_digest.txt")
    _write_json(digest_json, digest)
    _write_text(digest_txt, master_text)

    snapshot_path = os.path.join(config.data_dir, "analytics", "sales_daily_snapshots.jsonl")
    _upsert_daily_snapshot(
        snapshot_path,
        {
            "date": date_str,
            "generated_at": generated_at,
            "high_priority": len(data.get("high_priority") or []),
            "today": len(data.get("today") or []),
            "overdue": len(data.get("overdue") or []),
            "expiring": len(data.get("expiring") or []),
            "upsell_due": len(data.get("upsell_due") or []),
            "master": len(payload.get("master") or []),
        },
    )

    with open(template_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    html_out = (
        tpl.replace("{{LAST_UPDATE}}", generated_at)
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
