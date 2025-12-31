import math
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

from .utils import normalize_token, now_cst

MINERAL_MAP = {
    "rare earth": "Rare Earths",
    "rare earths": "Rare Earths",
    "ree": "Rare Earths",
    "re es": "Rare Earths",
    "ndpr": "Neodymium-Praseodymium",
    "neodymium praseodymium": "Neodymium-Praseodymium",
    "neodymium-praseodymium": "Neodymium-Praseodymium",
    "neodymium": "Neodymium",
    "praseodymium": "Praseodymium",
    "dysprosium": "Dysprosium",
    "terbium": "Terbium",
    "heavy rare earths": "Heavy Rare Earths",
    "light rare earths": "Light Rare Earths",
    "lithium": "Lithium",
    "cobalt": "Cobalt",
    "gallium": "Gallium",
    "germanium": "Germanium",
    "magnesium": "Magnesium",
    "nickel": "Nickel",
    "graphite": "Graphite",
    "manganese": "Manganese",
    "tungsten": "Tungsten",
    "antimony": "Antimony",
    "vanadium": "Vanadium",
}

COUNTRY_MAP = {
    "us": "United States",
    "u s": "United States",
    "u s a": "United States",
    "usa": "United States",
    "united states of america": "United States",
    "united states": "United States",
    "uk": "United Kingdom",
    "u k": "United Kingdom",
    "united kingdom": "United Kingdom",
    "uae": "United Arab Emirates",
    "drc": "Democratic Republic of the Congo",
    "dr congo": "Democratic Republic of the Congo",
    "republic of the congo": "Republic of the Congo",
    "south korea": "Republic of Korea",
    "north korea": "Democratic People's Republic of Korea",
    "russia": "Russian Federation",
}

ENTITY_ALIASES = {
    "u s department of defense": "U.S. Department of Defense",
    "us department of defense": "U.S. Department of Defense",
    "u s government": "U.S. Government",
    "us government": "U.S. Government",
    "eu commission": "European Commission",
}

ACTION_ALIASES = {
    "funding": "Investment",
    "financing": "Investment",
    "capex": "Investment",
    "subsidy": "Investment",
    "grant": "Investment",
    "policy": "Regulation",
    "ban": "Regulation",
    "export control": "Regulation",
    "export restriction": "Regulation",
    "license": "Regulation",
    "resource estimate": "Discovery",
    "feasibility": "Discovery",
    "price": "Price",
    "pricing": "Price",
    "sanction": "Geopolitics",
    "tariff": "Geopolitics",
}

SOURCE_WEIGHTS = {
    "rss": 55,
    "serper": 60,
    "curated": 75,
    "official": 85,
}

CONTENT_BASE = {
    "fulltext": 70,
    "rss_summary": 45,
}

CORROBORATION_WINDOW_DAYS = 30


