import html as html_lib
import json
import os
import re
from urllib.parse import quote_plus, urlparse

import feedparser
import requests
import yaml

from .base import Source
from ..utils import is_homepage_url

RSS_URL_TEMPLATE = (
    "https://news.google.com/rss/search?q={query}&hl={lang}&gl={region}&ceid={edition}"
)
DEFAULT_QUERY_TERMS = (
    '"rare earth" OR "rare earths" OR "critical minerals" OR "strategic minerals" '
    "OR 稀土 OR 关键矿产 OR 出口管制"
)

TG_MESSAGE_RE = re.compile(
    r'<div class="tgme_widget_message"[^>]*data-post="([^"]+)"[^>]*>'
    r'.*?<div class="tgme_widget_message_text[^\"]*"[^>]*>(.*?)</div>',
    re.S,
)
TG_LINK_RE = re.compile(r'href="(https?://[^"]+)"')
TG_BR_RE = re.compile(r"<br\s*/?>", re.I)
TG_TAG_RE = re.compile(r"<[^>]+>")


class OfficialSource(Source):
    source_type = "official"

    def __init__(self, config):
        self.config = config
        self.entries = self._load_sources(config.official_sources_file)

    def fetch_items(self):
        items = []
        if not self.entries:
            return items

        entries = self._filter_entries(self.entries)
        for entry in entries:
            mode = (entry.get("mode") or entry.get("type") or "site_search").lower()
            if mode == "rss":
                items.extend(self._fetch_rss(entry))
            elif mode == "html":
                items.extend(self._fetch_html(entry))
            elif mode == "telegram_channel":
                items.extend(self._fetch_telegram_channel(entry))
            else:
                items.extend(self._fetch_site_search(entry))

        return items

    def _load_sources(self, path):
        base_sources = self._load_yaml_sources(path)
        custom_sources = self._load_custom_sources(
            getattr(self.config, "custom_sources_file", "")
        )
        return self._merge_sources(base_sources, custom_sources)

    def _load_yaml_sources(self, path):
        if not path:
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except FileNotFoundError:
            return []
        if isinstance(data, dict):
            return data.get("sources", [])
        if isinstance(data, list):
            return data
        return []

    def _load_custom_sources(self, path):
        if not path or not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(data, dict):
            return data.get("sources", [])
        if isinstance(data, list):
            return data
        return []

    def _merge_sources(self, base_sources, custom_sources):
        base_sources = base_sources or []
        custom_sources = custom_sources or []

        custom_map = {
            entry.get("id"): entry
            for entry in custom_sources
            if isinstance(entry, dict) and entry.get("id")
        }
        merged = []
        seen = set()

        for entry in base_sources:
            if not isinstance(entry, dict):
                continue
            source_id = entry.get("id")
            merged_entry = dict(entry)
            if source_id and source_id in custom_map:
                merged_entry.update(custom_map[source_id])
            if self._is_enabled(merged_entry):
                merged.append(merged_entry)
            seen.add(source_id)

        for entry in custom_sources:
            if not isinstance(entry, dict):
                continue
            source_id = entry.get("id")
            if not source_id or source_id in seen:
                continue
            if self._is_enabled(entry):
                merged.append(entry)

        return merged

    def _is_enabled(self, entry):
        if not isinstance(entry, dict):
            return False
        value = entry.get("enabled")
        if value is None:
            return True
        return str(value).strip().lower() not in {"0", "false", "no", "off"}

    def _filter_entries(self, entries):
        source_ids = [
            value.strip()
            for value in (self.config.official_source_ids or [])
            if value and str(value).strip()
        ]
        if not source_ids:
            return entries
        wanted = set(source_ids)
        return [entry for entry in entries if entry.get("id") in wanted]

    def _entry_max(self, entry):
        try:
            return int(entry.get("max_items") or self.config.official_max_items)
        except (TypeError, ValueError):
            return self.config.official_max_items

    def _entry_lang(self, entry):
        return entry.get("news_lang") or self.config.google_news_lang

    def _entry_region(self, entry):
        return entry.get("news_region") or self.config.google_news_region

    def _entry_edition(self, entry):
        return entry.get("news_edition") or self.config.google_news_edition

    def _entry_language_label(self, entry):
        return entry.get("language") or self._entry_lang(entry)

    def _domain_from_url(self, url):
        if not url:
            return ""
        return urlparse(url).netloc

    def _build_item(self, entry, feed_entry):
        source_id = entry.get("id", "official")
        link = feed_entry.get("link", "") or ""
        if is_homepage_url(link):
            return None
        return {
            "source_type": f"official:{source_id}",
            "title": feed_entry.get("title", ""),
            "url": link,
            "summary": feed_entry.get("summary")
            or feed_entry.get("description")
            or "",
            "published_at": feed_entry.get("published")
            or feed_entry.get("updated")
            or "",
            "language": self._entry_language_label(entry),
        }

    def _fetch_rss(self, entry):
        url = entry.get("url")
        if not url:
            return []
        feed = feedparser.parse(url)
        items = []
        limit = self._entry_max(entry)
        for feed_entry in feed.entries[:limit]:
            item = self._build_item(entry, feed_entry)
            if item:
                items.append(item)
        return items

    def _fetch_site_search(self, entry):
        domain = entry.get("domain") or self._domain_from_url(entry.get("url"))
        if not domain:
            return []
        query_terms = entry.get("query_terms") or DEFAULT_QUERY_TERMS
        query = f"site:{domain} {query_terms}".strip()
        rss_url = RSS_URL_TEMPLATE.format(
            query=quote_plus(query),
            lang=self._entry_lang(entry),
            region=self._entry_region(entry),
            edition=self._entry_edition(entry),
        )
        feed = feedparser.parse(rss_url)
        items = []
        limit = self._entry_max(entry)
        for feed_entry in feed.entries[:limit]:
            item = self._build_item(entry, feed_entry)
            if item:
                items.append(item)
        return items

    def _fetch_html(self, entry):
        url = entry.get("url")
        if not url:
            return []
        # Fallback to site search when HTML parsing is not configured.
        return self._fetch_site_search(entry)

    def _fetch_telegram_channel(self, entry):
        url = entry.get("url") or entry.get("domain") or ""
        if not url:
            return []
        if not url.startswith("http"):
            url = f"https://{url}"

        try:
            resp = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; RareEarthSentinel/1.0)"
                },
                timeout=self.config.fetch_timeout_seconds,
            )
        except requests.RequestException:
            return []

        if resp.status_code != 200:
            return []

        html_text = resp.text
        items = []
        limit = self._entry_max(entry)
        source_id = entry.get("id", "official")

        for match in TG_MESSAGE_RE.finditer(html_text):
            post_id = match.group(1)
            text_html = match.group(2)
            text = self._strip_telegram_text(text_html)
            if not text:
                continue
            if not self._match_telegram_text(text, entry):
                continue

            lines = [line.strip() for line in text.splitlines() if line.strip()]
            title = lines[0] if lines else text[:120]
            summary = text

            origin_url = self._extract_first_link(text_html)
            message_url = f"https://t.me/s/{post_id}"
            item_url = origin_url or message_url

            items.append(
                {
                    "source_type": f"official:{source_id}",
                    "title": title,
                    "url": item_url,
                    "summary": summary,
                    "published_at": "",
                    "language": self._entry_language_label(entry),
                }
            )
            if len(items) >= limit:
                break

        return items

    def _strip_telegram_text(self, html_text):
        if not html_text:
            return ""
        text = TG_BR_RE.sub("\n", html_text)
        text = TG_TAG_RE.sub("", text)
        text = html_lib.unescape(text)
        return text.strip()

    def _extract_first_link(self, html_text):
        if not html_text:
            return ""
        match = TG_LINK_RE.search(html_text)
        if not match:
            return ""
        return match.group(1)

    def _parse_query_terms(self, query_terms):
        if not query_terms:
            return []
        parts = re.split(r"\s+OR\s+", query_terms, flags=re.I)
        terms = []
        for part in parts:
            term = part.strip().strip("\"").strip("'")
            if term:
                terms.append(term)
        return terms

    def _match_telegram_text(self, text, entry):
        if "#稀土" in text or "#rareearth" in text.lower():
            return True
        query_terms = self._parse_query_terms(entry.get("query_terms"))
        if not query_terms:
            return False
        lower_text = text.lower()
        for term in query_terms:
            if not term:
                continue
            if term.lower() in lower_text or term in text:
                return True
        return False
