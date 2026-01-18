# rareearth-sentinel

Global rare earth intelligence sentinel (focus on non-China). Multi-source crawl, Jina Reader fetch, Gemini extraction (CN/EN), JSONL history, static bilingual site, premium pay/unlock, and index watch.

## What it does
- Collects multi-source links (Google News RSS + official sources)
- Fetches article text via Jina Reader or direct fallback
- Extracts structured intelligence with Gemini in CN + EN
- Tags China involvement and filters out China-primary events
- Appends to JSONL history and generates static pages
- Generates premium briefs and pay/unlock flow
- Builds index watch signals from price sources
- Runs task execution for paid recommendations

## Architecture
Sources -> Fetcher -> Extractor -> Normalizer -> JSONL storage -> Reporter (static site)

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

## Repo layout
- `main.py` - main pipeline entry (fetch, extract, store, report)
- `requirements.txt` - dependencies
- `.env.example` - env template
- `config/` - keywords and source configs
- `src/` - core pipeline modules
- `templates/` - HTML templates for static site
- `scripts/` - operational scripts and admin utilities
- `data/` - JSONL history, premiums, indices
- `static/` - generated site output (cn/en)

## src/ modules
- `src/config.py` - env loading and defaults
- `src/fetcher.py` - Jina/direct fetching + Google News resolve
- `src/extractor.py` - Gemini extraction (CN/EN), daily summary
- `src/normalizer.py` - normalization + scoring
- `src/storage.py` - JSONL append, index/archives
- `src/context.py` - vector and keyword context builders
- `src/embeddings.py` - embeddings JSONL store
- `src/reporter.py` - static site generator (core)

### src/indices/
- `collector.py` - index source collection
- `normalizer.py` - Excel and raw -> JSONL series
- `signals.py` - index signal computation

### src/premium/
- `briefs.py` - $9 brief generation
- `pipeline.py` - premium pipeline and outputs

### src/sources/
- `google_news.py` - Google News RSS
- `official.py` - official sources
- `registry.py` - source registry

## templates/
CN/EN templates for index, premium, pay, admin, about, subscribe, rank, task pay, task page, and task report.

## scripts/
- `collect_indices.py` - fetch price sources
- `normalize_indices.py` - normalize index data
- `build_index_signals.py` - build index signals
- `pay_confirm_server.py` - pay confirmation API (Bark notify)
- `confirm_payment.py` - CLI confirm to generate unlock
- `build_admin_orders.py` - render admin orders and task orders pages
- `premium_backfill.py` - backfill premium briefs
- `official_backfill.py` - backfill official source ids
- `clean_invalid_official_links.py` - cleanup broken official links
- `run_tasks.py` - execute paid tasks and generate reports

## Payment flow (reports)
1) User visits `/cn/pay/<report_id>.html` or `/en/pay/<report_id>.html`
2) User pays with required memo format
3) User clicks "I have paid" -> pay_confirm_server
4) Admin confirms -> `/static/pay/status/*.json` and `/static/unlocks/*.json`
5) Pay page polls status and reveals unlock link

## Task pay (T2.x)
- T2.1/T2.2 done: task pay templates exist
- T2.3 done: generate `/cn/task/pay/<report_id>.html` and `/en/task/pay/<report_id>.html` and link CTA to them
- T2.4 done: task orders, task pages, and task reports (`/cn|en/task/<task_id>.html` + `/cn|en/task/<task_id>/reports/<date>.html`)
- TASK_ID format: `task-<report_id>` (current default)

## Configuration
Key env variables are defined in `src/config.py` and `.env.example`.
Typical keys:
- `GEMINI_API_KEY`, `GEMINI_MODEL`
- `DATA_DIR`, `STATIC_DIR`, `TEMPLATES_DIR`
- `PAY_CONFIRM_ENDPOINT`, `PAY_BARK_URL`, `PAY_ADMIN_BASE_URL`
- `INDEX_*` variables for index watch

## Deployment notes
- Primary domain: https://sentinel.dataspices.com
- CN/EN: `/cn/` and `/en/`
- Admin: `/cn/admin/premium/` (protected via NPM auth)
- Task admin: `/cn/admin/task-orders/`
- `pay_confirm_server.py` runs on port 8112
- Static site is generated under `static/` and served by Nginx Proxy Manager

## Operations
### Daily run (09:00 CST)
Example cron:
```
CRON_TZ=Asia/Shanghai
0 9 * * * cd /path/to/rareearth-sentinel && /usr/bin/python3 main.py >> cron.log 2>&1
```

### Task runner schedule
- Daily tasks: 11:00 CST
- Weekly tasks: Monday 09:00 CST
- Event trigger: keyword match (title + summary) + related report keywords

### Index update (manual)
1) Upload Excel to `data/indices/raw/manual/` (see config)
2) Run `normalize_indices.py --once --manual`
3) Run `build_index_signals.py --once`
4) Run `python main.py --rebuild`

## Safety rules
- Do not truncate or replace `src/reporter.py`
- Avoid placeholder comments like "... remaining methods unchanged ..."
- Make small, incremental edits and verify diff size

## Current status / todo
- Prompt optimization for premium report quality
- Prompt optimization for task execution outputs
- Prompt optimization for news extraction quality
- Ranking data source and script still TBD
- $99/$999 tiers remain hidden (reserved for later)
