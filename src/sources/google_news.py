from urllib.parse import quote_plus

import feedparser

from .base import Source
from ..config import load_keywords

RSS_URL_TEMPLATE = (
    "https://news.google.com/rss/search?q={query}&hl={lang}&gl={region}&ceid={edition}"
)


class GoogleNewsSource(Source):
    source_type = "rss"

    def __init__(self, config):
        self.config = config
        self.keywords = load_keywords(config.keywords_file)

    def fetch_items(self):
        items = []
        if not self.keywords:
            return items

        for keyword in self.keywords:
            rss_url = RSS_URL_TEMPLATE.format(
                query=quote_plus(keyword),
                lang=self.config.google_news_lang,
                region=self.config.google_news_region,
                edition=self.config.google_news_edition,
            )
            feed = feedparser.parse(rss_url)

            for entry in feed.entries[: self.config.max_items_per_keyword]:
                summary = entry.get("summary") or entry.get("description") or ""
                items.append(
                    {
                        "source_type": self.source_type,
                        "title": entry.get("title", ""),
                        "url": entry.get("link", ""),
                        "summary": summary,
                        "published_at": entry.get("published", ""),
                        "language": self.config.google_news_lang,
                    }
                )

        return items
