#!/usr/bin/env python3
import argparse
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config, load_keywords
from src.extractor import GeminiExtractor, _generate_with_timeout, _safe_response_text
from src.utils import extract_json, now_cst, isoformat


TOKEN_RE = re.compile(r"[A-Za-z0-9]{3,}|[\u4e00-\u9fff]{2,}")


def _read_jsonl(path):
    entries = []
    if not os.path.exists(path):
        return entries
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def _load_market_analysis(data_dir):
    path = os.path.join(data_dir, "status", "indices_market.json")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("analysis_text", "")).strip()


def _dedupe(entries, key):
    data = {}
    for entry in entries:
        value = entry.get(key)
        if not value:
            continue
        data[value] = entry
    return list(data.values())


def _parse_dt(text):
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        dt = None
    if dt is None:
        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            dt = None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=now_cst().tzinfo)
    return dt


def _record_dt(record):
    return _parse_dt(record.get("published_at", "")) or _parse_dt(record.get("collected_at", ""))


def _extract_tokens(*parts):
    tokens = set()
    for part in parts:
        if not part:
            continue
        text = str(part)
        for raw in TOKEN_RE.findall(text):
            token = raw.strip().lower()
            if token:
                tokens.add(token)
    return tokens


def _match_tokens(text, tokens):
    if not tokens:
        return True
    if not text:
        return False
    lower = str(text).lower()
    return any(token in lower for token in tokens)


def _match_keywords(text, keywords):
    if not keywords:
        return True
    if not text:
        return False
    lower = str(text).lower()
    for kw in keywords:
        if kw and kw.lower() in lower:
            return True
    return False


def _frequency_label(freq, lang="cn"):
    mapping_zh = {"weekly": "周报", "daily": "日报", "event": "事件触发"}
    mapping_en = {"weekly": "Weekly", "daily": "Daily", "event": "Event"}
    freq = str(freq or "weekly")
    return mapping_en.get(freq, freq) if lang == "en" else mapping_zh.get(freq, freq)


def _task_status_label(status, lang="cn"):
    mapping_zh = {
        "pending": "待执行",
        "active": "执行中",
        "paused": "已暂停",
        "completed": "已完成",
    }
    mapping_en = {
        "pending": "Pending",
        "active": "Active",
        "paused": "Paused",
        "completed": "Completed",
    }
    status = str(status or "pending")
    return mapping_en.get(status, status) if lang == "en" else mapping_zh.get(status, status)


def _task_reports_path(data_dir, task_id, lang="cn"):
    return os.path.join(data_dir, "tasks", task_id, f"reports_{lang}.jsonl")


def _task_reports_list(data_dir, task_id, lang="cn"):
    reports = _read_jsonl(_task_reports_path(data_dir, task_id, lang=lang))
    reports.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return reports


def _write_task_report_index(data_dir, task_id, lang, entry):
    path = _task_reports_path(data_dir, task_id, lang=lang)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False))
        f.write("\n")


def _update_task_orders(path, task_id, updates):
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    f.write(line)
                    continue
                if entry.get("task_id") == task_id:
                    entry.update(updates)
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                else:
                    f.write(line)
    except OSError:
        return


def _next_run_at(freq, now):
    if freq == "daily":
        nxt = now + timedelta(days=1)
        return nxt.replace(hour=11, minute=0, second=0, microsecond=0)
    if freq == "weekly":
        days = (7 - now.weekday()) % 7
        if days == 0:
            days = 7
        nxt = now + timedelta(days=days)
        return nxt.replace(hour=9, minute=0, second=0, microsecond=0)
    return None


def _should_run(freq, last_run_at, now, force=False):
    if force:
        return True
    if freq == "daily":
        if now.hour < 11:
            return False
        if not last_run_at:
            return True
        return last_run_at.date() < now.date()
    if freq == "weekly":
        if now.weekday() != 0 or now.hour < 9:
            return False
        if not last_run_at:
            return True
        return last_run_at.date() < now.date()
    return True


