# RareEarth Sentinel Ops Guide

Last updated: 2026-01-20

## 1) Deployment & Services
Server: GCP VM, Ubuntu
Reverse proxy: Nginx Proxy Manager (NPM) -> 172.17.0.1:8112
Pay confirm service: rareearth-pay-confirm-20260111n (port 8112)
Do not restart dataspices-api (different project)

Systemd timers:
- rareearth-sentinel.timer -> rareearth-sentinel.service (daily 09:00 CST)
- rareearth-run-tasks.timer -> rareearth-run-tasks.service (Mon-Fri 11:00 CST) [legacy]

## 2) Data Paths (Ops-relevant)
- data/intel.jsonl: main intelligence store
- data/pay/orders.jsonl: report orders
- static/pay/status/: order status files
- static/unlocks/: report unlock tokens
- data/indices/raw/manual/repe.xls: manual index source
- data/indices/normalized/index_timeseries.jsonl
- data/indices/normalized/index_signals.jsonl

## 3) Common Commands
- Update code: `cd /root/rareearth-sentinel && git pull`
- Full pipeline: `python3 main.py`
- Rebuild static only: `python3 main.py --rebuild`
- Build admin orders page: `python3 scripts/build_admin_orders.py`
- Restart pay_confirm: `sudo systemctl restart rareearth-pay-confirm-20260111n`
- Check 8112: `sudo ss -lptn | grep ':8112'`
- Pay confirm logs: `journalctl -u rareearth-pay-confirm-20260111n --since "5 minutes ago" --no-pager`
- Use grep (not rg) for server searches

## 4) Payment Troubleshooting (Order Confirm)
1) `systemctl status rareearth-pay-confirm-20260111n`
2) `sudo ss -lptn | grep ':8112'`
3) `journalctl -u rareearth-pay-confirm-20260111n --since "5 minutes ago" --no-pager`
4) Check `static/pay/status/<order_id>.json` and `data/pay/orders.jsonl`

## 5) Index Manual Update (REPE)
1) Copy Excel to: `data/indices/raw/manual/repe.xls`
2) `python3 scripts/normalize_indices.py --once --manual`
3) `python3 scripts/build_index_signals.py --once`
4) `python3 main.py --rebuild`

## 6) Operational Pitfalls (Must Avoid)
- Never truncate src/reporter.py or pay_confirm_server.py
- Do not use placeholder lines like "# ... remaining methods unchanged ..."
- SSH disconnect can kill manual main.py; use nohup for long runs
- NPM must forward new admin API paths (/api/pay/confirm, /api/pay/confirm_admin)
- pay_confirm_server is single-threaded; external calls must use timeouts or async
- Always restart rareearth-pay-confirm-20260111n (not dataspices-api)

## 7) Known Gotchas
- If pay confirm returns 404/500, verify 8112 is not occupied by http.server
- If Bark links show /zh/, clear old status/unlock and re-confirm
- Index page date not updating usually means manual Excel not copied to raw/manual or normalize/signals not rerun
