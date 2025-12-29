from urllib.parse import urlparse

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

        try:
            resp = self.session.get(
                url,
                headers=self.headers,
                timeout=self.config.fetch_timeout_seconds,
                allow_redirects=True,
                stream=True,
            )
        except requests.RequestException as exc:
            print(f"  - fetch: resolve_error {exc.__class__.__name__}")
            return url

        final_url = resp.url or url
        resp.close()
        return final_url

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
