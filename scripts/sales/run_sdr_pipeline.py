#!/usr/bin/env python3
from pathlib import Path
import os
import sys

CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from _common import Config
from build_lead_pool import build_lead_pool
from score_leads import score_leads
from generate_outreach_drafts import generate_drafts
from schedule_followups import schedule_followups
from classify_replies import classify_replies
from build_sdr_daily_snapshot import build_snapshot


def run_pipeline(config: Config):
    steps = []
    steps.append({"step": "build_lead_pool", **build_lead_pool(config)})
    steps.append({"step": "score_leads", **score_leads(config)})
    steps.append({"step": "generate_outreach_drafts", **generate_drafts(config)})
    steps.append({"step": "schedule_followups", **schedule_followups(config)})
    steps.append({"step": "classify_replies", **classify_replies(config)})
    steps.append({"step": "build_sdr_daily_snapshot", **build_snapshot(config)})
    return {"ok": True, "steps": steps}
def main():
    project_root = Path(__file__).resolve().parents[2]
    os.chdir(project_root)
    cfg = Config.from_env()
    result = run_pipeline(cfg)
    print(result)


if __name__ == "__main__":
    main()
