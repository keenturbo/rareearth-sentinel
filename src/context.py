from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
import re

from .utils import now_cst


TOKEN_RE = re.compile(r"[^\w\s\u4e00-\u9fff\-]+", re.UNICODE)


def _parse_date(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None


def _normalize_token(text):
    if not text:
        return ""
    text = str(text).strip().lower()
    text = TOKEN_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _token_list(value):
    if not value:
        return []
    if isinstance(value, list):
        return [v for v in value if v]
    return [value]


def _format_context_line(info, dt):
    date_label = dt.date().isoformat() if dt else ""
    minerals = ",".join(info.minerals[:2]) if info.minerals else ""
    title = info.title.strip()
    if len(title) > 80:
        title = title[:77] + "..."
    line = " | ".join(
        part
        for part in [
            date_label,
            info.country,
            minerals,
            info.action,
            f"风险{info.risk_level}" if info.risk_level else "",
            f"对中国{info.impact}" if info.impact else "",
            title,
        ]
        if part
    )
    return line


@dataclass(frozen=True)
class ContextRecord:
    record_id: str
    title: str
    country: str
    minerals: list
    entities: list
    action: str
    risk_level: int
    impact: str
    published_at: str
    collected_at: str

    @classmethod
    def from_record(cls, record):
        return cls(
            record_id=record.get("id") or record.get("url") or "",
            title=record.get("title") or "",
            country=record.get("country_primary") or "",
            minerals=record.get("minerals") or [],
            entities=record.get("entities") or [],
            action=record.get("action_type") or "",
            risk_level=int(record.get("risk_level") or 0),
            impact=record.get("china_impact") or "",
            published_at=record.get("published_at") or "",
            collected_at=record.get("collected_at") or "",
        )

    def date(self):
        return _parse_date(self.published_at) or _parse_date(self.collected_at)

    def tokens(self):
        tokens = []
        tokens.extend(_token_list(self.country))
        tokens.extend(_token_list(self.minerals))
        tokens.extend(_token_list(self.entities))
        return tokens


class ContextBuilder:
    def __init__(self, records, max_items=6, lookback_days=365, min_token_len=2):
        self.max_items = max_items
        self.lookback_days = lookback_days
        self.min_token_len = min_token_len
        self._records = {}
        self._index = defaultdict(set)
        self._token_cache = None
        for record in records:
            self.add_record(record)

    def add_record(self, record):
        info = ContextRecord.from_record(record)
        if not info.record_id:
            return
        self._records[info.record_id] = info
        for raw_token in info.tokens():
            token = _normalize_token(raw_token)
            if len(token) < self.min_token_len:
                continue
            self._index[token].add(info.record_id)
        self._token_cache = None

    def _all_tokens(self):
        if self._token_cache is None:
            self._token_cache = list(self._index.keys())
        return self._token_cache

    def build_context(self, title, summary, exclude_id=""):
        text = f"{title or ''}\n{summary or ''}".lower()
        text = _normalize_token(text)
        if not text:
            return ""

        matched = defaultdict(int)
        for token in self._all_tokens():
            if token and token in text:
                for record_id in self._index.get(token, []):
                    if exclude_id and record_id == exclude_id:
                        continue
                    matched[record_id] += 1

        if not matched:
            return ""

        cutoff = None
        if self.lookback_days:
            cutoff = now_cst() - timedelta(days=self.lookback_days)

        scored = []
        for record_id, score in matched.items():
            info = self._records.get(record_id)
            if not info:
                continue
            dt = info.date()
            if cutoff and dt and dt < cutoff:
                continue
            scored.append((score, dt, info))

        if not scored:
            return ""

        scored.sort(key=lambda item: (item[0], item[1] or datetime.min), reverse=True)
        lines = []
        for _score, dt, info in scored[: self.max_items]:
            line = _format_context_line(info, dt)
            if line:
                lines.append(f"- {line}")
        return "\n".join(lines)


class VectorContextBuilder:
    def __init__(self, records, embedding_store, max_items=6, lookback_days=365):
        self.max_items = max_items
        self.lookback_days = lookback_days
        self.embedding_store = embedding_store
        self._records = {}
        for record in records:
            self.add_record(record)

    def add_record(self, record):
        info = ContextRecord.from_record(record)
        if not info.record_id:
            return
        self._records[info.record_id] = info
        if self.embedding_store:
            self.embedding_store.ensure_record_embedding(record)

    def build_context(self, title, summary, exclude_id=""):
        query_text = f"{title or ''}\n{summary or ''}".strip()
        if not query_text:
            return ""
        if not self.embedding_store:
            return ""

        vector = self.embedding_store.embed_query(query_text)
        if not vector:
            return ""

        results = self.embedding_store.query(
            vector, top_k=self.max_items + 4, exclude_id=exclude_id
        )
        if not results:
            return ""

        cutoff = None
        if self.lookback_days:
            cutoff = now_cst() - timedelta(days=self.lookback_days)

        lines = []
        for record_id, _score in results:
            info = self._records.get(record_id)
            if not info:
                continue
            dt = info.date()
            if cutoff and dt and dt < cutoff:
                continue
            line = _format_context_line(info, dt)
            if line:
                lines.append(f"- {line}")
            if len(lines) >= self.max_items:
                break

        return "\n".join(lines)
