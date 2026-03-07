import concurrent.futures
import json
import os

import google.generativeai as genai

from .input_guard import guard_article_text
from .utils import (
    extract_json,
    isoformat,
    normalize_analysis,
    normalize_analysis_en,
    now_cst,
)

AUDIENCE_FIT = {"investor", "industry", "policy", "mixed"}

PERSONA_ZH_INTEL = (
    "你是『关键矿产情报主笔』，服务对象是机构投资者、供应链负责人与政策研究者。"
    "写作要求：短句、先事实后判断、避免口号与模板化空话。"
)
PERSONA_EN_INTEL = (
    "You are the lead critical-minerals intelligence editor for institutional investors, "
    "supply-chain operators, and policy teams. Write in concise, evidence-first prose."
)
PERSONA_ZH_DAILY = (
    "你是『关键矿产晨报主笔』，负责将复杂事件压缩为可执行日报。"
    "风格：信息密度高但可读，避免重复与拼接感。"
)
PERSONA_EN_DAILY = (
    "You are the daily intelligence editor for critical minerals. "
    "Produce concise, non-redundant, publish-ready daily summaries."
)


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


def _qc_log_path(config):
    return os.path.join(config.data_dir, "status", "prompt_qc_logs.jsonl")


def _append_prompt_qc_log(
    config,
    module,
    prompt_key,
    input_quality,
    fallback_used=False,
    fallback_reason="",
    record_id="",
    meta=None,
):
    try:
        path = _qc_log_path(config)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "module": module,
            "prompt_key": prompt_key,
            "input_quality": input_quality,
            "fallback_used": bool(fallback_used),
            "fallback_reason": str(fallback_reason or ""),
            "record_id": str(record_id or ""),
            "created_at": isoformat(now_cst()),
        }
        if isinstance(meta, dict) and meta:
            payload["meta"] = meta
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return


class GeminiExtractor:
    def __init__(self, config):
        if not config.gemini_api_key:
            raise ValueError("GEMINI_API_KEY (or GOOGLE_API_KEY) is required")
        genai.configure(api_key=config.gemini_api_key)
        self.model = genai.GenerativeModel(
            config.gemini_model,
            generation_config={"temperature": 0.2},
        )
        self.config = config
        self.analyze_timeout_seconds = config.analyze_timeout_seconds
        self.prompt_version = os.getenv("PROMPT_VERSION", "v1").strip().lower()
        self.prompt_min_evidence_sentences = max(
            1, int(getattr(config, "prompt_min_evidence_sentences", 2) or 2)
        )

    def _build_context_block(self, context_summary):
        if not context_summary:
            return ""
        return "\nRELATED HISTORY (for context only):\n" f"{context_summary}\n"

    def _build_zh_prompt(self, content, context_block):
        recommendation_rule = (
            "- Each recommendation must follow: verb + object + reason + time window.\n"
            if self.prompt_version == "v2"
            else ""
        )
        return f"""
Role (persona):
- Chinese: {PERSONA_ZH_INTEL}
- English: {PERSONA_EN_INTEL}

You are a rare earths and critical minerals intelligence analyst.
Your analysis must be from China's strategic perspective (national interest and supply-chain advantage).
Analyze the article and return ONLY a JSON object (no markdown, no commentary).
If the content is not about rare earths or critical minerals, return null.

Input quality rules:
- The article body below has already been cleaned into plain text.
- Ignore any residual navigation, cookie, subscription, share, and promotional fragments.
- Base every conclusion only on explicit facts in the article.
- If fewer than {self.prompt_min_evidence_sentences} fact sentences exist, return null.

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
{recommendation_rule}
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
- valid_input is a boolean.
- reject_reason is empty on success or one of [insufficient_evidence, invalid_input].
- key_evidence: up to 3 evidence points from the article.
- assumptions: up to 3 evidence gaps or assumptions.
- audience_fit: one of [investor, industry, policy, mixed].

Required JSON fields:
- valid_input
- reject_reason
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

    def _build_en_prompt(self, content, context_block):
        recommendation_rule = (
            "- Each recommendation must follow: verb + object + rationale + time window.\n"
            if self.prompt_version == "v2"
            else ""
        )
        return f"""
