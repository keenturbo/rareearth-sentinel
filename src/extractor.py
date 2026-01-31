import concurrent.futures
import os

import google.generativeai as genai

from .utils import extract_json, normalize_analysis, normalize_analysis_en

AUDIENCE_FIT = {"investor", "industry", "policy", "mixed"}


def _safe_response_text(response):
    try:
        return response.text or ""
    except Exception:
        pass
    try:
        cand = response.candidates[0] if getattr(response, "candidates", None) else None
        parts = getattr(getattr(cand, "content", None), "parts", []) if cand else []
        return "".join(
            getattr(part, "text", "")
            for part in parts
            if getattr(part, "text", "")
        )
    except Exception:
        return ""


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


def _as_audience_fit(value):
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text in AUDIENCE_FIT:
        return text
    return "mixed"


def _generate_with_timeout(model, prompt, timeout_seconds):
    if not timeout_seconds or timeout_seconds <= 0:
        return model.generate_content(prompt)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(model.generate_content, prompt)
        return future.result(timeout=timeout_seconds)


class GeminiExtractor:
    def __init__(self, config):
        if not config.gemini_api_key:
            raise ValueError("GEMINI_API_KEY (or GOOGLE_API_KEY) is required")
        genai.configure(api_key=config.gemini_api_key)
        self.model = genai.GenerativeModel(
            config.gemini_model,
            generation_config={"temperature": 0.2},
        )
        self.analyze_timeout_seconds = config.analyze_timeout_seconds
        self.prompt_version = os.getenv("PROMPT_VERSION", "v1").strip().lower()

    def analyze(self, text, context_summary=""):
        if not text:
            return None

        content = text[:12000]
        context_block = ""
        if context_summary:
            context_block = (
                "\nRELATED HISTORY (for context only):\n"
                f"{context_summary}\n"
            )

        if self.prompt_version == "v2":
            prompt = f"""
You are a rare earths and critical minerals intelligence analyst.
Your analysis must be from China's strategic perspective (national interest and supply-chain advantage).
Analyze the article and return ONLY a JSON object (no markdown, no commentary).
If the content is not about rare earths or critical minerals, return null.

Location guidance:
- country_primary is where the main project/action occurs.
- Do NOT set country_primary to China unless the primary event is in China.
- If China is only mentioned as background, set china_involved/china_role but keep country_primary as the main non-China country.
- If unsure, set country_primary to "Unknown".

China perspective output:
- china_impact must be one of [利好, 中性, 利空].
- china_risk_trend must be one of [加强, 持平, 缓和].
- expert_summary must be 1-2 Chinese sentences for decision makers.
- recommendations_strategy is an array of 2-3 strategic suggestions (policy/industry chain).
- recommendations_tactical is an array of 2-3 tactical actions (investment/M&A/export/stockpile).
- Each recommendation must follow: verb + object + reason + time window.

Scores:
- supply_risk_score, policy_risk_score, price_pressure_score, risk_score are integers 0-100.
- risk_level and importance_score are integers 1-5 and should align with risk_score.

Forecast windows:
- forecast_6m/12m/24m/48m are objects with price_pressure (Up|Down|Neutral) and confidence (0-100).

Output rules:
- summary_zh must be a concise professional Chinese summary.
- minerals and entities must be arrays (can be empty).
- action_type must be one of [Investment, Regulation, Discovery, Price, Geopolitics, Other].
- china_involved is a boolean.
- china_dependency is one of [None, Low, Medium, High].
- china_role is one of [Supplier, Processor, Investor, Policy, Mentioned].
- confidence is an integer 0-100.
- key_evidence: up to 3 evidence points from the article.
- assumptions: up to 3 evidence gaps or assumptions.
- audience_fit: one of [investor, industry, policy, mixed].

Required JSON fields:
- summary_zh
- country_primary
- event_location
- minerals
- entities
- action_type
- risk_level
- importance_score
- china_involved
- china_dependency
- china_role
- confidence
- china_impact
- china_risk_trend
- expert_summary
- recommendations_strategy
- recommendations_tactical
- supply_risk_score
- policy_risk_score
- price_pressure_score
- risk_score
- forecast_6m
- forecast_12m
- forecast_24m
- forecast_48m
- key_evidence
- assumptions
- audience_fit

Article:
{content}
{context_block}
"""
        else:
            prompt = f"""
You are a rare earths and critical minerals intelligence analyst.
Your analysis must be from China's strategic perspective (national interest and supply-chain advantage).
Analyze the article and return ONLY a JSON object (no markdown, no commentary).
If the content is not about rare earths or critical minerals, return null.

Location guidance:
- country_primary is where the main project/action occurs.
- Do NOT set country_primary to China unless the primary event is in China.
- If China is only mentioned as background, set china_involved/china_role but keep country_primary as the main non-China country.
- If unsure, set country_primary to "Unknown".

China perspective output:
- china_impact must be one of [利好, 中性, 利空].
- china_risk_trend must be one of [加强, 持平, 缓和].
- expert_summary must be 1-2 Chinese sentences for decision makers.
- recommendations_strategy is an array of 2-3 strategic suggestions (policy/industry chain).
- recommendations_tactical is an array of 2-3 tactical actions (investment/M&A/export/stockpile).

Scores:
- supply_risk_score, policy_risk_score, price_pressure_score, risk_score are integers 0-100.
- risk_level and importance_score are integers 1-5 and should align with risk_score.

Forecast windows:
- forecast_6m/12m/24m/48m are objects with price_pressure (Up|Down|Neutral) and confidence (0-100).

Output rules:
- summary_zh must be a concise professional Chinese summary.
- minerals and entities must be arrays (can be empty).
- action_type must be one of [Investment, Regulation, Discovery, Price, Geopolitics, Other].
- china_involved is a boolean.
- china_dependency is one of [None, Low, Medium, High].
- china_role is one of [Supplier, Processor, Investor, Policy, Mentioned].
- confidence is an integer 0-100.

Required JSON fields:
- summary_zh
- country_primary
- event_location
- minerals
- entities
- action_type
- risk_level
- importance_score
- china_involved
- china_dependency
- china_role
- confidence
- china_impact
- china_risk_trend
- expert_summary
- recommendations_strategy
- recommendations_tactical
- supply_risk_score
- policy_risk_score
- price_pressure_score
- risk_score
- forecast_6m
- forecast_12m
- forecast_24m
- forecast_48m

Article:
{content}
{context_block}
"""

        try:
            response = _generate_with_timeout(
                self.model,
                prompt,
                self.analyze_timeout_seconds,
            )
        except concurrent.futures.TimeoutError:
            print("[!] Gemini request timed out")
            return None
        except Exception:
            return None

        raw_text = _safe_response_text(response)
        if not raw_text.strip():
            return None

        data = extract_json(raw_text)
        if data is None:
            return None
        normalized = normalize_analysis(data)
        if normalized is None:
            return None
        normalized["key_evidence"] = _as_list(data.get("key_evidence"), limit=3)
        normalized["assumptions"] = _as_list(data.get("assumptions"), limit=3)
        normalized["audience_fit"] = _as_audience_fit(data.get("audience_fit"))
        return normalized

    def analyze_en(self, text, context_summary=""):
        if not text:
            return None

        content = text[:12000]
        context_block = ""
        if context_summary:
            context_block = (
                "\nRELATED HISTORY (for context only):\n"
                f"{context_summary}\n"
            )

        if self.prompt_version == "v2":
            prompt = f"""
You are a rare earths and critical minerals intelligence analyst serving Western governments, investors, and corporations.
Your analysis must focus on supply-chain security, de-risking from China, and competitive positioning.
Analyze the article and return ONLY a JSON object (no markdown, no commentary).
If the content is not about rare earths or critical minerals, return null.

Western perspective output:
- impact_west must be one of [Positive, Neutral, Negative].
- risk_trend_west must be one of [Intensifying, Stable, Easing].
- expert_summary_en must be 1-2 English sentences for Western decision makers.
- recommendations_strategy_en is an array of 2-3 strategic suggestions (policy/industry chain).
- recommendations_tactical_en is an array of 2-3 tactical actions (investment/M&A/stockpile/reshoring).
- Each recommendation must follow: verb + object + rationale + time window.

Scores:
- supply_risk_score, policy_risk_score, price_pressure_score, risk_score are integers 0-100.
- risk_level and importance_score are integers 1-5 and should align with risk_score.
- confidence is an integer 0-100.

Forecast windows:
- forecast_6m/12m/24m/48m are objects with price_pressure (Up|Down|Neutral) and confidence (0-100).

Output rules:
- key_evidence: up to 3 evidence points from the article.
- assumptions: up to 3 evidence gaps or assumptions.
- audience_fit: one of [investor, industry, policy, mixed].

Required JSON fields:
- summary_en
- impact_west
- risk_trend_west
- expert_summary_en
- recommendations_strategy_en
- recommendations_tactical_en
- supply_risk_score
- policy_risk_score
- price_pressure_score
- risk_score
- risk_level
- importance_score
- confidence
- forecast_6m
- forecast_12m
- forecast_24m
- forecast_48m
- key_evidence
- assumptions
- audience_fit

Article:
{content}
{context_block}
"""
        else:
            prompt = f"""
You are a rare earths and critical minerals intelligence analyst serving Western governments, investors, and corporations.
Your analysis must focus on supply-chain security, de-risking from China, and competitive positioning.
Analyze the article and return ONLY a JSON object (no markdown, no commentary).
If the content is not about rare earths or critical minerals, return null.

Western perspective output:
- impact_west must be one of [Positive, Neutral, Negative].
- risk_trend_west must be one of [Intensifying, Stable, Easing].
- expert_summary_en must be 1-2 English sentences for Western decision makers.
- recommendations_strategy_en is an array of 2-3 strategic suggestions (policy/industry chain).
- recommendations_tactical_en is an array of 2-3 tactical actions (investment/M&A/stockpile/reshoring).

Scores:
- supply_risk_score, policy_risk_score, price_pressure_score, risk_score are integers 0-100.
- risk_level and importance_score are integers 1-5 and should align with risk_score.
- confidence is an integer 0-100.

Forecast windows:
- forecast_6m/12m/24m/48m are objects with price_pressure (Up|Down|Neutral) and confidence (0-100).

Required JSON fields:
- summary_en
- impact_west
- risk_trend_west
- expert_summary_en
- recommendations_strategy_en
- recommendations_tactical_en
- supply_risk_score
- policy_risk_score
- price_pressure_score
- risk_score
- risk_level
- importance_score
- confidence
- forecast_6m
- forecast_12m
- forecast_24m
- forecast_48m

Article:
{content}
{context_block}
"""

        try:
            response = _generate_with_timeout(
                self.model,
                prompt,
                self.analyze_timeout_seconds,
            )
        except concurrent.futures.TimeoutError:
            print("[!] Gemini request timed out")
            return None
        except Exception:
            return None

        raw_text = _safe_response_text(response)
        if not raw_text.strip():
            return None

        data = extract_json(raw_text)
        if data is None:
            return None
        normalized = normalize_analysis_en(data)
        if normalized is None:
            return None
        normalized["key_evidence"] = _as_list(data.get("key_evidence"), limit=3)
        normalized["assumptions"] = _as_list(data.get("assumptions"), limit=3)
        normalized["audience_fit"] = _as_audience_fit(data.get("audience_fit"))
        return normalized

    def _normalize_daily_report(self, data):
        if not isinstance(data, dict):
            return None
        headline = str(data.get("headline", "")).strip()
        body = str(data.get("body", "")).strip()
        bullets = data.get("bullets") or []
        if isinstance(bullets, str):
            bullets = [
                entry.strip("- ").strip()
                for entry in bullets.splitlines()
                if entry.strip()
            ]
        if not isinstance(bullets, list):
            bullets = []
        bullets = [str(entry).strip() for entry in bullets if str(entry).strip()]
        return {
            "headline": headline,
            "body": body,
            "bullets": bullets[:3],
        }

    def analyze_daily_report(self, context_text, lang="zh", market_analysis=""):
        if not context_text:
            return None

        market_block = ""
        if market_analysis:
            if lang == "en":
                market_block = (
                    "\nMarket commentary (background only, not verified):\n"
                    f"{market_analysis}\n"
                )
            else:
                market_block = (
                    "\n市场行情评论（仅背景，不作为已验证事实）：\n"
                    f"{market_analysis}\n"
                )

        if lang == "en":
            if self.prompt_version == "v2":
                prompt = f"""
You are an intelligence editor. Write a concise daily report in English for Western decision makers.
Use the event list below. Output ONLY JSON with fields: headline, body, bullets.
Constraints:
- headline: short and clear (<= 12 words).
- body: 120-180 words, concise and analytical, emphasize change, impact path, and evidence.
- bullets: array of 3 short bullet points (event + impact).
If signals are weak, keep the tone neutral and avoid hype.
{market_block}
Event list:
{context_text}
"""
            else:
                prompt = f"""
You are an intelligence editor. Write a concise daily report in English for Western decision makers.
Use the event list below. Output ONLY JSON with fields: headline, body, bullets.
Constraints:
- headline: short and clear (<= 12 words).
- body: 120-180 words, concise and analytical.
- bullets: array of 3 short bullet points.
If signals are weak, keep the tone neutral.
{market_block}
Event list:
{context_text}
"""
        else:
            if self.prompt_version == "v2":
                prompt = f"""
你是情报编辑，请基于事件清单撰写中文日报（给中国决策者）。
只输出 JSON，字段为：headline, body, bullets。
要求：
- headline：<= 20 个汉字。
- body：150-300 字，强调变化、影响路径与证据，避免口号。
- bullets：3 条短要点（事件 + 影响）。
若信号偏弱，保持中性语气。
{market_block}
事件清单：
{context_text}
"""
            else:
                prompt = f"""
You are an intelligence editor. Write a concise daily report in Chinese for Chinese decision makers.
Use the event list below. Output ONLY JSON with fields: headline, body, bullets.
Constraints:
- headline: <= 20 Chinese characters.
- body: 150-300 Chinese characters, concise and analytical.
- bullets: array of 3 short bullet points.
If signals are weak, keep the tone neutral.
{market_block}
Event list:
{context_text}
"""

        try:
            response = _generate_with_timeout(
                self.model,
                prompt,
                self.analyze_timeout_seconds,
            )
        except concurrent.futures.TimeoutError:
            print("[!] Gemini request timed out")
            return None
        except Exception:
            return None

        raw_text = _safe_response_text(response)
        if not raw_text.strip():
            return None

        data = extract_json(raw_text)
        if data is None:
            return None
        return self._normalize_daily_report(data)
