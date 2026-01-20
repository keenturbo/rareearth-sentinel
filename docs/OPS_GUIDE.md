# RareEarth Sentinel Ops Guide

Last updated: 2026-01-20

## 1) Deployment & Services
Server: GCP VM, Ubuntu
Reverse proxy: Nginx Proxy Manager (NPM) -> 172.17.0.1:8112
Pay confirm service: rareearth-pay-confirm-20260111n (port 8112)
Do not restart dataspices-api (different project)

Systemd timers:
- rareearth-sentinel.timer -> rareearth-sentinel.service (daily 09:00 CST)
- rareearth-run-tasks.timer -> rareearth-run-tasks.service (Mon-Fri 11:00 CST)

## 2) Data Paths (Ops-relevant)
- data/intel.jsonl: main intelligence store
- data/pay/orders.jsonl: report orders
- data/pay/task_orders.jsonl: task orders
- static/pay/status/: order status files
- static/unlocks/: report unlock tokens
- data/status/indices_upload.json: index upload status
- logs/indices_upload.log: index upload errors

Indices upload file:
- data/indices/raw/manual/repe.xls

## 3) Common Commands
- Update code: `cd /root/rareearth-sentinel && git pull`
- Full pipeline: `python3 main.py`
- Rebuild static only: `python3 main.py --rebuild`
- Run tasks manually: `python3 scripts/run_tasks.py`
- Build admin pages: `python3 scripts/build_admin_orders.py`
- Restart pay_confirm: `sudo systemctl restart rareearth-pay-confirm-20260111n`
- Check 8112: `sudo ss -lptn | grep ':8112'`
- Pay confirm logs: `journalctl -u rareearth-pay-confirm-20260111n --since "5 minutes ago" --no-pager`
- Use grep (not rg) for server searches

## 4) Payment Troubleshooting (Order Confirm)
1) `systemctl status rareearth-pay-confirm-20260111n`
2) `sudo ss -lptn | grep ':8112'`
3) `journalctl -u rareearth-pay-confirm-20260111n --since "5 minutes ago" --no-pager`
4) Check `static/pay/status/<order_id>.json` and `data/pay/orders.jsonl` / `data/pay/task_orders.jsonl`

## 5) Operational Pitfalls (Must Avoid)
- Never truncate src/reporter.py or pay_confirm_server.py
- Do not use placeholder lines like "# ... remaining methods unchanged ..."
- SSH disconnect can kill manual main.py; use nohup for long runs
- NPM must forward new admin API paths (/api/admin/*)
- cgi.FieldStorage cannot be used in boolean checks; use "is None" instead
- pay_confirm_server is single-threaded; external calls must use timeouts or async
- Always restart rareearth-pay-confirm-20260111n (not dataspices-api)

## 6) Daily Maintenance Checklist
- Verify 09:00 run completed (status.json + journalctl)
- Verify 11:00 run_tasks output (journalctl)
- Check indices upload status after manual upload
- Check Telegram auto-post on new intel
