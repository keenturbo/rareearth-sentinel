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
    "你是『关键矿产市场情报主笔』，服务对象是机构投资者、产业链从业者与行业研究者。"
    "写作要求：客观中立、短句、先事实后判断、避免口号与模板化空话。"
)
PERSONA_EN_INTEL = (
    "You are an objective critical-minerals market intelligence editor for institutional investors, "
    "commodity analysts, and supply-chain researchers. Write concise, neutral, evidence-first prose."
)
PERSONA_ZH_DAILY = (
    "你是『关键矿产晨报主笔』，负责将公开行业事件压缩为客观可读的市场日报。"
    "风格：信息密度高、中立克制、避免重复与拼接感。"
)
PERSONA_EN_DAILY = (
    "You are a neutral daily market intelligence editor for critical minerals. "
    "Produce concise, objective, non-redundant daily summaries."
)

_EN_OPTIONAL_SCORE_MAP = {
    "supply_risk_score": "supply_risk_score_en",
    "policy_risk_score": "policy_risk_score_en",
    "price_pressure_score": "price_pressure_score_en",
    "risk_score": "risk_score_en",
    "risk_level": "risk_level_en",
    "importance_score": "importance_score_en",
    "confidence": "confidence_en",
    "forecast_6m": "forecast_6m_en",
    "forecast_12m": "forecast_12m_en",
    "forecast_24m": "forecast_24m_en",
    "forecast_48m": "forecast_48m_en",
}


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


