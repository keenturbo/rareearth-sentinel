#!/usr/bin/env python3
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import isoformat, now_cst

_TODAY_LIST_ENDPOINT = "/api/admin/leads/team-brief/today-list"
_LIST_ENDPOINT = "/api/admin/leads/team-brief/list"
_UPDATE_ENDPOINT = "/api/admin/leads/team-brief/update"


def _ensure_weekly_nav_link(html_doc: str) -> str:
    if "/cn/admin/leads/team-brief/weekly/" in html_doc:
        return html_doc
    target = '<a href="/cn/admin/leads/team-brief/">团队简报线索</a>'
    replacement = (
        '<a href="/cn/admin/leads/team-brief/">团队简报线索</a>\n'
        '      <span>·</span>\n'
        '      <a href="/cn/admin/leads/team-brief/weekly/">周报</a>'
    )
    return html_doc.replace(target, replacement, 1)


def build_page(config: Config):
    template_path = os.path.join(ROOT, "templates", "admin_team_brief_today.html")
    output_path = os.path.join(config.static_dir, "cn", "admin", "leads", "team-brief", "today", "index.html")

    if not os.path.exists(template_path):
        raise FileNotFoundError(f"template not found: {template_path}")

    with open(template_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    html_out = (
        tpl.replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{TODAY_LIST_ENDPOINT}}", _TODAY_LIST_ENDPOINT)
        .replace("{{LEADS_LIST_ENDPOINT}}", _LIST_ENDPOINT)
        .replace("{{LEADS_UPDATE_ENDPOINT}}", _UPDATE_ENDPOINT)
    )
    html_out = _ensure_weekly_nav_link(html_out)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)


if __name__ == "__main__":
    cfg = Config.from_env()
    build_page(cfg)
    print("Admin Team Brief today page built")
