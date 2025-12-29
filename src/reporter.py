import html
import os
from datetime import datetime

from .utils import isoformat, now_cst, is_china_primary


class Reporter:
    def __init__(self, config):
        self.config = config

    def rebuild(self, storage):
        total_count, recent_records = storage.read_recent(self.config.index_limit)
        items = [self._prune_record(r) for r in reversed(recent_records)]
        stats = self._build_stats(items, total_count)
        index_data = {
            "last_update": isoformat(now_cst()),
            "total_count": total_count,
            "items": items,
            "stats": stats,
        }
        storage.write_index_json(index_data)
        html_content = self._build_html(index_data)
        storage.write_index_html(html_content)

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

    def _build_html(self, index_data):
        template_path = os.path.join(self.config.templates_dir, "index.html")
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()

        cards_html = self._render_cards(index_data.get("items", []))
        stats = index_data.get("stats", {})

        html_out = (
            template.replace("{{TOTAL_COUNT}}", str(stats.get("total_count", 0)))
            .replace("{{HIGH_RISK_COUNT}}", str(stats.get("high_risk_count", 0)))
            .replace("{{TODAY_COUNT}}", str(stats.get("today_count", 0)))
            .replace("{{LAST_UPDATE}}", index_data.get("last_update", ""))
            .replace("{{CARDS}}", cards_html)
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

            rows.append(
                """
<div class="{row_class}">
  <div class="meta">{meta_line}</div>
  <div class="title"><a href="{url}" target="_blank">{title}</a></div>
  <div class="meta">摘要：{summary}</div>
  <div class="tags">{tags_html}</div>
</div>
""".format(
                    row_class=row_class,
                    meta_line=meta_line,
                    title=title,
                    url=html.escape(item.get("url", "")),
                    summary=summary or "-",
                    tags_html=tags_html,
                )
            )

        return "\n".join(rows)

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
        ]
        return {key: record.get(key, "") for key in fields}
