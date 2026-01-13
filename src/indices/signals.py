import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _pct_change(latest: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return (latest - previous) / previous * 100


def build_index_watch(
    normalized_dir: str,
    daily_threshold: float = 3.0,
    weekly_threshold: float = 10.0,
    max_items: int | None = 6,
) -> Dict:
    path = os.path.join(normalized_dir, "index_timeseries.jsonl")
    if not os.path.exists(path):
        return {}

    series = defaultdict(list)
    for line in _iter_jsonl(path):
        date = _parse_date(line.get("published_at"))
        if not date:
            continue
        mineral = str(line.get("mineral", "")).strip()
        if not mineral:
            continue
        try:
            value = float(line.get("value"))
        except (TypeError, ValueError):
            continue
        unit = str(line.get("unit", "CNY/ton"))
        series[mineral].append((date, value, unit))

    if not series:
        return {}

    signals = []
    latest_dates = []
    for mineral, entries in series.items():
        entries.sort(key=lambda x: x[0])
        latest_date, latest_value, unit = entries[-1]
        latest_dates.append(latest_date)

        prev_value = entries[-2][1] if len(entries) > 1 else None
        daily_pct = (
            round(_pct_change(latest_value, prev_value), 2)
            if prev_value is not None
            else None
        )

        week_target = latest_date - timedelta(days=7)
        weekly_value = None
        for d, v, _ in reversed(entries):
            if d <= week_target:
                weekly_value = v
                break
        weekly_pct = (
            round(_pct_change(latest_value, weekly_value), 2)
            if weekly_value is not None
            else None
        )

        if daily_pct is None:
            signal = "flat"
        elif daily_pct > 0:
            signal = "up"
        elif daily_pct < 0:
            signal = "down"
        else:
            signal = "flat"

        alert = False
        if daily_pct is not None and abs(daily_pct) >= daily_threshold:
            alert = True
        if weekly_pct is not None and abs(weekly_pct) >= weekly_threshold:
            alert = True

        signals.append(
            {
                "mineral": mineral,
                "value": latest_value,
                "unit": unit,
                "date": latest_date.strftime("%Y-%m-%d"),
                "daily_pct": daily_pct,
                "weekly_pct": weekly_pct,
                "signal": signal,
                "alert": alert,
            }
        )

    signals.sort(
        key=lambda x: abs(x.get("daily_pct") or 0), reverse=True
    )
    if max_items is not None:
        signals = signals[:max_items]

    latest_date = max(latest_dates).strftime("%Y-%m-%d") if latest_dates else ""
    return {
        "date": latest_date,
        "alerts": sum(1 for item in signals if item.get("alert")),
        "daily_threshold": daily_threshold,
        "weekly_threshold": weekly_threshold,
        "signals": signals,
    }


def write_index_signals(
    normalized_dir: str,
    daily_threshold: float = 3.0,
    weekly_threshold: float = 10.0,
) -> Dict:
    watch = build_index_watch(
        normalized_dir,
        daily_threshold=daily_threshold,
        weekly_threshold=weekly_threshold,
        max_items=None,
    )
    signals = watch.get("signals", [])
    out_path = os.path.join(normalized_dir, "index_signals.jsonl")
    with open(out_path, "w", encoding="utf-8") as f:
        for entry in signals:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return watch


def _iter_jsonl(path: str) -> List[Dict]:
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records