def _is_null_response(raw_text):
    cleaned = str(raw_text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    return cleaned.lower() in {"null", "none"}


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


def _normalize_en_result(data):
    if not isinstance(data, dict):
        return None
    normalized = normalize_analysis_en(data)
    if normalized is None:
        return None
    # Preserve absent numeric/forecast keys as unset so Storage.build_record()
    # inherits the authoritative values from the Chinese pass instead of defaults.
    for raw_key, en_key in _EN_OPTIONAL_SCORE_MAP.items():
        if raw_key not in data or data.get(raw_key) in (None, "", {}):
            normalized.pop(en_key, None)
    return normalized


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
        self.analyze_max_output_tokens = int(
            getattr(config, "analyze_max_output_tokens", 900) or 900
        )
        self.analyze_en_max_output_tokens = int(
            getattr(config, "analyze_en_max_output_tokens", 500) or 500
        )
        zh_gen_cfg = {"temperature": 0.2}
        if self.analyze_max_output_tokens > 0:
            zh_gen_cfg["max_output_tokens"] = self.analyze_max_output_tokens
        en_gen_cfg = {"temperature": 0.2}
        if self.analyze_en_max_output_tokens > 0:
            en_gen_cfg["max_output_tokens"] = self.analyze_en_max_output_tokens

        self.model = genai.GenerativeModel(
            config.gemini_model,
            generation_config=zh_gen_cfg,
        )
        self.model_en = genai.GenerativeModel(
            config.gemini_model,
            generation_config=en_gen_cfg,
        )
        self.config = config
        self.analyze_timeout_seconds = config.analyze_timeout_seconds
        self.prompt_version = os.getenv("PROMPT_VERSION", "v1").strip().lower()
        self.prompt_min_evidence_sentences = max(
            1, int(getattr(config, "prompt_min_evidence_sentences", 2) or 2)
        )
        self.last_failure_reason = ""

    def _build_context_block(self, context_summary):
        if not context_summary:
            return ""
        return "\nRELATED HISTORY (for context only):\n" f"{context_summary}\n"

    def _build_zh_prompt(self, content, context_block):
        recommendation_rule = (
            "- Each watchpoint follows: action/signal + object + reason + time window.\n"
            if self.prompt_version == "v2"
            else ""
        )
        return f"""
Role: {PERSONA_ZH_INTEL}

You are an objective rare earths and critical minerals market and supply-chain analyst.
Return ONLY one strict JSON object (no markdown, no commentary).

Early-exit rule (strict, saves tokens):
- If the article is NOT primarily about rare earths or critical minerals, output ONLY:
  {{"valid_input":false,"reject_reason":"off_topic"}}
- If fewer than {self.prompt_min_evidence_sentences} explicit factual sentences exist in the article, output ONLY:
  {{"valid_input":false,"reject_reason":"insufficient_evidence"}}
- Do NOT include any other fields when rejecting.

Input & factual rules:
- Ignore residual navigation, cookie, subscription, or promo text.
- Rely strictly on explicit facts in the article; stay neutral and avoid geopolitical confrontation or investment guarantees.

Location rules:
- country_primary: where the main project/action occurs (do NOT set to China unless the primary event is in China; use "Unknown" if unclear).
- If China is only mentioned as background, set china_involved/china_role while keeping country_primary as the primary country.

Concise output rules (valid_input=true):
- summary_zh: concise neutral Chinese summary (<= 120 Chinese chars).
- expert_summary: 1-2 neutral Chinese sentences on supply/demand/price impact (<= 80 Chinese chars).
- china_impact: one of [利好, 中性, 利空] (objective impact on China's related industry/market).
- china_risk_trend: one of [加强, 持平, 缓和].
- minerals: array of mentioned minerals (empty if none).
- entities: array of up to 8 key companies/projects/institutions.
- action_type: one of [Investment, Regulation, Discovery, Price, Geopolitics, Other].
- china_involved: boolean; china_dependency: [None, Low, Medium, High]; china_role: [Supplier, Processor, Investor, Policy, Mentioned].
- Scores: supply_risk_score, policy_risk_score, price_pressure_score, risk_score, confidence are integers 0-100; risk_level and importance_score are integers 1-5 aligned with risk_score.
- Forecast windows: forecast_6m and forecast_12m ONLY, each {{"price_pressure":"Up|Down|Neutral","confidence":0-100}}. Do NOT output forecast_24m or forecast_48m.
- recommendations_strategy & recommendations_tactical: if importance_score <= 2, return [] for both. If importance_score >= 3, return 1-2 concise neutral industry/market watchpoints each (<= 45 Chinese chars per item).
{recommendation_rule}- key_evidence: 2-3 factual points directly from the article (<= 50 Chinese chars each).
- assumptions: up to 2 evidence gaps (<= 40 Chinese chars each).
- audience_fit: one of [investor, industry, policy, mixed].
- valid_input: true; reject_reason: "".

JSON keys when valid_input=true:
valid_input, reject_reason, summary_zh, country_primary, event_location, minerals, entities, action_type, risk_level, importance_score, china_involved, china_dependency, china_role, confidence, china_impact, china_risk_trend, expert_summary, recommendations_strategy, recommendations_tactical, supply_risk_score, policy_risk_score, price_pressure_score, risk_score, forecast_6m, forecast_12m, key_evidence, assumptions, audience_fit

Article:
{content}
{context_block}
"""

    def _build_en_prompt(self, content, context_block):
        recommendation_rule = (
            "- Each watchpoint follows: signal/action + object + rationale + time window.\n"
            if self.prompt_version == "v2"
            else ""
        )
        return f"""
Role: {PERSONA_EN_INTEL}

You are an objective global rare earths and critical minerals market analyst serving institutional investors, commodity traders, and supply-chain researchers.
Report factual supply, demand, capacity, pricing, and regulatory developments neutrally. Do NOT produce confrontational geopolitical strategies or policy advocacy.
Return ONLY one strict JSON object (no markdown, no commentary).

Early-exit rule:
- If the article is NOT primarily about rare earths/critical minerals or has fewer than {self.prompt_min_evidence_sentences} factual sentences, output ONLY:
  {{"valid_input":false,"reject_reason":"off_topic"}}

Concise output rules (valid_input=true):
- summary_en: concise neutral English summary (<= 65 words).
- impact_west: one of [Positive, Neutral, Negative] (objective commercial impact on international/non-China markets and buyers).
- risk_trend_west: one of [Intensifying, Stable, Easing] (supply/price volatility trend for international buyers).
- expert_summary_en: 1-2 neutral English sentences on market implications (<= 45 words).
- recommendations_strategy_en: 1-2 concise commercial/research watchpoints (<= 20 words each).
- recommendations_tactical_en: 1-2 concise near-term market/operational tracking items (<= 20 words each).
{recommendation_rule}- key_evidence: 2-3 factual points directly from the article (<= 25 words each).
- assumptions: up to 2 evidence gaps (<= 20 words each).
- audience_fit: one of [investor, industry, policy, mixed].
- valid_input: true; reject_reason: "".
- Do NOT output numeric scores or multi-year forecasts (inherited automatically).

JSON keys when valid_input=true:
valid_input, reject_reason, summary_en, impact_west, risk_trend_west, expert_summary_en, recommendations_strategy_en, recommendations_tactical_en, key_evidence, assumptions, audience_fit

Article:
{content}
{context_block}
"""

    def _guard_input(self, text, prompt_key):
        guard = guard_article_text(text, self.config)
        if not guard.get("pass"):
            reason = guard.get("reason", "invalid_input")
            self.last_failure_reason = reason
            meta = dict(guard.get("stats") or {})
            signals = guard.get("pollution_signals") or []
            if signals:
                meta["pollution_signals"] = signals
            _append_prompt_qc_log(
                self.config,
                module="extractor",
                prompt_key=prompt_key,
                input_quality=guard.get("quality", "invalid_input"),
                fallback_used=False,
                fallback_reason=reason,
                meta=meta,
            )
            return None
        return guard

    def _analyze_common(
        self,
        text,
        context_summary,
        prompt_key,
        prompt_builder,
        normalizer,
        summary_key,
        model=None,
    ):
        self.last_failure_reason = ""
        if not text:
            self.last_failure_reason = "empty_input"
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
            self.last_failure_reason = "empty_prompt_text"
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
        active_model = model or self.model

        try:
            response = _generate_with_timeout(
                active_model,
                prompt,
                self.analyze_timeout_seconds,
            )
        except concurrent.futures.TimeoutError:
            print("[!] Gemini request timed out")
            self.last_failure_reason = "timeout"
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
            self.last_failure_reason = "gemini_exception"
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
            self.last_failure_reason = "empty_response"
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

        if _is_null_response(raw_text):
            self.last_failure_reason = "off_topic"
            _append_prompt_qc_log(
                self.config,
                module="extractor",
                prompt_key=prompt_key,
                input_quality="invalid_input",
                fallback_used=False,
                fallback_reason="off_topic",
                meta=guard.get("stats"),
            )
            return None

        data = extract_json(raw_text)
        if data is None:
            self.last_failure_reason = "invalid_json"
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
                reason = reject_reason or "model_rejected"
                self.last_failure_reason = reason
                _append_prompt_qc_log(
                    self.config,
                    module="extractor",
                    prompt_key=prompt_key,
                    input_quality=(
                        "invalid_input"
                        if reason in {"invalid_input", "off_topic"}
                        else "insufficient_evidence"
                    ),
                    fallback_used=False,
                    fallback_reason=reason,
                    meta=guard.get("stats"),
                )
                return None

        normalized = normalizer(data)
        if normalized is None:
            self.last_failure_reason = "normalizer_failed"
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
            self.last_failure_reason = "too_few_evidence_sentences"
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
            self.last_failure_reason = "missing_summary"
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
            model=self.model,
        )

    def analyze_en(self, text, context_summary=""):
        return self._analyze_common(
            text=text,
            context_summary=context_summary,
            prompt_key="extractor_en",
            prompt_builder=self._build_en_prompt,
            normalizer=_normalize_en_result,
            summary_key="summary_en",
            model=self.model_en,
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

You are a neutral intelligence editor. Write a concise daily report in English for global market and supply-chain readers.
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

You are a neutral intelligence editor. Write a concise daily report in English for global market and supply-chain readers.
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

你是中立客观的情报编辑，请基于事件清单撰写中文市场日报（面向产业与投资者读者）。
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

You are a neutral intelligence editor. Write a concise daily report in Chinese for industry and market readers.
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
