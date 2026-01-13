import argparse

from src.config import Config
from src.indices.collector import IndexCollector


def main():
    parser = argparse.ArgumentParser(description="Collect index raw data")
    parser.add_argument("--once", action="store_true", help="Collect once and exit")
    args = parser.parse_args()

    config = Config.from_env()
    collector = IndexCollector(config)
    collector.collect()

    if not args.once:
        return


if __name__ == "__main__":
    main()
