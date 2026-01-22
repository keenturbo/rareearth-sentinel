import argparse

from src.config import Config
from src.telegram import post_telegram_top3_due


def main():
    parser = argparse.ArgumentParser(description="Post Top3 daily updates")
    _args = parser.parse_args()
    config = Config.from_env()
    posted, total = post_telegram_top3_due(config)
    if total:
        print(f"[top3] posted {posted}/{total}")


if __name__ == "__main__":
    main()
