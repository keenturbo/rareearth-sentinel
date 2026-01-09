import json
import os
from datetime import datetime

from .briefs import PremiumBriefGenerator, brief_quality_score
from ..utils import now_cst


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def _today_dir(base_dir):
    today = now_cst().strftime("%Y-%m-%d")
    path = os.path.join(base_dir, today)
    _ensure_dir(path)
    return path, today


def _brief_filename(report_id, lang):
    return f"brief_{report_id}_{lang}.json"


def _brief_md_filename(report_id, lang):
    return f"brief_{report_id}_{lang}.md"


def _build_report_id(record):
    return record.get("id") or f"event_{int(now_cst().timestamp())}"


def _report_preview(brief, lang="zh"):
    if not brief:
        return {}
    title = brief.get("conclusion", "")
    value = brief.get("value_proposition", "")
    preview_text = "; ".join(brief.get("fact_checks", [])[:2])
    if lang == "en":
        if not preview_text:
            preview_text = "Includes fact checks, key numbers, and next milestones."
    else:
        if not preview_text:
            preview_text = "包含事实核查、关键数字与追踪节点。"
    return {
        "title": title,
        "value": value,
        "preview": preview_text,
    }


def _brief_to_markdown(brief, lang="zh"):
    if not brief:
        return ""
    lines = []
    if lang == "en":
        lines.append("# Event Brief")
        lines.append(f"**Conclusion**: {brief.get('conclusion','')}")
        lines.append("\n## Fact Checks")
        for item in brief.get("fact_checks", []):
            lines.append(f"- {item}")
        lines.append("\n## Key Numbers")
        for item in brief.get("key_numbers", []):
            lines.append(f"- {item}")
        lines.append("\n## Impact Score")
        lines.append(f"- {brief.get('impact_score', '')}/10")
        lines.append("\n## Tracking Nodes")
        for item in brief.get("tracking_nodes", []):
            lines.append(f"- {item}")
        lines.append("\n## Why It Matters")
        lines.append(brief.get("value_proposition", ""))
    else:
        lines.append("# 事件简报")
        lines.append(f"**结论**：{brief.get('conclusion','')}")
        lines.append("\n## 事实核查")
        for item in brief.get("fact_checks", []):
            lines.append(f"- {item}")
        lines.append("\n## 关键数字")
        for item in brief.get("key_numbers", []):
            lines.append(f"- {item}")
        lines.append("\n## 对中国冲击评分")
        lines.append(f"- {brief.get('impact_score', '')}/10")
        lines.append("\n## 追踪节点")
        for item in brief.get("tracking_nodes", []):
            lines.append(f"- {item}")
        lines.append("\n## 价值说明")
        lines.append(brief.get("value_proposition", ""))
    return "\n".join(lines).strip()


def _should_generate(record, config):
    credibility = record.get("credibility", {}) or {}
    score = credibility.get("score", 0)
    importance = record.get("importance_score", 0) or 0
    return score >= config.premium_min_credibility and importance >= config.premium_min_importance


def _score_for_top(record):
    try:
        risk = int(record.get("risk_score", 0) or 0)
    except (TypeError, ValueError):
        risk = 0
    try:
        importance = int(record.get("importance_score", 0) or 0)
    except (TypeError, ValueError):
        importance = 0
    credibility = record.get("credibility", {}) or {}
    credibility_score = credibility.get("score", 0)
    return 0.4 * risk + 0.3 * importance * 20 + 0.2 * credibility_score + 0.1 * (5 if str(record.get("source_type","" )).startswith("official") else 0)


def generate_daily_briefs(config, records, limit=None):
    if not config.premium_enabled:
        return []

    today = now_cst().date()
    today_records = []
    for record in records:
        collected_at = record.get("collected_at", "")
        if not collected_at:
            continue
        try:
            dt = datetime.fromisoformat(collected_at)
        except ValueError:
            continue
        if dt.date() != today:
            continue
        if not _should_generate(record, config):
            continue
        today_records.append(record)

    if not today_records:
        return []

    today_records = sorted(today_records, key=_score_for_top, reverse=True)
    limit = limit or config.premium_top_n
    top_records = today_records[:limit]

    generator = PremiumBriefGenerator(config)
    output_dir, date_key = _today_dir(config.premium_dir)

    index_entries = []
    for record in top_records:
        report_id = _build_report_id(record)
        entry = {
            "report_id": report_id,
            "event_id": record.get("id"),
            "tier": "brief",
            "price": 9,
            "date": date_key,
            "source_title": record.get("title", ""),
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        entry_has_any = False

        for lang in ("zh", "en"):
            brief = generator.generate(record, lang=lang)
            if brief is None:
                continue
            quality = brief_quality_score(brief)
            if quality < config.premium_min_quality:
                continue
            entry_has_any = True
            entry[f"quality_{lang}"] = quality
            entry[f"preview_{lang}"] = _report_preview(brief, lang=lang)

            report_path = os.path.join(output_dir, _brief_filename(report_id, lang))
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(brief, f, ensure_ascii=False, indent=2)

            md_path = os.path.join(output_dir, _brief_md_filename(report_id, lang))
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(_brief_to_markdown(brief, lang=lang))

        if entry_has_any:
            index_entries.append(entry)

    if index_entries:
        index_path = os.path.join(output_dir, "index.json")
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index_entries, f, ensure_ascii=False, indent=2)

    return index_entries
