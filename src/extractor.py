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

    def analyze(self, text):
        if not text:
            return None

        content = text[:12000]

        prompt = f"""
You are a rare earths and critical minerals intelligence analyst.
Analyze the article and return ONLY a JSON object (no markdown, no commentary).
If the content is not about rare earths or critical minerals, return null.

Location guidance:
- country_primary is where the main project/action occurs.
- Do NOT set country_primary to China unless the primary event is in China.
- If China is only mentioned as background, set china_involved/china_role but keep country_primary as the main non-China country.
- If unsure, set country_primary to "Unknown".

Output rules:
- summary_zh must be a concise professional Chinese summary.
- minerals and entities must be arrays (can be empty).
- action_type must be one of [Investment, Regulation, Discovery, Price, Geopolitics, Other].
- risk_level and importance_score are integers 1-5.
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

Article:
{content}
"""

        try:
            response = self.model.generate_content(prompt)
        except Exception:
            return None

        data = extract_json(getattr(response, "text", ""))
        if data is None:
            return None
        return normalize_analysis(data)
