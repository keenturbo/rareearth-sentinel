import html
import json
import os
import re

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


def _load_json_dict(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}



def _clip_text(text, limit):
    raw = str(text or "").strip()
    if not raw or len(raw) <= limit:
        return raw
    return raw[:limit].rstrip("，。;；, .") + "…"



def _extract_simple_line(item, lang="zh"):
    if lang == "en":
        candidates = [
            item.get("value_en"),
            item.get("report_contains_en"),
            item.get("summary_en"),
            item.get("expert_summary_en"),
            item.get("title_en"),
            item.get("title"),
        ]
        limit = 160
    else:
        candidates = [
            item.get("value_zh"),
            item.get("report_contains_zh"),
            item.get("summary_zh"),
            item.get("expert_summary"),
            item.get("title_zh"),
            item.get("title"),
        ]
        limit = 64

    for value in candidates:
        text = str(value or "").strip()
        if not text:
            continue
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"^[•\-*\u2022]+\s*", "", text)
        return _clip_text(text, limit)
    return ""



def _pick_item_link(item, lang="zh"):
    explicit = str(item.get("link_zh") if lang == "zh" else item.get("link_en") or "").strip()
    if explicit:
        return explicit
    report_id = str(item.get("report_id") or "").strip()
    if report_id:
        subdir = "cn" if lang == "zh" else "en"
        return f"/{subdir}/pay/{report_id}.html"
    subdir = "cn" if lang == "zh" else "en"
    return f"/{subdir}/news/"



def _build_simple_group_html(items, lang="zh"):
    items = [item for item in (items or []) if isinstance(item, dict)]
    if not items:
        return '<div class="meta">暂无重点追踪</div>' if lang == "zh" else '<div class="meta">No top track yet.</div>'

    if lang == "en":
        core_label = "Core event"
        signal_labels = ["Signal 1", "Signal 2", "Signal 3"]
        button_label = "View related signal"
        title = str(items[0].get("title_en") or items[0].get("title") or "").strip()
    else:
        core_label = "核心事件"
        signal_labels = ["关键信号一", "关键信号二", "关键信号三"]
        button_label = "查看相关情报"
        title = str(items[0].get("title_zh") or items[0].get("title") or "").strip()

    lines = []
    if title:
        lines.append(
            f'<div class="premium-reason"><span class="label-gray">{html.escape(core_label)}</span>{("：" if lang == "zh" else ": ")}{html.escape(_clip_text(title, 72 if lang == "zh" else 180))}</div>'
        )

    for idx, item in enumerate(items[:3]):
        line = _extract_simple_line(item, lang=lang)
        if not line:
            continue
        label = signal_labels[idx] if idx < len(signal_labels) else signal_labels[-1]
        sep = "：" if lang == "zh" else ": "
        lines.append(
            f'<div class="premium-reason"><span class="label-gray">{html.escape(label)}</span>{sep}{html.escape(line)}</div>'
        )

    if not lines:
        return '<div class="meta">暂无重点追踪</div>' if lang == "zh" else '<div class="meta">No top track yet.</div>'

    link = _pick_item_link(items[0], lang=lang)
    return (
        '<div class="premium-item premium-aggregate">'
        + "".join(lines)
        + f'<a class="premium-btn" href="{html.escape(link)}">{html.escape(button_label)}</a>'
        + '</div>'
    )



def _replace_top_tracks_section(raw_html, title_candidates, new_title, aggregate_html):
    for title in title_candidates:
        pattern = re.compile(
            r'(<section class="section">\s*<div class="section-title">)'
            + re.escape(title)
            + r'(</div>)(.*?)(</section>)',
            re.S,
        )
        if not pattern.search(raw_html):
            continue
        replaced = pattern.sub(
            lambda m: f"{m.group(1)}{new_title}{m.group(2)}\n    {aggregate_html}\n  {m.group(4)}",
            raw_html,
            count=1,
        )
        return replaced, True
    return raw_html, False



def _rewrite_homepage_top_tracks_if_needed(config, payload):
    source = str((payload or {}).get("source") or "").strip()
    if source == "premium_today":
        return
    items = (payload or {}).get("items") or []
    if not items:
        return

    aggregate_cn = _build_simple_group_html(items, lang="zh")
    aggregate_en = _build_simple_group_html(items, lang="en")

    targets = [
        (
            os.path.join(config.static_dir, "index.html"),
            ["今日重点追踪（Top 3）", "今日重点专题"],
            "今日重点专题",
            aggregate_cn,
        ),
        (
            os.path.join(config.static_dir, "cn", "index.html"),
            ["今日重点追踪（Top 3）", "今日重点专题"],
            "今日重点专题",
            aggregate_cn,
        ),
        (
            os.path.join(config.static_dir, "en", "index.html"),
            ["Top Tracks (Top 3)", "Today's Focus"],
            "Today's Focus",
            aggregate_en,
        ),
    ]

    for path, title_candidates, new_title, aggregate_html in targets:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw_html = f.read()
        except OSError:
            continue
        updated_html, replaced = _replace_top_tracks_section(raw_html, title_candidates, new_title, aggregate_html)
        if not replaced:
            continue
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(updated_html)
        except OSError:
            continue



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
            _rewrite_homepage_top_tracks_if_needed(self.config, payload)
            health = build_pipeline_health(
                self.config,
                stats={},
                records=records,
                top3_payload=payload,
                error="",
            )
            write_pipeline_health(self.config, health)
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