def _parse_date(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None


def _clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


def _parse_number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower().replace(",", "")
    if not text:
        return None
    multiplier = 1.0
    if text.endswith("b"):
        multiplier = 1_000_000_000.0
        text = text[:-1]
    elif text.endswith("m"):
        multiplier = 1_000_000.0
        text = text[:-1]
    elif text.endswith("k"):
        multiplier = 1_000.0
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _normalize_minerals(values):
    if not values:
        return []
    normalized = []
    seen = set()
    for value in values:
        raw = str(value).strip()
        if not raw:
            continue
        key = normalize_token(raw)
        key = key.replace("-", " ")
        canonical = MINERAL_MAP.get(key)
        if not canonical:
            canonical = raw.strip()
        seen_key = canonical.lower()
        if seen_key in seen:
            continue
        seen.add(seen_key)
        normalized.append(canonical)
    return normalized


def _normalize_country(country):
    if not country:
        return ""
    raw = str(country).strip()
    if not raw:
        return ""
    key = normalize_token(raw)
    canonical = COUNTRY_MAP.get(key)
    if canonical:
        return canonical
    if raw.isupper() or raw.islower():
        return raw.title()
    return raw


def _normalize_entities(values):
    if not values:
        return []
    normalized = []
    seen = set()
    for value in values:
        raw = str(value).strip()
        if not raw:
            continue
        key = normalize_token(raw)
        canonical = ENTITY_ALIASES.get(key, raw)
        seen_key = canonical.lower()
        if seen_key in seen:
            continue
        seen.add(seen_key)
        normalized.append(canonical)
    return normalized


def _normalize_action(action):
    raw = str(action).strip()
    if not raw:
        return "Other"
    key = normalize_token(raw)
    alias = ACTION_ALIASES.get(key)
    if alias:
        return alias
    if raw in {"Investment", "Regulation", "Discovery", "Price", "Geopolitics", "Other"}:
        return raw
    return "Other"


def _score_content_quality(content, content_source):
    base = CONTENT_BASE.get(content_source, 50)
    length = len(content or "")
    bonus = 0
    if length >= 5000:
        bonus = 20
    elif length >= 2000:
        bonus = 15
    elif length >= 1000:
        bonus = 10
    elif length >= 400:
        bonus = 5
    return _clamp(base + bonus, 0, 100)


def _score_corroboration(count):
    if count <= 0:
        return 20
    if count == 1:
        return 40
    if count == 2:
        return 60
    if count == 3:
        return 75
    return 85


class Normalizer:
    def __init__(self, records, config):
        self.config = config
        self.index = {}
        self.seen_ids = set()
        for record in records:
            self.add_record(record)

    def _build_key(self, normalized):
        country = normalized.get("country_primary", "")
        action = normalized.get("action_type", "")
        minerals = normalized.get("minerals", []) or []
        primary_mineral = minerals[0] if minerals else ""
        return (country, action, primary_mineral)

    def _count_recent(self, key, exclude_id=None):
        now = now_cst()
        window_start = now - timedelta(days=CORROBORATION_WINDOW_DAYS)
        entries = self.index.get(key, [])
        count = 0
        for dt, record_id in entries:
            if exclude_id and record_id == exclude_id:
                continue
            if dt and dt >= window_start:
                count += 1
        return count

    def add_record(self, record):
        record_id = record.get("id")
        if record_id and record_id in self.seen_ids:
            return
        normalized = record.get("normalized") or self.normalize_record(record)
        dt = _parse_date(record.get("published_at", "")) or _parse_date(
            record.get("collected_at", "")
        )
        key = self._build_key(normalized)
        self.index.setdefault(key, []).append((dt, record_id))
        if record_id:
            self.seen_ids.add(record_id)

    def normalize_record(self, record):
        minerals = _normalize_minerals(record.get("minerals") or [])
        country = _normalize_country(record.get("country_primary"))
        entities = _normalize_entities(record.get("entities") or [])
        action = _normalize_action(record.get("action_type"))
        capex = _parse_number(record.get("capex_usd"))
        capacity = _parse_number(record.get("capacity_delta_tpa"))
        return {
            "minerals": minerals,
            "country_primary": country,
            "entities": entities,
            "action_type": action,
            "capex_usd": capex,
            "capacity_delta_tpa": capacity,
        }

    def normalize_item(self, item, analysis):
        minerals = _normalize_minerals(analysis.get("minerals") or [])
        country = _normalize_country(analysis.get("country_primary"))
        entities = _normalize_entities(analysis.get("entities") or [])
        action = _normalize_action(analysis.get("action_type"))
        capex = _parse_number(analysis.get("capex_usd"))
        capacity = _parse_number(analysis.get("capacity_delta_tpa"))
        return {
            "minerals": minerals,
            "country_primary": country,
            "entities": entities,
            "action_type": action,
            "capex_usd": capex,
            "capacity_delta_tpa": capacity,
        }

    def score_record(self, record, normalized):
        source_type = str(record.get("source_type", "")).strip().lower()
        source_weight = SOURCE_WEIGHTS.get(source_type, 50)
        content_source = record.get("content_source", "fulltext")
        content_quality = _score_content_quality(
            record.get("content", ""), content_source
        )
        key = self._build_key(normalized)
        count = self._count_recent(key, exclude_id=record.get("id"))
        corroboration = _score_corroboration(count)
        confidence = record.get("confidence", 60)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 60
        score = (
            0.35 * source_weight
            + 0.20 * content_quality
            + 0.30 * corroboration
            + 0.15 * confidence
        )
        score = _clamp(score, 0, 100)
        level = int(math.ceil(score / 20.0)) or 1
        level = _clamp(level, 1, 5)
        return {
            "score": round(score),
            "level": level,
            "source_weight": source_weight,
            "content_quality": round(content_quality),
            "corroboration": round(corroboration),
            "extraction_confidence": round(confidence),
            "corroboration_count": count,
            "corroboration_window_days": CORROBORATION_WINDOW_DAYS,
        }

    def normalize_and_score(self, item, analysis, content, content_source):
        normalized = self.normalize_item(item, analysis)
        record_stub = {
            "id": "",
            "source_type": item.get("source_type", ""),
            "content_source": content_source,
            "content": content,
            "published_at": item.get("published_at", ""),
            "collected_at": isoformat(now_cst()),
            "confidence": analysis.get("confidence", 60),
            "country_primary": normalized.get("country_primary"),
            "action_type": normalized.get("action_type"),
            "minerals": normalized.get("minerals"),
            "entities": normalized.get("entities"),
        }
        credibility = self.score_record(record_stub, normalized)
        return normalized, credibility

    def normalize_and_score_from_record(self, record):
        normalized = record.get("normalized") or self.normalize_record(record)
        credibility = record.get("credibility") or self.score_record(
            record, normalized
        )
        return normalized, credibility
