import json
import math
from datetime import datetime
from urllib.parse import urlparse

import google.generativeai as genai

from ..utils import extract_json


class PremiumBriefGenerator:
    def __init__(self, config):
        if not config.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required for premium reports")
        genai.configure(api_key=config.gemini_api_key)
        self.model = genai.GenerativeModel(
            config.gemini_model,
            generation_config={"temperature": 0.2},
        )

    def generate(self, record, lang="zh"):
        content = str(record.get("content", "")).strip()
        if not content:
            return None
        content = content[:6000]

        title = str(record.get("title", "")).strip()
        summary_zh = str(record.get("summary_zh", "")).strip()
        summary_en = str(record.get("summary_en", "")).strip()
        country = str(record.get("country_primary", "")).strip()
        action = str(record.get("action_type", "")).strip()
        minerals = ", ".join(record.get("minerals") or [])
        credibility = record.get("credibility", {}) or {}
        credibility_score = credibility.get("score", 0)
        source_type = str(record.get("source_type", "")).strip()
        url = str(record.get("url", "")).strip()
        domain = ""
        if url:
            domain = urlparse(url).netloc
        official_id = str(record.get("official_source_id", "")).strip()

        meta_block = (
            f"Title: {title}\n"
            f"Country: {country}\n"
            f"Action: {action}\n"
            f"Minerals: {minerals}\n"
            f"CredibilityScore: {credibility_score}\n"
            f"SourceType: {source_type}\n"
            f"Domain: {domain}\n"
            f"OfficialSource: {official_id}\n"
        )

        if lang == "en":
            summary = summary_en or summary_zh
            prompt = f"""
You are a rare earth intelligence verification analyst. Generate a paid brief (USD $9) for Western decision makers.
Return ONLY JSON with fields:
- conclusion (1 sentence)
- fact_checks (array of 2-3 short items)
- fact_sources (array of 2-4 sources or domains used in fact checks)
- key_numbers (array of 2-4 items)
- impact_score (1-10 integer)
- tracking_nodes (array of 3 time-bound items)
- confidence_notes (array of 1-2 items explaining evidence limits)
- value_proposition (1 sentence explaining why this matters)

Constraints:
- Be concise, factual, and actionable.
- If data is missing, mark with "Unknown" rather than guessing.

Metadata:
{meta_block}
Summary:
{summary}

Article:
{content}
"""
        else:
            summary = summary_zh or summary_en
            prompt = f"""
你是一名稀土情报核查专家，请生成售价9美元的付费简报（给中国决策者）。
只输出JSON，包含字段：
- conclusion（一句话结论）
- fact_checks（2-3条核查要点）
- fact_sources（2-4条核查来源或域名）
- key_numbers（2-4条关键数字）
- impact_score（1-10分）
- tracking_nodes（3条需追踪节点）
- confidence_notes（1-2条证据局限说明）
- value_proposition（一句话说明为什么值得跟踪）

要求：简洁、可执行，不要猜测，缺失数据写“未知”。

元信息：
{meta_block}
摘要：
{summary}

正文：
{content}
"""

        try:
            response = self.model.generate_content(prompt)
        except Exception:
            return None

        data = extract_json(getattr(response, "text", ""))
        return normalize_brief(data, lang=lang)


def normalize_brief(data, lang="zh"):
    if not isinstance(data, dict):
        return None

    def _as_str(value):
        if value is None:
            return ""
        return str(value).strip()

    def _as_list(value, limit=4):
        if value is None:
            return []
        if isinstance(value, list):
            items = [str(v).strip() for v in value if str(v).strip()]
        elif isinstance(value, str):
            items = [v.strip("- ").strip() for v in value.splitlines() if v.strip()]
        else:
            items = []
        return items[:limit]

    def _score(value):
        try:
            num = int(value)
        except (TypeError, ValueError):
            return 5
        return max(1, min(10, num))

    normalized = {
        "conclusion": _as_str(data.get("conclusion")),
        "fact_checks": _as_list(data.get("fact_checks"), limit=3),
        "fact_sources": _as_list(data.get("fact_sources"), limit=4),
        "key_numbers": _as_list(data.get("key_numbers"), limit=4),
        "impact_score": _score(data.get("impact_score")),
        "tracking_nodes": _as_list(data.get("tracking_nodes"), limit=3),
        "confidence_notes": _as_list(data.get("confidence_notes"), limit=2),
        "value_proposition": _as_str(data.get("value_proposition")),
    }

    if not normalized["value_proposition"]:
        if lang == "en":
            normalized["value_proposition"] = "Strategic signal with direct supply-chain implications."
        else:
            normalized["value_proposition"] = "具备直接供应链影响的战略信号，值得持续跟踪。"

    return normalized


def brief_quality_score(brief):
    if not isinstance(brief, dict):
        return 0
    score = 0
    if len(str(brief.get("conclusion", ""))) >= 15:
        score += 15
    if len(brief.get("fact_checks", [])) >= 2:
        score += 20
    if len(brief.get("fact_sources", [])) >= 2:
        score += 10
    if len(brief.get("key_numbers", [])) >= 2:
        score += 15
    if len(brief.get("tracking_nodes", [])) >= 2:
        score += 15
    if len(brief.get("confidence_notes", [])) >= 1:
        score += 10
    if len(str(brief.get("value_proposition", ""))) >= 12:
        score += 15
    return score
