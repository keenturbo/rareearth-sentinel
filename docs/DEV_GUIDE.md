# RareEarth Sentinel Dev Guide

Last updated: 2026-01-20

## 1) Project Summary
RareEarth Sentinel is a CN/EN rare earth intelligence platform: global source aggregation, Gemini extraction, structured JSONL storage, and static site generation. It sells paid reports (one-off unlock). Task execution is planned but not fully wired in the current main branch.

Primary domain: https://sentinel.dataspices.com
CN: https://sentinel.dataspices.com/cn/
EN: https://sentinel.dataspices.com/en/

Admin (NPM Basic Auth):
- /cn/admin/premium/
- /cn/admin/orders/
- /cn/admin/task-orders/ (legacy)
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

## 3) Current Pages & UX (Main)
- Home shows 5 cards; full list at /cn/news/ and /en/news/ (200 items)
- Premium list: /cn/premium/ and /en/premium/
- Pay pages: /cn/pay/<report_id>.html and /en/pay/<report_id>.html
- Admin report pages: /cn/admin/premium/ and /en/admin/premium/
- Admin orders pages: /cn/admin/orders/ and /en/admin/orders/
- About/Subscribe/Rank pages exist for CN/EN

Index Watch:
- Table rendering with ↑/↓ color
- EN mineral names are mapped to English
- Display count capped by INDEX_WATCH_MAX (default 6)

Menus:
- Top nav includes Home/About/Subscribe/Ranking/Premium/Language/⚙️

## 4) Pay & Unlock Flow (Reports)
- Pay page generates order_id; buyer must include report_id + tier + order_id + email in memo
- Buyer clicks "I have paid" -> /api/pay/confirm (Bark notify)
- Admin confirms -> /api/pay/confirm_admin -> status + unlock token generated
- Unlock link: /cn/pay/<report_id>.html?unlock=<token> (7 days)
- CN pay shows WeChat/Alipay/token; EN pay shows USDT + email
- $99/$999 tiers are hidden by default

## 5) Data Paths
- data/intel.jsonl: main intelligence store
- data/pay/orders.jsonl: report orders
- static/pay/status/: order status files
- static/unlocks/: report unlock tokens
- data/indices/raw/manual/repe.xls: manual index source
- data/indices/normalized/index_timeseries.jsonl
- data/indices/normalized/index_signals.jsonl

## 6) Task Module (Status)
- Templates exist: templates/task_pay.html and templates/task_pay_en.html
- Task pay page generation + task links are not yet wired (T2.3 pending)
- CTA copy is unified: “哨兵执行操作建议” / English counterpart

## 7) Admin Pages
- /cn/admin/premium/ : report list
- /cn/admin/orders/ : report orders
- /cn/admin/sources/ : external sources (add/edit/delete + single source run)
- /cn/admin/keywords/ : keyword management
- /cn/admin/indices/ : price index upload (.xls -> pipeline)

## 8) UI & Analytics
- Share buttons icon-only with inline SVG (no external SVG dependencies)
- Cloudflare Web Analytics on visitor pages only (no admin pages)
- About pages include a Contact block

## 9) Sources
Configured in config/external_sources.yml + custom sources (admin).
Notable sources include Zaobao (Telegram), caus.com, Kyodo, DW Chinese, and REEx site_search.

## 10) Prompt Versioning
PROMPT_VERSION env var:
- v1 (default)
- v2 (enhanced, adds key_evidence/assumptions/audience_fit)

## 11) Index Update (Manual)
When REPE Excel is updated manually:
1) Copy to data/indices/raw/manual/repe.xls
2) python3 scripts/normalize_indices.py --once --manual
3) python3 scripts/build_index_signals.py --once
4) python3 main.py --rebuild

## 12) Dev Pitfalls (Must Avoid)
- Never truncate src/reporter.py or pay_confirm_server.py
- Do not use placeholder lines like "# ... remaining methods unchanged ..."
- Always check wc -l before/after reporter edits
- Avoid large template replacements; small, focused edits only
- pay_confirm_server is single-threaded; external calls must use timeouts or async
