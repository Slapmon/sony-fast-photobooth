#!/usr/bin/env python3
"""Phase 0 camera spike — standalone, no app deps beyond python-gphoto2.

Run directly on the Pi with the a6400 tethered. Writes results to
bench_results.db so `tools/trace_report.py` and `just bench-pi` can pull a
table of numbers back to the dev laptop. See IMPLEMENTATION_PLAN.md §6.

Checklist this script must cover (fill in as each lands):
  T-C1  gphoto2 CLI cold invocation baseline, 10 shots
  T-C2  persistent python-gphoto2 session, 20 shots, per-stage timing at S/M/L
  T-C3  GP_FILE_TYPE_PREVIEW availability + latency
  T-C4  trigger_capture+wait_for_event vs capture()
  T-C5  PTP throughput (MB/s) per JPEG size -> size ceiling
  T-C6  RAW+JPEG with Save Dest: Card+PC - is only the JPEG pulled?
  T-C7  Save Dest PC Only vs Card+PC latency effect
  T-C8  reconnect timing after USB unplug; uhubctl power-cycle support
"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shots", type=int, default=10)
    parser.add_argument("--out", default="bench_results.db")
    parser.parse_args()
    raise NotImplementedError(
        "Phase 0 spike - implement against real hardware, see task list in this file's docstring"
    )


if __name__ == "__main__":
    main()
