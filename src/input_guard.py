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

# High-precision domain signals that pass prefilter on a single match.
DOMAIN_STRONG_EN_RE = re.compile(
    r"(?i)\b("
    r"rare\s*earths?|rees?|reo|lree|hree|ndpr|ndfeb|smco|"
    r"permanent\s+magnets?|magnet\s+recycl\w*|monazite|bastnaesite|ion[- ]adsorption\s+clay|"
    r"neodymium|praseodymium|dysprosium|terbium|lanthanum|cerium|samarium|europium|"
    r"gadolinium|holmium|erbium|thulium|ytterbium|lutetium|yttrium|scandium|promethium|"
    r"critical\s+(?:minerals?|materials?|raw\s+materials?)|strategic\s+minerals?|"
    r"gallium|germanium|antimony|tungsten|graphite|iridium|platinum\s+group|silicon\s+carbide|"
    r"lynas|mp\s+materials|iluka|arafura|aclara|hastings\s+technology|energy\s+fuels|"
    r"ucore|pensana|mkango|lindian|niocorp|northern\s+minerals|rainbow\s+rare|"
    r"ramaco|usa\s+rare\s+earth|neo\s+performance|shenghe|mountain\s+pass|mt\s+weld|"
    r"yangibana|browns\s+range|nolans\s+project|nechalacho|caremag|"
    r"australian\s+strategic\s+materials|brazilian\s+rare|defense\s+metals|"
    r"critical\s+metals|rare\s+element\s+resources|texas\s+mineral|geomega|"
    r"avalon\s+advanced|integral\s+metals"
    r")\b"
)

DOMAIN_STRONG_ZH_RE = re.compile(
    r"("
    r"稀土|镨钕|氧化镨钕|氧化铽|氧化镝|氧化镧|氧化铈|钕铁硼|永磁|磁材|磁体|"
    r"独居石|氟碳铈矿|离子型稀土|轻稀土|重稀土|中重稀土|"
    r"关键矿产|战略矿产|金属镓|氧化镓|金属锗|氧化锗|金属锑|钨矿|仲钨酸铵|"
    r"开采指标|冶炼分离|分离冶炼|稀土配额|"
    r"北方稀土|中国稀土|盛和资源|中稀有色|广晟有色|金力永磁|中科三环|正海磁材|"
    r"宁波韵升|大地熊|银河磁体|英洛华|包钢稀土|龙磁科技|天和磁材|中科磁业|"
    r"华宏科技|有研新材|厦门钨业|第一稀元素"
    r")"
)

# Broader battery/industrial minerals require a supply-chain/mining context term
# so generic consumer-electronics or EV reviews are filtered out locally.
DOMAIN_BROAD_MINERAL_RE = re.compile(
    r"(?i)(\b(?:lithium|cobalt|nickel|magnesium|fluorine|zircon\w*)\b|锂矿|钴矿|镍矿|金属镁|萤石|石墨|锆)"
)
DOMAIN_SUPPLY_CONTEXT_RE = re.compile(
    r"(?i)(\b(?:mine|mining|miner|deposit|ore|oxide|concentrate|refinery|refining|smelter|"
    r"processing|separation|offtake|supply\s*chain|export\s*control|export\s*restriction|"
    r"tariff|quota|stockpile|drilling|feasibility|resource\s*estimate)\b|"
    r"矿山|矿区|矿床|采矿|选矿|冶炼|精炼|分离|产能|供应链|出口管制|配额|关税|储备|勘探|资源量)"
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


def prefilter_domain_relevance(title, text, config=None):
    """Zero-cost local relevance gate before calling the LLM."""
    if config is not None and not getattr(config, "prefilter_domain_enabled", True):
        return {"pass": True, "reason": "disabled", "matched": []}

    max_chars = 4000
    if config is not None:
        max_chars = int(getattr(config, "input_guard_max_prompt_chars", 4000) or 4000)

    sample = f"{title or ''}\n{str(text or '')[:max_chars]}".strip()
    if not sample:
        return {"pass": False, "reason": "off_topic_prefilter", "matched": []}

    matched = []
    for pattern in (DOMAIN_STRONG_EN_RE, DOMAIN_STRONG_ZH_RE):
        for match in pattern.finditer(sample):
            token = match.group(0).strip()
            if token and token.lower() not in {m.lower() for m in matched}:
                matched.append(token)
            if len(matched) >= 5:
                break
        if len(matched) >= 5:
            break

    if matched:
        return {"pass": True, "reason": "ok", "matched": matched}

    broad_match = DOMAIN_BROAD_MINERAL_RE.search(sample)
    if broad_match and DOMAIN_SUPPLY_CONTEXT_RE.search(sample):
        ctx_match = DOMAIN_SUPPLY_CONTEXT_RE.search(sample)
        tokens = [broad_match.group(0).strip(), ctx_match.group(0).strip()]
        return {"pass": True, "reason": "ok", "matched": tokens}

    return {"pass": False, "reason": "off_topic_prefilter", "matched": []}


def guard_article_text(raw_text, config):
    raw = str(raw_text or "")
    max_raw_len = int(getattr(config, "input_guard_max_raw_len", 50000) or 50000)
    max_prompt_chars = int(
        getattr(config, "input_guard_max_prompt_chars", 4000) or 4000
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
