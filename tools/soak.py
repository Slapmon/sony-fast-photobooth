#!/usr/bin/env python3
"""Multi-hour randomized-guest-flow soak harness (IMPLEMENTATION_PLAN.md
T-5.3). Unlike `tools/cam_test.py` (which drives `GphotoBackend` directly,
hardware-only, single process), this drives a *running FastAPI app instance*
over its real HTTP surface the way a kiosk browser would — arm, capture,
dismiss, repeat, with randomized think-time between guest actions rather
than a tight loop.

Point it at any already-running `photobooth.web.app:app` instance, dev or
Pi, mock backend or real hardware:

    just dev                                    # terminal 1
    python tools/soak.py --minutes 5             # terminal 2

## Fault-injection compatibility

This tool does not inject faults itself. It is meant to run *against* an app
instance that was started with `MockCameraConfig`'s fault knobs turned on in
its config profile (`disconnect_every_n`, `download_timeout_pct`,
`slow_download_pct` — see `config/dev.yaml` and `config/models.py`'s
`MockCameraConfig`), which is the actual mechanism by which "soak + fault
injection" combines: the operator configures and starts the app with faults
enabled, then points this tool at it. When a capture fails under those
conditions, this tool logs the failure and its reason, confirms the app's
session state machine recovered to a sane post-failure state (per
IMPLEMENTATION_PLAN.md T-1.8's `CAPTURING -> IDLE` edge for a failed
capture), and continues the loop rather than crashing or wedging.

## What was and wasn't verified for this task

Sanity-checked with a short live run against `just dev` (mock backend, no
faults) for a few iterations via `--minutes` set very low — see this task's
report for exactly what that run showed. Not verified: a multi-hour run, a
real fault-injection run against a live app instance, or anything against
real camera hardware on the Pi.
"""

from __future__ import annotations

import argparse
import json
import random
import signal
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent / "out" / "soak"


@dataclass
class IterationResult:
    ok: bool
    reason: str | None
    duration_s: float


@dataclass
class SoakStats:
    started_at: float = field(default_factory=time.time)
    iterations: int = 0
    successes: int = 0
    failures: int = 0
    failure_reasons: dict[str, int] = field(default_factory=dict)
    iteration_durations_s: list[float] = field(default_factory=list)

    def record(self, result: IterationResult) -> None:
        self.iterations += 1
        self.iteration_durations_s.append(result.duration_s)
        if result.ok:
            self.successes += 1
        else:
            self.failures += 1
            reason = result.reason or "unknown"
            self.failure_reasons[reason] = self.failure_reasons.get(reason, 0) + 1

    def elapsed_s(self) -> float:
        return time.time() - self.started_at

    def timing_stats(self) -> dict[str, float]:
        durations = self.iteration_durations_s
        if not durations:
            return {}
        sorted_durations = sorted(durations)
        return {
            "min_s": sorted_durations[0],
            "max_s": sorted_durations[-1],
            "mean_s": statistics.fmean(sorted_durations),
            "median_s": statistics.median(sorted_durations),
        }

    def summary(self) -> dict[str, object]:
        timing = self.timing_stats()
        return {
            "elapsed_s": self.elapsed_s(),
            "iterations": self.iterations,
            "successes": self.successes,
            "failures": self.failures,
            "failure_rate": (self.failures / self.iterations) if self.iterations else 0.0,
            "failure_reasons": dict(self.failure_reasons),
            "iteration_timing": timing,
        }

    def print_report(self, label: str) -> None:
        s = self.summary()
        print(
            f"[{label}] elapsed={s['elapsed_s']:.0f}s "
            f"iterations={s['iterations']} ok={s['successes']} fail={s['failures']} "
            f"fail_rate={s['failure_rate']:.1%}",
            flush=True,
        )
        if self.failure_reasons:
            print(f"  failure reasons: {self.failure_reasons}", flush=True)
        t = self.timing_stats()
        if t:
            print(
                f"  iteration timing: min={t['min_s']:.2f}s max={t['max_s']:.2f}s "
                f"mean={t['mean_s']:.2f}s median={t['median_s']:.2f}s",
                flush=True,
            )


