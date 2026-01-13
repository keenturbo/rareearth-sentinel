import html
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .extractor import GeminiExtractor
from .indices.signals import build_index_watch, write_index_signals
from .utils import isoformat, now_cst, is_china_primary


class Reporter:
    def __init__(self, config):
        self.config = config
        self._daily_extractor = None

    def _safe_prune_record(self, record, lang="zh"):
        prune = getattr(self, "_prune_record", None)
        if callable(prune):
            return prune(record, lang=lang)
        return record

    def rebuild(self, storage):
        all_records = list(storage.iter_records())
        total_count = len(all_records)
        sorted_records = sorted(
            all_records, key=self._record_ts, reverse=True
        )
        recent_records = (
            sorted_records[: self.config.index_limit] if total_count else []
        )
        record_map = {r.get("id"): r for r in all_records if r.get("id")}

        daily_reports = {
            "zh": self._generate_daily_report(all_records, lang="zh"),
            "en": self._generate_daily_report(all_records, lang="en"),
        }

        premium_briefs = self._load_premium_briefs(storage)
        index_watch = self._load_index_watch()

        def build_lang(lang, subdir):
            items = [
                self._safe_prune_record(r, lang=lang)
                for r in recent_records
            ]
            stats = self._build_stats(items, total_count, lang=lang)
            rollups = self._build_rollups(all_records, lang=lang)
            archives = self._build_archives(
                storage,
                all_records,
                rollups,
                lang=lang,
                subdir=subdir,
                index_watch=index_watch,
            )

            index_data = {
                "last_update": isoformat(now_cst()),
                "total_count": total_count,
                "items": items,
                "stats": stats,
                "archives": archives,
                "rollups": rollups,
                "premium": premium_briefs.get(lang, []),
                "index_watch": index_watch,
            }
            storage.write_rollups_json(rollups, subdir=subdir)
            storage.write_index_json(index_data, subdir=subdir)
            html_content = self._build_html(
                index_data,
                lang=lang,
                daily_report=daily_reports.get(lang),
                index_watch=index_watch,
            )
            storage.write_index_html(html_content, subdir=subdir)

        # Root defaults to Chinese
        build_lang("zh", "")
        build_lang("zh", "cn")
        build_lang("en", "en")

        # Always build premium archive pages (shows empty state if no data)
        self.build_premium_archive(storage, lang="zh", subdir="cn")
        self.build_premium_archive(storage, lang="en", subdir="en")

        # Build admin pages for premium reports
        self.build_premium_admin_pages(storage, record_map, lang="zh", subdir="cn")
        self.build_premium_admin_pages(storage, record_map, lang="en", subdir="en")

        # Build pay pages for premium reports
        self.build_pay_pages(storage, record_map, lang="zh", subdir="cn")
        self.build_pay_pages(storage, record_map, lang="en", subdir="en")

    def _load_index_watch(self):
        if not self.config.index_enabled:
            return {}
        try:
            watch = build_index_watch(
                self.config.index_normalized_dir,
                daily_threshold=self.config.index_daily_threshold,
                weekly_threshold=self.config.index_weekly_threshold,
                max_items=self.config.index_watch_max,
            )
            if watch:
                write_index_signals(
                    self.config.index_normalized_dir,
                    daily_threshold=self.config.index_daily_threshold,
                    weekly_threshold=self.config.index_weekly_threshold,
                )
            return watch
        except Exception:
            return {}

    def _load_premium_briefs(self, storage):
        data = {"zh": [], "en": []}
        if not self.config.premium_enabled:
            return data
        base_dir = storage.premium_dir
        date_key = now_cst().strftime("%Y-%m-%d")
        index_path = os.path.join(base_dir, date_key, "index.json")
        if not os.path.exists(index_path):
            return data
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except (OSError, json.JSONDecodeError):
            return data
        if not isinstance(entries, list):
            return data

        for entry in entries:
            report_id = entry.get("report_id", "")
            preview_zh = entry.get("preview_zh") or {}
            preview_en = entry.get("preview_en") or {}
            if preview_zh:
                data["zh"].append(
                    {
                        "report_id": report_id,
                        "title": preview_zh.get("title", ""),
                        "value": preview_zh.get("value", ""),
                        "preview": preview_zh.get("preview", ""),
                        "price": 9,
                        "tier": "brief",
                        "unlock": self._pay_link("zh", report_id)
                        if report_id
                        else "mailto:sentinel@dataspices.com",
                    }
                )
            if preview_en:
                data["en"].append(
                    {
                        "report_id": report_id,
                        "title": preview_en.get("title", ""),
                        "value": preview_en.get("value", ""),
                        "preview": preview_en.get("preview", ""),
                        "price": 9,
                        "tier": "brief",
                        "unlock": self._pay_link("en", report_id)
                        if report_id
                        else "mailto:sentinel@dataspices.com",
                    }
                )
        return data

    def _pay_link(self, lang, report_id):
        if not report_id:
            return "mailto:sentinel@dataspices.com"
        subdir = "cn" if lang == "zh" else "en"
        return f"/{subdir}/pay/{report_id}.html"

    def _get_daily_extractor(self):
        if self._daily_extractor is None:
            self._daily_extractor = GeminiExtractor(self.config)
        return self._daily_extractor

    def _parse_date(self, text):
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

    def _record_datetime(self, record):
        published_at = record.get("published_at", "")
        collected_at = record.get("collected_at", "")
        return self._parse_date(published_at) or self._parse_date(collected_at)

    def _record_ts(self, record):
        dt = self._record_datetime(record)
        if not dt:
            return 0
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()

    def _month_key(self, dt):
        if not dt:
            return "unknown"
        return dt.strftime("%Y-%m")

    def _build_archives(
        self,
        storage,
        records,
        rollups,
        lang="zh",
        subdir="",
        index_watch=None,
    ):
        grouped = defaultdict(list)
        for record in records:
            dt = self._record_datetime(record)
            month = self._month_key(dt)
            if month == "unknown":
                continue
            grouped[month].append(record)

        months = sorted(grouped.keys(), reverse=True)
        archives = []

        for month in months:
            month_records = sorted(
                grouped[month], key=self._record_ts, reverse=True
            )
            items = [
                self._safe_prune_record(r, lang=lang)
                for r in month_records
            ]
            stats = self._build_stats(items, len(month_records), lang=lang)
            archive_data = {
                "last_update": isoformat(now_cst()),
                "total_count": len(month_records),
                "items": items,
                "stats": stats,
                "archives": [{"month": m, "count": len(grouped[m])} for m in months],
                "rollups": rollups,
                "archive_month": month,
            }
            storage.write_archive_json(month, archive_data, subdir=subdir)
            html_content = self._build_html(
                archive_data,
                lang=lang,
                archive_month=month,
                archive_prefix="../",
                daily_report=None,
                index_watch=index_watch,
            )
            storage.write_archive_html(month, html_content, subdir=subdir)
            archives.append({"month": month, "count": len(month_records)})

        return archives

    def _build_rollups(self, records, lang="zh"):
        now = now_cst()
        window_30 = now - timedelta(days=30)
        window_90 = now - timedelta(days=90)

        counts_minerals = Counter()
        counts_countries = Counter()
        counts_actions = Counter()
        monthly_counts = Counter()
        monthly_high = Counter()
        impact_counts_30 = Counter()
        impact_counts_90 = Counter()
        count_30 = 0
        count_90 = 0
        high_30 = 0
        high_90 = 0

        risk_key = "risk_level_en" if lang == "en" else "risk_level"
        impact_key = "impact_west" if lang == "en" else "china_impact"

        for record in records:
            minerals = record.get("minerals") or []
            for mineral in minerals:
                counts_minerals[str(mineral).strip()] += 1

            country = str(record.get("country_primary", "")).strip()
            if country:
                counts_countries[country] += 1

            action = str(record.get("action_type", "")).strip()
            if action:
                counts_actions[action] += 1

            dt = self._record_datetime(record)
            if dt:
                monthly_counts[self._month_key(dt)] += 1
                if int(record.get(risk_key, 0) or 0) >= 4:
                    monthly_high[self._month_key(dt)] += 1

                if dt >= window_30:
                    count_30 += 1
                    if int(record.get(risk_key, 0) or 0) >= 4:
                        high_30 += 1
                    impact = str(record.get(impact_key, "")).strip()
                    if impact:
                        impact_counts_30[impact] += 1

                if dt >= window_90:
                    count_90 += 1
                    if int(record.get(risk_key, 0) or 0) >= 4:
                        high_90 += 1
                    impact = str(record.get(impact_key, "")).strip()
                    if impact:
                        impact_counts_90[impact] += 1

        monthly = []
        for month in sorted(monthly_counts.keys(), reverse=True)[:12]:
            monthly.append(
                {
                    "month": month,
                    "count": monthly_counts[month],
                    "high_risk": monthly_high.get(month, 0),
                }
            )

        def _top(counter, limit=6):
            return [
                {"name": name, "count": count}
                for name, count in counter.most_common(limit)
                if name
            ]

        return {
            "generated_at": isoformat(now),
            "total_records": len(records),
            "windows": {
                "30d": {
                    "count": count_30,
                    "high_risk": high_30,
                    "china_impact": dict(impact_counts_30),
                },
                "90d": {
                    "count": count_90,
                    "high_risk": high_90,
                    "china_impact": dict(impact_counts_90),
                },
            },
            "top_minerals": _top(counts_minerals),
            "top_countries": _top(counts_countries),
            "top_actions": _top(counts_actions),
            "monthly": monthly,
        }

    def _build_stats(self, items, total_count, lang="zh"):
        high_risk = sum(1 for i in items if int(i.get("risk_level", 0)) >= 4)
        today_count = 0
        today = now_cst().date()
        for item in items:
            collected_at = item.get("collected_at", "")
            if collected_at:
                try:
                    dt = datetime.fromisoformat(collected_at)
                    if dt.date() == today:
                        today_count += 1
                except ValueError:
                    continue
        return {
            "total_count": total_count,
            "high_risk_count": high_risk,
            "today_count": today_count,
        }

    def _build_html(
        self,
        index_data,
        lang="zh",
        archive_month="",
        archive_prefix="",
        daily_report=None,
        index_watch=None,
    ):
        template_name = "index_en.html" if lang == "en" else "index.html"
        template_path = os.path.join(self.config.templates_dir, template_name)
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()

        items = index_data.get("items", [])
        cards_html = self._render_cards(items, lang=lang)
        stats = index_data.get("stats", {})
        daily_report_html = self._render_daily_report(daily_report, lang=lang)
        daily_brief = self._render_daily_brief(items, lang=lang)
        archive_nav = self._render_archive_nav(
            index_data.get("archives", []),
            archive_month=archive_month,
            prefix=archive_prefix,
            lang=lang,
        )
        rollups_html = self._render_rollups(index_data.get("rollups", {}), lang=lang)
        premium_html = self._render_premium(
            index_data.get("premium", []), items, lang=lang
        )
        index_watch_html = self._render_index_watch(
            index_watch or index_data.get("index_watch", {}), lang=lang
        )

        page_note = ""
        if archive_month:
            page_note = (
                f"归档月份：{archive_month}"
                if lang == "zh"
                else f"Archive: {archive_month}"
            )

        html_out = (
            template.replace("{{TOTAL_COUNT}}", str(stats.get("total_count", 0)))
            .replace("{{HIGH_RISK_COUNT}}", str(stats.get("high_risk_count", 0)))
            .replace("{{TODAY_COUNT}}", str(stats.get("today_count", 0)))
            .replace("{{LAST_UPDATE}}", index_data.get("last_update", ""))
            .replace("{{DAILY_REPORT}}", daily_report_html)
            .replace("{{DAILY_BRIEF}}", daily_brief)
            .replace("{{CARDS}}", cards_html)
            .replace("{{ARCHIVE_NAV}}", archive_nav)
            .replace("{{ROLLUPS}}", rollups_html)
            .replace("{{PAGE_NOTE}}", page_note)
            .replace("{{PREMIUM_TRACKING}}", premium_html)
            .replace("{{INDEX_WATCH}}", index_watch_html)
        )
        return html_out

    def _render_index_watch(self, watch, lang="zh"):
        title = "价格指数风向标" if lang == "zh" else "Price Index Watch"
        if not watch:
            empty = "暂无指数数据" if lang == "zh" else "No index data yet."
            return f'<div class="section-title">{title}</div><div class="meta">{empty}</div>'

        date = watch.get("date", "")
        alerts = watch.get("alerts", 0)
        daily_th = watch.get("daily_threshold", self.config.index_daily_threshold)
        weekly_th = watch.get("weekly_threshold", self.config.index_weekly_threshold)
        signals = watch.get("signals", []) or []

        if lang == "zh":
            summary = (
                f"日期 {date} · 触发预警 {alerts} · 阈值：日 {daily_th}% / 周 {weekly_th}%"
            )
            daily_label = "日变动"
            weekly_label = "周变动"
        else:
            summary = (
                f"Date {date} · Alerts {alerts} · Thresholds: Daily {daily_th}% / Weekly {weekly_th}%"
            )
            daily_label = "Daily"
            weekly_label = "Weekly"

        def _fmt_pct(val):
            if val is None:
                return "-"
            sign = "+" if val > 0 else ""
            return f"{sign}{val:.2f}%"

        cards = []
        for item in signals:
            mineral = html.escape(str(item.get("mineral", "")))
            value = item.get("value")
            unit = html.escape(str(item.get("unit", "")))
            daily_pct = _fmt_pct(item.get("daily_pct"))
            weekly_pct = _fmt_pct(item.get("weekly_pct"))
            signal = item.get("signal", "flat")
            arrow = {"up": "↑", "down": "↓", "flat": "→"}.get(signal, "→")
            alert = " alert" if item.get("alert") else ""
            value_text = f"{value:,.0f}" if isinstance(value, (int, float)) else str(value or "-")
            cards.append(
                '<div class="index-card{alert}">'
                f'<div class="index-name">{mineral}</div>'
                f'<div class="index-value">{value_text} {unit}</div>'
                f'<div class="index-change">{arrow} {daily_label} {daily_pct} · {weekly_label} {weekly_pct}</div>'
                "</div>"
            )
        cards_html = "".join(cards)
        return (
            f'<div class="section-title">{title}</div>'
            f'<div class="index-watch-meta">{summary}</div>'
            f'<div class="index-grid">{cards_html}</div>'
        )

    # Remaining methods unchanged (build_premium_archive, build_pay_pages, _render_pay_preview, _render_payment_section,
    # _daily_context_lines, _generate_daily_report, _render_daily_report, _render_premium, _render_archive_nav,
    # _render_rollups, _render_cards, _render_related_context, _render_forecast, _render_daily_brief,
    # _load_brief_json, build_premium_admin_pages, _markdown_to_html, _render_admin_url, _prune_record)
