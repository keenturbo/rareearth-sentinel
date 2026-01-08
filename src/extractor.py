import google.generativeai as genai

from .utils import extract_json, normalize_analysis, normalize_analysis_en


class GeminiExtractor:
    def __init__(self, config):
        if not config.gemini_api_key:
            raise ValueError("GEMINI_API_KEY (or GOOGLE_API_KEY) is required")
        genai.configure(api_key=config.gemini_api_key)
        self.model = genai.GenerativeModel(
            config.gemini_model,
            generation_config={"temperature": 0.2},
        )

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
- china_impact must be one of [83d, 42000c, 4940a]
- china_risk_trend must be one of [47a3a, 42b73, 31390c]
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
            response = self.model.generate_content(prompt)
        except Exception:
            return None

        data = extract_json(getattr(response, "text", ""))
        if data is None:
            return None
        return normalize_analysis(data)

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
            response = self.model.generate_content(prompt)
        except Exception:
            return None

        data = extract_json(getattr(response, "text", ""))
        if data is None:
            return None
        return normalize_analysis_en(data)

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

    def analyze_daily_report(self, context_text, lang="zh"):
        if not context_text:
            return None

        if lang == "en":
            prompt = f"""
You are an intelligence editor. Write a concise daily report in English for Western decision makers.
Use the event list below. Output ONLY JSON with fields: headline, body, bullets.
Constraints:
- headline: short and clear (<= 12 words).
- body: 120-180 words, concise and analytical.
- bullets: array of 3 short bullet points.
If signals are weak, keep the tone neutral.

Event list:
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

Event list:
{context_text}
"""

        try:
            response = self.model.generate_content(prompt)
        except Exception:
            return None

        data = extract_json(getattr(response, "text", ""))
        if data is None:
            return None
        return self._normalize_daily_report(data)
