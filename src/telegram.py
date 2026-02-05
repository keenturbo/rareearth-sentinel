import json
import os
import re
import time
from datetime import datetime
from email.utils import parsedate_to_datetime

import requests

from src.utils import now_cst

TOP3_SCHEDULE_HOURS = (10, 15, 20)


def _escape_markdown(text):
    if not text:
        return ""
    text = text.replace("\\", "\\\\")
    for ch in ("_", "*", "[", "]", "(", ")", "`"):
        text = text.replace(ch, f"\\{ch}")
    return text


def _normalize_channel(channel):
    if not channel:
        return ""
    value = channel.strip()
    value = value.replace("https://t.me/", "").replace("http://t.me/", "")
    value = value.replace("t.me/", "")
    if value.startswith("@"):
        return value
    if value.startswith("-100") or value.startswith("-") or value.isdigit():
        return value
    return f"@{value}"


def _clip_text(text, limit):
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _split_summary_lines(text, limit=2):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= limit:
        return lines[:limit]
    if text:
        sentences = re.split(r"[。.!?！？]+", text)
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if sentence in lines:
                continue
            lines.append(sentence)
            if len(lines) >= limit:
                break
    return lines[:limit]


def _normalize_line(text):
    return re.sub(r"[。.!?！？]+$", "", (text or "").strip())


def _build_message(record, base_url, lang="zh"):
    record_id = str(record.get("id", "")).strip()
    if not record_id:
        return ""
    title = str(record.get("title", "")).strip()

    if lang == "en":
        summary = record.get("summary_en") or ""
        expert = record.get("expert_summary_en") or ""
        if not summary and not expert:
            return ""
        is_cn = False
        link = f"{base_url.rstrip('/')}/en/task/pay/{record_id}.html"
    else:
        summary = (
            record.get("summary_zh")
            or record.get("summary_en")
            or record.get("expert_summary")
            or ""
        )
        expert = record.get("expert_summary") or record.get("expert_summary_en") or ""
        if not summary and not expert:
            return ""
        is_cn = True
        link = f"{base_url.rstrip('/')}/cn/task/pay/{record_id}.html"

    summary = str(summary).strip()
    expert = str(expert).strip()
    summary_lines = _split_summary_lines(summary, limit=2)
    summary_lines = [_clip_text(line, 200) for line in summary_lines if line]
    expert_line = _clip_text(expert, 200) if expert else ""

    parts = []
    if title:
        parts.append(_escape_markdown(title))

    summary_label = "摘要：" if is_cn else "Summary: "
    expert_label = "专家结论：" if is_cn else "Expert: "

    if summary_lines:
        parts.append(_escape_markdown(f"{summary_label}{summary_lines[0]}"))

    second_line = ""
    if expert_line and _normalize_line(expert_line) != _normalize_line(
        summary_lines[0] if summary_lines else ""
    ):
        second_line = f"{expert_label}{expert_line}"
    elif len(summary_lines) > 1 and _normalize_line(summary_lines[1]) != _normalize_line(
        summary_lines[0]
    ):
        second_line = summary_lines[1]

    if second_line:
        parts.append(_escape_markdown(second_line))

    parts.append(link)
    return "\n".join([part for part in parts if part])


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def _record_date_label(record, lang="zh"):
    dt = _parse_dt(record.get("published_at") or "")
    if not dt:
        dt = _parse_dt(record.get("collected_at") or "")
    if not dt:
        dt = now_cst()
    try:
        dt = dt.astimezone(now_cst().tzinfo)
    except (ValueError, OSError):
        pass
    if lang == "en":
        return dt.strftime("%a, %d %b %Y")
    return dt.strftime("%Y-%m-%d")


