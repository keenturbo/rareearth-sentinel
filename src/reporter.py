import html
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .extractor import GeminiExtractor
from .utils import isoformat, now_cst, is_china_primary


class Reporter:
    def __init__(self, config):
        self.config = config
        self._daily_extractor = None

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

        def build_lang(lang, subdir):
            items = [
                self._prune_record(r, lang=lang)
                for r in recent_records
            ]
            stats = self._build_stats(items, total_count, lang=lang)
            rollups = self._build_rollups(all_records, lang=lang)
            archives = self._build_archives(
                storage, all_records, rollups, lang=lang, subdir=subdir
            )

            index_data = {
                "last_update": isoformat(now_cst()),
                "total_count": total_count,
                "items": items,
                "stats": stats,
                "archives": archives,
                "rollups": rollups,
                "premium": premium_briefs.get(lang, []),
            }
            storage.write_rollups_json(rollups, subdir=subdir)
            storage.write_index_json(index_data, subdir=subdir)
            html_content = self._build_html(
                index_data,
                lang=lang,
                daily_report=daily_reports.get(lang),
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

    def _build_archives(self, storage, records, rollups, lang="zh", subdir=""):
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
                self._prune_record(r, lang=lang)
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
        )
        return html_out

    def build_premium_archive(self, storage, lang="zh", subdir=""):
        base_dir = storage.premium_dir
        index_path = os.path.join(base_dir, "reports_index.jsonl")
        entries = []
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(entry, dict):
                        entries.append(entry)

        entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
        display_entries = entries[:300]

        def render_list(items):
            if not items:
                empty = "暂无历史报告" if lang == "zh" else "No reports yet."
                return f'<div class="empty">{empty}</div>'
            blocks = []
            for item in items:
                title = html.escape(item.get("source_title", ""))
                report_id = item.get("report_id", "")
                price = item.get("price", 9)
                preview = item.get("preview_zh") if lang == "zh" else item.get("preview_en")
                value = ""
                preview_text = ""
                if isinstance(preview, dict):
                    value = preview.get("value", "")
                    preview_text = preview.get("preview", "")

                value_html = (
                    f'<div class="card-value">{html.escape(value)}</div>' if value else ""
                )
                preview_html = (
                    f'<div class="card-preview">{html.escape(preview_text)}</div>'
                    if preview_text
                    else ""
                )
                meta = item.get("created_at", "")
                btn_label = (
                    f"解锁深度报告 (${price})" if lang == "zh" else f"Unlock Report (${price})"
                )
                pay_link = self._pay_link(lang, report_id)
                blocks.append(
                    '<div class="card">'
                    f'<div class="card-title">{title}</div>'
                    f'<div class="card-meta">{html.escape(meta)} · {html.escape(report_id)}</div>'
                    f"{value_html}{preview_html}"
                    f'<a class="btn" href="{html.escape(pay_link)}">{btn_label}</a>'
                    '</div>'
                )
            return "".join(blocks)

        list_html = render_list(display_entries)
        template_name = "premium_en.html" if lang == "en" else "premium.html"
        template_path = os.path.join(self.config.templates_dir, template_name)
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()

        html_out = (
            template.replace("{{TOTAL_COUNT}}", str(len(entries)))
            .replace("{{LAST_UPDATE}}", isoformat(now_cst()))
            .replace("{{PREMIUM_LIST}}", list_html)
        )
        # write to /premium/index.html under subdir
        base = storage._subdir_base(subdir)
        premium_dir = os.path.join(base, "premium")
        os.makedirs(premium_dir, exist_ok=True)
        with open(os.path.join(premium_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(html_out)

    def build_pay_pages(self, storage, record_map, lang="zh", subdir="cn"):
        base_dir = storage.premium_dir
        index_path = os.path.join(base_dir, "reports_index.jsonl")
        entries = []
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(entry, dict):
                        entries.append(entry)

        entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
        display_entries = entries[:300]

        template_name = "pay_en.html" if lang == "en" else "pay.html"
        template_path = os.path.join(self.config.templates_dir, template_name)
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()

        pay_root = os.path.join(storage._subdir_base(subdir), "pay")
        os.makedirs(pay_root, exist_ok=True)

        for entry in display_entries:
            report_id = entry.get("report_id")
            if not report_id:
                continue
            date_key = entry.get("date", "")
            brief_json = self._load_brief_json(base_dir, date_key, report_id, "en" if lang == "en" else "zh")
            preview = entry.get("preview_zh") if lang == "zh" else entry.get("preview_en")

            title = ""
            value = ""
            if isinstance(preview, dict):
                title = preview.get("title", "")
                value = preview.get("value", "")
            if not title:
                title = entry.get("source_title", "")

            preview_html = self._render_pay_preview(brief_json, lang=lang)
            payment_html = self._render_payment_section(lang=lang)

            html_out = (
                template.replace("{{REPORT_ID}}", html.escape(report_id))
                .replace("{{REPORT_TITLE}}", html.escape(title))
                .replace("{{REPORT_VALUE}}", html.escape(value))
                .replace("{{REPORT_PREVIEW}}", preview_html)
                .replace("{{PAYMENT_SECTION}}", payment_html)
            )

            with open(os.path.join(pay_root, f"{report_id}.html"), "w", encoding="utf-8") as f:
                f.write(html_out)

    def _render_pay_preview(self, brief, lang="zh"):
        if not isinstance(brief, dict) or not brief:
            empty = "暂无预览" if lang == "zh" else "No preview yet."
            return f'<div class="meta">{empty}</div>'

        def _first(items):
            if not items:
                return "-"
            return str(items[0])

        conclusion = brief.get("conclusion", "")
        fact_first = _first(brief.get("fact_checks", []))
        number_first = _first(brief.get("key_numbers", []))

        if lang == "en":
            items = [
                ("Conclusion", conclusion or "-"),
                ("Fact check", fact_first),
                ("Key number", number_first),
            ]
        else:
            items = [
                ("结论", conclusion or "-"),
                ("事实核查", fact_first),
                ("关键数字", number_first),
            ]

        lines = []
        for label, value in items:
            lines.append(
                f'<li class="preview-item"><span class="preview-label">{html.escape(label)}</span>{html.escape(value)}</li>'
            )
        return f'<ul class="preview-list">{"".join(lines)}</ul>'

    def _render_payment_section(self, lang="zh"):
        wechat = self.config.pay_wechat_qr_url
        alipay = self.config.pay_alipay_qr_url
        token = self.config.pay_alipay_token
        usdt = self.config.pay_usdt_trc20

        if lang == "en":
            tier_title = "Pricing (only $9 open)"
            tier_brief = "$9 Brief · 24h delivery"
            tier_standard = "$99 Standard · Manual · T+1"
            tier_strategic = "$999 Strategic · Manual · T+3"
            wechat_label = "WeChat QR"
            alipay_label = "Alipay QR"
            token_label = "Alipay Token"
            usdt_label = "USDT (TRC20)"
            empty = "Not set"
        else:
            tier_title = "价格档位（仅开放 $9）"
            tier_brief = "$9 简报 · 24小时内交付"
            tier_standard = "$99 深度 · 人工定制 T+1"
            tier_strategic = "$999 战略 · 人工定制 T+3"
            wechat_label = "微信收款码"
            alipay_label = "支付宝收款码"
            token_label = "支付宝口令"
            usdt_label = "USDT (TRC20)"
            empty = "未配置"

        tiers = (
            '<div class="pay-card">'
            f'<div class="pay-title">{tier_title}</div>'
            f'<div class="meta">{tier_brief}</div>'
            f'<div class="meta">{tier_standard}</div>'
            f'<div class="meta">{tier_strategic}</div>'
            '</div>'
        )

        def _img_card(label, url):
            if not url:
                return f'<div class="pay-card"><div class="pay-title">{label}</div><div class="copy">{empty}</div></div>'
            return (
                f'<div class="pay-card"><div class="pay-title">{label}</div>'
                f'<img src="{html.escape(url)}" alt="{html.escape(label)}"></div>'
            )

        def _text_card(label, value):
            if not value:
                return f'<div class="pay-card"><div class="pay-title">{label}</div><div class="copy">{empty}</div></div>'
            return (
                f'<div class="pay-card"><div class="pay-title">{label}</div>'
                f'<div class="copy">{html.escape(value)}</div></div>'
            )

        cards = [
            tiers,
            _img_card(wechat_label, wechat),
            _img_card(alipay_label, alipay),
            _text_card(token_label, token),
            _text_card(usdt_label, usdt),
        ]
        return f'<div class="pay-grid">{"".join(cards)}</div>'

    def build_premium_admin_pages(self, storage, record_map, lang="zh", subdir="cn"):
        base_dir = storage.premium_dir
        index_path = os.path.join(base_dir, "reports_index.jsonl")
        entries = []
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(entry, dict):
                        entries.append(entry)

        entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
        display_entries = entries[:300]
        list_html = self._render_admin_list(display_entries, record_map, lang, subdir)

        template_name = "admin_premium_en.html" if lang == "en" else "admin_premium.html"
        template_path = os.path.join(self.config.templates_dir, template_name)
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()

        html_out = (
            template.replace("{{TOTAL_COUNT}}", str(len(entries)))
            .replace("{{LAST_UPDATE}}", isoformat(now_cst()))
            .replace("{{PREMIUM_LIST}}", list_html)
        )

        admin_root = os.path.join(storage._subdir_base(subdir), "admin", "premium")
        os.makedirs(admin_root, exist_ok=True)
        with open(os.path.join(admin_root, "index.html"), "w", encoding="utf-8") as f:
            f.write(html_out)

        for entry in display_entries:
            report_id = entry.get("report_id")
            if not report_id:
                continue
            detail_html = self._render_admin_detail(entry, record_map, base_dir, lang, subdir)
            if not detail_html:
                continue
            detail_path = os.path.join(admin_root, f"{report_id}.html")
            with open(detail_path, "w", encoding="utf-8") as f:
                f.write(detail_html)

    def _md_inline(self, text):
        safe = html.escape(text)
        safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
        safe = re.sub(r"`([^`]+)`", r"<code>\1</code>", safe)
        return safe

    def _markdown_to_html(self, text):
        if not text:
            return ""
        lines = text.splitlines()
        html_lines = []
        in_list = False

        for raw in lines:
            line = raw.rstrip()
            if not line.strip():
                if in_list:
                    html_lines.append("</ul>")
                    in_list = False
                continue
            if line.startswith("### "):
                if in_list:
                    html_lines.append("</ul>")
                    in_list = False
                html_lines.append(f"<h3>{self._md_inline(line[4:])}</h3>")
                continue
            if line.startswith("## "):
                if in_list:
                    html_lines.append("</ul>")
                    in_list = False
                html_lines.append(f"<h2>{self._md_inline(line[3:])}</h2>")
                continue
            if line.startswith("# "):
                if in_list:
                    html_lines.append("</ul>")
                    in_list = False
                html_lines.append(f"<h1>{self._md_inline(line[2:])}</h1>")
                continue
            if line.startswith("- "):
                if not in_list:
                    html_lines.append("<ul>")
                    in_list = True
                html_lines.append(f"<li>{self._md_inline(line[2:])}</li>")
                continue
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<p>{self._md_inline(line)}</p>")

        if in_list:
            html_lines.append("</ul>")
        return "\n".join(html_lines)

    def _load_brief_text(self, base_dir, date_key, report_id, lang):
        if not date_key or not report_id:
            return ""
        path = os.path.join(base_dir, date_key, f"brief_{report_id}_{lang}.md")
        if not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def _load_brief_json(self, base_dir, date_key, report_id, lang):
        if not date_key or not report_id:
            return {}
        path = os.path.join(base_dir, date_key, f"brief_{report_id}_{lang}.json")
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _render_admin_list(self, entries, record_map, lang, subdir):
        if not entries:
            empty = "暂无报告" if lang == "zh" else "No reports."
            return f'<div class="empty">{empty}</div>'
        blocks = []
        prefix = f"/{subdir}" if subdir else ""

        for entry in entries:
            report_id = entry.get("report_id", "")
            preview = entry.get("preview_zh") if lang == "zh" else entry.get("preview_en")
            title = entry.get("source_title", "")
            value = ""
            preview_text = ""
            if isinstance(preview, dict):
                title = preview.get("title") or title
                value = preview.get("value", "")
                preview_text = preview.get("preview", "")
            price = entry.get("price", 9)
            created_at = entry.get("created_at", "")
            link = f"{prefix}/admin/premium/{report_id}.html"
            meta = f"{created_at} · {report_id} · ${price}"

            blocks.append(
                '<div class="card">'
                f'<div class="card-title"><a href="{html.escape(link)}">{html.escape(title)}</a></div>'
                f'<div class="card-meta">{html.escape(meta)}</div>'
                f'<div class="card-value">{html.escape(value)}</div>'
                f'<div class="card-preview">{html.escape(preview_text)}</div>'
                '</div>'
            )
        return "".join(blocks)

    def _render_admin_detail(self, entry, record_map, base_dir, lang, subdir):
        report_id = entry.get("report_id", "")
        if not report_id:
            return ""
        date_key = entry.get("date", "")
        brief_md = self._load_brief_text(base_dir, date_key, report_id, lang)
        brief_json = self._load_brief_json(base_dir, date_key, report_id, lang)
        body_html = (
            self._markdown_to_html(brief_md)
            if brief_md
            else '<div class="empty">Report content missing.</div>'
        )

        record = record_map.get(entry.get("event_id", ""), {}) or {}
        source_url = record.get("url", "")
        credibility = ""
        cred = record.get("credibility", {}) or {}
        if cred:
            credibility = str(cred.get("score", ""))
        impact_score = ""
        if isinstance(brief_json, dict):
            impact_score = str(brief_json.get("impact_score", ""))

        title = entry.get("source_title", "")
        if isinstance(brief_json, dict):
            title = brief_json.get("conclusion") or title
        created_at = entry.get("created_at", "") or entry.get("date", "")
        price = entry.get("price", 9)
        prefix = f"/{subdir}" if subdir else ""
        back_link = f"{prefix}/admin/premium/"

        url_html = "-"
        if source_url:
            url_html = f'<a href="{html.escape(source_url)}" target="_blank">{html.escape(source_url)}</a>'

        template_name = "admin_premium_detail_en.html" if lang == "en" else "admin_premium_detail.html"
        template_path = os.path.join(self.config.templates_dir, template_name)
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()

        html_out = (
            template.replace("{{REPORT_TITLE}}", html.escape(title))
            .replace("{{REPORT_ID}}", html.escape(report_id))
            .replace("{{REPORT_DATE}}", html.escape(created_at))
            .replace("{{REPORT_PRICE}}", html.escape(str(price)))
            .replace("{{REPORT_URL}}", url_html)
            .replace("{{REPORT_CREDIBILITY}}", html.escape(credibility or "-"))
            .replace("{{REPORT_IMPACT}}", html.escape(impact_score or "-"))
            .replace("{{REPORT_BODY}}", body_html)
            .replace("{{BACK_LINK}}", html.escape(back_link))
        )
        return html_out

    def _render_cards(self, items, lang="zh"):
        if not items:
            return (
                '<div class="row">'
                '<div class="meta">No data</div>'
                '<div>暂无情报，请稍后再试。</div>'
                "</div>"
            )

        summary_label = "摘要" if lang == "zh" else "Summary"
        risk_label = "风险" if lang == "zh" else "Risk"

        rows = []
        for item in items:
            title = html.escape(item.get("title", ""))
            summary = html.escape(item.get("summary", ""))
            country = html.escape(item.get("country_primary", ""))
            action = html.escape(item.get("action_type", "Other"))
            risk_level = int(item.get("risk_level", 0) or 0)
            published = html.escape(item.get("published_at", ""))
            collected = html.escape(item.get("collected_at", ""))
            impact = html.escape(item.get("impact") or ("中性" if lang == "zh" else "Neutral"))
            date_label = published or collected
            meta_line = f"{country} · {action} · {risk_label} {risk_level}"
            if date_label:
                meta_line = f"{meta_line} · {date_label[:16]}"

            row_class = "row"
            if risk_level >= 4:
                row_class += " risk"
            elif risk_level == 3:
                row_class += " warn"

            tags = []
            tags.extend(item.get("minerals", []))
            tags.extend([f"@{e}" for e in item.get("entities", [])])
            if is_china_primary(item.get("country_primary", "")):
                tags.append("ChinaPrimary")
            if item.get("china_dependency") and item.get("china_dependency") != "None":
                tags.append(f"ChinaDep: {item.get('china_dependency')}")
            if item.get("china_involved"):
                tags.append(f"ChinaRole: {item.get('china_role', 'Mentioned')}")

            tags_html = "".join(
                f'<span class="tag">{html.escape(str(tag))}</span>' for tag in tags
            )

            analysis_html = self._render_expert(item, lang=lang)
            context_html = self._render_related_context(item, lang=lang)

            rows.append(
                """
<div class="{row_class}" data-impact="{impact}">
  <div class="meta">{meta_line}</div>
  <div class="title"><a href="{url}" target="_blank">{title}</a></div>
  <div class="meta">{summary_label}：{summary}</div>
  {analysis_html}
  {context_html}
  <div class="tags">{tags_html}</div>
</div>
""".format(
                    row_class=row_class,
                    meta_line=meta_line,
                    title=title,
                    url=html.escape(item.get("url", "")),
                    summary_label=summary_label,
                    summary=summary or "-",
                    analysis_html=analysis_html,
                    context_html=context_html,
                    tags_html=tags_html,
                    impact=impact,
                )
            )

        return "\n".join(rows)

    def _chip_class(self, label, mapping, default):
        return mapping.get(label, default)

    def _format_forecast(self, label, forecast):
        if not isinstance(forecast, dict):
            return ""
        trend = forecast.get("price_pressure")
        confidence = forecast.get("confidence")
        if not trend:
            return ""
        symbol = {"Up": "↑", "Down": "↓", "Neutral": "→"}.get(trend, trend)
        conf_text = f"{confidence}" if confidence not in (None, "") else "-"
        return f"{label}{symbol}({conf_text})"

    def _render_list(self, items, label):
        if not items:
            return ""
        safe_items = []
        for entry in items:
            text = str(entry).strip()
            if text:
                safe_items.append(html.escape(text))
            if len(safe_items) >= 3:
                break
        if not safe_items:
            return ""
        list_html = "".join(f"<li>{item}</li>" for item in safe_items)
        return (
            f'<div class="analysis-list"><div class="meta">{label}</div>'
            f"<ul>{list_html}</ul></div>"
        )

    def _render_expert(self, item, lang="zh"):
        impact = str(item.get("impact", "")).strip()
        risk_trend = str(item.get("risk_trend", "")).strip()
        expert_summary = html.escape(item.get("expert_summary", ""))

        supply = item.get("supply_risk_score")
        policy = item.get("policy_risk_score")
        price = item.get("price_pressure_score")
        risk_score = item.get("risk_score")

        labels = {
            "summary": "专家结论" if lang == "zh" else "Expert Insight",
            "impact": "对中国" if lang == "zh" else "Impact",
            "risk": "风险" if lang == "zh" else "Risk",
            "subscores": "分项" if lang == "zh" else "Subscores",
            "forecast": "预测" if lang == "zh" else "Forecast",
            "strategy": "战略建议" if lang == "zh" else "Strategy",
            "tactical": "操作建议" if lang == "zh" else "Tactics",
            "score": "综合风险" if lang == "zh" else "Overall Risk",
        }

        if lang == "en":
            impact_class = self._chip_class(
                impact,
                {"Positive": "good", "Neutral": "neutral", "Negative": "bad"},
                "neutral",
            )
            trend_class = self._chip_class(
                risk_trend,
                {"Intensifying": "trend-up", "Stable": "trend-flat", "Easing": "trend-down"},
                "trend-flat",
            )
        else:
            impact_class = self._chip_class(
                impact,
                {"利好": "good", "中性": "neutral", "利空": "bad"},
                "neutral",
            )
            trend_class = self._chip_class(
                risk_trend,
                {"加强": "trend-up", "持平": "trend-flat", "缓和": "trend-down"},
                "trend-flat",
            )

        forecast_items = [
            self._format_forecast("6月" if lang == "zh" else "6m", item.get("forecast_6m")),
            self._format_forecast("12月" if lang == "zh" else "12m", item.get("forecast_12m")),
            self._format_forecast("24月" if lang == "zh" else "24m", item.get("forecast_24m")),
            self._format_forecast("48月" if lang == "zh" else "48m", item.get("forecast_48m")),
        ]
        forecast_line = " · ".join([f for f in forecast_items if f])

        strategy_html = self._render_list(item.get("recommendations_strategy", []), labels["strategy"])
        tactical_html = self._render_list(item.get("recommendations_tactical", []), labels["tactical"])

        has_any = any(
            [
                impact,
                risk_trend,
                expert_summary,
                supply is not None,
                policy is not None,
                price is not None,
                risk_score is not None,
                forecast_line,
                strategy_html,
                tactical_html,
            ]
        )
        if not has_any:
            return ""

        score_line = ""
        if supply is not None or policy is not None or price is not None:
            if lang == "en":
                score_line = (
                    f"Supply {supply}/100 · Policy {policy}/100 · Price {price}/100"
                )
            else:
                score_line = (
                    f"供应链 {supply}/100 · 政策 {policy}/100 · 价格 {price}/100"
                )

        score_badge = ""
        if risk_score is not None and risk_score != "":
            score_badge = f"{labels['score']} {risk_score}/100"

        head_parts = []
        if impact:
            head_parts.append(
                f'<span class="chip {impact_class}">{labels["impact"]}：{html.escape(impact)}</span>'
            )
        if risk_trend:
            head_parts.append(
                f'<span class="chip {trend_class}">{labels["risk"]}：{html.escape(risk_trend)}</span>'
            )
        if score_badge:
            head_parts.append(f'<span class="chip score">{score_badge}</span>')

        head_html = "".join(head_parts)
        summary_html = (
            f'<div class="analysis-summary">{labels["summary"]}：{expert_summary}</div>'
            if expert_summary
            else ""
        )

        detail_blocks = []
        if score_line:
            detail_blocks.append(f'<div class="analysis-grid">{labels["subscores"]}：{score_line}</div>')
        if forecast_line:
            detail_blocks.append(
                f'<div class="analysis-grid">{labels["forecast"]}：{forecast_line}</div>'
            )
        if strategy_html:
            detail_blocks.append(strategy_html)
        if tactical_html:
            detail_blocks.append(tactical_html)

        details_html = "".join(detail_blocks)

        return (
            '<div class="analysis">'
            f'<div class="analysis-head">{head_html}</div>'
            f"{summary_html}"
            f"{details_html}"
            "</div>"
        )

    def _render_related_context(self, item, lang="zh"):
        context_lines = item.get("related_context") or []
        if not context_lines:
            return ""
        safe_lines = []
        for line in context_lines:
            text = str(line).strip()
            if not text:
                continue
            if text.startswith("- "):
                text = text[2:].strip()
            if lang == "en":
                parts = [p.strip() for p in text.split("|")]
                cleaned = []
                for part in parts:
                    if not part:
                        continue
                    if part.startswith("风险") or part.startswith("对中国"):
                        continue
                    cleaned.append(part)
                text = " | ".join(cleaned)
                if not text:
                    continue
            safe_lines.append(html.escape(text))
            if len(safe_lines) >= 6:
                break
        if not safe_lines:
            return ""

        label = "关联脉络" if lang == "zh" else "Related Context"
        list_html = "".join(f"<li>{line}</li>" for line in safe_lines)
        return (
            '<div class="analysis related">'
            f'<div class="analysis-head"><span class="chip">{label}</span></div>'
            f'<div class="analysis-list"><ul>{list_html}</ul></div>'
            "</div>"
        )

    def _append_unique(self, target, seen, value, limit):
        if len(target) >= limit:
            return
        text = str(value).strip()
        if not text:
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        target.append(text)

    def _render_brief_list(self, items, label):
        if not items:
            return (
                f'<div class="brief-col"><div class="meta">{label}</div>'
                '<div class="meta">暂无建议</div></div>'
            )
        list_html = "".join(f"<li>{html.escape(item)}</li>" for item in items)
        return (
            f'<div class="brief-col"><div class="meta">{label}</div>'
            f"<ul>{list_html}</ul></div>"
        )

    def _is_today(self, item):
        collected_at = item.get("collected_at", "")
        if not collected_at:
            return False
        try:
            dt = datetime.fromisoformat(collected_at)
        except ValueError:
            return False
        return dt.date() == now_cst().date()

    def _render_daily_brief(self, items, lang="zh"):
        strategy = []
        tactical = []
        seen_strategy = set()
        seen_tactical = set()

        if lang == "en":
            impact_counts = {"Positive": 0, "Neutral": 0, "Negative": 0}
            impact_labels = ("Positive", "Neutral", "Negative")
            counts_tpl = "Samples {sample} · Positive {pos} · Neutral {neu} · Negative {neg}"
            strategy_label = "Strategy Recommendations"
            tactical_label = "Tactical Recommendations"
        else:
            impact_counts = {"利好": 0, "中性": 0, "利空": 0}
            impact_labels = ("利好", "中性", "利空")
            counts_tpl = "样本 {sample} · 利好 {pos} · 中性 {neu} · 利空 {neg}"
            strategy_label = "战略建议"
            tactical_label = "操作建议"

        sample_count = 0

        for item in items:
            if not self._is_today(item):
                continue
            sample_count += 1
            impact = str(item.get("impact", "")).strip()
            if impact in impact_counts:
                impact_counts[impact] += 1

            for rec in item.get("recommendations_strategy") or []:
                self._append_unique(strategy, seen_strategy, rec, 5)
            for rec in item.get("recommendations_tactical") or []:
                self._append_unique(tactical, seen_tactical, rec, 5)

        if sample_count == 0:
            return (
                '<div class="brief-empty">今日暂无摘要</div>'
                if lang == "zh"
                else '<div class="brief-empty">No brief for today.</div>'
            )

        counts_line = counts_tpl.format(
            sample=sample_count,
            pos=impact_counts[impact_labels[0]],
            neu=impact_counts[impact_labels[1]],
            neg=impact_counts[impact_labels[2]],
        )
        strategy_html = self._render_brief_list(strategy, strategy_label)
        tactical_html = self._render_brief_list(tactical, tactical_label)

        return (
            f'<div class="brief-row">{counts_line}</div>'
            f'<div class="brief-grid">{strategy_html}{tactical_html}</div>'
        )

    def _daily_context_lines(self, records, lang="zh", limit=40):
        lines = []

        def _risk(record):
            key = "risk_score_en" if lang == "en" else "risk_score"
            try:
                return float(record.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        today_records = [r for r in records if self._is_today(r)]
        today_records = sorted(today_records, key=_risk, reverse=True)

        for record in today_records:
            title = str(record.get("title", "")).strip()
            if not title:
                continue
            country = str(record.get("country_primary", "")).strip()
            action = str(record.get("action_type", "")).strip()
            impact_key = "impact_west" if lang == "en" else "china_impact"
            impact = str(record.get(impact_key, "")).strip()
            summary_key = "summary_en" if lang == "en" else "summary_zh"
            summary = str(record.get(summary_key, "")).strip()
            risk = _risk(record)
            line = f"- {country} | {action} | {impact} | risk {int(risk)} | {title}"
            if summary:
                line += f" | {summary[:140]}"
            lines.append(line)
            if len(lines) >= limit:
                break

        return lines

    def _fallback_daily_report(self, records, lang="zh"):
        impact_counts = Counter()
        minerals = Counter()
        countries = Counter()
        actions = Counter()
        risk_scores = []

        for record in records:
            if not self._is_today(record):
                continue
            impact_key = "impact_west" if lang == "en" else "china_impact"
            impact = str(record.get(impact_key, "")).strip()
            if impact:
                impact_counts[impact] += 1
            for mineral in record.get("minerals") or []:
                minerals[str(mineral).strip()] += 1
            country = str(record.get("country_primary", "")).strip()
            if country:
                countries[country] += 1
            action = str(record.get("action_type", "")).strip()
            if action:
                actions[action] += 1
            key = "risk_score_en" if lang == "en" else "risk_score"
            try:
                score = float(record.get(key) or 0)
            except (TypeError, ValueError):
                score = 0
            if score:
                risk_scores.append(score)

        dominant = impact_counts.most_common(1)[0][0] if impact_counts else "Neutral"
        avg_risk = round(sum(risk_scores) / len(risk_scores)) if risk_scores else 0
        top_mineral = minerals.most_common(1)[0][0] if minerals else "-"
        top_country = countries.most_common(1)[0][0] if countries else "-"
        top_action = actions.most_common(1)[0][0] if actions else "-"

        if lang == "en":
            headline = "Daily Intelligence Report"
            body = (
                f"Overall tone is {dominant.lower()} across today's signals. "
                f"Average composite risk is {avg_risk}/100. "
                f"Attention centers on {top_country} and {top_mineral} with actions in {top_action}."
            )
            bullets = [
                f"Highest attention country: {top_country}",
                f"Key mineral focus: {top_mineral}",
                f"Dominant action type: {top_action}",
            ]
        else:
            headline = "每日情报综合报告"
            body = (
                f"今日整体偏{dominant}，综合风险均值为{avg_risk}/100。"
                f"关注重点集中在{top_country}与{top_mineral}，主要动作类型为{top_action}。"
            )
            bullets = [
                f"高关注国家：{top_country}",
                f"关键矿种：{top_mineral}",
                f"主要动作：{top_action}",
            ]

        return {
            "headline": headline,
            "body": body,
            "bullets": bullets,
        }

    def _generate_daily_report(self, records, lang="zh"):
        today_records = [r for r in records if self._is_today(r)]
        if not today_records:
            return None

        lines = self._daily_context_lines(records, lang=lang, limit=40)
        if not lines:
            return None

        context_text = "\n".join(lines)
        extractor = self._get_daily_extractor()
        report = extractor.analyze_daily_report(context_text, lang=lang)
        if report is None:
            return self._fallback_daily_report(records, lang=lang)

        if not report.get("headline"):
            report["headline"] = (
                "Daily Intelligence Report" if lang == "en" else "每日情报综合报告"
            )
        return report

    def _render_daily_report(self, daily_report, lang="zh"):
        title = "每日情报综合报告" if lang == "zh" else "Daily Intelligence Report"
        if not daily_report:
            empty = "今日暂无综合报告" if lang == "zh" else "No report for today."
            return (
                f'<div class="daily-title">{title}</div>'
                f'<div class="daily-empty">{empty}</div>'
            )

        headline = html.escape(daily_report.get("headline", "").strip())
        body = html.escape(daily_report.get("body", "").strip())
        bullets = daily_report.get("bullets") or []

        headline_html = (
            f'<div class="daily-headline">{headline}</div>' if headline else ""
        )
        body_html = f'<div class="daily-body">{body}</div>' if body else ""

        bullet_items = []
        for bullet in bullets[:3]:
            text = str(bullet).strip()
            if text:
                bullet_items.append(f"<li>{html.escape(text)}</li>")
        bullets_html = (
            f'<ul class="daily-bullets">{"".join(bullet_items)}</ul>'
            if bullet_items
            else ""
        )

        return (
            f'<div class="daily-title">{title}</div>'
            f"{headline_html}"
            f"{body_html}"
            f"{bullets_html}"
        )

    def _render_premium(self, premium_items, items, lang="zh"):
        if premium_items:
            blocks = []
            reason_label = "价值" if lang == "zh" else "Why it matters"
            preview_label = "报告包含" if lang == "zh" else "Includes"
            for item in premium_items[:3]:
                title = html.escape(item.get("title", ""))
                value = html.escape(item.get("value", ""))
                preview = html.escape(item.get("preview", ""))
                price = item.get("price", 9)
                unlock = item.get("unlock") or "mailto:sentinel@dataspices.com"
                btn_label = (
                    f"解锁深度报告 (${price})" if lang == "zh" else f"Unlock Deep Report (${price})"
                )
                preview_html = (
                    f'<div class="premium-preview">{preview_label}：{preview}</div>'
                    if preview
                    else ""
                )
                block = (
                    '<div class="premium-item">'
                    f'<div class="premium-topic">{title}</div>'
                    f'<div class="premium-reason">{reason_label}：{value}</div>'
                    f"{preview_html}"
                    f'<a class="premium-btn" href="{html.escape(unlock)}">{btn_label}</a>'
                    "</div>"
                )
                blocks.append(block)
            return "".join(blocks)

        today_items = [item for item in items if self._is_today(item)]
        if not today_items:
            return (
                '<div class="meta">暂无重点追踪</div>'
                if lang == "zh"
                else '<div class="meta">No premium leads today.</div>'
            )

        def score(item):
            return int(item.get("risk_score", 0) or 0)

        top_items = sorted(today_items, key=score, reverse=True)[:3]
        blocks = []
        for item in top_items:
            title = html.escape(item.get("title", ""))
            reason = html.escape(item.get("expert_summary", "") or item.get("summary", ""))
            reason_label = "价值" if lang == "zh" else "Why it matters"
            btn_label = "解锁深度报告" if lang == "zh" else "Unlock Deep Report"
            block = (
                '<div class="premium-item">'
                f'<div class="premium-topic">{title}</div>'
                f'<div class="premium-reason">{reason_label}：{reason}</div>'
                f'<a class="premium-btn" href="mailto:sentinel@dataspices.com">{btn_label}</a>'
                '</div>'
            )
            blocks.append(block)
        return "".join(blocks)

    def _render_archive_nav(self, archives, archive_month="", prefix="", lang="zh"):
        if not archives:
            return ""
        show_limit = 7
        links = []
        hidden = 0

        for idx, entry in enumerate(archives):
            month = entry.get("month")
            count = entry.get("count", 0)
            if not month:
                continue
            active = " active" if month == archive_month else ""
            is_extra = idx >= show_limit and not active
            extra_class = " archive-extra" if is_extra else ""
            if is_extra:
                hidden += 1
            href = f"{prefix}archive/{month}.html"
            links.append(
                f'<a class="archive-link{active}{extra_class}" href="{href}">{month} ({count})</a>'
            )

        toggle_html = ""
        if hidden > 0:
            show_label = "展开更多" if lang == "zh" else "Show more"
            hide_label = "收起" if lang == "zh" else "Show less"
            toggle_html = (
                f'<button class="archive-toggle" data-show="{show_label}" '
                f'data-hide="{hide_label}" data-expanded="false">{show_label}</button>'
            )

        return "".join(links) + toggle_html

    def _render_rollups(self, rollups, lang="zh"):
        if not rollups:
            return '<div class="rollups-empty">暂无汇总</div>'

        windows = rollups.get("windows", {})
        window_30 = windows.get("30d", {})
        window_90 = windows.get("90d", {})

        def _top_chips(entries):
            chips = []
            for entry in entries:
                name = entry.get("name")
                count = entry.get("count")
                if not name:
                    continue
                chips.append(f'<span class="rollup-chip">{html.escape(name)} {count}</span>')
            return "".join(chips)

        top_minerals = _top_chips(rollups.get("top_minerals", []))
        top_countries = _top_chips(rollups.get("top_countries", []))
        top_actions = _top_chips(rollups.get("top_actions", []))

        if lang == "en":
            summary_line = (
                f"Last 30d {window_30.get('count', 0)} · High Risk {window_30.get('high_risk', 0)}"
                f" ｜ Last 90d {window_90.get('count', 0)} · High Risk {window_90.get('high_risk', 0)}"
            )
            mineral_label = "Minerals"
            country_label = "Countries"
            action_label = "Actions"
            fallback = '<div class="meta">None</div>'
        else:
            summary_line = (
                f"近30天 {window_30.get('count', 0)} 条 · 高风险 {window_30.get('high_risk', 0)}"
                f" ｜ 近90天 {window_90.get('count', 0)} 条 · 高风险 {window_90.get('high_risk', 0)}"
            )
            mineral_label = "矿种聚合"
            country_label = "国家聚合"
            action_label = "动作聚合"
            fallback = '<div class="meta">暂无</div>'

        return (
            f'<div class="rollups-row">{summary_line}</div>'
            f'<div class="rollups-grid">'
            f'<div class="rollup-col"><div class="meta">{mineral_label}</div>{top_minerals or fallback}</div>'
            f'<div class="rollup-col"><div class="meta">{country_label}</div>{top_countries or fallback}</div>'
            f'<div class="rollup-col"><div class="meta">{action_label}</div>{top_actions or fallback}</div>'
            f'</div>'
        )

    def _prune_record(self, record, lang="zh"):
        if lang == "en":
            summary = record.get("summary_en") or record.get("summary_zh", "")
            impact = record.get("impact_west", "Neutral")
            risk_trend = record.get("risk_trend_west", "Stable")
            expert_summary = record.get("expert_summary_en") or ""
            rec_strategy = record.get("recommendations_strategy_en", [])
            rec_tactical = record.get("recommendations_tactical_en", [])
            supply = record.get("supply_risk_score_en", record.get("supply_risk_score"))
            policy = record.get("policy_risk_score_en", record.get("policy_risk_score"))
            price = record.get("price_pressure_score_en", record.get("price_pressure_score"))
            risk_score = record.get("risk_score_en", record.get("risk_score"))
            risk_level = record.get("risk_level_en", record.get("risk_level"))
            forecast_6m = record.get("forecast_6m_en") or record.get("forecast_6m", {})
            forecast_12m = record.get("forecast_12m_en") or record.get("forecast_12m", {})
            forecast_24m = record.get("forecast_24m_en") or record.get("forecast_24m", {})
            forecast_48m = record.get("forecast_48m_en") or record.get("forecast_48m", {})
        else:
            summary = record.get("summary_zh", "")
            impact = record.get("china_impact", "中性")
            risk_trend = record.get("china_risk_trend", "持平")
            expert_summary = record.get("expert_summary", "")
            rec_strategy = record.get("recommendations_strategy", [])
            rec_tactical = record.get("recommendations_tactical", [])
            supply = record.get("supply_risk_score")
            policy = record.get("policy_risk_score")
            price = record.get("price_pressure_score")
            risk_score = record.get("risk_score")
            risk_level = record.get("risk_level")
            forecast_6m = record.get("forecast_6m", {})
            forecast_12m = record.get("forecast_12m", {})
            forecast_24m = record.get("forecast_24m", {})
            forecast_48m = record.get("forecast_48m", {})

        fields = {
            "id": record.get("id"),
            "title": record.get("title", ""),
            "url": record.get("url", ""),
            "source_type": record.get("source_type", ""),
            "published_at": record.get("published_at", ""),
            "collected_at": record.get("collected_at", ""),
            "language": record.get("language", ""),
            "summary": summary,
            "country_primary": record.get("country_primary", ""),
            "event_location": record.get("event_location", ""),
            "minerals": record.get("minerals", []),
            "entities": record.get("entities", []),
            "action_type": record.get("action_type", "Other"),
            "risk_level": risk_level or 0,
            "importance_score": record.get("importance_score", 0),
            "china_involved": record.get("china_involved", False),
            "china_dependency": record.get("china_dependency", "None"),
            "china_role": record.get("china_role", "Mentioned"),
            "confidence": record.get("confidence", 60),
            "impact": impact,
            "risk_trend": risk_trend,
            "expert_summary": expert_summary,
            "recommendations_strategy": rec_strategy,
            "recommendations_tactical": rec_tactical,
            "supply_risk_score": supply,
            "policy_risk_score": policy,
            "price_pressure_score": price,
            "risk_score": risk_score,
            "forecast_6m": forecast_6m,
            "forecast_12m": forecast_12m,
            "forecast_24m": forecast_24m,
            "forecast_48m": forecast_48m,
            "related_context": record.get("related_context", []),
            "analysis_with_context": record.get("analysis_with_context"),
        }
        return fields