def _time_window(freq, now):
    if freq == "daily":
        return now - timedelta(days=1)
    if freq == "weekly":
        return now - timedelta(days=7)
    return now - timedelta(days=30)


def _format_evidence(record, lang="cn"):
    title = str(record.get("title", "")).strip()
    summary = record.get("summary_zh") or record.get("summary_en") or ""
    if lang == "en":
        summary = record.get("summary_en") or summary
    url = str(record.get("url", "")).strip()
    dt = _record_dt(record)
    date_label = dt.date().isoformat() if dt else ""
    if summary:
        summary = summary.replace("\n", " ")[:180]
    return {
        "title": title,
        "summary": summary,
        "url": url,
        "date": date_label,
    }


def _build_prompt(entry, evidence, lang="cn", time_window="", market_analysis=""):
    task_suggestion = entry.get("task_suggestion", "")
    report_title = entry.get("report_title", "")
    report_value = entry.get("report_value", "")
    frequency = entry.get("frequency", "weekly")

    lines = []
    for item in evidence:
        title = item.get("title", "")
        summary = item.get("summary", "")
        url = item.get("url", "")
        date_label = item.get("date", "")
        lines.append(f"- {date_label} | {title} | {summary} | {url}")

    evidence_block = "\n".join(lines) if lines else "(no evidence)"
    prompt_version = os.getenv("PROMPT_VERSION", "v1").strip().lower()

    market_block = ""
    if market_analysis:
        if lang == "en":
            market_block = (
                "\nMarket commentary (background only, not verified facts):\n"
                f"{market_analysis}\n"
            )
        else:
            market_block = (
                "\n市场行情评论（仅背景，不作为已验证事实）：\n"
                f"{market_analysis}\n"
            )

    if prompt_version == "v2":
        if lang == "en":
            return f"""
You are a neutral rare earths intelligence editor for global market and supply-chain readers.
Generate a task monitoring report based on the task info and evidence list below.
Return ONLY JSON with fields: summary, action_table, sources, conclusion, key_evidence, assumptions.
- summary: 120-180 words, concise and analytical. State whether the watchpoint is supported, weakened, or needs watch.
- action_table: array of up to 5 items, each with action, path, evidence (evidence must come from the evidence list).
- sources: array of up to 5 items, each with title and url.
- conclusion: 1-2 sentences, avoid exaggerated claims.
- key_evidence: up to 3 evidence points from the evidence list.
- assumptions: up to 3 evidence gaps or assumptions.
If evidence is weak, keep neutral and leave lists empty rather than guessing.

Task info:
- suggestion: {task_suggestion}
- linked report: {report_title}
- value: {report_value}
- frequency: {frequency}
- time window: {time_window}
{market_block}
Evidence:
{evidence_block}
"""

        return f"""
你是客观中立的稀土与关键矿产供应链情报编辑。
请基于任务信息与证据清单生成本期跟踪报告。
仅返回 JSON，字段为：summary, action_table, sources, conclusion, key_evidence, assumptions。
- summary：150-250字，必须回应跟踪观察点“被支持/被削弱/需观察”，避免口号。
- action_table：最多 5 行，每行包含 action, path, evidence（证据必须来自证据清单）。
- sources：最多 5 行，每行包含 title, url。
- conclusion：1-2 句，保持中性克制。
- key_evidence：最多 3 条，必须来自证据清单。
- assumptions：最多 3 条证据缺口/假设。
证据不足时保持中性，不要猜测。

任务信息：
- 操作建议：{task_suggestion}
- 关联报告：{report_title}
- 价值：{report_value}
- 频率：{frequency}
- 观察窗口：{time_window}
{market_block}
证据清单：
{evidence_block}
"""

    if lang == "en":
        return f"""
You are a neutral rare earths intelligence editor for global market and supply-chain readers.
Generate a task monitoring report based on the task info and evidence list below.
Return ONLY JSON with fields: summary, action_table, sources, conclusion.
- summary: 120-180 words, concise and analytical.
- action_table: array of up to 5 items, each with action, path, evidence.
- sources: array of up to 5 items, each with title and url.
- conclusion: 1-2 sentences.

Task info:
- suggestion: {task_suggestion}
- linked report: {report_title}
- value: {report_value}
- frequency: {frequency}
- time window: {time_window}
{market_block}
Evidence:
{evidence_block}
"""

    return f"""
你是客观中立的稀土与关键矿产供应链情报编辑。
请基于任务信息与证据清单生成本期跟踪报告。
仅返回 JSON，字段为：summary, action_table, sources, conclusion。
- summary：150-250 字，简洁、分析性。
- action_table：最多 5 行，每行包含 action, path, evidence。
- sources：最多 5 行，每行包含 title, url。
- conclusion：1-2 句总结。

任务信息：
- 操作建议：{task_suggestion}
- 关联报告：{report_title}
- 价值：{report_value}
- 频率：{frequency}
- 观察窗口：{time_window}
{market_block}
证据清单：
{evidence_block}
"""


