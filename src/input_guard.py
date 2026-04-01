import html
import re

from .utils import clean_text

SCRIPT_BLOCK_RE = re.compile(r"(?is)<(script|style|noscript).*?>.*?</\1>")
COMMENT_RE = re.compile(r"(?is)<!--.*?-->")
TAG_RE = re.compile(r"(?is)<[^>]+>")
HTML_TOKEN_RE = re.compile(r"(?is)</?[a-z][^>]*>")
SCRIPT_TOKEN_RE = re.compile(
    r"(?is)<script|<style|<noscript|window\.|document\.|googletag|doubleclick|gtm\.js|data:image/|base64,"
)
NOISE_TOKEN_RE = re.compile(
    r"(?is)cookie|consent|subscribe|sign up|sign in|newsletter|advertis|all rights reserved|paywall|enable javascript"
)
WRAPPER_TOKEN_RE = re.compile(
    r"(?is)\b("
    r"google news|rss/articles|privacy choices?|cookie settings|accept all|reject all|"
    r"all sections|latest news|related articles|share this article|follow us|"
    r"contact us|about us|authors?|events|terms of use|privacy policy|"
    r"skip to main content|view comments|read more|newsletter signup"
    r")\b"
)
NAV_TOKEN_RE = re.compile(
    r"(?is)\b("
    r"home|menu|sign in|sign up|subscribe|newsletter|sections|markets|business|"
    r"technology|world|opinion|lifestyle|sports|video|podcast|search"
    r")\b"
)
SENTENCE_SPLIT_RE = re.compile(r"[。！？.!?;\n]+")
FACT_HINT_RE = re.compile(
    r"\d|%|\$|€|¥|亿元|亿美元|million|billion|ton|tons|吨|kg|quota|tariff|project|agreement|announced|approved|signed|export|funding|investment|产能|项目|签署|宣布|批准|出口|配额|关税|投资",
    re.I,
)


def _html_ratio(raw_text):
    raw = str(raw_text or "")
    if not raw:
        return 0.0
    html_chars = sum(len(match.group(0)) for match in HTML_TOKEN_RE.finditer(raw))
    return round(html_chars / max(1, len(raw)), 4)


def _count_hits(pattern, text):
    return len(pattern.findall(str(text or "")))


def _count_fact_sentences(text):
    sentences = [
        segment.strip()
        for segment in SENTENCE_SPLIT_RE.split(str(text or ""))
        if segment.strip()
    ]
    count = 0
    for sentence in sentences:
        if FACT_HINT_RE.search(sentence):
            count += 1
    return count


def clean_model_input(raw_text):
    text = str(raw_text or "")
    text = COMMENT_RE.sub(" ", text)
    text = SCRIPT_BLOCK_RE.sub(" ", text)
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = clean_text(text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def guard_article_text(raw_text, config):
    raw = str(raw_text or "")
    max_raw_len = int(getattr(config, "input_guard_max_raw_len", 50000) or 50000)
    max_prompt_chars = int(
        getattr(config, "input_guard_max_prompt_chars", 12000) or 12000
    )
    min_text_len = int(getattr(config, "input_guard_min_text_len", 240) or 240)
    max_script_hits = int(getattr(config, "input_guard_max_script_hits", 2) or 2)
    max_html_ratio = float(
        getattr(config, "input_guard_max_html_ratio", 0.22) or 0.22
    )
    min_evidence = int(
        getattr(config, "prompt_min_evidence_sentences", 2) or 2
    )

    raw = raw[:max_raw_len]
    script_hits = _count_hits(SCRIPT_TOKEN_RE, raw)
    noise_hits = _count_hits(NOISE_TOKEN_RE, raw)
    html_ratio = _html_ratio(raw)

    cleaned = clean_model_input(raw)
    cleaned_len = len(cleaned)
    fact_sentences = _count_fact_sentences(cleaned)
    wrapper_hits = _count_hits(WRAPPER_TOKEN_RE, cleaned)
    nav_hits = _count_hits(NAV_TOKEN_RE, cleaned)

    pollution_signals = []
    if script_hits:
        pollution_signals.append("script_token")
    if noise_hits >= 3:
        pollution_signals.append("noise_token")
    if html_ratio > max_html_ratio:
        pollution_signals.append("high_html_ratio")
    if wrapper_hits >= 2:
        pollution_signals.append("wrapper_token")
    if nav_hits >= 10:
        pollution_signals.append("navigation_token")

    quality = "pass"
    reason = "ok"
    passed = True
    min_fact_for_noisy = max(4, min_evidence + 1)

    if not raw.strip():
        quality = "invalid_input"
        reason = "empty_input"
        passed = False
    elif not cleaned:
        quality = "invalid_input"
        reason = "empty_after_clean"
        passed = False
    elif cleaned_len < min_text_len:
        quality = "insufficient_evidence"
        reason = "cleaned_too_short"
        passed = False
    elif wrapper_hits >= 2 and fact_sentences < min_fact_for_noisy:
        quality = "invalid_input"
        reason = "wrapper_noise"
        passed = False
    elif nav_hits >= 10 and fact_sentences < min_fact_for_noisy:
        quality = "invalid_input"
        reason = "navigation_noise"
        passed = False
    elif script_hits > max_script_hits and fact_sentences < max(2, min_evidence * 2):
        quality = "invalid_input"
        reason = "script_noise"
        passed = False
    elif noise_hits >= 3 and fact_sentences < min_evidence:
        quality = "invalid_input"
        reason = "noise_tokens"
        passed = False
    elif html_ratio > max_html_ratio and (
        script_hits > 0 or wrapper_hits >= 2 or nav_hits >= 10
    ):
        quality = "invalid_input"
        reason = "html_wrapper"
        passed = False
    elif html_ratio > max_html_ratio and fact_sentences < min_evidence:
        quality = "invalid_input"
        reason = "html_ratio"
        passed = False

    return {
        "pass": passed,
        "quality": quality,
        "reason": reason,
        "raw_text": raw,
        "cleaned_text": cleaned,
        "prompt_text": cleaned[:max_prompt_chars].strip(),
        "stats": {
            "raw_length": len(raw),
            "cleaned_length": cleaned_len,
            "html_ratio": html_ratio,
            "script_hits": script_hits,
            "noise_hits": noise_hits,
            "fact_sentences": fact_sentences,
            "wrapper_hits": wrapper_hits,
            "nav_hits": nav_hits,
        },
        "pollution_signals": pollution_signals,
    }
