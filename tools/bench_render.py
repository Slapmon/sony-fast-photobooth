#!/usr/bin/env python3
"""Phase 0 render spike — pyvips timings, no app deps. See IMPLEMENTATION_PLAN.md §6.

  T-R1  decode+resize to 1920px display variant, per source size
  T-R2  shrink-on-load vs full decode
  T-R3  full 2x2 collage composite, border + text, at 300 dpi
  T-R4  all three variants (print/web/thumb) end to end
  T-R5  peak RSS during T-R3
"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures-dir", default="fixtures/shots")
    parser.parse_args()
    raise NotImplementedError("Phase 0 spike - implement once pyvips is available (pi extra)")


if __name__ == "__main__":
    main()
