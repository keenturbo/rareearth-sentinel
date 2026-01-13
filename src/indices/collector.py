import json
import os
import re
from typing import List
from urllib.parse import quote_plus, urlparse

import feedparser
import requests

from ..utils import isoformat, now_cst, is_homepage_url

RSS_URL_TEMPLATE = (
    "https://news.google.com/rss/search?q={query}&hl={lang}&gl={region}&ceid={edition}"
)

DEFAULT_QUERY = (
    '"rare earth" OR "rare earths" OR "critical minerals" OR 稀土 OR 价格 指数'
)

EXCEL_URL_RE = re.compile(r"https?://[^\s'\"]+\.xls", re.IGNORECASE)


class IndexCollector:
    def __init__(self, config):
        self.config = config
        self.sources = self._load_sources(config.index_sources_file)

    def collect(self) -> List[str]:
        if not self.sources:
            return []
        date_str = now_cst().strftime("%Y-%m-%d")
        out_dir = os.path.join(self.config.index_raw_dir, date_str)
        os.makedirs(out_dir, exist_ok=True)
        written = []
        for source in self.sources:
            record = self._fetch_source(source)
            if not record:
                continue
            if record.get("binary"):
                filename = record.get("filename") or f"{record['id']}.bin"
                file_path = os.path.join(out_dir, filename)
                with open(file_path, "wb") as f:
                    f.write(record.get("content_bytes") or b"")
                record.pop("content_bytes", None)
                record["file_path"] = file_path
            meta_path = os.path.join(out_dir, f"{record['id']}.json")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
            written.append(meta_path)
        return written

    def _load_sources(self, path):
        if not path:
            return []
        candidates = [path]
        if not os.path.isabs(path):
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            candidates.append(os.path.join(root, path))
        content = None
        for candidate in candidates:
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    content = f.read()
                break
            except FileNotFoundError:
                continue
        if content is None:
            return []
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            try:
                import yaml
            except ImportError:
                return []
            data = yaml.safe_load(content)
        if isinstance(data, dict):
            return data.get("sources", [])
        if isinstance(data, list):
            return data
        return []

    def _fetch_source(self, source):
        mode = (source.get("mode") or "html").lower()
        if mode == "rss":
            return self._fetch_rss(source)
        if mode == "site_search":
            return self._fetch_site_search(source)
        if mode in {"excel", "excel_from_page"}:
            return self._fetch_excel(source)
        return self._fetch_html(source)

    def _entry_max(self, source):
        try:
            return int(source.get("max_items") or 10)
        except (TypeError, ValueError):
            return 10

    def _entry_lang(self, source):
        return source.get("news_lang") or self.config.google_news_lang

    def _entry_region(self, source):
        return source.get("news_region") or self.config.google_news_region

    def _entry_edition(self, source):
        return source.get("news_edition") or self.config.google_news_edition

    def _fetch_rss(self, source):
        url = source.get("url")
        if not url:
            return None
        feed = feedparser.parse(url)
        items = self._collect_feed_items(feed.entries[: self._entry_max(source)])
        return {
            "id": source.get("id") or "index_rss",
            "name": source.get("name") or url,
            "mode": "rss",
            "source_url": url,
            "fetched_at": isoformat(now_cst()),
            "items": items,
        }

    def _fetch_site_search(self, source):
        domain = source.get("domain") or self._domain_from_url(source.get("url"))
        if not domain:
            return None
        query_terms = source.get("query_terms") or DEFAULT_QUERY
        query = f"site:{domain} {query_terms}".strip()
        rss_url = RSS_URL_TEMPLATE.format(
            query=quote_plus(query),
            lang=self._entry_lang(source),
            region=self._entry_region(source),
            edition=self._entry_edition(source),
        )
        feed = feedparser.parse(rss_url)
        items = self._collect_feed_items(feed.entries[: self._entry_max(source)])
        return {
            "id": source.get("id") or "index_site",
            "name": source.get("name") or domain,
            "mode": "site_search",
            "source_url": rss_url,
            "fetched_at": isoformat(now_cst()),
            "items": items,
        }

    def _fetch_excel(self, source):
        excel_url = source.get("excel_url") or source.get("url")
        page_url = source.get("page_url")
        if excel_url and not excel_url.lower().endswith(".xls"):
            excel_url = None
        urls_to_try = []
        if excel_url:
            urls_to_try.append(excel_url)
        if page_url:
            extracted = self._extract_excel_url(page_url)
            if extracted and extracted not in urls_to_try:
                urls_to_try.append(extracted)
        if not urls_to_try:
            return None
        headers = {"User-Agent": "Mozilla/5.0 (compatible; RareEarthSentinel/1.0)"}
        last_error = None
        for url in urls_to_try:
            try:
                response = requests.get(url, headers=headers, timeout=30)
                status = response.status_code
                if status != 200:
                    last_error = f"status {status}"
                    continue
                filename = source.get("filename") or f"{source.get('id','index_excel')}.xls"
                return {
                    "id": source.get("id") or "index_excel",
                    "name": source.get("name") or url,
                    "mode": "excel",
                    "source_url": url,
                    "fetched_at": isoformat(now_cst()),
                    "status": status,
                    "binary": True,
                    "filename": filename,
                    "content_bytes": response.content,
                }
            except Exception as exc:
                last_error = str(exc)
                continue
        return {
            "id": source.get("id") or "index_excel",
            "name": source.get("name") or (excel_url or page_url or ""),
            "mode": "excel",
            "source_url": excel_url or page_url or "",
            "fetched_at": isoformat(now_cst()),
            "status": "error",
            "error": last_error or "fetch_failed",
        }

    def _extract_excel_url(self, page_url: str) -> str:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; RareEarthSentinel/1.0)"}
        try:
            response = requests.get(page_url, headers=headers, timeout=20)
            text = response.text
        except Exception:
            return ""
        matches = EXCEL_URL_RE.findall(text or "")
        if matches:
            return matches[0]
        return ""

    def _fetch_html(self, source):
        url = source.get("url")
        if not url:
            return None
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; RareEarthSentinel/1.0)"
        }
        try:
            response = requests.get(
                url, headers=headers, timeout=self.config.fetch_timeout_seconds
            )
            content = response.text
            status = response.status_code
        except Exception as exc:
            return {
                "id": source.get("id") or "index_html",
                "name": source.get("name") or url,
                "mode": "html",
                "source_url": url,
                "fetched_at": isoformat(now_cst()),
                "status": "error",
                "error": str(exc),
            }
        return {
            "id": source.get("id") or "index_html",
            "name": source.get("name") or url,
            "mode": "html",
            "source_url": url,
            "fetched_at": isoformat(now_cst()),
            "status": status,
            "content": content,
        }

    def _collect_feed_items(self, entries):
        items = []
        for entry in entries:
            link = entry.get("link") or ""
            if is_homepage_url(link):
                continue
            items.append(
                {
                    "title": entry.get("title") or "",
                    "url": link,
                    "summary": entry.get("summary") or entry.get("description") or "",
                    "published_at": entry.get("published")
                    or entry.get("updated")
                    or "",
                }
            )
        return items

    def _domain_from_url(self, url):
        if not url:
            return ""
        try:
            return urlparse(url).netloc
        except Exception:
            return ""
