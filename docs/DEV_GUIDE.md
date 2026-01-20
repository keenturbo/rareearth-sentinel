# RareEarth Sentinel Dev Guide

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

## 3) Data Paths
- data/intel.jsonl: main intelligence store
- data/pay/orders.jsonl: report orders
- data/pay/task_orders.jsonl: task orders
- static/pay/status/: order status files
- static/unlocks/: report unlock tokens
- data/keywords/custom_keywords.json: approved keywords (admin-managed)
- data/keywords/keyword_candidates.jsonl: candidate keywords (auto-extracted)

## 4) Task Module (T2.3/T2.4)
- Task pay pages: /cn/task/pay/<report_id>.html and /en/task/pay/<report_id>.html
- Task pages: /cn/task/task-<report_id>.html and /en/task/task-<report_id>.html
- Task report pages: /cn/task/task-<report_id>/reports/YYYY-MM-DD.html and /en/task/task-<report_id>/reports/YYYY-MM-DD.html
- Task status flow: pending -> active -> paused -> completed
- Task orders stored in data/pay/task_orders.jsonl

## 5) Payments
Report and task payments share pay_confirm_server but are stored separately.
- Report orders: data/pay/orders.jsonl
- Task orders: data/pay/task_orders.jsonl

Unlock flow:
- pay page -> pay_confirm_server -> admin confirm -> status/unlock JSON

Pay page notes:
- Email is required and used for confirmation
- Task orders are routed by order_type=task and memo task_id fallback
- Bark notify is sent asynchronously with timeout to avoid blocking

## 6) Admin Pages
- /cn/admin/premium/ : report list
- /cn/admin/orders/ : report orders
- /cn/admin/task-orders/ : task orders
- /cn/admin/sources/ : external sources (add/edit/delete + single source run)
- /cn/admin/keywords/ : keyword management (list + candidates + approve/reject)
- /cn/admin/indices/ : price index upload (.xls -> pipeline)

## 7) UI & Analytics
- Share buttons are icon-only with inline SVG (no external SVG dependencies)
- WeChat/Copy are enabled; copy shows toast (CN: "Copied" equivalent)
- Cloudflare Web Analytics injected on visitor pages only (no admin pages)
- About pages include a Contact block (email, domain, subscribe link)

## 8) Sources
Configured in config/external_sources.yml + custom sources (admin).
Notable sources:
- Zaobao via Telegram: t.me/s/zaobao_news (tag #稀土 priority)
- caus.com (site search + Jina full text)
- Kyodo (china.kyodonews.net/articles)
- DW Chinese: dw.com/zh and dw.com/zh-hant (full text used for analysis only)

## 9) Telegram Push
- New intel cards auto-posted after each run
- Format: title + 2 lines summary + CTA
- CTA links to /cn/task/pay/<report_id>.html
- Bot token in .env (never commit)
- Channel: https://t.me/rare_earth_intelligence

## 10) Prompt Versioning
PROMPT_VERSION env var:
- v1 (default)
- v2 (enhanced, adds key_evidence/assumptions/audience_fit)

## 11) Recent Fixes (Summary)
- Task order routing fixed (task vs report orders)
- Bark notify made async with timeout to avoid blocking
- Share bars icon-only with WeChat/Copy + toast
- Cloudflare Web Analytics added to visitor pages
- About pages contact block added
- DW Chinese sources added

## 12) Next ToDo
P0:
- Monitor daily 09:00 and weekday 11:00 timers
- Watch noise after keyword expansion

P1:
- Prompt optimization (report/task/extraction)
- Ranking data script (TBD)

P2:
- Professional payment system (deferred)

## 13) Dev Pitfalls (Must Avoid)
- Never truncate src/reporter.py or pay_confirm_server.py
- Do not use placeholder lines like "# ... remaining methods unchanged ..."
- Jina/remote fetch can hang; keep timeouts in config
- pay_confirm_server is single-threaded; external calls must use timeouts or async