def _generate_report(extractor, prompt):
    try:
        response = _generate_with_timeout(
            extractor.model,
            prompt,
            extractor.analyze_timeout_seconds,
        )
    except Exception:
        return None

    raw_text = _safe_response_text(response)
    if not raw_text.strip():
        return None
    return extract_json(raw_text)


def _normalize_report(data):
    if not isinstance(data, dict):
        return {
            "summary": "",
            "action_table": [],
            "sources": [],
            "conclusion": "",
            "key_evidence": [],
            "assumptions": [],
        }

    def _as_list(value, limit=3):
        if value is None:
            return []
        if isinstance(value, list):
            items = [str(v).strip() for v in value if str(v).strip()]
        elif isinstance(value, str):
            items = [v.strip("- ").strip() for v in value.splitlines() if v.strip()]
        else:
            items = []
        return items[:limit]

    summary = str(data.get("summary", "")).strip()
    conclusion = str(data.get("conclusion", "")).strip()
    if conclusion:
        summary = f"{summary}\n{conclusion}" if summary else conclusion
    action_table = data.get("action_table")
    if not isinstance(action_table, list):
        action_table = []
    sources = data.get("sources")
    if not isinstance(sources, list):
        sources = []
    return {
        "summary": summary,
        "action_table": action_table,
        "sources": sources,
        "conclusion": conclusion,
        "key_evidence": _as_list(data.get("key_evidence"), limit=3),
        "assumptions": _as_list(data.get("assumptions"), limit=3),
    }


def _render_summary(summary, lang="cn"):
    if not summary:
        empty = "暂无执行结果" if lang != "en" else "No results yet."
        return f'<div class="empty">{empty}</div>'
    lines = [line.strip() for line in summary.splitlines() if line.strip()]
    paragraphs = "".join(f'<div class="summary">{html.escape(line)}</div>' for line in lines)
    return paragraphs


def _render_action_table(rows, lang="cn"):
    if not rows:
        empty = "暂无关键信息" if lang != "en" else "No key signals yet."
        return f'<div class="empty">{empty}</div>'
    if lang == "en":
        headers = ("Action", "Impact path", "Evidence")
    else:
        headers = ("行动建议", "影响路径", "证据来源")

    body_rows = []
    for row in rows[:5]:
        if isinstance(row, dict):
            action = row.get("action") or row.get("行动") or ""
            path = row.get("path") or row.get("impact_path") or row.get("影响路径") or ""
            evidence = row.get("evidence") or row.get("证据") or ""
        else:
            action = str(row)
            path = ""
            evidence = ""
        body_rows.append(
            "<tr>"
            f"<td>{html.escape(str(action))}</td>"
            f"<td>{html.escape(str(path))}</td>"
            f"<td>{html.escape(str(evidence))}</td>"
            "</tr>"
        )

    return (
        '<table class="task-table">'
        "<thead><tr>"
        f"<th>{headers[0]}</th><th>{headers[1]}</th><th>{headers[2]}</th>"
        "</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table>"
    )


