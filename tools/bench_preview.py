#!/usr/bin/env python3
"""Phase 0 preview spike — go2rtc MJPEG timings. See IMPLEMENTATION_PLAN.md §6.

  T-P1  go2rtc MJPEG copy at 1280x720, 30 min soak, dropped/corrupt frame count
  T-P2  same at 640x480
  T-P3  YUYV->MJPEG transcode CPU cost + artifact rate vs copy
  T-P4  CPU cost of the app proxying go2rtc's MJPEG to a browser
  T-P5  latency: physical motion -> pixels on screen
  T-P6  HDMI blanking behaviour during capture

  T-X1  does preview streaming degrade PTP download speed?
  T-X2  camera on USB2 + stick on USB3 vs both on USB2
  T-X3  Godox recycle time vs 4-shot collage cadence
"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream-url", default="http://127.0.0.1:1984/api/stream.mjpeg?src=photobooth")
    parser.parse_args()
    raise NotImplementedError("Phase 0 spike - implement against go2rtc on the Pi")


if __name__ == "__main__":
    main()
