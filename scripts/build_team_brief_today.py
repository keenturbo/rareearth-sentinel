#!/usr/bin/env python3
import os
import sys
import json

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.utils import isoformat, now_cst

_TODAY_LIST_ENDPOINT = "/api/admin/leads/team-brief/today-list"
_LIST_ENDPOINT = "/api/admin/leads/team-brief/list"
_UPDATE_ENDPOINT = "/api/admin/leads/team-brief/update"


def _ensure_team_brief_nav_links(html_doc: str) -> str:
    target = '<a href="/cn/admin/leads/team-brief/">团队简报线索</a>'
    if target not in html_doc:
        return html_doc

    extras = []
    if '/cn/admin/leads/team-brief/daily-brief/' not in html_doc:
        extras.append(
            '\n      <span>·</span>\n      <a href="/cn/admin/leads/team-brief/daily-brief/">每日摘要</a>'
        )
    if '/cn/admin/leads/team-brief/weekly/' not in html_doc:
        extras.append(
            '\n      <span>·</span>\n      <a href="/cn/admin/leads/team-brief/weekly/">周报</a>'
        )

    if not extras:
        return html_doc

    replacement = target + "".join(extras)
    return html_doc.replace(target, replacement, 1)


def _load_daily_digest(config: Config):
    path = os.path.join(config.data_dir, "status", "team_brief_daily_digest.json")
    if not os.path.exists(path):
        return "", ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        generated_at = str(obj.get("generated_at") or "").strip()
        master_text = str(obj.get("master_text") or "").strip()
        return generated_at, master_text
    except Exception:
        return "", ""


def build_page(config: Config):
    template_path = os.path.join(ROOT, "templates", "admin_team_brief_today.html")
    output_path = os.path.join(config.static_dir, "cn", "admin", "leads", "team-brief", "today", "index.html")

    if not os.path.exists(template_path):
        raise FileNotFoundError(f"template not found: {template_path}")

    with open(template_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    digest_last_update, digest_master_text = _load_daily_digest(config)

    html_out = (
        tpl.replace("{{LAST_UPDATE}}", isoformat(now_cst()))
        .replace("{{TODAY_LIST_ENDPOINT}}", _TODAY_LIST_ENDPOINT)
        .replace("{{LEADS_LIST_ENDPOINT}}", _LIST_ENDPOINT)
        .replace("{{LEADS_UPDATE_ENDPOINT}}", _UPDATE_ENDPOINT)
        .replace("{{DAILY_DIGEST_LAST_UPDATE}}", digest_last_update or "暂无")
        .replace("{{DAILY_DIGEST_MASTER_TEXT_JSON}}", json.dumps(digest_master_text, ensure_ascii=False))
    )
    html_out = _ensure_team_brief_nav_links(html_out)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)


if __name__ == "__main__":
    cfg = Config.from_env()
    build_page(cfg)
    print("Admin Team Brief today page built")
