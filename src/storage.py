import json
import os
from collections import deque

from .utils import hash_url, isoformat, now_cst


class Storage:
    def __init__(self, config):
        self.config = config
        os.makedirs(self.config.data_dir, exist_ok=True)
        os.makedirs(self.config.static_dir, exist_ok=True)

    @property
    def jsonl_path(self):
        return os.path.join(self.config.data_dir, "intel.jsonl")

    @property
    def index_json_path(self):
        return os.path.join(self.config.static_dir, "index.json")

    @property
    def index_html_path(self):
        return os.path.join(self.config.static_dir, "index.html")

    @property
    def status_json_path(self):
        return os.path.join(self.config.static_dir, "status.json")

    def load_existing_ids(self):
        seen = set()
        if not os.path.exists(self.jsonl_path):
            return seen
        with open(self.jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    record_id = record.get("id")
                    if record_id:
                        seen.add(record_id)
                except json.JSONDecodeError:
                    continue
        return seen

    def is_seen(self, url, seen_ids):
        return hash_url(url) in seen_ids

    def build_record(self, item, analysis, content, content_source="fulltext", fetch_status=None, resolved_url=None):
        now = now_cst()
        return {
            "id": hash_url(item.get("url", "")),
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "resolved_url": resolved_url or item.get("url", ""),
            "source_type": item.get("source_type", ""),
            "published_at": item.get("published_at", ""),
            "collected_at": isoformat(now),
            "language": item.get("language", ""),
            "summary_zh": analysis.get("summary_zh", ""),
            "country_primary": analysis.get("country_primary", ""),
            "event_location": analysis.get("event_location", ""),
            "minerals": analysis.get("minerals", []),
            "entities": analysis.get("entities", []),
            "action_type": analysis.get("action_type", "Other"),
            "risk_level": analysis.get("risk_level", 3),
            "importance_score": analysis.get("importance_score", 3),
            "china_involved": analysis.get("china_involved", False),
            "china_dependency": analysis.get("china_dependency", "None"),
            "china_role": analysis.get("china_role", "Mentioned"),
            "confidence": analysis.get("confidence", 60),
            "content_source": content_source,
            "fetch_status": fetch_status or "",
            "content": content,
        }

    def append_record(self, record):
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")

    def read_recent(self, limit):
        total = 0
        recent = deque(maxlen=limit)
        if not os.path.exists(self.jsonl_path):
            return total, []
        with open(self.jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total += 1
                recent.append(record)
        return total, list(recent)

    def write_index_json(self, index_data):
        with open(self.index_json_path, "w", encoding="utf-8") as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)

    def write_index_html(self, html_content):
        with open(self.index_html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

    def write_status_json(self, status_data):
        with open(self.status_json_path, "w", encoding="utf-8") as f:
            json.dump(status_data, f, ensure_ascii=False, indent=2)
