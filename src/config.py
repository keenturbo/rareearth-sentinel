from dataclasses import dataclass
import json
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


def _get_bool(key, default=False):
    value = _get_env(key)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_list(key, default):
    value = _get_env(key)
    if value is None:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_custom_keywords(path):
    items = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return items
    except json.JSONDecodeError:
        return items

    if isinstance(data, dict):
        data = data.get("keywords", [])
    if not isinstance(data, list):
        return items

    for entry in data:
        keyword = ""
        enabled = True
        if isinstance(entry, str):
            keyword = entry.strip()
        elif isinstance(entry, dict):
            enabled = entry.get("enabled", True)
            keyword = str(entry.get("keyword", "")).strip()
        if not enabled or not keyword:
            continue
        items.append(keyword)
    return items


@dataclass(frozen=True)
class Config:
    gemini_api_key: str
    gemini_model: str
    data_dir: str
    static_dir: str
    templates_dir: str
    site_base_url: str
    keywords_file: str
    sources: list
    google_news_lang: str
    google_news_region: str
    google_news_edition: str
    max_items_per_keyword: int
    fetch_timeout_seconds: int
    fetch_delay_seconds: float
    analyze_delay_seconds: float
    analyze_timeout_seconds: int
    min_content_chars: int
    summary_min_chars: int
    index_limit: int
    official_sources_file: str
    custom_sources_file: str
    official_source_ids: list
    official_max_items: int
    index_enabled: bool
    index_sources_file: str
    index_raw_dir: str
    index_normalized_dir: str
    index_daily_threshold: float
    index_weekly_threshold: float
    index_watch_max: int
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
    datasets_dir: str
    dataset_min_credibility: int
    dataset_negative_ratio: float
    dataset_min_chars: int
    dataset_split_train: float
    dataset_split_valid: float
    dataset_random_seed: int
    premium_enabled: bool
    premium_dir: str
    premium_top_n: int
    premium_min_credibility: int
    premium_min_importance: int
    premium_min_quality: int
    pay_wechat_qr_url: str
    pay_alipay_qr_url: str
    pay_alipay_token: str
    pay_usdt_trc20: str
    pay_confirm_endpoint: str
    pay_confirm_admin_endpoint: str
    pay_bark_url: str
    pay_admin_base_url: str
    pay_admin_base_url_en: str
    pay_admin_orders_url: str
    pay_admin_orders_url_en: str
    paypal_client_id: str
    paypal_client_secret: str
    paypal_webhook_id: str
    paypal_env: str
    paypal_api_base: str
    telegram_bot_token: str
    telegram_channel: str
    telegram_en_bot_token: str
    telegram_en_channel: str
    unlock_expires_days: int
    input_guard_max_html_ratio: float
    input_guard_max_script_hits: int
    input_guard_min_text_len: int
    input_guard_max_raw_len: int
    input_guard_max_prompt_chars: int
    prompt_min_evidence_sentences: int
    show_related_context: bool
    content_variants_enabled: bool
    content_variants_dir: str
    content_variants_min_risk: int
    content_variants_min_importance: int
    content_variants_top_n: int
    content_variants_timeout_seconds: int
    content_variants_max_content_chars: int

    @classmethod
    def from_env(cls):
        api_key = _get_env("GEMINI_API_KEY") or _get_env("GOOGLE_API_KEY") or ""
        return cls(
            gemini_api_key=api_key,
            gemini_model=_get_env("GEMINI_MODEL", "gemini-3-flash-preview"),
            data_dir=_get_env("DATA_DIR", "data"),
            static_dir=_get_env("STATIC_DIR", "static"),
            templates_dir=_get_env("TEMPLATES_DIR", "templates"),
            site_base_url=_get_env(
                "SITE_BASE_URL", "https://rareearthsentinel.com"
            ),
            keywords_file=_get_env("KEYWORDS_FILE", "config/keywords.txt"),
            sources=_get_list("SOURCES", ["google_news"]),
            google_news_lang=_get_env("GOOGLE_NEWS_LANG", "en-US"),
            google_news_region=_get_env("GOOGLE_NEWS_REGION", "US"),
            google_news_edition=_get_env("GOOGLE_NEWS_EDITION", "US:en"),
            max_items_per_keyword=_get_int("MAX_ITEMS_PER_KEYWORD", 8),
            fetch_timeout_seconds=_get_int("FETCH_TIMEOUT_SECONDS", 20),
            fetch_delay_seconds=_get_float("FETCH_DELAY_SECONDS", 1.5),
            analyze_delay_seconds=_get_float("ANALYZE_DELAY_SECONDS", 1.5),
            analyze_timeout_seconds=_get_int("ANALYZE_TIMEOUT_SECONDS", 25),
            min_content_chars=_get_int("MIN_CONTENT_CHARS", 300),
            summary_min_chars=_get_int("SUMMARY_MIN_CHARS", 120),
            index_limit=_get_int("INDEX_LIMIT", 200),
            official_sources_file=_get_env(
                "OFFICIAL_SOURCES_FILE", "config/external_sources.yml"
            ),
            custom_sources_file=_get_env(
                "CUSTOM_SOURCES_FILE", "data/sources/custom_sources.json"
            ),
            official_source_ids=_get_list("OFFICIAL_SOURCE_IDS", []),
            official_max_items=_get_int("OFFICIAL_MAX_ITEMS", 6),
            index_enabled=_get_bool("INDEX_ENABLED", True),
            index_sources_file=_get_env(
                "INDEX_SOURCES_FILE", "config/index_sources.yml"
            ),
            index_raw_dir=_get_env("INDEX_RAW_DIR", "data/indices/raw"),
            index_normalized_dir=_get_env(
                "INDEX_NORMALIZED_DIR", "data/indices/normalized"
            ),
            index_daily_threshold=_get_float("INDEX_DAILY_THRESHOLD", 3.0),
            index_weekly_threshold=_get_float("INDEX_WEEKLY_THRESHOLD", 10.0),
            index_watch_max=_get_int("INDEX_WATCH_MAX", 6),
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
            datasets_dir=_get_env("DATASETS_DIR", "data/datasets"),
            dataset_min_credibility=_get_int("DATASET_MIN_CREDIBILITY", 60),
            dataset_negative_ratio=_get_float("DATASET_NEGATIVE_RATIO", 0.15),
            dataset_min_chars=_get_int("DATASET_MIN_CHARS", 200),
            dataset_split_train=_get_float("DATASET_SPLIT_TRAIN", 0.8),
            dataset_split_valid=_get_float("DATASET_SPLIT_VALID", 0.1),
            dataset_random_seed=_get_int("DATASET_RANDOM_SEED", 42),
            premium_enabled=_get_bool("PREMIUM_ENABLED", True),
            premium_dir=_get_env("PREMIUM_DIR", "data/premium"),
            premium_top_n=_get_int("PREMIUM_TOP_N", 3),
            premium_min_credibility=_get_int("PREMIUM_MIN_CREDIBILITY", 60),
            premium_min_importance=_get_int("PREMIUM_MIN_IMPORTANCE", 3),
            premium_min_quality=_get_int("PREMIUM_MIN_QUALITY", 60),
            pay_wechat_qr_url=_get_env(
                "PAY_WECHAT_QR_URL", "https://pic.icon.pp.ua/wechat-pay.jpeg"
            ),
            pay_alipay_qr_url=_get_env(
                "PAY_ALIPAY_QR_URL", "https://pic.icon.pp.ua/alipay.jpeg"
            ),
            pay_alipay_token=_get_env("PAY_ALIPAY_TOKEN", ""),
            pay_usdt_trc20=_get_env("PAY_USDT_TRC20", ""),
            pay_confirm_endpoint=_get_env("PAY_CONFIRM_ENDPOINT", "/api/pay/confirm"),
            pay_confirm_admin_endpoint=_get_env(
                "PAY_CONFIRM_ADMIN_ENDPOINT", "/api/pay/confirm_admin"
            ),
            pay_bark_url=_get_env("PAY_BARK_URL", ""),
            pay_admin_base_url=_get_env("PAY_ADMIN_BASE_URL", ""),
            pay_admin_base_url_en=_get_env("PAY_ADMIN_BASE_URL_EN", ""),
            pay_admin_orders_url=_get_env("PAY_ADMIN_ORDERS_URL", ""),
            pay_admin_orders_url_en=_get_env("PAY_ADMIN_ORDERS_URL_EN", ""),
            paypal_client_id=_get_env("PAYPAL_CLIENT_ID", ""),
            paypal_client_secret=_get_env("PAYPAL_CLIENT_SECRET", ""),
            paypal_webhook_id=_get_env("PAYPAL_WEBHOOK_ID", ""),
            paypal_env=_get_env("PAYPAL_ENV", "live"),
            paypal_api_base=_get_env("PAYPAL_API_BASE", "https://api-m.paypal.com"),
            telegram_bot_token=_get_env("TELEGRAM_BOT_TOKEN", ""),
            telegram_channel=_get_env("TELEGRAM_CHANNEL", ""),
            telegram_en_bot_token=_get_env("TELEGRAM_EN_BOT_TOKEN", ""),
            telegram_en_channel=_get_env("TELEGRAM_EN_CHANNEL", ""),
            unlock_expires_days=_get_int("UNLOCK_EXPIRES_DAYS", 7),
            input_guard_max_html_ratio=_get_float("INPUT_GUARD_MAX_HTML_RATIO", 0.22),
            input_guard_max_script_hits=_get_int("INPUT_GUARD_MAX_SCRIPT_HITS", 2),
            input_guard_min_text_len=_get_int("INPUT_GUARD_MIN_TEXT_LEN", 240),
            input_guard_max_raw_len=_get_int("INPUT_GUARD_MAX_RAW_LEN", 50000),
            input_guard_max_prompt_chars=_get_int("INPUT_GUARD_MAX_PROMPT_CHARS", 12000),
            prompt_min_evidence_sentences=_get_int("PROMPT_MIN_EVIDENCE_SENTENCES", 2),
            show_related_context=_get_bool("SHOW_RELATED_CONTEXT", False),
            content_variants_enabled=_get_bool("CONTENT_VARIANTS_ENABLED", True),
            content_variants_dir=_get_env("CONTENT_VARIANTS_DIR", "data/status"),
            content_variants_min_risk=_get_int("CONTENT_VARIANTS_MIN_RISK", 70),
            content_variants_min_importance=_get_int("CONTENT_VARIANTS_MIN_IMPORTANCE", 4),
            content_variants_top_n=_get_int("CONTENT_VARIANTS_TOP_N", 5),
            content_variants_timeout_seconds=_get_int("CONTENT_VARIANTS_TIMEOUT_SECONDS", 20),
            content_variants_max_content_chars=_get_int("CONTENT_VARIANTS_MAX_CONTENT_CHARS", 8000),
        )


def load_keywords(path, custom_path=None):
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

    if custom_path is None:
        custom_path = _get_env(
            "CUSTOM_KEYWORDS_FILE", "data/keywords/custom_keywords.json"
        )
    keywords.extend(_load_custom_keywords(custom_path))

    seen = set()
    merged = []
    for keyword in keywords:
        if keyword in seen:
            continue
        merged.append(keyword)
        seen.add(keyword)
    return merged