def _render_sources(sources, fallback, lang="cn"):
    items = []
    for source in sources[:5]:
        if isinstance(source, dict):
            title = str(source.get("title", "")).strip()
            url = str(source.get("url", "")).strip()
        else:
            title = str(source).strip()
            url = ""
        if not title:
            continue
        if url:
            items.append(
                f'<li><a href="{html.escape(url)}" target="_blank">{html.escape(title)}</a></li>'
            )
        else:
            items.append(f"<li>{html.escape(title)}</li>")

    if not items:
        for item in fallback[:5]:
            title = item.get("title", "")
            url = item.get("url", "")
            if not title:
                continue
            if url:
                items.append(
                    f'<li><a href="{html.escape(url)}" target="_blank">{html.escape(title)}</a></li>'
                )
            else:
                items.append(f"<li>{html.escape(title)}</li>")

    if not items:
        empty = "暂无来源" if lang != "en" else "No sources yet."
        return f'<div class="empty">{empty}</div>'
    return f'<ul class="source-list">{"".join(items)}</ul>'


def _render_report_list(reports, lang="cn"):
    if not reports:
        empty = "暂无历史报告" if lang != "en" else "No reports yet."
        return f'<div class="empty">{empty}</div>'
    items = []
    for report in reports[:10]:
        date_label = report.get("report_date", "")
        url = report.get("report_url", "")
        if url:
            items.append(
                f'<li><a href="{html.escape(url)}" target="_blank">{html.escape(date_label)}</a></li>'
            )
        else:
            items.append(f"<li>{html.escape(date_label)}</li>")
    return f'<ul class="history-list">{"".join(items)}</ul>'


def _write_task_page(config, entry, summary_html, action_table_html, reports, lang="cn"):
    template_name = "task_en.html" if lang == "en" else "task.html"
    template_path = os.path.join(config.templates_dir, template_name)
    if not os.path.exists(template_path):
        return
    task_id = str(entry.get("task_id", "")).strip()
    if not task_id:
        return

    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    html_out = (
        template.replace("{{TASK_ID}}", html.escape(task_id))
        .replace("{{REPORT_TITLE}}", html.escape(str(entry.get("report_title", ""))))
        .replace("{{REPORT_VALUE}}", html.escape(str(entry.get("report_value", ""))))
        .replace("{{TASK_SUGGESTION}}", html.escape(str(entry.get("task_suggestion", ""))))
        .replace("{{TASK_FREQUENCY}}", html.escape(_frequency_label(entry.get("frequency"), lang)))
        .replace("{{TASK_STATUS}}", html.escape(_task_status_label(entry.get("task_status"), lang)))
        .replace("{{LAST_UPDATE}}", html.escape(entry.get("last_run_at", "") or ""))
        .replace("{{TASK_SUMMARY}}", summary_html)
        .replace("{{ACTION_TABLE}}", action_table_html)
        .replace("{{REPORT_LIST}}", _render_report_list(reports, lang=lang))
    )

    prefix = "en" if lang == "en" else "cn"
    target_dir = os.path.join(config.static_dir, prefix, "task")
    os.makedirs(target_dir, exist_ok=True)
    with open(os.path.join(target_dir, f"{task_id}.html"), "w", encoding="utf-8") as f:
        f.write(html_out)


