#!/usr/bin/env python3
"""Manual camera test CLI — runs against the real python-gphoto2 backend
(photobooth.camera.gphoto.GphotoBackend), the same class the deployed
camera-worker process uses. Use this for eyeballing behaviour and images
one shot at a time; tools/bench_camera.py is the automated Phase 0 timing
sweep. Requires the `pi` extra installed (libgphoto2 + python-gphoto2) and
a camera in PC Remote mode — see photobooth-plan.md §3.4.

Examples:
    python tools/cam_test.py detect
    python tools/cam_test.py summary
    python tools/cam_test.py autofocus
    python tools/cam_test.py capture-full --size S
    python tools/cam_test.py capture-preview-then-full --size M
    python tools/cam_test.py soak --shots 20 --size S --mode preview-then-full
"""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

from photobooth.camera.gphoto import GphotoBackend
from photobooth.camera.protocol import CameraError

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "out" / "cam_test"


def _timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _save(data: bytes, out_dir: Path, label: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_timestamp()}-{label}.jpg"
    path.write_bytes(data)
    return path


def cmd_detect(args: argparse.Namespace) -> None:
    import gphoto2 as gp

    cameras = gp.Camera.autodetect()
    if cameras.count() == 0:
        print("no camera detected")
        return
    for i in range(cameras.count()):
        print(f"{cameras.get_name(i)}  ({cameras.get_value(i)})")


def cmd_summary(args: argparse.Namespace) -> None:
    backend = GphotoBackend(jpeg_size=args.size)
    t0 = time.monotonic()
    backend.connect()
    print(f"connect: {time.monotonic() - t0:.3f}s")
    print(f"connected: {backend.is_connected()}")
    backend.disconnect()


def cmd_autofocus(args: argparse.Namespace) -> None:
    backend = GphotoBackend(jpeg_size=args.size)
    backend.connect()
    try:
        t0 = time.monotonic()
        backend.trigger_autofocus()
        print(
            f"autofocus trigger: {time.monotonic() - t0:.3f}s "
            "(fired; body does not report AF-lock status back over PTP)"
        )
    finally:
        backend.disconnect()


def _do_capture_full(backend: GphotoBackend, out_dir: Path) -> None:
    t0 = time.monotonic()
    capture_id = backend.trigger_capture()
    t_trigger = time.monotonic()
    image = backend.download_full(capture_id)
    t_full = time.monotonic()
    path = _save(image.data, out_dir, "full")
    print(
        f"trigger->file_added: {t_trigger - t0:.3f}s  "
        f"full_download: {t_full - t_trigger:.3f}s  "
        f"total: {t_full - t0:.3f}s  "
        f"{image.width}x{image.height}  {len(image.data) / 1024:.0f} KB  -> {path.name}"
    )


def _do_capture_preview_then_full(backend: GphotoBackend, out_dir: Path) -> None:
    """Two-stage capture per IMPLEMENTATION_PLAN.md section 5.

    Preview download is on the critical path (this is what "the screen
    shows an image" would wait on); full download runs on a background
    thread afterward. Both downloads are still serial on the one USB/PTP
    session -- the overlap is at the application level (the caller gets
    control back after the preview, not after the full download), not on
    the wire. See photobooth-plan.md section 3.3.
    """
    t0 = time.monotonic()
    capture_id = backend.trigger_capture()
    t_trigger = time.monotonic()

    preview = backend.download_preview(capture_id)
    t_preview = time.monotonic()
    if preview is None:
        print("no PTP preview support on this body -- falling back to a direct full download")
        image = backend.download_full(capture_id)
        t_full = time.monotonic()
        path = _save(image.data, out_dir, "full")
        print(
            f"trigger->file_added: {t_trigger - t0:.3f}s  "
            f"full_download: {t_full - t_preview:.3f}s  "
            f"total: {t_full - t0:.3f}s  "
            f"{image.width}x{image.height}  -> {path.name}"
        )
        return

    preview_path = _save(preview.data, out_dir, "preview")
    print(
        f"trigger->file_added: {t_trigger - t0:.3f}s  "
        f"preview_download: {t_preview - t_trigger:.3f}s  "
        f"PREVIEW READY at {t_preview - t0:.3f}s  "
        f"{preview.width}x{preview.height}  "
        f"{len(preview.data) / 1024:.0f} KB  -> {preview_path.name}"
    )

    def _background_full() -> None:
        t_full_start = time.monotonic()
        image = backend.download_full(capture_id)
        t_full_done = time.monotonic()
        full_path = _save(image.data, out_dir, "full")
        print(
            f"[background] full_download: {t_full_done - t_full_start:.3f}s  "
            f"total_since_trigger: {t_full_done - t0:.3f}s  "
            f"{image.width}x{image.height}  {len(image.data) / 1024:.0f} KB  -> {full_path.name}"
        )

    # A single-shot CLI has nothing else useful to do while this runs, so we
    # join it here -- but the point being demonstrated is that a caller (e.g.
    # the web app emitting preview_ready) is free NOT to join.
    thread = threading.Thread(target=_background_full, daemon=True)
    thread.start()
    thread.join()


