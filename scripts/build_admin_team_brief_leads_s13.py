#!/usr/bin/env python3
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from scripts.build_admin_team_brief_leads import build_page as build_leads_page
from scripts.patch_admin_team_brief_leads_won_reviews import patch_leads_page as patch_won_reviews


def main():
    cfg = Config.from_env()
    build_leads_page(cfg)
    patch_res = patch_won_reviews(cfg)
    print(json.dumps({"ok": True, "patched": patch_res}, ensure_ascii=False))


if __name__ == "__main__":
    main()
