from urllib.parse import urlparse

import requests


class Fetcher:
    def __init__(self, config):
        self.config = config

    def fetch(self, url):
        if not url:
            return None
        jina_url = self._to_jina_url(url)
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; RareEarthSentinel/1.0)"
        }
        try:
            resp = requests.get(
                jina_url,
                headers=headers,
                timeout=self.config.fetch_timeout_seconds,
            )
        except requests.RequestException:
            return None

        if resp.status_code != 200:
            return None
        return resp.text

    def _to_jina_url(self, url):
        parsed = urlparse(url)
        if parsed.scheme == "http":
            return f"https://r.jina.ai/http://{parsed.netloc}{parsed.path}?{parsed.query}".rstrip(
                "?"
            )
        if parsed.scheme == "https":
            return f"https://r.jina.ai/https://{parsed.netloc}{parsed.path}?{parsed.query}".rstrip(
                "?"
            )
        return f"https://r.jina.ai/http://{url}"
