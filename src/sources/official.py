from urllib.parse import quote_plus, urlparse

import feedparser
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


class OfficialSource(Source):
    source_type = "official"

    def __init__(self, config):
        self.config = config
        self.entries = self._load_sources(config.official_sources_file)

    def fetch_items(self):
        items = []
        if not self.entries:
            return items

        for entry in self.entries:
            mode = (entry.get("mode") or entry.get("type") or "site_search").lower()
            if mode == "rss":
                items.extend(self._fetch_rss(entry))
            elif mode == "html":
                items.extend(self._fetch_html(entry))
            else:
                items.extend(self._fetch_site_search(entry))

        return items

    def _load_sources(self, path):
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
