import google.generativeai as genai

from .utils import extract_json, normalize_analysis


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
            response = self.model.generate_content(prompt)
        except Exception:
            return None

        data = extract_json(getattr(response, "text", ""))
        if data is None:
            return None
        return normalize_analysis(data)
