#!/usr/bin/env python3
"""Waterfall + percentile report from the spans table in a photobooth SQLite
DB (bench_results.db from tools/bench_camera.py, or a live out/photobooth.db).
Mirrors what /debug/traces and /debug/timings will show in-app later.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def report(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name, COUNT(*), AVG(t_end - t_start) FROM spans GROUP BY name ORDER BY name"
    ).fetchall()
    if not rows:
        print(f"no spans recorded yet in {db_path}")
        return
    for name, count, avg_s in rows:
        print(f"{name:30s} n={count:<5d} avg={avg_s * 1000:.1f} ms")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", type=Path)
    args = parser.parse_args()
    report(args.db_path)


if __name__ == "__main__":
    main()
