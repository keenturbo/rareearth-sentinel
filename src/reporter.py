import html
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

from .utils import isoformat, now_cst, is_china_primary


class Reporter:
    def __init__(self, config):
        self.config = config

    def rebuild(self, storage):
        all_records = list(storage.iter_records())
        total_count = len(all_records)
        recent_records = (
            all_records[-self.config.index_limit :] if total_count else []
        )
        items = [self._prune_record(r) for r in reversed(recent_records)]
        stats = self._build_stats(items, total_count)
        rollups = self._build_rollups(all_records)
        archives = self._build_archives(storage, all_records, rollups)

        index_data = {
            "last_update": isoformat(now_cst()),
            "total_count": total_count,
            "items": items,
            "stats": stats,
            "archives": archives,
            "rollups": rollups,
        }
        storage.write_rollups_json(rollups)
        storage.write_index_json(index_data)
        html_content = self._build_html(index_data)
        storage.write_index_html(html_content)

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

    def _month_key(self, dt):
        if not dt:
            return "unknown"
        return dt.strftime("%Y-%m")

    def _build_archives(self, storage, records, rollups):
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
            month_records = grouped[month]
            items = [self._prune_record(r) for r in reversed(month_records)]
            stats = self._build_stats(items, len(month_records))
            archive_data = {
                "last_update": isoformat(now_cst()),
                "total_count": len(month_records),
                "items": items,
                "stats": stats,
                "archives": [{"month": m, "count": len(grouped[m])} for m in months],
                "rollups": rollups,
                "archive_month": month,
            }
            storage.write_archive_json(month, archive_data)
            html_content = self._build_html(
                archive_data,
                archive_month=month,
                archive_prefix="../",
            )
            storage.write_archive_html(month, html_content)
            archives.append({"month": month, "count": len(month_records)})

        return archives

    def _build_rollups(self, records):
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
                if int(record.get("risk_level", 0) or 0) >= 4:
                    monthly_high[self._month_key(dt)] += 1

                if dt >= window_30:
                    count_30 += 1
                    if int(record.get("risk_level", 0) or 0) >= 4:
                        high_30 += 1
                    impact = str(record.get("china_impact", "")).strip()
                    if impact:
                        impact_counts_30[impact] += 1

                if dt >= window_90:
                    count_90 += 1
                    if int(record.get("risk_level", 0) or 0) >= 4:
                        high_90 += 1
                    impact = str(record.get("china_impact", "")).strip()
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

    def _build_stats(self, items, total_count):
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

    def _build_html(self, index_data, archive_month="", archive_prefix=""):
        template_path = os.path.join(self.config.templates_dir, "index.html")
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()

        items = index_data.get("items", [])
        cards_html = self._render_cards(items)
        stats = index_data.get("stats", {})
        daily_brief = self._render_daily_brief(items)
        archive_nav = self._render_archive_nav(
            index_data.get("archives", []),
            archive_month=archive_month,
            prefix=archive_prefix,
        )
        rollups_html = self._render_rollups(index_data.get("rollups", {}))

        page_note = ""
        if archive_month:
            page_note = f"归档月份：{archive_month}"

        html_out = (
            template.replace("{{TOTAL_COUNT}}", str(stats.get("total_count", 0)))
            .replace("{{HIGH_RISK_COUNT}}", str(stats.get("high_risk_count", 0)))
            .replace("{{TODAY_COUNT}}", str(stats.get("today_count", 0)))
            .replace("{{LAST_UPDATE}}", index_data.get("last_update", ""))
            .replace("{{DAILY_BRIEF}}", daily_brief)
            .replace("{{CARDS}}", cards_html)
            .replace("{{ARCHIVE_NAV}}", archive_nav)
            .replace("{{ROLLUPS}}", rollups_html)
            .replace("{{PAGE_NOTE}}", page_note)
        )
        return html_out

    def _render_cards(self, items):
        if not items:
            return (
                '<div class="row">'
                '<div class="meta">No data</div>'
                '<div>暂无情报，请稍后再试。</div>'
                "</div>"
            )

        rows = []
        for item in items:
            title = html.escape(item.get("title", ""))
            summary = html.escape(item.get("summary_zh", ""))
            country = html.escape(item.get("country_primary", ""))
            action = html.escape(item.get("action_type", "Other"))
            risk_level = int(item.get("risk_level", 0) or 0)
            published = html.escape(item.get("published_at", ""))
            collected = html.escape(item.get("collected_at", ""))
            impact = html.escape(item.get("china_impact") or "未知")
            date_label = published or collected
            meta_line = f"{country} · {action} · 风险 {risk_level}"
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

            analysis_html = self._render_expert(item)

            rows.append(
                """
<div class="{row_class}" data-impact="{impact}">
  <div class="meta">{meta_line}</div>
  <div class="title"><a href="{url}" target="_blank">{title}</a></div>
  <div class="meta">摘要：{summary}</div>
  {analysis_html}
  <div class="tags">{tags_html}</div>
</div>
""".format(
                    row_class=row_class,
                    meta_line=meta_line,
                    title=title,
                    url=html.escape(item.get("url", "")),
                    summary=summary or "-",
                    analysis_html=analysis_html,
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

    def _render_expert(self, item):
        china_impact = str(item.get("china_impact", "")).strip()
        china_risk_trend = str(item.get("china_risk_trend", "")).strip()
        expert_summary = html.escape(item.get("expert_summary", ""))

        supply = item.get("supply_risk_score")
        policy = item.get("policy_risk_score")
        price = item.get("price_pressure_score")
        risk_score = item.get("risk_score")

        forecast_items = [
            self._format_forecast("6月", item.get("forecast_6m")),
            self._format_forecast("12月", item.get("forecast_12m")),
            self._format_forecast("24月", item.get("forecast_24m")),
            self._format_forecast("48月", item.get("forecast_48m")),
        ]
        forecast_line = " · ".join([f for f in forecast_items if f])

        strategy_html = self._render_list(item.get("recommendations_strategy", []), "战略建议")
        tactical_html = self._render_list(item.get("recommendations_tactical", []), "操作建议")

        impact_class = self._chip_class(
            china_impact,
            {"利好": "good", "中性": "neutral", "利空": "bad"},
            "neutral",
        )
        trend_class = self._chip_class(
            china_risk_trend,
            {"加强": "trend-up", "持平": "trend-flat", "缓和": "trend-down"},
            "trend-flat",
        )

        has_any = any(
            [
                china_impact,
                china_risk_trend,
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
            score_line = (
                f"供应链 {supply}/100 · 政策 {policy}/100 · 价格 {price}/100"
            )

        score_badge = ""
        if risk_score is not None and risk_score != "":
            score_badge = f"综合风险 {risk_score}/100"

        head_parts = []
        if china_impact:
            head_parts.append(
                f'<span class="chip {impact_class}">对中国：{html.escape(china_impact)}</span>'
            )
        if china_risk_trend:
            head_parts.append(
                f'<span class="chip {trend_class}">风险：{html.escape(china_risk_trend)}</span>'
            )
        if score_badge:
            head_parts.append(f'<span class="chip score">{score_badge}</span>')

        head_html = "".join(head_parts)
        summary_html = (
            f'<div class="analysis-summary">专家结论：{expert_summary}</div>'
            if expert_summary
            else ""
        )

        detail_blocks = []
        if score_line:
            detail_blocks.append(f'<div class="analysis-grid">分项：{score_line}</div>')
        if forecast_line:
            detail_blocks.append(
                f'<div class="analysis-grid">预测：{forecast_line}</div>'
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

    def _render_daily_brief(self, items):
        strategy = []
        tactical = []
        seen_strategy = set()
        seen_tactical = set()
        impact_counts = {"利好": 0, "中性": 0, "利空": 0}
        sample_count = 0

        for item in items:
            if not self._is_today(item):
                continue
            sample_count += 1
            impact = str(item.get("china_impact", "")).strip()
            if impact in impact_counts:
                impact_counts[impact] += 1

            for rec in item.get("recommendations_strategy") or []:
                self._append_unique(strategy, seen_strategy, rec, 5)
            for rec in item.get("recommendations_tactical") or []:
                self._append_unique(tactical, seen_tactical, rec, 5)

        if sample_count == 0:
            return '<div class="brief-empty">今日暂无摘要</div>'

        counts_line = (
            f"样本 {sample_count} · 利好 {impact_counts['利好']} · "
            f"中性 {impact_counts['中性']} · 利空 {impact_counts['利空']}"
        )
        strategy_html = self._render_brief_list(strategy, "战略建议")
        tactical_html = self._render_brief_list(tactical, "操作建议")

        return (
            f'<div class="brief-row">{counts_line}</div>'
            f'<div class="brief-grid">{strategy_html}{tactical_html}</div>'
        )

    def _render_archive_nav(self, archives, archive_month="", prefix=""):
        if not archives:
            return ""
        links = []
        for entry in archives:
            month = entry.get("month")
            count = entry.get("count", 0)
            if not month:
                continue
            active = " active" if month == archive_month else ""
            href = f"{prefix}archive/{month}.html"
            links.append(
                f'<a class="archive-link{active}" href="{href}">{month} ({count})</a>'
            )
        return "".join(links)

    def _render_rollups(self, rollups):
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

        summary_line = (
            f"近30天 {window_30.get('count', 0)} 条 · 高风险 {window_30.get('high_risk', 0)}"
            f" ｜ 近90天 {window_90.get('count', 0)} 条 · 高风险 {window_90.get('high_risk', 0)}"
        )

        return (
            f'<div class="rollups-row">{summary_line}</div>'
            f'<div class="rollups-grid">'
            f'<div class="rollup-col"><div class="meta">矿种聚合</div>{top_minerals or "<div class=\"meta\">暂无</div>"}</div>'
            f'<div class="rollup-col"><div class="meta">国家聚合</div>{top_countries or "<div class=\"meta\">暂无</div>"}</div>'
            f'<div class="rollup-col"><div class="meta">动作聚合</div>{top_actions or "<div class=\"meta\">暂无</div>"}</div>'
            f'</div>'
        )

    def _prune_record(self, record):
        fields = [
            "id",
            "title",
            "url",
            "source_type",
            "published_at",
            "collected_at",
            "language",
            "summary_zh",
            "country_primary",
            "event_location",
            "minerals",
            "entities",
            "action_type",
            "risk_level",
            "importance_score",
            "china_involved",
            "china_dependency",
            "china_role",
            "confidence",
            "china_impact",
            "china_risk_trend",
            "expert_summary",
            "recommendations_strategy",
            "recommendations_tactical",
            "supply_risk_score",
            "policy_risk_score",
            "price_pressure_score",
            "risk_score",
            "forecast_6m",
            "forecast_12m",
            "forecast_24m",
            "forecast_48m",
        ]
        return {key: record.get(key, "") for key in fields}
