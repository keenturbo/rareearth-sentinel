import hashlib
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

ACTION_TYPES = {
    "Investment",
    "Regulation",
    "Discovery",
    "Price",
    "Geopolitics",
    "Other",
}

CHINA_DEPENDENCY = {"None", "Low", "Medium", "High"}
CHINA_ROLE = {"Supplier", "Processor", "Investor", "Policy", "Mentioned"}
CHINA_IMPACT = {"利好", "中性", "利空"}
CHINA_RISK_TREND = {"加强", "持平", "缓和"}
FORECAST_TRENDS = {"Up", "Down", "Neutral"}
TREND_ALIASES = {
    "上涨": "Up",
    "上行": "Up",
    "上升": "Up",
    "走强": "Up",
    "下行": "Down",
    "下跌": "Down",
    "下降": "Down",
    "走弱": "Down",
    "中性": "Neutral",
    "持平": "Neutral",
    "平稳": "Neutral",
    "稳定": "Neutral",
    "无明显": "Neutral",
}

NOISE_PHRASES = [
    "cookie",
    "privacy",
    "terms of service",
    "terms of use",
    "subscribe",
    "sign up",
    "sign in",
    "log in",
    "newsletter",
    "advertis",
    "all rights reserved",
    "accept all",
    "manage consent",
    "consent preferences",
    "javascript",
    "enable javascript",
    "access denied",
    "subscription",
    "paywall",
]


# Tokenization helper for context indexing
TOKEN_RE = re.compile(r"[^\w\s\u4e00-\u9fff\-]+", re.UNICODE)


def normalize_token(text):
    if not text:
        return ""
    text = str(text).strip().lower()
    text = TOKEN_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def now_cst():
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def isoformat(dt):
    return dt.isoformat(timespec="seconds")


def hash_url(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def extract_json(text):
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(json)?", "", cleaned).strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
    if cleaned in {"null", "None", ""}:
        return None
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _as_str(value):
    if value is None:
        return ""
    return str(value).strip()


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"true", "yes", "y", "1", "是"}


def _as_choice(value, allowed, default):
    text = _as_str(value)
    if not text:
        return default
    if text in allowed:
        return text
    return default


def _clamp_int(value, min_value, max_value, default):
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, ivalue))


def _normalize_trend(value):
    text = _as_str(value)
    if not text:
        return "Neutral"
    if text in FORECAST_TRENDS:
        return text
    if text in TREND_ALIASES:
        return TREND_ALIASES[text]
    return "Neutral"


def _normalize_forecast(value):
    if not isinstance(value, dict):
        return {}
    return {
        "price_pressure": _normalize_trend(value.get("price_pressure")),
        "confidence": _clamp_int(value.get("confidence"), 0, 100, 50),
    }


def normalize_analysis(data):
    if not isinstance(data, dict):
        return None

    normalized = {
        "summary_zh": _as_str(data.get("summary_zh")),
        "country_primary": _as_str(data.get("country_primary")),
        "event_location": _as_str(data.get("event_location")),
        "minerals": _as_list(data.get("minerals")),
        "entities": _as_list(data.get("entities")),
        "action_type": _as_choice(data.get("action_type"), ACTION_TYPES, "Other"),
        "risk_level": _clamp_int(data.get("risk_level"), 1, 5, 3),
        "importance_score": _clamp_int(data.get("importance_score"), 1, 5, 3),
        "china_involved": _as_bool(data.get("china_involved")),
        "china_dependency": _as_choice(
            data.get("china_dependency"), CHINA_DEPENDENCY, "None"
        ),
        "china_role": _as_choice(data.get("china_role"), CHINA_ROLE, "Mentioned"),
        "confidence": _clamp_int(data.get("confidence"), 0, 100, 60),
        "china_impact": _as_choice(data.get("china_impact"), CHINA_IMPACT, "中性"),
        "china_risk_trend": _as_choice(
            data.get("china_risk_trend"), CHINA_RISK_TREND, "持平"
        ),
        "expert_summary": _as_str(data.get("expert_summary")),
        "recommendations_strategy": _as_list(data.get("recommendations_strategy")),
        "recommendations_tactical": _as_list(data.get("recommendations_tactical")),
        "supply_risk_score": _clamp_int(data.get("supply_risk_score"), 0, 100, 50),
        "policy_risk_score": _clamp_int(data.get("policy_risk_score"), 0, 100, 50),
        "price_pressure_score": _clamp_int(
            data.get("price_pressure_score"), 0, 100, 50
        ),
        "risk_score": _clamp_int(data.get("risk_score"), 0, 100, 50),
        "forecast_6m": _normalize_forecast(data.get("forecast_6m")),
        "forecast_12m": _normalize_forecast(data.get("forecast_12m")),
        "forecast_24m": _normalize_forecast(data.get("forecast_24m")),
        "forecast_48m": _normalize_forecast(data.get("forecast_48m")),
    }

    related_context = data.get("related_context")
    if isinstance(related_context, list):
        normalized["related_context"] = [
            _as_str(entry) for entry in related_context if _as_str(entry)
        ]
    if data.get("analysis_with_context") is not None:
        normalized["analysis_with_context"] = _as_bool(
            data.get("analysis_with_context")
        )

    return normalized


def is_china_primary(country):
    if not country:
        return False
    text = str(country).strip().lower()
    return text in {
        "china",
        "people's republic of china",
        "prc",
        "中国",
        "中华人民共和国",
    }


def clean_text(text):
    if not text:
        return ""
    cleaned_lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or len(line) < 3:
            continue
        lower = line.lower()
        if any(phrase in lower for phrase in NOISE_PHRASES):
            continue
        if re.fullmatch(r"\W+", line):
            continue
        line = re.sub(r"\s+", " ", line)
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()