def _run_one_guest_flow(client: httpx.Client, rng: random.Random) -> IterationResult:
    """One simulated guest interaction: arm, capture, dismiss — with
    randomized think-time between the *guest-visible* actions (not the
    server-side wait inside capture, which is the real countdown/download
    time and shouldn't be shortened or padded).

    `POST /session/capture` (see web/routers/kiosk.py) already blocks until
    every shot in the session's active template finishes downloading (or
    raises), so there is no separate polling step needed to know when a
    capture is "done" — the response itself is the completion signal. A WS
    connection to observe the intermediate CountdownStarted/PreviewReady
    events was considered but is not needed for this tool's purpose (driving
    load + verifying end-state), so it's left out to keep this a plain HTTP
    client with no extra dependency.
    """
    t0 = time.monotonic()
    try:
        arm_resp = client.post("/session/arm")
        arm_resp.raise_for_status()
    except httpx.HTTPError as exc:
        return IterationResult(ok=False, reason=f"arm_failed: {exc}", duration_s=_since(t0))

    _think(rng)

    try:
        capture_resp = client.post("/session/capture")
    except httpx.HTTPError as exc:
        return IterationResult(
            ok=False, reason=f"capture_request_failed: {exc}", duration_s=_since(t0)
        )

    if capture_resp.status_code == 502:
        # CameraDisconnectedError/CameraError surfaced as 502 (kiosk.py) —
        # exactly the fault-injection path this tool is meant to tolerate.
        # SessionManager.capture() already transitioned state -> IDLE on
        # this path (web/session.py), so no dismiss() call is needed or
        # valid here; just confirm the app agrees it's back to idle.
        detail = _safe_json(capture_resp).get("detail", "capture failed")
        recovered = _verify_recovered_to_idle(client)
        reason = f"capture_failed: {detail}" + ("" if recovered else " [DID NOT RECOVER TO IDLE]")
        return IterationResult(ok=False, reason=reason, duration_s=_since(t0))

    if capture_resp.status_code != 200:
        detail = _safe_json(capture_resp).get("detail", capture_resp.text[:200])
        return IterationResult(
            ok=False,
            reason=f"capture_unexpected_status_{capture_resp.status_code}: {detail}",
            duration_s=_since(t0),
        )

    _think(rng)

    try:
        dismiss_resp = client.post("/session/dismiss")
        dismiss_resp.raise_for_status()
    except httpx.HTTPError as exc:
        return IterationResult(ok=False, reason=f"dismiss_failed: {exc}", duration_s=_since(t0))

    return IterationResult(ok=True, reason=None, duration_s=_since(t0))


