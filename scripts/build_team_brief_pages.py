#!/usr/bin/env python3
import os
import sys
from src.config import Config
from src.utils import isoformat, now_cst

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _render_template(template_path, replacements):
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()
    for k, v in replacements.items():
        html = html.replace(k, v)
    return html


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def build_team_brief_pages(config):
    last_update = isoformat(now_cst())
    mappings = [
        (
            os.path.join(ROOT, "templates", "team_brief.html"),
            os.path.join(config.static_dir, "cn", "team-brief", "index.html"),
            "cn",
        ),
        (
            os.path.join(ROOT, "templates", "team_brief_en.html"),
            os.path.join(config.static_dir, "en", "team-brief", "index.html"),
            "en",
        ),
    ]
    for tpl, out, lang in mappings:
        if not os.path.exists(tpl):
            continue
        html = _render_template(
            tpl,
            {
                "{{LAST_UPDATE}}": last_update,
                "{{TEAM_BRIEF_LEAD_ENDPOINT}}": "/api/lead/team-brief",
                "{{PAGE_LANG}}": lang,
            },
        )
        _write(out, html)


def main():
    config = Config.from_env()
    build_team_brief_pages(config)
    print("Team brief pages built")


if __name__ == "__main__":
    main()
