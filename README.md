# rareearth-sentinel

Global (non-China) rare earth intelligence sentinel. Multi-source crawl, Jina Reader fetch, Gemini extraction, JSONL history, static report.

## What it does
- Collects multi-source links (default: Google News RSS)
- Fetches article text via Jina Reader
- Uses Gemini (gemini-3-flash-preview) to extract structured intelligence in Chinese
- Filters out China-primary events, while tagging China involvement
- Appends to data/intel.jsonl and generates static/index.json + static/index.html

## Quick start
1) Create `.env` based on `.env.example`
2) Install dependencies
3) Run once

```
pip install -r requirements.txt
python main.py
```

## Manual rebuild
```
python main.py --rebuild
```

## Configuration
- `config/keywords.txt`: one keyword per line
- `.env`: control model, limits, delays, output paths

## Output
- `data/intel.jsonl` (append-only history)
- `static/index.json` (lightweight index)
- `static/index.html` (static dashboard)

These outputs are ignored by git.

## Scheduling (GCP cron, 09:00 CST)
Add a cron entry with Asia/Shanghai timezone.

```
CRON_TZ=Asia/Shanghai
0 9 * * * cd /path/to/rareearth-sentinel && /usr/bin/python3 main.py >> cron.log 2>&1
```