def _verify_recovered_to_idle(client: httpx.Client, timeout_s: float = 5.0) -> bool:
    """Poll GET /debug/health's camera_idle line (web/routers/debug.py) — it
    reports the session state machine's own value, so this is checking the
    exact IMPLEMENTATION_PLAN.md T-1.8 `CAPTURING -> IDLE` edge actually
    happened, not just guessing.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            resp = client.get("/debug/health")
            resp.raise_for_status()
            checks = resp.json()
        except httpx.HTTPError:
            time.sleep(0.2)
            continue
        for check in checks:
            if check.get("name") == "camera_idle" and "idle" in str(check.get("detail", "")):
                return True
        time.sleep(0.2)
    return False


def _think(rng: random.Random, min_s: float = 1.0, max_s: float = 8.0) -> None:
    time.sleep(rng.uniform(min_s, max_s))


def _since(t0: float) -> float:
    return time.monotonic() - t0


def _safe_json(resp: httpx.Response) -> dict[str, object]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _fetch_timings(client: httpx.Client) -> dict[str, object]:
    """Pull the app's own /debug/timings (already built, T-1.2) rather than
    reimplementing timing capture in this tool — see module docstring.
    """
    try:
        resp = client.get("/debug/timings")
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except httpx.HTTPError:
        return {}


class _StopRequested(Exception):
    pass


def _install_sigint_handler() -> None:
    def _handler(signum: int, frame: FrameType | None) -> None:
        raise _StopRequested

    signal.signal(signal.SIGINT, _handler)


def _should_stop(
    stats: SoakStats,
    deadline: float | None,
    max_shots: int | None,
) -> bool:
    if deadline is not None and time.time() >= deadline:
        return True
    return max_shots is not None and stats.iterations >= max_shots


def run_soak(args: argparse.Namespace) -> SoakStats:
    rng = random.Random(args.seed)
    stats = SoakStats()

    deadline: float | None = None
    if args.hours is not None:
        deadline = time.time() + args.hours * 3600
    elif args.minutes is not None:
        deadline = time.time() + args.minutes * 60

    _install_sigint_handler()

    print(
        f"soak: base_url={args.base_url} "
        f"duration={'unbounded' if deadline is None else f'{(deadline - time.time()):.0f}s'} "
        f"max_shots={args.shots} think=[{args.min_think_s},{args.max_think_s}]s",
        flush=True,
    )

    with httpx.Client(base_url=args.base_url, timeout=args.request_timeout_s) as client:
        try:
            while not _should_stop(stats, deadline, args.shots):
                result = _run_one_guest_flow(client, rng)
                stats.record(result)
                if not result.ok:
                    print(f"  iteration {stats.iterations} FAILED: {result.reason}", flush=True)
                if stats.iterations % args.report_every == 0:
                    stats.print_report("progress")
                _think(rng, args.min_think_s, args.max_think_s)
        except _StopRequested:
            print("\nsoak: Ctrl+C received, finishing current summary...", flush=True)

        stats.print_report("final")
        timings = _fetch_timings(client)

    _write_summary(args.out_dir, stats, timings, args.base_url)
    return stats


def _write_summary(
    out_dir: Path, stats: SoakStats, timings: dict[str, object], base_url: str
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{ts}.json"
    payload = {
        "base_url": base_url,
        "summary": stats.summary(),
        "app_timings": timings,
    }
    path.write_text(json.dumps(payload, indent=2))
    print(f"soak: summary written to {path}", flush=True)
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"running app URL (default: {DEFAULT_BASE_URL})",
    )
    duration = parser.add_mutually_exclusive_group()
    duration.add_argument("--hours", type=float, default=None, help="run for this many hours")
    duration.add_argument("--minutes", type=float, default=None, help="run for this many minutes")
    parser.add_argument(
        "--shots",
        type=int,
        default=None,
        help="stop after this many guest-flow iterations (combinable with --hours/--minutes; "
        "whichever limit is hit first wins). With neither a duration nor --shots given, runs "
        "until Ctrl+C.",
    )
    parser.add_argument(
        "--min-think-s",
        type=float,
        default=1.0,
        help="min randomized think-time between guest actions",
    )
    parser.add_argument(
        "--max-think-s",
        type=float,
        default=8.0,
        help="max randomized think-time between guest actions",
    )
    parser.add_argument(
        "--report-every", type=int, default=10, help="print a running summary every N iterations"
    )
    parser.add_argument(
        "--request-timeout-s",
        type=float,
        default=30.0,
        help="per-HTTP-request timeout (a capture can legitimately take several seconds)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="where to write the final summary JSON",
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="RNG seed for reproducible think-times"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.min_think_s > args.max_think_s:
        parser.error("--min-think-s must be <= --max-think-s")
    stats = run_soak(args)
    return 1 if stats.failures and stats.iterations and stats.failures == stats.iterations else 0


if __name__ == "__main__":
    sys.exit(main())
