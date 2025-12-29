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
You are a strategic intelligence analyst focused on rare earths and critical minerals.
Analyze the article and return ONLY a JSON object (no markdown, no commentary).
If the content is not about rare earths/critical minerals, return null.

Guidance for location fields:
- country_primary is where the main project/action occurs.
- Do NOT set country_primary to China unless the primary event is in China.
- If China is only mentioned as background, set china_involved/china_role but keep country_primary as the main non-China country.
- If unsure, set country_primary to "Unknown".

Required JSON fields:
- summary_zh: one-sentence summary in Chinese
- country_primary: primary event country, use English name
- event_location: specific location if available, else empty string
- minerals: list of minerals (e.g., Rare Earths, Lithium, Cobalt)
- entities: list of companies or government bodies
- action_type: one of [Investment, Regulation, Discovery, Price, Geopolitics, Other]
- risk_level: integer 1-5
- importance_score: integer 1-5
- china_involved: boolean
- china_dependency: one of [None, Low, Medium, High]
- china_role: one of [Supplier, Processor, Investor, Policy, Mentioned]
- confidence: integer 0-100

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
