#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from html import unescape
from pathlib import Path
from urllib.request import Request, urlopen


def now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def fetch_html(url: str, timeout: int = 20) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        content_type = resp.headers.get("Content-Type", "")
        charset = "utf-8"
        m = re.search(r"charset=([a-zA-Z0-9_-]+)", content_type)
        if m:
            charset = m.group(1)
        return resp.read().decode(charset, errors="ignore")


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    html = re.sub(r"(?is)<style.*?>.*?</style>", " ", html)
    paragraphs = re.findall(r"(?is)<p[^>]*>(.*?)</p>", html)
    chunks = paragraphs if paragraphs else [html]

    out = []
    for c in chunks:
        t = re.sub(r"(?is)<[^>]+>", " ", c)
        t = unescape(t)
        t = re.sub(r"\s+", " ", t).strip()
        if len(t) >= 10:
            out.append(t)

    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def load_urls(intel_path: Path) -> set:
    urls = set()
    if not intel_path.exists():
        return urls
    with intel_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            url = obj.get("url")
            if url:
                urls.add(url)
    return urls


def build_record(args, article_text: str) -> dict:
    digest = hashlib.md5(f"{args.url}|{args.title}".encode("utf-8")).hexdigest()[:12]
    rid = f"manual-{dt.datetime.utcnow().strftime('%Y%m%d')}-{digest}"
    published_at = args.published_at.strip() if args.published_at else now_iso()

    summary = article_text[:280].strip()
    if len(article_text) > 280:
        summary += "..."
    if not summary:
        summary = args.title

    zh_mode = args.lang.lower().startswith("zh")

    rec = {
        "id": rid,
        "title": args.title,
        "url": args.url,
        "source_id": args.source_id,
        "published_at": published_at,
        "risk_score": int(args.risk_score),
        "impact_score": int(args.impact_score),
        "importance_score": int(args.importance_score),
        "confidence": int(args.confidence),
        "action_type": args.action_type,
        "summary_zh": summary if zh_mode else "",
        "summary_en": summary if not zh_mode else "",
        "expert_summary": summary if zh_mode else "",
        "expert_summary_en": summary if not zh_mode else "",
        "recommendations_strategy": [],
        "recommendations_tactical": [],
        "manual_ingest": True,
        "manual_ingest_at": now_iso(),
    }
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description="Manual ingest one article URL into data/intel.jsonl")
    ap.add_argument("--url", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--source-id", default="manual")
    ap.add_argument("--published-at", default="")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--data-dir", default=os.getenv("DATA_DIR", "data"))

    ap.add_argument("--risk-score", default=60)
    ap.add_argument("--impact-score", default=6)
    ap.add_argument("--importance-score", default=70)
    ap.add_argument("--confidence", default=60)
    ap.add_argument("--action-type", default="Other")

    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    intel_path = data_dir / "intel.jsonl"
    data_dir.mkdir(parents=True, exist_ok=True)

    existing_urls = load_urls(intel_path)
    if args.url in existing_urls:
        print("skip: url already exists in intel.jsonl")
        return 0

    try:
        html = fetch_html(args.url)
        text = html_to_text(html)
    except Exception as e:
        print(f"warn: fetch failed, fallback to title only: {e}")
        text = ""

    rec = build_record(args, text)

    with intel_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"ok: appended {rec['id']} to {intel_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
