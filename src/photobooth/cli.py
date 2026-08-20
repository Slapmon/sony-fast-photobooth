"""Console entrypoint (`photobooth` script, see pyproject.toml)."""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(prog="photobooth")
    parser.add_argument(
        "--config", default="config/dev.yaml", help="path to a profile config (dev.yaml / pi.yaml)"
    )
    parser.parse_args()
    raise NotImplementedError("wire up Settings.load + uvicorn once T-1.1 skeleton settles")


if __name__ == "__main__":
    main()
