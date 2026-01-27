import os
import shutil

from src.config import Config
from src.storage import Storage
from src.reporter import Reporter
from src.premium import generate_daily_briefs
from src.utils import now_cst


def _clear_today_dir(base_dir, date_key):
    path = os.path.join(base_dir, date_key)
    if os.path.isdir(path):
        shutil.rmtree(path)


def main():
    config = Config.from_env()
    if not config.premium_enabled:
        print("[premium] disabled")
        return

    date_key = now_cst().strftime("%Y-%m-%d")
    _clear_today_dir(config.premium_dir, date_key)

    storage = Storage(config)
    records = list(storage.iter_records())
    entries = generate_daily_briefs(config, records)

    print(f"[premium] generated {len(entries)} entries for {date_key}")

    reporter = Reporter(config)
    reporter.rebuild(storage)
    print("[premium] rebuild done")


if __name__ == "__main__":
    main()