def _write_task_report_page(config, entry, report, summary_html, action_table_html, sources_html, lang="cn"):
    template_name = "task_report_en.html" if lang == "en" else "task_report.html"
    template_path = os.path.join(config.templates_dir, template_name)
    if not os.path.exists(template_path):
        return ""

    task_id = str(entry.get("task_id", "")).strip()
    if not task_id:
        return ""

    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    prefix = "en" if lang == "en" else "cn"
    task_link = f"/{prefix}/task/{task_id}.html"

    html_out = (
        template.replace("{{TASK_LINK}}", html.escape(task_link))
        .replace("{{TASK_ID}}", html.escape(task_id))
        .replace("{{REPORT_DATE}}", html.escape(report.get("report_date", "")))
        .replace("{{REPORT_TITLE}}", html.escape(str(entry.get("report_title", ""))))
        .replace("{{TASK_SUGGESTION}}", html.escape(str(entry.get("task_suggestion", ""))))
        .replace("{{REPORT_SUMMARY}}", summary_html)
        .replace("{{ACTION_TABLE}}", action_table_html)
        .replace("{{SOURCE_LIST}}", sources_html)
    )

    report_date = report.get("report_date", "")
    target_dir = os.path.join(config.static_dir, prefix, "task", task_id, "reports")
    os.makedirs(target_dir, exist_ok=True)
    output_path = os.path.join(target_dir, f"{report_date}.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    return f"/{prefix}/task/{task_id}/reports/{report_date}.html"


def _filter_evidence(records, related_tokens, keywords, window_start, require_keyword=True, lang="cn"):
    items = []
    for record in records:
        dt = _record_dt(record)
        if window_start and dt and dt < window_start:
            continue
        title = record.get("title", "") or ""
        summary = record.get("summary_zh") or record.get("summary_en") or ""
        if lang == "en":
            summary = record.get("summary_en") or summary
        text = f"{title}\n{summary}"
        if require_keyword and not _match_keywords(text, keywords):
            continue
        if not _match_tokens(text, related_tokens):
            continue
        items.append(record)
    return items


def run_tasks(task_id="", force=False):
    config = Config.from_env()
    tasks_path = os.path.join(config.data_dir, "pay", "task_orders.jsonl")
    intel_path = os.path.join(config.data_dir, "intel.jsonl")

    raw_entries = _read_jsonl(tasks_path)
    if not raw_entries:
        print("No task orders found")
        return 0

    entries = _dedupe(raw_entries, "order_id")
    entries = _dedupe(entries, "task_id")
    if task_id:
        entries = [e for e in entries if e.get("task_id") == task_id]

    records = _read_jsonl(intel_path)
    keywords = load_keywords(config.keywords_file)
    extractor = GeminiExtractor(config)
    market_analysis = _load_market_analysis(config.data_dir)

    now = now_cst()
    ran = 0

    for entry in entries:
        status = entry.get("task_status") or ("active" if entry.get("status") == "confirmed" else "pending")
        if status != "active":
            continue

        freq = entry.get("frequency", "weekly")
        last_run_at = _parse_dt(entry.get("last_run_at", ""))
        if not _should_run(freq, last_run_at, now, force=force) and freq != "event":
            continue

        related_tokens = _extract_tokens(
            entry.get("report_title"),
            entry.get("report_value"),
            entry.get("task_suggestion"),
        )

        window_start = _time_window(freq, now)
        require_keyword = True
        if freq != "event":
            require_keyword = False
        if not keywords:
            require_keyword = False

        evidence = _filter_evidence(
            records,
            related_tokens,
            keywords,
            window_start=window_start,
            require_keyword=require_keyword,
            lang="cn",
        )

        if freq == "event" and not evidence:
            continue

        report_date = now.date().isoformat()
        reports_cn = _task_reports_list(config.data_dir, entry.get("task_id", ""), lang="cn")
        reports_en = _task_reports_list(config.data_dir, entry.get("task_id", ""), lang="en")
        if any(r.get("report_date") == report_date for r in reports_cn):
            if not force:
                continue

        evidence_cn = [_format_evidence(r, lang="cn") for r in evidence[:12]]
        evidence_en = [_format_evidence(r, lang="en") for r in evidence[:12]]

        time_label = f"{window_start.date().isoformat()} ~ {report_date}" if window_start else report_date

        report_data_cn = None
        report_data_en = None
        if evidence:
            prompt_cn = _build_prompt(
                entry,
                evidence_cn,
                lang="cn",
                time_window=time_label,
                market_analysis=market_analysis,
            )
            prompt_en = _build_prompt(
                entry,
                evidence_en,
                lang="en",
                time_window=time_label,
                market_analysis=market_analysis,
            )
            report_data_cn = _normalize_report(_generate_report(extractor, prompt_cn))
            report_data_en = _normalize_report(_generate_report(extractor, prompt_en))

        if report_data_cn is None:
            report_data_cn = {
                "summary": "本期暂无新增相关事件。",
                "action_table": [],
                "sources": [],
                "conclusion": "",
                "key_evidence": [],
                "assumptions": [],
            }
        if report_data_en is None:
            report_data_en = {
                "summary": "No new events in this period.",
                "action_table": [],
                "sources": [],
                "conclusion": "",
                "key_evidence": [],
                "assumptions": [],
            }

        summary_cn = _render_summary(report_data_cn.get("summary", ""), lang="cn")
        summary_en = _render_summary(report_data_en.get("summary", ""), lang="en")
        table_cn = _render_action_table(report_data_cn.get("action_table", []), lang="cn")
        table_en = _render_action_table(report_data_en.get("action_table", []), lang="en")
        sources_cn = _render_sources(report_data_cn.get("sources", []), evidence_cn, lang="cn")
        sources_en = _render_sources(report_data_en.get("sources", []), evidence_en, lang="en")

        report_entry_cn = {
            "task_id": entry.get("task_id"),
            "report_date": report_date,
            "created_at": isoformat(now),
            "summary": report_data_cn.get("summary", ""),
            "key_evidence": report_data_cn.get("key_evidence", []),
            "assumptions": report_data_cn.get("assumptions", []),
        }
        report_entry_en = {
            "task_id": entry.get("task_id"),
            "report_date": report_date,
            "created_at": isoformat(now),
            "summary": report_data_en.get("summary", ""),
            "key_evidence": report_data_en.get("key_evidence", []),
            "assumptions": report_data_en.get("assumptions", []),
        }

        report_url_cn = _write_task_report_page(
            config,
            entry,
            report_entry_cn,
            summary_cn,
            table_cn,
            sources_cn,
            lang="cn",
        )
        report_url_en = _write_task_report_page(
            config,
            entry,
            report_entry_en,
            summary_en,
            table_en,
            sources_en,
            lang="en",
        )
        report_entry_cn["report_url"] = report_url_cn
        report_entry_en["report_url"] = report_url_en

        _write_task_report_index(config.data_dir, entry.get("task_id", ""), "cn", report_entry_cn)
        _write_task_report_index(config.data_dir, entry.get("task_id", ""), "en", report_entry_en)

        updated_entry = dict(entry)
        updated_entry["last_run_at"] = isoformat(now)
        updated_entry["task_status"] = "active"
        updated_entry["result_count"] = int(updated_entry.get("result_count") or 0) + 1
        next_run = _next_run_at(freq, now)
        if next_run:
            updated_entry["next_run_at"] = isoformat(next_run)

        reports_cn = _task_reports_list(config.data_dir, entry.get("task_id", ""), lang="cn")
        reports_en = _task_reports_list(config.data_dir, entry.get("task_id", ""), lang="en")

        _write_task_page(
            config,
            updated_entry,
            summary_cn,
            table_cn,
            reports_cn,
            lang="cn",
        )
        _write_task_page(
            config,
            updated_entry,
            summary_en,
            table_en,
            reports_en,
            lang="en",
        )

        _update_task_orders(tasks_path, entry.get("task_id", ""), updated_entry)
        ran += 1

    print(f"Tasks executed: {ran}")
    return ran


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    run_tasks(task_id=args.task_id, force=args.force)


if __name__ == "__main__":
    main()
