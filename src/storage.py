import json
import os
from collections import deque
from datetime import datetime, timedelta

from .utils import hash_url, isoformat, now_cst

# Only deterministic rejections may be cached; transient API/network failures
# (timeout, gemini_exception, empty_response, invalid_json, fetch_fail) are excluded.
CACHEABLE_REJECT_REASONS = {
    "off_topic_prefilter",
    "off_topic",
    "model_rejected",
    "insufficient_evidence",
    "invalid_input",
    "too_few_evidence_sentences",
    "missing_summary",
    "too_short",
    "empty_input",
    "empty_after_clean",
    "cleaned_too_short",
    "wrapper_noise",
    "navigation_noise",
    "script_noise",
    "noise_tokens",
    "html_wrapper",
    "html_ratio",
    "official_homepage",
}


class Storage:
    def __init__(self, config):
        self.config = config
        os.makedirs(self.config.data_dir, exist_ok=True)
        os.makedirs(self.config.static_dir, exist_ok=True)
        os.makedirs(self.archive_dir, exist_ok=True)

    def _subdir_base(self, subdir=""):
        if not subdir:
            return self.config.static_dir
        path = os.path.join(self.config.static_dir, subdir)
        os.makedirs(path, exist_ok=True)
        return path

    def _archive_dir_for(self, subdir=""):
        base = self._subdir_base(subdir)
        path = os.path.join(base, "archive")
        os.makedirs(path, exist_ok=True)
        return path

    @property
    def jsonl_path(self):
        return os.path.join(self.config.data_dir, "intel.jsonl")

    @property
    def rejected_urls_path(self):
        return os.path.join(self.config.data_dir, "status", "rejected_urls.jsonl")

    @property
    def content_variants_jsonl_path(self):
        return os.path.join(self.config.data_dir, "status", "content_variants.jsonl")

    @property
    def premium_dir(self):
        return self.config.premium_dir

    @property
    def index_json_path(self):
        return os.path.join(self.config.static_dir, "index.json")

    @property
    def index_html_path(self):
        return os.path.join(self.config.static_dir, "index.html")

    @property
    def status_json_path(self):
        return os.path.join(self.config.static_dir, "status.json")

    @property
    def archive_dir(self):
        return os.path.join(self.config.static_dir, "archive")

    @property
    def rollups_json_path(self):
        return os.path.join(self.config.static_dir, "rollups.json")

    def archive_json_path(self, month):
        return os.path.join(self.archive_dir, f"{month}.json")

    def archive_html_path(self, month):
        return os.path.join(self.archive_dir, f"{month}.html")

    def archive_json_path_for(self, month, subdir=""):
        archive_dir = self._archive_dir_for(subdir)
        return os.path.join(archive_dir, f"{month}.json")

    def archive_html_path_for(self, month, subdir=""):
        archive_dir = self._archive_dir_for(subdir)
        return os.path.join(archive_dir, f"{month}.html")

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

    def load_rejected_ids(self, ttl_days=None, now=None):
        if ttl_days is None:
            ttl_days = int(getattr(self.config, "rejected_url_ttl_days", 14) or 0)
        if ttl_days <= 0 or not os.path.exists(self.rejected_urls_path):
            return set()

        current = now or now_cst()
        cutoff = current - timedelta(days=ttl_days)
        rejected = set()

        with open(self.rejected_urls_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                record_id = str(entry.get("id") or "").strip()
                if not record_id:
                    continue
                raw_ts = str(entry.get("rejected_at") or "").strip()
                if not raw_ts:
                    continue
                try:
                    ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts.tzinfo is None and current.tzinfo is not None:
                    ts = ts.replace(tzinfo=current.tzinfo)
                elif ts.tzinfo is not None and current.tzinfo is not None:
                    ts = ts.astimezone(current.tzinfo)
                if ts >= cutoff:
                    rejected.add(record_id)
        return rejected

    def append_rejected_url(self, url, reason):
        ttl_days = int(getattr(self.config, "rejected_url_ttl_days", 14) or 0)
        if ttl_days <= 0 or not url:
            return False
        reason_str = str(reason or "").strip()
        if reason_str not in CACHEABLE_REJECT_REASONS:
            return False

        os.makedirs(os.path.dirname(self.rejected_urls_path), exist_ok=True)
        payload = {
            "id": hash_url(url),
            "url": str(url),
            "reason": reason_str,
            "rejected_at": isoformat(now_cst()),
        }
        with open(self.rejected_urls_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
            f.write("\n")
        return True

    def is_seen(self, url, seen_ids):
        return hash_url(url) in seen_ids

    def build_record(
        self,
        item,
        analysis,
        content,
        content_source="fulltext",
        fetch_status=None,
        resolved_url=None,
        related_context=None,
        analysis_with_context=False,
        normalized=None,
        credibility=None,
        analysis_en=None,
    ):
        now = now_cst()
        analysis_en = analysis_en or {}
        source_type = item.get("source_type", "")
        official_source_id = ""
        if isinstance(source_type, str) and source_type.startswith("official:"):
            official_source_id = source_type.split(":", 1)[1]
        record = {
            "id": hash_url(item.get("url", "")),
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "resolved_url": resolved_url or item.get("url", ""),
            "source_type": source_type,
            "official_source_id": official_source_id,
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
            "china_impact": analysis.get("china_impact", "中性"),
            "china_risk_trend": analysis.get("china_risk_trend", "持平"),
            "expert_summary": analysis.get("expert_summary", ""),
            "recommendations_strategy": analysis.get(
                "recommendations_strategy", []
            ),
            "recommendations_tactical": analysis.get(
                "recommendations_tactical", []
            ),
            "key_evidence": analysis.get("key_evidence", []),
            "assumptions": analysis.get("assumptions", []),
            "audience_fit": analysis.get("audience_fit", "mixed"),
            "supply_risk_score": analysis.get("supply_risk_score", 50),
            "policy_risk_score": analysis.get("policy_risk_score", 50),
            "price_pressure_score": analysis.get("price_pressure_score", 50),
            "risk_score": analysis.get("risk_score", 50),
            "forecast_6m": analysis.get("forecast_6m", {}),
            "forecast_12m": analysis.get("forecast_12m", {}),
            "forecast_24m": analysis.get("forecast_24m", {}),
            "forecast_48m": analysis.get("forecast_48m", {}),
            "summary_en": analysis_en.get("summary_en", ""),
            "impact_west": analysis_en.get("impact_west", "Neutral"),
            "risk_trend_west": analysis_en.get("risk_trend_west", "Stable"),
            "expert_summary_en": analysis_en.get("expert_summary_en", ""),
            "recommendations_strategy_en": analysis_en.get(
                "recommendations_strategy_en", []
            ),
            "recommendations_tactical_en": analysis_en.get(
                "recommendations_tactical_en", []
            ),
            "key_evidence_en": analysis_en.get("key_evidence", []),
            "assumptions_en": analysis_en.get("assumptions", []),
            "audience_fit_en": analysis_en.get("audience_fit", "mixed"),
            "supply_risk_score_en": analysis_en.get(
                "supply_risk_score_en", analysis.get("supply_risk_score", 50)
            ),
            "policy_risk_score_en": analysis_en.get(
                "policy_risk_score_en", analysis.get("policy_risk_score", 50)
            ),
            "price_pressure_score_en": analysis_en.get(
                "price_pressure_score_en", analysis.get("price_pressure_score", 50)
            ),
            "risk_score_en": analysis_en.get(
                "risk_score_en", analysis.get("risk_score", 50)
            ),
            "risk_level_en": analysis_en.get(
                "risk_level_en", analysis.get("risk_level", 3)
            ),
            "importance_score_en": analysis_en.get(
                "importance_score_en", analysis.get("importance_score", 3)
            ),
            "confidence_en": analysis_en.get(
                "confidence_en", analysis.get("confidence", 60)
            ),
            "forecast_6m_en": analysis_en.get(
                "forecast_6m_en", analysis.get("forecast_6m", {})
            ),
            "forecast_12m_en": analysis_en.get(
                "forecast_12m_en", analysis.get("forecast_12m", {})
            ),
            "forecast_24m_en": analysis_en.get(
                "forecast_24m_en", analysis.get("forecast_24m", {})
            ),
            "forecast_48m_en": analysis_en.get(
                "forecast_48m_en", analysis.get("forecast_48m", {})
            ),
            "analysis_with_context": bool(analysis_with_context),
            "related_context": related_context or [],
            "content_source": content_source,
            "fetch_status": fetch_status or "",
            "content": content,
        }
        if normalized is not None:
            record["normalized"] = normalized
        if credibility is not None:
            record["credibility"] = credibility
        return record

    def append_record(self, record):
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")

    def append_content_variant(self, payload):
        os.makedirs(os.path.dirname(self.content_variants_jsonl_path), exist_ok=True)
        with open(self.content_variants_jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
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

    def iter_records(self):
        if not os.path.exists(self.jsonl_path):
            return
        with open(self.jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                yield record

    def iter_content_variants(self):
        if not os.path.exists(self.content_variants_jsonl_path):
            return
        with open(self.content_variants_jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                yield payload

    def write_index_json(self, index_data, subdir=""):
        path = os.path.join(self._subdir_base(subdir), "index.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)

    def write_index_html(self, html_content, subdir=""):
        path = os.path.join(self._subdir_base(subdir), "index.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_content)

    def write_status_json(self, status_data):
        with open(self.status_json_path, "w", encoding="utf-8") as f:
            json.dump(status_data, f, ensure_ascii=False, indent=2)

    def write_archive_json(self, month, data, subdir=""):
        path = self.archive_json_path_for(month, subdir=subdir)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def write_archive_html(self, month, html_content, subdir=""):
        path = self.archive_html_path_for(month, subdir=subdir)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_content)

    def write_rollups_json(self, rollups, subdir=""):
        path = os.path.join(self._subdir_base(subdir), "rollups.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rollups, f, ensure_ascii=False, indent=2)