def _risk_level_value(record):
    level = record.get("risk_level")
    if level:
        return str(level)
    try:
        risk_score = int(record.get("risk_score", 0) or 0)
    except (TypeError, ValueError):
        return ""
    level = max(1, min(5, (risk_score + 19) // 20))
    return str(level)


def _build_top3_message(item, base_url, lang="zh"):
    report_id = str(item.get("report_id", "")).strip()
    if not report_id:
        return ""

    if lang == "en":
        title = str(item.get("title_en", "")).strip()
        value = item.get("value_en") or ""
        report_contains = item.get("report_contains_en") or ""
        link = f"{base_url.rstrip('/')}/en/pay/{report_id}.html"
    else:
        title = str(item.get("title_zh") or item.get("title", "")).strip()
        value = item.get("value_zh") or ""
        report_contains = item.get("report_contains_zh") or ""
        link = f"{base_url.rstrip('/')}/cn/pay/{report_id}.html"

    if not title:
        return ""

    if not value and not report_contains:
        return ""

    value = _clip_text(str(value).strip(), 160)
    report_contains = _clip_text(str(report_contains).strip(), 300)

    parts = []
    if title:
        parts.append(_escape_markdown(title))

    if value:
        label = "价值：" if lang == "zh" else "Value: "
        parts.append(_escape_markdown(f"{label}{value}"))

    if report_contains:
        label = "报告包含：" if lang == "zh" else "Report includes: "
        parts.append(_escape_markdown(f"{label}{report_contains}"))

    parts.append(link)
    return "\n".join([part for part in parts if part])


def _send_message(token, channel, text, retries=1):
    if not token or not channel or not text:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": channel,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.ok:
                data = resp.json()
                if data.get("ok") is True:
                    return True
        except requests.RequestException:
            pass
        if attempt < retries:
            time.sleep(1.0)
    return False


def _top3_list_path(config):
    return os.path.join(config.data_dir, "status", "top3_list.json")


def _top3_state_path(config):
    return os.path.join(config.data_dir, "status", "top3_status.json")


def _load_top3_list(config):
    path = _top3_list_path(config)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _load_top3_state(config):
    path = _top3_state_path(config)
    if not os.path.exists(path):
        return {"date": "", "sent": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    return {
        "date": data.get("date", ""),
        "sent": data.get("sent", {}) or {},
    }


def _save_top3_state(config, state):
    path = _top3_state_path(config)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _due_slots(now_dt):
    due = []
    for idx, hour in enumerate(TOP3_SCHEDULE_HOURS, start=1):
        if now_dt.hour > hour or (now_dt.hour == hour and now_dt.minute >= 0):
            due.append(idx)
    return due


def post_telegram_records(records, config, lang="zh"):
    lang = (lang or "zh").strip().lower()
    if lang == "en":
        token = (getattr(config, "telegram_en_bot_token", "") or "").strip()
        channel = _normalize_channel(
            getattr(config, "telegram_en_channel", "") or ""
        )
    else:
        token = (config.telegram_bot_token or "").strip()
        channel = _normalize_channel(config.telegram_channel or "")

    if not token or not channel:
        return 0, 0
    base_url = (getattr(config, "site_base_url", "") or "").strip()
    if not base_url:
        base_url = "https://rareearthsentinel.com"

    total = 0
    posted = 0
    for record in records:
        message = _build_message(record, base_url, lang=lang)
        if not message:
            continue
        total += 1
        if _send_message(token, channel, message, retries=1):
            posted += 1
        time.sleep(0.2)
    return posted, total


def post_telegram_top3_due(config, now_dt=None):
    data = _load_top3_list(config)
    date_key = data.get("date", "")
    items = data.get("items") or []
    if not date_key or not items:
        return 0, 0

    now_dt = now_dt or now_cst()
    if now_dt.strftime("%Y-%m-%d") != date_key:
        return 0, 0

    due_slots = _due_slots(now_dt)
    if not due_slots:
        return 0, 0

    state = _load_top3_state(config)
    if state.get("date") != date_key:
        state = {"date": date_key, "sent": {}}
    sent = state.get("sent", {}) or {}

    base_url = (getattr(config, "site_base_url", "") or "").strip()
    if not base_url:
        base_url = "https://rareearthsentinel.com"

    posted = 0
    total = 0
    for slot in due_slots:
        if len(items) < slot:
            continue
        item = items[slot - 1]
        for lang in ("zh", "en"):
            key = f"{lang}_{slot}"
            if sent.get(key):
                continue
            if lang == "en":
                token = (getattr(config, "telegram_en_bot_token", "") or "").strip()
                channel = _normalize_channel(
                    getattr(config, "telegram_en_channel", "") or ""
                )
            else:
                token = (config.telegram_bot_token or "").strip()
                channel = _normalize_channel(config.telegram_channel or "")
            if not token or not channel:
                continue
            message = _build_top3_message(item, base_url, lang=lang)
            if not message:
                continue
            total += 1
            if _send_message(token, channel, message, retries=1):
                posted += 1
                sent[key] = now_cst().isoformat(timespec="seconds")
            time.sleep(0.2)

    state["sent"] = sent
    _save_top3_state(config, state)
    return posted, total
