from dataclasses import dataclass
import os

from dotenv import load_dotenv

load_dotenv()


def _get_env(key, default=None):
    value = os.getenv(key)
    if value is None or value == "":
        return default
    return value


def _get_int(key, default):
    value = _get_env(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_float(key, default):
    value = _get_env(key)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _get_list(key, default):
    value = _get_env(key)
    if value is None:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Config:
    gemini_api_key: str
    gemini_model: str
    data_dir: str
    static_dir: str
    templates_dir: str
    keywords_file: str
    sources: list
    google_news_lang: str
    google_news_region: str
    google_news_edition: str
    max_items_per_keyword: int
    fetch_timeout_seconds: int
    fetch_delay_seconds: float
    analyze_delay_seconds: float
    min_content_chars: int
    summary_min_chars: int
    index_limit: int
    context_window_days: int
    context_max_items: int
    context_mode: str
    embedding_model: str
    embedding_doc_task_type: str
    embedding_query_task_type: str
    embedding_max_chars: int
    embedding_min_chars: int
    embedding_warmup_limit: int
    embedding_delay_seconds: float

    @classmethod
    def from_env(cls):
        api_key = _get_env("GEMINI_API_KEY") or _get_env("GOOGLE_API_KEY") or ""
        return cls(
            gemini_api_key=api_key,
            gemini_model=_get_env("GEMINI_MODEL", "gemini-3-flash-preview"),
            data_dir=_get_env("DATA_DIR", "data"),
            static_dir=_get_env("STATIC_DIR", "static"),
            templates_dir=_get_env("TEMPLATES_DIR", "templates"),
            keywords_file=_get_env("KEYWORDS_FILE", "config/keywords.txt"),
            sources=_get_list("SOURCES", ["google_news"]),
            google_news_lang=_get_env("GOOGLE_NEWS_LANG", "en-US"),
            google_news_region=_get_env("GOOGLE_NEWS_REGION", "US"),
            google_news_edition=_get_env("GOOGLE_NEWS_EDITION", "US:en"),
            max_items_per_keyword=_get_int("MAX_ITEMS_PER_KEYWORD", 8),
            fetch_timeout_seconds=_get_int("FETCH_TIMEOUT_SECONDS", 20),
            fetch_delay_seconds=_get_float("FETCH_DELAY_SECONDS", 1.5),
            analyze_delay_seconds=_get_float("ANALYZE_DELAY_SECONDS", 1.5),
            min_content_chars=_get_int("MIN_CONTENT_CHARS", 300),
            summary_min_chars=_get_int("SUMMARY_MIN_CHARS", 120),
            index_limit=_get_int("INDEX_LIMIT", 200),
            context_window_days=_get_int("CONTEXT_WINDOW_DAYS", 365),
            context_max_items=_get_int("CONTEXT_MAX_ITEMS", 6),
            context_mode=_get_env("CONTEXT_MODE", "vector"),
            embedding_model=_get_env(
                "EMBEDDING_MODEL", "models/text-embedding-004"
            ),
            embedding_doc_task_type=_get_env(
                "EMBEDDING_DOC_TASK", "retrieval_document"
            ),
            embedding_query_task_type=_get_env(
                "EMBEDDING_QUERY_TASK", "retrieval_query"
            ),
            embedding_max_chars=_get_int("EMBEDDING_MAX_CHARS", 4000),
            embedding_min_chars=_get_int("EMBEDDING_MIN_CHARS", 80),
            embedding_warmup_limit=_get_int("EMBEDDING_WARMUP_LIMIT", 2000),
            embedding_delay_seconds=_get_float("EMBEDDING_DELAY_SECONDS", 0.2),
        )


def load_keywords(path):
    keywords = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                keywords.append(line)
    except FileNotFoundError:
        pass
    return keywords