Role (persona):
- Chinese: {PERSONA_ZH_INTEL}
- English: {PERSONA_EN_INTEL}

You are a rare earths and critical minerals intelligence analyst serving Western governments, investors, and corporations.
Your analysis must focus on supply-chain security, de-risking from China, and competitive positioning.
Analyze the article and return ONLY a JSON object (no markdown, no commentary).
If the content is not about rare earths or critical minerals, return null.

Input quality rules:
- The article body below has already been cleaned into plain text.
- Ignore any residual navigation, cookie, subscription, share, and promotional fragments.
- Base every conclusion only on explicit facts in the article.
- If fewer than {self.prompt_min_evidence_sentences} fact sentences exist, return null.

Western perspective output:
- impact_west must be one of [Positive, Neutral, Negative].
- risk_trend_west must be one of [Intensifying, Stable, Easing].
- expert_summary_en must be 1-2 English sentences for Western decision makers.
- recommendations_strategy_en is an array of 2-3 strategic suggestions (policy/industry chain).
- recommendations_tactical_en is an array of 2-3 tactical actions (investment/M&A/stockpile/reshoring).
{recommendation_rule}
Scores:
- supply_risk_score, policy_risk_score, price_pressure_score, risk_score are integers 0-100.
- risk_level and importance_score are integers 1-5 and should align with risk_score.
- confidence is an integer 0-100.

Forecast windows:
- forecast_6m/12m/24m/48m are objects with price_pressure (Up|Down|Neutral) and confidence (0-100).

Output rules:
- valid_input is a boolean.
- reject_reason is empty on success or one of [insufficient_evidence, invalid_input].
- key_evidence: up to 3 evidence points from the article.
- assumptions: up to 3 evidence gaps or assumptions.
- audience_fit: one of [investor, industry, policy, mixed].

