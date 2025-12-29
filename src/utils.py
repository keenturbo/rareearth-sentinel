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
    }

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
