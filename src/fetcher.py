import html
import json
import re
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests


class Fetcher:
    def __init__(self, config):
        self.config = config
        self.session = requests.Session()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (compatible; RareEarthSentinel/1.0)"
        }

    def fetch(self, url):
        if not url:
            return None
        target_url = self._resolve_url(url)
        content = self._fetch_jina(target_url)
        if content:
            return content
        return self._fetch_direct(target_url)

    def _fetch_jina(self, url):
        jina_url = self._to_jina_url(url)
        try:
            resp = self.session.get(
                jina_url,
                headers=self.headers,
                timeout=self.config.fetch_timeout_seconds,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            print(f"  - fetch: jina_error {exc.__class__.__name__}")
            return None

        if resp.status_code != 200:
            print(f"  - fetch: jina_status {resp.status_code}")
            return None
        return resp.text

    def _fetch_direct(self, url):
        try:
            resp = self.session.get(
                url,
                headers=self.headers,
                timeout=self.config.fetch_timeout_seconds,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            print(f"  - fetch: direct_error {exc.__class__.__name__}")
            return None

        if resp.status_code != 200:
            print(f"  - fetch: direct_status {resp.status_code}")
            return None
        return resp.text

    def _resolve_url(self, url):
        parsed = urlparse(url)
        if parsed.netloc != "news.google.com":
            return url

        # Some Google News links include the final URL in query params.
        query = parse_qs(parsed.query)
        for key in ("url", "u", "q"):
            if key in query and query[key]:
                return query[key][0]

        decoded = self._decode_google_news(url)
        if decoded:
            return decoded

        try:
            resp = self.session.get(
                url,
                headers=self.headers,
                timeout=self.config.fetch_timeout_seconds,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            print(f"  - fetch: resolve_error {exc.__class__.__name__}")
            return url

        final_url = resp.url or url
        if urlparse(final_url).netloc != "news.google.com":
            resp.close()
            return final_url

        resolved = self._extract_canonical(resp.text)
        resp.close()
        return resolved or final_url

    def _decode_google_news(self, url):
        parsed = urlparse(url)
        article_id = parsed.path.split("/")[-1]
        if not article_id:
            return None

        info_url = f"https://news.google.com/rss/articles/{article_id}"
        try:
            resp = self.session.get(
                info_url,
                headers=self.headers,
                timeout=self.config.fetch_timeout_seconds,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            print(f"  - fetch: decode_error {exc.__class__.__name__}")
            return None

        if resp.status_code != 200:
            return None

        signature = self._extract_attr(resp.text, "data-n-a-sg")
        timestamp = self._extract_attr(resp.text, "data-n-a-ts")
        if not signature or not timestamp:
            return None

        payload = [
            [
                "Fbv4je",
                json.dumps(
                    [
                        "garturlreq",
                        [
                            [
                                "X",
                                "X",
                                ["X", "X"],
                                None,
                                None,
                                1,
                                1,
                                "US:en",
                                None,
                                1,
                                None,
                                None,
                                None,
                                None,
                                None,
                                0,
                                1,
                            ],
                            "X",
                            "X",
                            1,
                            [1, 1, 1],
                            1,
                            1,
                            None,
                            0,
                            0,
                            None,
                            0,
                        ],
                        article_id,
                        int(timestamp),
                        signature,
                    ]
                ),
                None,
                "generic",
            ]
        ]

        data = "f.req=" + quote(json.dumps([payload]))
        try:
            resp = self.session.post(
                "https://news.google.com/_/DotsSplashUi/data/batchexecute",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                    "User-Agent": self.headers["User-Agent"],
                },
                data=data,
                timeout=self.config.fetch_timeout_seconds,
            )
        except requests.RequestException:
            return None

        if resp.status_code != 200:
            return None

        return self._extract_batchexecute_url(resp.text)

    def _extract_batchexecute_url(self, text):
        if not text:
            return None
        try:
            json_text = text.split("\n\n", 1)[1]
            outer = json.loads(json_text)
        except (IndexError, json.JSONDecodeError, TypeError):
            return None

        for entry in outer:
            if not isinstance(entry, list) or len(entry) < 3:
                continue
            payload = entry[2]
            if not payload or "garturlres" not in payload:
                continue
            try:
                inner = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if inner and inner[0] == "garturlres":
                return inner[1]
        return None

    def _extract_attr(self, html_text, attr_name):
        if not html_text:
            return None
        match = re.search(rf'{attr_name}="([^"]+)"', html_text)
        if not match:
            return None
        return match.group(1)

    def _extract_canonical(self, html_text):
        if not html_text:
            return None

        patterns = [
            r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"',
            r'<meta[^>]+property="og:url"[^>]+content="([^"]+)"',
            r'url=(https?%3A%2F%2F[^&"\s]+)',
            r'url=(https?://[^&"\s]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if not match:
                continue
            candidate = html.unescape(match.group(1))
            candidate = unquote(candidate)
            if candidate.startswith("http"):
                return candidate
        return None

    def _to_jina_url(self, url):
        parsed = urlparse(url)
        if parsed.scheme == "http":
            return (
                f"https://r.jina.ai/http://{parsed.netloc}{parsed.path}?{parsed.query}"
            ).rstrip("?")
        if parsed.scheme == "https":
            return (
                f"https://r.jina.ai/https://{parsed.netloc}{parsed.path}?{parsed.query}"
            ).rstrip("?")
        return f"https://r.jina.ai/http://{url}"
