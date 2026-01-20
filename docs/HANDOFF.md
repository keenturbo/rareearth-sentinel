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

Systemd timers:
- rareearth-sentinel.timer -> rareearth-sentinel.service (daily 09:00 CST)
- rareearth-run-tasks.timer -> rareearth-run-tasks.service (Mon-Fri 11:00 CST)

## 4) Data Paths
- data/intel.jsonl: main intelligence store
- data/pay/orders.jsonl: report orders
- data/pay/task_orders.jsonl: task orders
- data/keywords/custom_keywords.json: approved keywords (admin-managed)
- data/keywords/keyword_candidates.jsonl: candidate keywords (auto-extracted)
- data/status/indices_upload.json: index upload status
- logs/indices_upload.log: index upload errors

Indices upload file:
- data/indices/raw/manual/repe.xls

## 5) Task Module (T2.3/T2.4)
- Task pay pages: /cn/task/pay/<report_id>.html and /en/task/pay/<report_id>.html
- Task pages: /cn/task/task-<report_id>.html
- Task report pages: /cn/task/task-<report_id>/reports/YYYY-MM-DD.html
- Task status flow: pending -> active -> paused -> completed
- Task orders stored in data/pay/task_orders.jsonl

## 6) Payments
Report and task payments share pay_confirm_server but are stored separately.
- Report orders: data/pay/orders.jsonl
- Task orders: data/pay/task_orders.jsonl

Unlock flow:
- pay page -> pay_confirm_server -> admin confirm -> status/unlock JSON

## 7) Admin Pages
- /cn/admin/premium/ : report list
- /cn/admin/orders/ : report orders
- /cn/admin/task-orders/ : task orders
- /cn/admin/sources/ : external sources (add/edit/delete + single source run)
- /cn/admin/keywords/ : keyword management (list + candidates + approve/reject)
- /cn/admin/indices/ : price index upload (.xls -> pipeline)

## 8) Sources
Configured in config/external_sources.yml + custom sources (admin).
Notable sources:
- Zaobao via Telegram: t.me/s/zaobao_news (tag #稀土 priority)
- caus.com (site search + Jina full text)
- Kyodo (china.kyodonews.net/articles)

## 9) Telegram Push
- New intel cards auto-posted after each run
- Format: title + 2 lines summary + CTA
- CTA links to /cn/task/pay/<report_id>.html
- Bot token in .env (never commit)

## 10) Prompt Versioning
PROMPT_VERSION env var:
- v1 (default)
- v2 (enhanced, adds key_evidence/assumptions/audience_fit)

## 11) Common Commands
- Update code: `cd /root/rareearth-sentinel && git pull`
- Full pipeline: `python3 main.py`
- Rebuild static only: `python3 main.py --rebuild`
- Run tasks manually: `python3 scripts/run_tasks.py`
- Build admin pages: `python3 scripts/build_admin_orders.py`
- Restart pay_confirm: `sudo systemctl restart rareearth-pay-confirm-20260111n`

## 12) Operational Pitfalls (Must Avoid)
- Never truncate src/reporter.py or pay_confirm_server.py
- Do not use placeholder lines like "# ... remaining methods unchanged ..."
- SSH disconnect can kill manual main.py; use nohup for long runs
- NPM must forward new admin API paths (/api/admin/*)
- cgi.FieldStorage cannot be used in boolean checks; use "is None" instead
- Jina/remote fetch can hang; keep timeouts in config

## 13) Recent Fixes (Summary)
- Task order routing fixed (task vs report orders)
- Telegram summary de-dup + expert summary line
- Admin sources run: lock + status + auto rebuild
- Admin indices upload: error logging + status
- Keywords: custom store + candidates + admin page

## 14) Next ToDo
P0:
- Monitor daily 09:00 and weekday 11:00 timers
- Watch noise after keyword expansion

P1:
- Prompt optimization (report/task/extraction)
- Ranking data script (TBD)

P2:
- Professional payment system (deferred)

## 15) Daily Maintenance Checklist
- Verify 09:00 run completed (status.json + journalctl)
- Verify 11:00 run_tasks output (journalctl)
- Check indices upload status after manual upload
- Check Telegram auto-post on new intel
