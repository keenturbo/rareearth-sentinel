# Caremag Axis Tracker Runbook

## Purpose

Track the France-Japan de-risking / critical-minerals / Caremag axis as an independent watch pool before promoting selected items into `data/intel.jsonl`.

## Files

- Tracker script: `scripts/track_caremag_axis.py`
- Targets config: `data/trackers/caremag_axis_targets.json`
- Watch pool: `data/trackers/caremag_axis_watch.jsonl`
- Status file: `data/status/caremag_axis_tracker_status.json`

## Run

```bash
cd /root/rareearth-sentinel
```

> 预期：进入仓库根目录。

```bash
PYTHONPATH=/root/rareearth-sentinel python3 /root/rareearth-sentinel/scripts/track_caremag_axis.py --run
```

> 预期：打印 JSON 状态，包含 `items_fetched`、`items_new`、`items_deduped`。

## Dry Run

```bash
PYTHONPATH=/root/rareearth-sentinel python3 /root/rareearth-sentinel/scripts/track_caremag_axis.py --run --dry-run
```

> 预期：打印状态但不写入 `watch.jsonl` 或 `status.json`。

## Limit per Target

```bash
PYTHONPATH=/root/rareearth-sentinel python3 /root/rareearth-sentinel/scripts/track_caremag_axis.py --run --limit 5
```

> 预期：每个 target 最多抓 5 条 RSS 结果。

## Inspect Status

```bash
cat /root/rareearth-sentinel/data/status/caremag_axis_tracker_status.json
```

> 预期：看到 tracker 最近一次运行统计。

## Inspect Watch Pool Tail

```bash
tail -n 20 /root/rareearth-sentinel/data/trackers/caremag_axis_watch.jsonl
```

> 预期：看到新增候选条目 JSON 行。

## Suggested Cron

```cron
30 8 * * * cd /root/rareearth-sentinel && PYTHONPATH=/root/rareearth-sentinel python3 /root/rareearth-sentinel/scripts/track_caremag_axis.py --run >> /root/rareearth-sentinel/logs/caremag_axis.log 2>&1
```

```cron
30 18 * * * cd /root/rareearth-sentinel && PYTHONPATH=/root/rareearth-sentinel python3 /root/rareearth-sentinel/scripts/track_caremag_axis.py --run >> /root/rareearth-sentinel/logs/caremag_axis.log 2>&1
```

> 预期：每天两次更新 watch pool；本版本不自动 promote。

## Rollback

Delete tracker script:

```bash
rm /root/rareearth-sentinel/scripts/track_caremag_axis.py
```

> 预期：脚本删除。

Delete targets config:

```bash
rm /root/rareearth-sentinel/data/trackers/caremag_axis_targets.json
```

> 预期：目标配置删除。

Delete watch pool:

```bash
rm /root/rareearth-sentinel/data/trackers/caremag_axis_watch.jsonl
```

> 预期：追踪池删除。

Delete status file:

```bash
rm /root/rareearth-sentinel/data/status/caremag_axis_tracker_status.json
```

> 预期：状态文件删除。
