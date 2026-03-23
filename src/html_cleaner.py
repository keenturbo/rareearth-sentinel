import html
import re

HTML_BLOCK_PATTERNS = [
    re.compile(r"<script\b[^>]*>.*?</script>", re.I | re.S),
    re.compile(r"<style\b[^>]*>.*?</style>", re.I | re.S),
    re.compile(r"<noscript\b[^>]*>.*?</noscript>", re.I | re.S),
    re.compile(r"<svg\b[^>]*>.*?</svg>", re.I | re.S),
    re.compile(r"<canvas\b[^>]*>.*?</canvas>", re.I | re.S),
    re.compile(r"<iframe\b[^>]*>.*?</iframe>", re.I | re.S),
]

MAIN_CONTAINER_PATTERNS = [
    re.compile(r"<article\b[^>]*>(.*?)</article>", re.I | re.S),
    re.compile(r"<main\b[^>]*>(.*?)</main>", re.I | re.S),
    re.compile(r'<div\b[^>]+id=["\'](?:content|main|article|post-body|entry-content)["\'][^>]*>(.*?)</div>', re.I | re.S),
    re.compile(r'<section\b[^>]+id=["\'](?:content|main)["\'][^>]*>(.*?)</section>', re.I | re.S),
    re.compile(r'<div\b[^>]+class=["\'][^"\']*(?:article|article-body|article-content|post-content|entry-content|story-body|content-body)[^"\']*["\'][^>]*>(.*?)</div>', re.I | re.S),
]

NOISE_LINE_PATTERNS = [
    re.compile(r"\bfunction\s*\("),
    re.compile(r"\bwindow\."),
    re.compile(r"\bdocument\."),
    re.compile(r"\bjQuery\s*\("),
    re.compile(r"\bgtag\s*\("),
    re.compile(r"\bdataLayer\b"),
    re.compile(r"\bdatadog\b", re.I),
    re.compile(r"\balgolia\b", re.I),
    re.compile(r"__NEXT_DATA__"),
    re.compile(r"webpack"),
    re.compile(r"googletagmanager", re.I),
]

NOISE_PHRASES = [
    "cookie",
    "privacy policy",
    "terms of use",
    "terms of service",
    "subscribe",
    "subscription",
    "sign up",
    "sign in",
    "log in",
    "newsletter",
    "advertisement",
    "advertising",
    "all rights reserved",
    "accept all",
    "manage consent",
    "consent preferences",
    "enable javascript",
    "javascript is required",
    "share this",
    "follow us",
    "recommended for you",
]


def _strip_html_blocks(raw_html):
    text = str(raw_html or "")
    for pattern in HTML_BLOCK_PATTERNS:
        text = pattern.sub(" ", text)
    return text



def _extract_main_fragment(text):
    for pattern in MAIN_CONTAINER_PATTERNS:
        match = pattern.search(text)
        if match:
            fragment = match.group(1)
            if fragment and len(fragment) >= 200:
                return fragment
    body_match = re.search(r"<body\b[^>]*>(.*?)</body>", text, re.I | re.S)
    if body_match:
        return body_match.group(1)
    return text



def _looks_like_noise(line):
    lower = line.lower()
    if any(p in lower for p in NOISE_PHRASES):
        return True
    if len(line) > 500 and ("{" in line or "function(" in lower or "window." in lower):
        return True
    for pattern in NOISE_LINE_PATTERNS:
        if pattern.search(line):
            return True
    return False



def extract_main_text(raw_html):
    if not raw_html:
        return ""

    text = _strip_html_blocks(raw_html)
    text = _extract_main_fragment(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"</div\s*>", "\n", text, flags=re.I)
    text = re.sub(r"</li\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")

    cleaned_lines = []
    seen = set()
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        if len(line) < 20:
            continue
        if _looks_like_noise(line):
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()