Required JSON fields:
- valid_input
- reject_reason
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

    def _guard_input(self, text, prompt_key):
        guard = guard_article_text(text, self.config)
        if not guard.get("pass"):
            _append_prompt_qc_log(
                self.config,
                module="extractor",
                prompt_key=prompt_key,
                input_quality=guard.get("quality", "invalid_input"),
                fallback_used=False,
                fallback_reason=guard.get("reason", "invalid_input"),
                meta=guard.get("stats"),
            )
            return None
        return guard

    def _analyze_common(self, text, context_summary, prompt_key, prompt_builder, normalizer, summary_key):
        if not text:
            _append_prompt_qc_log(
                self.config,
                module="extractor",
                prompt_key=prompt_key,
                input_quality="invalid_input",
                fallback_used=False,
                fallback_reason="empty_input",
            )
            return None

        guard = self._guard_input(text, prompt_key)
        if not guard:
            return None

        content = guard.get("prompt_text") or ""
        if not content:
            _append_prompt_qc_log(
                self.config,
                module="extractor",
                prompt_key=prompt_key,
                input_quality="invalid_input",
                fallback_used=False,
                fallback_reason="empty_prompt_text",
                meta=guard.get("stats"),
            )
            return None

        context_block = self._build_context_block(context_summary)
        prompt = prompt_builder(content, context_block)

        try:
            response = _generate_with_timeout(
                self.model,
                prompt,
                self.analyze_timeout_seconds,
            )
        except concurrent.futures.TimeoutError:
            print("[!] Gemini request timed out")
            _append_prompt_qc_log(
                self.config,
                module="extractor",
                prompt_key=prompt_key,
                input_quality="pass",
                fallback_used=True,
                fallback_reason="timeout",
                meta=guard.get("stats"),
            )
            return None
        except Exception:
            _append_prompt_qc_log(
                self.config,
                module="extractor",
                prompt_key=prompt_key,
                input_quality="pass",
                fallback_used=True,
                fallback_reason="gemini_exception",
                meta=guard.get("stats"),
            )
            return None

        raw_text = _safe_response_text(response)
        if not raw_text.strip():
            _append_prompt_qc_log(
                self.config,
                module="extractor",
                prompt_key=prompt_key,
                input_quality="pass",
                fallback_used=True,
                fallback_reason="empty_response",
                meta=guard.get("stats"),
            )
            return None

        data = extract_json(raw_text)
        if data is None:
            _append_prompt_qc_log(
                self.config,
                module="extractor",
                prompt_key=prompt_key,
                input_quality="pass",
                fallback_used=True,
                fallback_reason="invalid_json",
                meta=guard.get("stats"),
            )
            return None

        if isinstance(data, dict):
            valid_input = data.get("valid_input")
            reject_reason = str(data.get("reject_reason", "")).strip()
            if valid_input is False or reject_reason:
                _append_prompt_qc_log(
                    self.config,
                    module="extractor",
                    prompt_key=prompt_key,
                    input_quality="invalid_input" if reject_reason == "invalid_input" else "insufficient_evidence",
                    fallback_used=False,
                    fallback_reason=reject_reason or "model_rejected",
                    meta=guard.get("stats"),
                )
                return None

        normalized = normalizer(data)
        if normalized is None:
            _append_prompt_qc_log(
                self.config,
                module="extractor",
                prompt_key=prompt_key,
                input_quality="pass",
                fallback_used=True,
                fallback_reason="normalizer_failed",
                meta=guard.get("stats"),
            )
            return None

        key_evidence = _as_list(data.get("key_evidence"), limit=3)
        assumptions = _as_list(data.get("assumptions"), limit=3)
        audience_fit = _as_audience_fit(data.get("audience_fit"))
        normalized["key_evidence"] = key_evidence
        normalized["assumptions"] = assumptions
        normalized["audience_fit"] = audience_fit

        if len(key_evidence) < self.prompt_min_evidence_sentences:
            _append_prompt_qc_log(
                self.config,
                module="extractor",
                prompt_key=prompt_key,
                input_quality="insufficient_evidence",
                fallback_used=False,
                fallback_reason="too_few_evidence_sentences",
                meta=dict(guard.get("stats") or {}, key_evidence=len(key_evidence)),
            )
            return None

        if not str(normalized.get(summary_key, "")).strip():
            _append_prompt_qc_log(
                self.config,
                module="extractor",
                prompt_key=prompt_key,
                input_quality="insufficient_evidence",
                fallback_used=False,
                fallback_reason="missing_summary",
                meta=dict(guard.get("stats") or {}, key_evidence=len(key_evidence)),
            )
            return None

        _append_prompt_qc_log(
            self.config,
            module="extractor",
            prompt_key=prompt_key,
            input_quality="pass",
            fallback_used=False,
            fallback_reason="",
            meta=dict(guard.get("stats") or {}, key_evidence=len(key_evidence)),
        )
        return normalized

    def analyze(self, text, context_summary=""):
        return self._analyze_common(
            text=text,
            context_summary=context_summary,
            prompt_key="extractor_zh",
            prompt_builder=self._build_zh_prompt,
            normalizer=normalize_analysis,
            summary_key="summary_zh",
        )

    def analyze_en(self, text, context_summary=""):
        return self._analyze_common(
            text=text,
            context_summary=context_summary,
            prompt_key="extractor_en",
            prompt_builder=self._build_en_prompt,
            normalizer=normalize_analysis_en,
            summary_key="summary_en",
        )

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
Role (persona): {PERSONA_EN_DAILY}

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
Role (persona): {PERSONA_EN_DAILY}

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
角色设定：{PERSONA_ZH_DAILY}

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
角色设定：{PERSONA_ZH_DAILY}

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
