#!/usr/bin/env python3
import html
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import isoformat, now_cst


def _read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return rows


def _render_items(rows):
    if not rows:
        return '<tr><td colspan="8" class="muted">暂无线索</td></tr>'

    out = []
    for r in rows[:300]:
        lead_id = html.escape(str(r.get("lead_id") or ""))
        created_at = html.escape(str(r.get("created_at") or ""))
        name = html.escape(str(r.get("name") or ""))
        email = html.escape(str(r.get("email") or ""))
        company = html.escape(str(r.get("company") or ""))
        role = html.escape(str(r.get("role") or ""))
        need = html.escape(str(r.get("brief_need") or ""))
        budget = html.escape(str(r.get("budget_range") or ""))
        channel = html.escape(str(r.get("source_channel") or ""))
        message = html.escape(str(r.get("message") or ""))
        utm = html.escape(
            " / ".join(
                [
                    str(r.get("utm_source") or ""),
                    str(r.get("utm_campaign") or ""),
                    str(r.get("utm_content") or ""),
                ]
            ).strip(" /")
        )
        status = html.escape(str(r.get("status") or "new"))

        out.append(
            "<tr>"
            f"<td>{created_at}</td>"
            f"<td><div>{lead_id}</div><div class='muted'>{status}</div></td>"
            f"<td><div>{name}</div><div class='muted'>{email}</div></td>"
            f"<td><div>{company}</div><div class='muted'>{role}</div></td>"
            f"<td><div>{need}</div><div class='muted'>{budget}</div></td>"
            f"<td><div>{channel}</div><div class='muted'>{utm}</div></td>"
            f"<td>{message}</td>"
            "</tr>"
        )
    return "".join(out)


def build_page(config):
    leads_path = os.path.join(config.data_dir, "pay", "team_brief_leads.jsonl")
    template_path = os.path.join(ROOT, "templates", "admin_team_brief_leads.html")
    output_path = os.path.join(config.static_dir, "cn", "admin", "leads", "team-brief", "index.html")

    rows = _read_jsonl(leads_path)

    if not os.path.exists(template_path):
        return
    with open(template_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    html_out = (
        tpl.replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{TOTAL_COUNT}}", str(len(rows)))
        .replace("{{LEADS_PATH}}", html.escape(leads_path))
        .replace("{{LEADS_ROWS}}", _render_items(rows))
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)


def main():
    config = Config.from_env()
    build_page(config)
    print("Admin team brief leads page built")


if __name__ == "__main__":
    main()
