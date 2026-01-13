import argparse
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.config import Config
from src.indices.normalizer import IndexNormalizer


def main():
    parser = argparse.ArgumentParser(description="Normalize index raw data")
    parser.add_argument("--once", action="store_true", help="Normalize once and exit")
    args = parser.parse_args()

    config = Config.from_env()
    normalizer = IndexNormalizer(config)
    normalizer.normalize()

    if not args.once:
        return


if __name__ == "__main__":
    main()
