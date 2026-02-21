#!/usr/bin/env python3
import os
import sys
import json
import re

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
    if '/cn/admin/leads/team-brief/ops/' not in html_doc:
        extras.append(
            '\n      <span>·</span>\n      <a href="/cn/admin/leads/team-brief/ops/">运营总览</a>'
        )
    if '/cn/admin/pay/health/' not in html_doc:
        extras.append(
            '\n      <span>·</span>\n      <a href="/cn/admin/pay/health/">支付健康</a>'
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


def _write_today_selfcheck(html_out: str, config: Config):
    flags = {
        "placeholder_clean": ("{{" not in html_out and "}}" not in html_out),
        "no_new_url_constructor": ("new URL(" not in html_out),
    }

    bad_dt_re = re.compile(
        r'type="datetime-local"[^>]*value="[^"]*(Z|[+-]\d{2}:\d{2}|:\d{2}:\d{2})"|type="date"[^>]*value="[^"]*T',
        re.I,
    )
    flags["no_invalid_datetime_literal"] = bad_dt_re.search(html_out) is None

    notes = []
    if not flags["placeholder_clean"]:
        notes.append("placeholder_not_replaced")
    if not flags["no_new_url_constructor"]:
        notes.append("new_url_constructor_detected")
    if not flags["no_invalid_datetime_literal"]:
        notes.append("invalid_datetime_literal_detected")

    result = {
        "generated_at": isoformat(now_cst()),
        "flags": flags,
        "pass": all(flags.values()),
        "notes": notes,
    }

    out_path = os.path.join(config.data_dir, "status", "team_brief_today_selfcheck.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


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

    _write_today_selfcheck(html_out, config)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)


if __name__ == "__main__":
    cfg = Config.from_env()
    build_page(cfg)
    print("Admin Team Brief today page built")
