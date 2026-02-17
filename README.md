# RareEarth Sentinel

Global (non-China) rare earth intelligence sentinel: multi-source crawl, Gemini extraction, JSONL history, static report.

## Product Matrix (L0-L3)

- L0 Free: home Top1/Top tracks, social copy, Telegram signals
- L1 Paid: single deep report ($9), weekly pack (5 reports)
- L2 Decision: task sprint (14 days)
- L3 Team: team brief (manual delivery)

### New page builders

Build product pages (pricing / weekly-pack / task sprint):

```bash
PYTHONPATH=/root/rareearth-sentinel python3 /root/rareearth-sentinel/main.py --build-products
```

Generated paths:

- `/cn/pricing/`, `/en/pricing/`
- `/cn/weekly-pack/`, `/en/weekly-pack/`
- `/cn/task/sprint/`, `/en/task/sprint/`

Data files:

- `data/products/catalog.json`
- `data/products/packs.json`

## Local run

```bash
pip install -r requirements.txt
```

```bash
python3 main.py
```

## Rebuild static pages

```bash
PYTHONPATH=/root/rareearth-sentinel python3 main.py --rebuild
```

## Build datasets

```bash
PYTHONPATH=/root/rareearth-sentinel python3 main.py --build-datasets
```
