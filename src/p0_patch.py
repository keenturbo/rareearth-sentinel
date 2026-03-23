import requests

from .fetcher import Fetcher
from .reporter import Reporter
from .storage import Storage
from . import telegram as telegram_mod
from .pipeline_health import build_pipeline_health, write_pipeline_health
from .top3_builder import (
    build_top3_payload,
    load_top3_build_status,
    write_top3_build_status,
    write_top3_list,
)


def _patch_reporter():
    if getattr(Reporter, "_p0_top3_patch", False):
        return
    original_rebuild = Reporter.rebuild

    def rebuild_with_top3(self, storage):
        result = original_rebuild(self, storage)
        try:
            records = list(storage.iter_records())
            payload = build_top3_payload(self.config, records)
            write_top3_list(self.config, payload)
            write_top3_build_status(self.config, payload)
        except Exception as exc:
            print(f"[p0-top3] rebuild patch error: {exc}")
        return result

    Reporter.rebuild = rebuild_with_top3
    Reporter._p0_top3_patch = True



def _patch_storage():
    if getattr(Storage, "_p0_health_patch", False):
        return
    original_write_status_json = Storage.write_status_json

    def write_status_json_with_health(self, status_data):
        original_write_status_json(self, status_data)
        if not isinstance(status_data, dict):
            return
        if status_data.get("running"):
            return
        try:
            records = list(self.iter_records())
            top3_payload = load_top3_build_status(self.config)
            health = build_pipeline_health(
                self.config,
                stats=status_data.get("stats") or {},
                records=records,
                top3_payload=top3_payload,
                error=status_data.get("error") or "",
            )
            write_pipeline_health(self.config, health)
        except Exception as exc:
            print(f"[p0-health] status patch error: {exc}")

    Storage.write_status_json = write_status_json_with_health
    Storage._p0_health_patch = True



def _patch_telegram():
    if getattr(telegram_mod, "_p0_top3_message_patch", False):
        return

    def build_top3_message_p0(item, base_url, lang="zh"):
        report_id = str(item.get("report_id", "")).strip()
        if lang == "en":
            title = str(item.get("title_en", "")).strip() or str(item.get("title", "")).strip()
            value = item.get("value_en") or ""
            report_contains = item.get("report_contains_en") or ""
            explicit_link = str(item.get("link_en") or item.get("link") or "").strip()
            default_link = f"{base_url.rstrip('/')}/en/pay/{report_id}.html" if report_id else ""
        else:
            title = str(item.get("title_zh") or item.get("title", "")).strip()
            value = item.get("value_zh") or ""
            report_contains = item.get("report_contains_zh") or ""
            explicit_link = str(item.get("link_zh") or item.get("link") or "").strip()
            default_link = f"{base_url.rstrip('/')}/cn/pay/{report_id}.html" if report_id else ""

        if not title:
            return ""
        if not value and not report_contains:
            return ""

        if explicit_link:
            if explicit_link.startswith("http://") or explicit_link.startswith("https://"):
                link = explicit_link
            else:
                link = f"{base_url.rstrip('/')}{explicit_link}"
        else:
            link = default_link

        value = telegram_mod._clip_text(str(value).strip(), 160)
        report_contains = telegram_mod._clip_text(str(report_contains).strip(), 300)

        parts = [telegram_mod._escape_markdown(title)]
        if value:
            label = "价值：" if lang == "zh" else "Value: "
            parts.append(telegram_mod._escape_markdown(f"{label}{value}"))
        if report_contains:
            label = "报告包含：" if lang == "zh" else "Report includes: "
            parts.append(telegram_mod._escape_markdown(f"{label}{report_contains}"))
        if link:
            parts.append(link)
        return "\n".join([part for part in parts if part])

    telegram_mod._build_top3_message = build_top3_message_p0
    telegram_mod._p0_top3_message_patch = True



def apply_p0_patches():
    _patch_reporter()
    _patch_storage()
    _patch_telegram()


apply_p0_patches()
