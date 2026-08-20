#!/usr/bin/env python3
"""T-S1: 300 consecutive captures. Does the Sony PTP session drop? Memory
growth? Thermal throttle? Log everything. See IMPLEMENTATION_PLAN.md §6, §11.

Later (T-5.3) this grows into the multi-hour randomized-guest-flow +
fault-injection harness; keep the shape compatible.
"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shots", type=int, default=300)
    parser.parse_args()
    raise NotImplementedError("Phase 0 soak - implement against real hardware")


if __name__ == "__main__":
    main()
