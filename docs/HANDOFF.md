# RareEarth Sentinel Handoff

Last updated: 2026-01-20

## 1) Project Summary
RareEarth Sentinel is a CN/EN rare earth intelligence platform: global source aggregation, Gemini extraction, structured JSONL storage, and static site generation. It sells two paid products:
- Paid reports (one-off unlock)
- Task execution (ongoing monitoring, paid task pages + periodic reports)

Primary domain: https://sentinel.dataspices.com
CN: https://sentinel.dataspices.com/cn/
EN: https://sentinel.dataspices.com/en/

Admin (NPM Basic Auth):
- /cn/admin/premium/
- /cn/admin/orders/
- /cn/admin/task-orders/
- /cn/admin/sources/
- /cn/admin/keywords/
- /cn/admin/indices/

## 2) Architecture Overview
Pipeline:
1) Sources -> fetcher (Jina/direct/telegram HTML)
2) Gemini extraction (CN+EN)
3) JSONL storage
4) Reporter generates static site
5) Paid flows handled by pay_confirm_server (8112)

Key components:
- main.py: full pipeline (fetch->extract->store->report)
- src/reporter.py: static site generation (do not truncate)
- scripts/pay_confirm_server.py: payment + admin APIs (do not truncate)

## 3) Deployment & Services
Server: GCP VM, Ubuntu
Reverse proxy: Nginx Proxy Manager (NPM) -> 172.17.0.1:8112
Pay confirm service: rareearth-pay-confirm-20260111n (port 8112)
Do not restart dataspices-api (different project)

Systemd timers:
- rareearth-sentinel.timer -> rareearth-sentinel.service (daily 09:00 CST)
- rareearth-run-tasks.timer -> rareearth-run-tasks.service (Mon-Fri 11:00 CST)

## 4) Data Paths
- data/intel.jsonl: main intelligence store
- data/pay/orders.jsonl: report orders
- data/pay/task_orders.jsonl: task orders
- static/pay/status/: order status files
- static/unlocks/: report unlock tokens
- data/keywords/custom_keywords.json: approved keywords (admin-managed)
- data/keywords/keyword_candidates.jsonl: candidate keywords (auto-extracted)
- data/status/indices_upload.json: index upload status
- logs/indices_upload.log: index upload errors

Indices upload file:
- data/indices/raw/manual/repe.xls

## 5) Task Module (T2.3/T2.4)
- Task pay pages: /cn/task/pay/<report_id>.html and /en/task/pay/<report_id>.html
- Task pages: /cn/task/task-<report_id>.html and /en/task/task-<report_id>.html
- Task report pages: /cn/task/task-<report_id>/reports/YYYY-MM-DD.html and /en/task/task-<report_id>/reports/YYYY-MM-DD.html
- Task status flow: pending -> active -> paused -> completed
- Task orders stored in data/pay/task_orders.jsonl

## 6) Payments
Report and task payments share pay_confirm_server but are stored separately.
- Report orders: data/pay/orders.jsonl
- Task orders: data/pay/task_orders.jsonl

Unlock flow:
- pay page -> pay_confirm_server -> admin confirm -> status/unlock JSON

Pay page notes:
- Email is required and used for confirmation
- Task orders are routed by order_type=task and memo task_id fallback
- Bark notify is sent asynchronously with timeout to avoid blocking

## 7) Admin Pages
- /cn/admin/premium/ : report list
- /cn/admin/orders/ : report orders
- /cn/admin/task-orders/ : task orders
- /cn/admin/sources/ : external sources (add/edit/delete + single source run)
- /cn/admin/keywords/ : keyword management (list + candidates + approve/reject)
- /cn/admin/indices/ : price index upload (.xls -> pipeline)

## 8) UI & Analytics
- Share buttons are icon-only with inline SVG (no external SVG dependencies)
- WeChat/Copy are enabled; copy shows toast (CN: "Copied" equivalent)
- Cloudflare Web Analytics injected on visitor pages only (no admin pages)
- About pages include a Contact block (email, domain, subscribe link)

## 9) Sources
Configured in config/external_sources.yml + custom sources (admin).
Notable sources:
- Zaobao via Telegram: t.me/s/zaobao_news (tag #稀土 priority)
- caus.com (site search + Jina full text)
- Kyodo (china.kyodonews.net/articles)
- DW Chinese: dw.com/zh and dw.com/zh-hant (full text used for analysis only)

## 10) Telegram Push
- New intel cards auto-posted after each run
- Format: title + 2 lines summary + CTA
- CTA links to /cn/task/pay/<report_id>.html
- Bot token in .env (never commit)
- Channel: https://t.me/rare_earth_intelligence

## 11) Prompt Versioning
PROMPT_VERSION env var:
- v1 (default)
- v2 (enhanced, adds key_evidence/assumptions/audience_fit)

## 12) Common Commands
- Update code: `cd /root/rareearth-sentinel && git pull`
- Full pipeline: `python3 main.py`
- Rebuild static only: `python3 main.py --rebuild`
- Run tasks manually: `python3 scripts/run_tasks.py`
- Build admin pages: `python3 scripts/build_admin_orders.py`
- Restart pay_confirm: `sudo systemctl restart rareearth-pay-confirm-20260111n`
- Check 8112: `sudo ss -lptn | grep ':8112'`
- Pay confirm logs: `journalctl -u rareearth-pay-confirm-20260111n --since "5 minutes ago" --no-pager`
- Use grep (not rg) for server searches

## 13) Operational Pitfalls (Must Avoid)
- Never truncate src/reporter.py or pay_confirm_server.py
- Do not use placeholder lines like "# ... remaining methods unchanged ..."
- SSH disconnect can kill manual main.py; use nohup for long runs
- NPM must forward new admin API paths (/api/admin/*)
- cgi.FieldStorage cannot be used in boolean checks; use "is None" instead
- Jina/remote fetch can hang; keep timeouts in config
- pay_confirm_server is single-threaded; external calls must use timeouts or async
- Always restart rareearth-pay-confirm-20260111n (not dataspices-api)

## 14) Recent Fixes (Summary)
- Task order routing fixed (task vs report orders)
- Bark notify made async with timeout to avoid blocking
- Share bars icon-only with WeChat/Copy + toast
- Cloudflare Web Analytics added to visitor pages
- About pages contact block added
- DW Chinese sources added

## 15) Next ToDo
P0:
- Monitor daily 09:00 and weekday 11:00 timers
- Watch noise after keyword expansion

P1:
- Prompt optimization (report/task/extraction)
- Ranking data script (TBD)

P2:
- Professional payment system (deferred)

## 16) Daily Maintenance Checklist
- Verify 09:00 run completed (status.json + journalctl)
- Verify 11:00 run_tasks output (journalctl)
- Check indices upload status after manual upload
- Check Telegram auto-post on new intel