def cmd_capture_full(args: argparse.Namespace) -> None:
    backend = GphotoBackend(jpeg_size=args.size)
    backend.connect()
    try:
        _do_capture_full(backend, Path(args.out))
    finally:
        backend.disconnect()


def cmd_capture_preview_then_full(args: argparse.Namespace) -> None:
    backend = GphotoBackend(jpeg_size=args.size)
    backend.connect()
    try:
        _do_capture_preview_then_full(backend, Path(args.out))
    finally:
        backend.disconnect()


def cmd_reconnect_test(args: argparse.Namespace) -> None:
    """T-C8: unplug the camera mid-session, measure detection + recovery time.

    Interactive by design — this is a physical fault-injection test, not
    something we can script the trigger for. Two phases: wait for a real
    capture failure (you unplug the cable), then repeatedly call
    backend.reconnect() until a real capture succeeds again (you replug).
    """
    out_dir = Path(args.out)
    backend = GphotoBackend(jpeg_size=args.size)
    backend.connect()
    print("Connected. Capturing a baseline shot to confirm the session is healthy...")
    _do_capture_full(backend, out_dir)

    print()
    print("=" * 60)
    print(f"Now UNPLUG the camera's USB cable. Watching for up to {args.detect_timeout:.0f}s.")
    print("=" * 60)

    t_fail_detected = None
    deadline = time.monotonic() + args.detect_timeout
    while time.monotonic() < deadline:
        try:
            backend.trigger_capture()
            time.sleep(1.0)
        except CameraError as exc:
            t_fail_detected = time.monotonic()
            print(f"Disconnect detected: {exc}")
            break

    if t_fail_detected is None:
        print("No disconnect detected within the timeout -- did you unplug it?")
        backend.disconnect()
        return

    print()
    print("=" * 60)
    print(f"Now REPLUG the camera's USB cable. Retrying every {args.retry_interval:.0f}s.")
    print("=" * 60)

    reconnect_deadline = time.monotonic() + args.reconnect_timeout
    attempt = 0
    t_recovered = None
    while time.monotonic() < reconnect_deadline:
        attempt += 1
        try:
            backend.reconnect()
            _do_capture_full(backend, out_dir)  # verify with a real capture, not just connect()
            t_recovered = time.monotonic()
            break
        except CameraError as exc:
            print(f"  attempt {attempt}: still failing ({exc})")
            time.sleep(args.retry_interval)

    if t_recovered is None:
        print(f"FAILED to recover within {args.reconnect_timeout:.0f}s ({attempt} attempts)")
    else:
        downtime = t_recovered - t_fail_detected
        print(f"RECOVERED after {downtime:.1f}s downtime ({attempt} reconnect attempt(s))")

    backend.disconnect()


def cmd_soak(args: argparse.Namespace) -> None:
    backend = GphotoBackend(jpeg_size=args.size)
    backend.connect()
    out_dir = Path(args.out)
    capture_fn = (
        _do_capture_preview_then_full if args.mode == "preview-then-full" else _do_capture_full
    )
    try:
        for i in range(1, args.shots + 1):
            print(f"--- shot {i}/{args.shots} ---")
            try:
                capture_fn(backend, out_dir)
            except CameraError as exc:
                print(f"FAILED: {exc}")
            if args.interval:
                time.sleep(args.interval)
    finally:
        backend.disconnect()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("detect", help="list cameras gphoto2 can see")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("summary", help="connect, print status, disconnect")
    p.add_argument("--size", choices=["S", "M", "L"], default="S")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("autofocus", help="trigger AF only, no capture")
    p.add_argument("--size", choices=["S", "M", "L"], default="S")
    p.set_defaults(func=cmd_autofocus)

    p = sub.add_parser("capture-full", help="trigger + download full image directly")
    p.add_argument("--size", choices=["S", "M", "L"], default="S")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.set_defaults(func=cmd_capture_full)

    p = sub.add_parser(
        "capture-preview-then-full",
        help="trigger + download preview, then full in a background thread",
    )
    p.add_argument("--size", choices=["S", "M", "L"], default="S")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.set_defaults(func=cmd_capture_preview_then_full)

    p = sub.add_parser(
        "reconnect-test",
        help="T-C8: unplug/replug the camera mid-session, measure detection + recovery time",
    )
    p.add_argument("--size", choices=["S", "M", "L"], default="S")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument(
        "--detect-timeout", type=float, default=30.0, help="seconds to wait for the unplug"
    )
    p.add_argument(
        "--reconnect-timeout", type=float, default=60.0, help="seconds to wait for recovery"
    )
    p.add_argument(
        "--retry-interval", type=float, default=2.0, help="seconds between reconnect attempts"
    )
    p.set_defaults(func=cmd_reconnect_test)

    p = sub.add_parser("soak", help="repeat a capture mode N times on one persistent session")
    p.add_argument("--shots", type=int, default=10)
    p.add_argument("--size", choices=["S", "M", "L"], default="S")
    p.add_argument("--mode", choices=["full", "preview-then-full"], default="full")
    p.add_argument("--interval", type=float, default=2.0, help="seconds between shots")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.set_defaults(func=cmd_soak)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
