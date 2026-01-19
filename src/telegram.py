import re
import time

import requests


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


def _build_message(record, base_url):
    record_id = str(record.get("id", "")).strip()
    if not record_id:
        return ""
    title = str(record.get("title", "")).strip()
    summary = (
        record.get("summary_zh")
        or record.get("summary_en")
        or record.get("expert_summary")
        or ""
    )
    expert = record.get("expert_summary") or record.get("expert_summary_en") or ""
    summary = str(summary).strip()
    expert = str(expert).strip()
    summary_lines = _split_summary_lines(summary, limit=2)
    summary_lines = [_clip_text(line, 200) for line in summary_lines if line]
    expert_line = _clip_text(expert, 200) if expert else ""

    link = f"{base_url.rstrip('/')}/cn/task/pay/{record_id}.html"

    parts = []
    if title:
        parts.append(_escape_markdown(title))

    is_cn = bool(record.get("summary_zh") or record.get("expert_summary"))
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


def post_telegram_records(records, config):
    token = (config.telegram_bot_token or "").strip()
    channel = _normalize_channel(config.telegram_channel or "")
    if not token or not channel:
        return 0, 0
    base_url = (getattr(config, "site_base_url", "") or "").strip()
    if not base_url:
        base_url = "https://sentinel.dataspices.com"

    total = 0
    posted = 0
    for record in records:
        message = _build_message(record, base_url)
        if not message:
            continue
        total += 1
        if _send_message(token, channel, message, retries=1):
            posted += 1
        time.sleep(0.2)
    return posted, total
