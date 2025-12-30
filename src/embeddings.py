import json
import math
import os
import time

import google.generativeai as genai

from .utils import isoformat, now_cst


def _vector_norm(values):
    total = 0.0
    for value in values:
        total += value * value
    if total <= 0.0:
        return 1.0
    return math.sqrt(total)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


class GeminiEmbedder:
    def __init__(self, config):
        if not config.gemini_api_key:
            raise ValueError("GEMINI_API_KEY (or GOOGLE_API_KEY) is required")
        genai.configure(api_key=config.gemini_api_key)
        self.model = config.embedding_model
        self.doc_task = config.embedding_doc_task_type
        self.query_task = config.embedding_query_task_type
        self.max_chars = config.embedding_max_chars

    def embed(self, text, task_type):
        content = (text or "").strip()
        if not content:
            return None
        content = content[: self.max_chars]
        try:
            result = genai.embed_content(
                model=self.model,
                content=content,
                task_type=task_type,
            )
        except Exception:
            return None
        if isinstance(result, dict):
            return result.get("embedding") or result.get("values")
        return getattr(result, "embedding", None)


class EmbeddingStore:
    def __init__(self, config):
        self.config = config
        self.path = os.path.join(self.config.data_dir, "embeddings.jsonl")
        self.embedder = GeminiEmbedder(config)
        self._vectors = {}
        self._norms = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                record_id = record.get("id")
                vector = record.get("embedding")
                if not record_id or not isinstance(vector, list):
                    continue
                norm = record.get("norm")
                if not norm:
                    norm = _vector_norm(vector)
                self._vectors[record_id] = vector
                self._norms[record_id] = norm

    def _delay(self):
        if self.config.embedding_delay_seconds > 0:
            time.sleep(self.config.embedding_delay_seconds)

    def _append(self, record_id, vector, norm):
        payload = {
            "id": record_id,
            "embedding": vector,
            "norm": norm,
            "updated_at": isoformat(now_cst()),
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
            f.write("\n")

    def _build_text(self, record):
        title = str(record.get("title", "")).strip()
        summary = str(record.get("summary_zh") or record.get("summary") or "").strip()
        content = str(record.get("content", "")).strip()
        text = "\n".join([title, summary]).strip()
        if len(text) < self.config.embedding_min_chars and content:
            text = "\n".join([title, content]).strip()
        return text[: self.config.embedding_max_chars]

    def ensure_record_embedding(self, record):
        record_id = record.get("id")
        if not record_id:
            return False
        if record_id in self._vectors:
            return True
        text = self._build_text(record)
        if len(text) < self.config.embedding_min_chars:
            return False
        vector = self.embedder.embed(text, task_type=self.embedder.doc_task)
        if not vector:
            return False
        norm = _vector_norm(vector)
        self._vectors[record_id] = vector
        self._norms[record_id] = norm
        self._append(record_id, vector, norm)
        self._delay()
        return True

    def embed_query(self, text):
        vector = self.embedder.embed(text, task_type=self.embedder.query_task)
        if vector:
            self._delay()
        return vector

    def warmup(self, records, limit=0):
        added = 0
        if limit is None:
            limit = 0
        for record in records:
            if limit and added >= limit:
                break
            if self.ensure_record_embedding(record):
                added += 1
        if added:
            print(f"[embeddings] warmup added: {added}")
        return added

    def query(self, vector, top_k=6, exclude_id=""):
        if not vector:
            return []
        if not self._vectors:
            return []
        target_norm = _vector_norm(vector)
        if target_norm == 0.0:
            return []
        scored = []
        for record_id, other in self._vectors.items():
            if exclude_id and record_id == exclude_id:
                continue
            norm = self._norms.get(record_id) or _vector_norm(other)
            denom = target_norm * norm
            if denom == 0.0:
                continue
            score = _dot(vector, other) / denom
            scored.append((score, record_id))
        scored.sort(key=lambda item: item[0], reverse=True)
        results = []
        for score, record_id in scored[:top_k]:
            results.append((record_id, score))
        return results
