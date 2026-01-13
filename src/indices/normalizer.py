import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List

import pandas as pd

from ..utils import now_cst

UNIT_FACTORS = {
    "元/吨": ("CNY/ton", 1.0),
    "元/千克": ("CNY/ton", 1000.0),
    "元/公斤": ("CNY/ton", 1000.0),
}


@dataclass
class IndexRow:
    date: str
    product: str
    max_price: float
    min_price: float
    avg_price: float
    unit: str


class IndexNormalizer:
    def __init__(self, config):
        self.config = config
        self.raw_dir = config.index_raw_dir
        self.out_dir = config.index_normalized_dir
        os.makedirs(self.out_dir, exist_ok=True)

    def normalize(self) -> List[Dict]:
        records = []
        for meta_path in self._iter_meta_files():
            meta = self._load_meta(meta_path)
            file_path = meta.get("file_path")
            if not file_path or not os.path.exists(file_path):
                continue
            if file_path.lower().endswith(".xls"):
                records.extend(self._parse_repe_excel(file_path, meta))
        if records:
            self._write_timeseries(records)
        return records

    def normalize_manual(self, manual_dir: str | None = None) -> List[Dict]:
        manual_dir = manual_dir or os.path.join(self.raw_dir, "manual")
        if not os.path.isdir(manual_dir):
            return []
        latest = self._latest_excel(manual_dir)
        if not latest:
            return []
        meta = {
            "name": "REPE",
            "source_url": "",
            "fetched_at": now_cst().isoformat(),
        }
        records = self._parse_repe_excel(latest, meta)
        if records:
            self._write_timeseries(records)
        return records

    def _latest_excel(self, manual_dir: str) -> str | None:
        candidates = []
        for name in os.listdir(manual_dir):
            if name.lower().endswith((".xls", ".xlsx")):
                candidates.append(os.path.join(manual_dir, name))
        if not candidates:
            return None
        return max(candidates, key=os.path.getmtime)

    def _iter_meta_files(self) -> Iterable[str]:
        if not os.path.isdir(self.raw_dir):
            return []
        for root, _, files in os.walk(self.raw_dir):
            for name in files:
                if name.endswith(".json"):
                    yield os.path.join(root, name)

    def _load_meta(self, path: str) -> Dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _parse_repe_excel(self, path: str, meta: Dict) -> List[Dict]:
        try:
            df = pd.read_excel(path)
        except Exception:
            return []
        df = df.rename(columns=lambda c: str(c).strip())
        required = {"日期", "产品名称", "最高价", "最低价", "均价", "单位"}
        if not required.issubset(set(df.columns)):
            return []
        rows = []
        for _, row in df.iterrows():
            try:
                row_obj = IndexRow(
                    date=str(row["日期"]).strip(),
                    product=str(row["产品名称"]).strip(),
                    max_price=float(row["最高价"]),
                    min_price=float(row["最低价"]),
                    avg_price=float(row["均价"]),
                    unit=str(row["单位"]).strip(),
                )
            except Exception:
                continue
            unit = row_obj.unit
            unit_name, factor = UNIT_FACTORS.get(unit, ("CNY/ton", 1.0))
            record = {
                "index_id": "repe_price_table",
                "index_name": f"稀交所报价-{row_obj.product}",
                "mineral": row_obj.product,
                "value": round(row_obj.avg_price * factor, 4),
                "value_min": round(row_obj.min_price * factor, 4),
                "value_max": round(row_obj.max_price * factor, 4),
                "unit": unit_name,
                "currency": "CNY",
                "published_at": row_obj.date,
                "source_name": meta.get("name") or "REPE",
                "source_url": meta.get("source_url") or "",
                "source_file": path,
                "collected_at": meta.get("fetched_at") or now_cst().isoformat(),
                "original_unit": unit,
                "original_value": row_obj.avg_price,
            }
            rows.append(record)
        return rows

    def _write_timeseries(self, records: List[Dict]):
        out_path = os.path.join(self.out_dir, "index_timeseries.jsonl")
        with open(out_path, "a", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
