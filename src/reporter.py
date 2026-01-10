@@
     def _build_html(
         self,
         index_data,
         lang="zh",
         archive_month="",
         archive_prefix="",
         daily_report=None,
     ):
@@
             .replace("{{PREMIUM_TRACKING}}", premium_html)
         )
         return html_out
+
+    def build_premium_archive(self, storage, lang="zh", subdir=""):
+        base_dir = storage.premium_dir
+        index_path = os.path.join(base_dir, "reports_index.jsonl")
+        entries = []
+        if os.path.exists(index_path):
+            with open(index_path, "r", encoding="utf-8") as f:
+                for line in f:
+                    line = line.strip()
+                    if not line:
+                        continue
+                    try:
+                        entry = json.loads(line)
+                    except json.JSONDecodeError:
+                        continue
+                    if isinstance(entry, dict):
+                        entries.append(entry)
+
+        entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
+        display_entries = entries[:300]
+
+        def render_list(items):
+            if not items:
+                empty = "暂无历史报告" if lang == "zh" else "No reports yet."
+                return f'<div class="empty">{empty}</div>'
+            blocks = []
+            for item in items:
+                title = html.escape(item.get("source_title", ""))
+                report_id = item.get("report_id", "")
+                price = item.get("price", 9)
+                preview = item.get("preview_zh") if lang == "zh" else item.get("preview_en")
+                value = ""
+                preview_text = ""
+                if isinstance(preview, dict):
+                    value = preview.get("value", "")
+                    preview_text = preview.get("preview", "")
+
+                value_html = (
+                    f'<div class="card-value">{html.escape(value)}</div>' if value else ""
+                )
+                preview_html = (
+                    f'<div class="card-preview">{html.escape(preview_text)}</div>'
+                    if preview_text
+                    else ""
+                )
+                meta = item.get("created_at", "")
+                btn_label = (
+                    f"解锁深度报告 (${price})" if lang == "zh" else f"Unlock Report (${price})"
+                )
+                blocks.append(
+                    '<div class="card">'
+                    f'<div class="card-title">{title}</div>'
+                    f'<div class="card-meta">{html.escape(meta)} · {html.escape(report_id)}</div>'
+                    f"{value_html}{preview_html}"
+                    f'<a class="btn" href="mailto:sentinel@dataspices.com">{btn_label}</a>'
+                    '</div>'
+                )
+            return "".join(blocks)
+
+        list_html = render_list(display_entries)
+        template_name = "premium_en.html" if lang == "en" else "premium.html"
+        template_path = os.path.join(self.config.templates_dir, template_name)
+        with open(template_path, "r", encoding="utf-8") as f:
+            template = f.read()
+
+        html_out = (
+            template.replace("{{TOTAL_COUNT}}", str(len(entries)))
+            .replace("{{LAST_UPDATE}}", isoformat(now_cst()))
+            .replace("{{PREMIUM_LIST}}", list_html)
+        )
+        # write to /premium/index.html under subdir
+        base = storage._subdir_base(subdir)
+        premium_dir = os.path.join(base, "premium")
+        os.makedirs(premium_dir, exist_ok=True)
+        with open(os.path.join(premium_dir, "index.html"), "w", encoding="utf-8") as f:
+            f.write(html_out)
*** End Patch
