# Photobooth — Implementation Plan

> Living document. Check boxes as we go. Update the "Measured" tables with real numbers as they come in — they drive decisions later in the plan.

**Companion doc:** `photobooth-plan.md` (goals, product scope, hardware, legal)

---

## 0. Correction to the earlier plan: preview via go2rtc

The previous doc said "read V4L2 MJPEG directly, no ffmpeg." Your old debugging session shows why that's wrong for *your* capture stick:

- The stick's MJPEG mode throws `No JPEG data found` under raw ffmpeg, but survives through go2rtc.
- `/dev/video1` allows **exactly one consumer**. Any component that opens it directly (app, ffmpeg, browser) blocks all others. You already hit this repeatedly.

**Decision: go2rtc owns `/dev/video1`. Nothing else ever touches it.**

The app consumes `http://127.0.0.1:1984/api/stream.mjpeg?src=photobooth` and re-serves it to the frontend. This gives us the single-owner property for free, plus fan-out (kiosk + admin preview simultaneously), plus a proven-working config.

Config we start from — the one you already got working, MJPEG passthrough with `copy`:

```yaml
streams:
  photobooth: exec:ffmpeg -hide_banner -v error -f v4l2 -input_format mjpeg
    -video_size 1280x720 -framerate 15 -i /dev/video1 -c:v copy -f mjpeg -#killsignal=2
```

Known fallback ladder if artifacts return (from your notes): drop to 640×480 → transcode YUYV→MJPEG instead of `copy` (costs CPU, cleaner output). Task T-P3 below benchmarks all three.

---

## 1. Stack

| Concern | Choice |
|---|---|
| Language / tooling | Python 3.12, `uv` for deps and venv |
| Web | FastAPI + uvicorn, WebSocket for events |
| Frontend | Svelte 5 + Vite + TypeScript |
| Camera | `python-gphoto2` in an isolated worker process |
| IPC | UNIX socket, length-prefixed `msgspec` frames |
| Images | `pyvips` (libvips) |
| DB | SQLite, WAL, plain `sqlite3` + thin repo layer (no ORM) |
| Preview | go2rtc (system service) → MJPEG proxy in app |
| Printing | CUPS via `pycups` |
| Logging | `structlog`, JSON lines |
| Tests | `pytest`, `pytest-asyncio`, Playwright for UI |
| Quality | `ruff`, `mypy --strict` on `core/` and `camera/` |
| Deploy | systemd units + `just` recipes |

---

## 2. Project layout

```
photobooth/
├── pyproject.toml
├── justfile
├── src/photobooth/
│   ├── config/          # pydantic models, profile loading, event configs
│   ├── core/            # state machine, event bus, domain types
│   ├── camera/
│   │   ├── protocol.py  # CameraBackend ABC + IPC message types
│   │   ├── worker.py    # the isolated process entrypoint
│   │   ├── gphoto.py    # real backend
│   │   ├── mock.py      # fixture-driven backend w/ fault injection
│   │   └── client.py    # async client used by the app
│   ├── preview/         # go2rtc supervision + MJPEG proxy
│   ├── pipeline/        # template engine, variant rendering (pyvips)
│   ├── delivery/        # upload backends (sftp, s3, local)
│   ├── printing/        # cups backend, null backend
│   ├── storage/         # sqlite repos, durable job queues
│   ├── telemetry/       # tracing spans, resource sampler
│   ├── web/             # routers: kiosk, gallery, admin, debug
│   └── cli.py
├── frontend/            # kiosk + gallery + admin (one Vite app, 3 routes)
├── tests/
│   ├── contract/        # runs against EVERY backend impl
│   ├── unit/
│   ├── golden/          # render pipeline reference images
│   └── e2e/             # Playwright
├── tools/
│   ├── bench_camera.py  # Phase 0 spike scripts (standalone, no app deps)
│   ├── bench_render.py
│   ├── bench_preview.py
│   ├── soak.py          # long-running reliability harness
│   └── trace_report.py  # waterfall + percentiles from SQLite
├── fixtures/shots/      # sample JPEGs at S/M/L for the mock backend
├── events/              # per-event config + assets
└── templates/           # layout YAMLs
```

---

## 3. Developing on your machine

The rule that makes this work: **every hardware dependency sits behind an interface with a mock, and the mock is exercised by the same contract test suite as the real thing.**

| Dependency | Real | Dev substitute |
|---|---|---|
| Camera | `GphotoBackend` | `MockBackend` — serves from `fixtures/shots/`, simulates trigger delay, thumb latency, and full-download throughput at a configurable MB/s |
| Preview | go2rtc → capture stick | go2rtc → laptop webcam, **or** a looping video file (`exec:ffmpeg ... -stream_loop -1 -i fixtures/preview.mp4`). Same config shape, same URL. |
| Printer | `CupsBackend` | `NullPrinter` writing PDFs to `out/prints/`, with simulated 13 s job time and injectable "out of media" |
| Upload | `SftpBackend` / `S3Backend` | `LocalDirBackend` + injectable failures |
| GPIO button | real | keyboard shortcut in kiosk UI |

Profiles: `config/dev.yaml` and `config/pi.yaml`. Identical structure, different backend selection. `just dev` runs the full stack locally.

**Honest limit:** dev proves *correctness, flows, and reliability logic*. It cannot prove *latency* — x86 vs ARM and USB behaviour don't transfer. So:

- Calibrate `MockBackend` timings from the real Phase 0 numbers, so dev-mode timings are at least plausible.
- `just bench-pi` runs the identical harness over SSH on the Pi and pulls results back to your machine. **Latency is only ever validated on Pi.**

---

## 4. Instrumentation — built in from commit one

This is the part that stops us from repeating the "it's slow and I don't know why" problem. Not bolted on later.

### 4.1 Trace spans

Every capture gets a `capture_id` (UUID). Every stage opens a span:

```python
with trace.span("ptp.download_full", capture_id, bytes=size):
    ...
```

Spans land in SQLite `spans(capture_id, name, t_start, t_end, meta_json)` and in the JSON log. Mandatory span names:

```
session.start
  capture.trigger          # command sent → shutter confirmed
  ptp.wait_file_added      # shutter → camera reports object
  ptp.download_thumb
  display.encode_thumb
  display.push_ws
  display.browser_decode   # reported back from frontend via WS
  ptp.download_full
  ptp.delete_on_camera
  pipeline.compose
  pipeline.variant.print
  pipeline.variant.web
  pipeline.variant.thumb
  print.submit → print.complete
  upload.attempt
```

### 4.2 Debug endpoints

- `GET /debug/traces` — waterfall view per capture. This is the tool that answers "where did the 10 seconds go."
- `GET /debug/timings` — p50/p95/p99/max per span name over the last N captures
- `GET /debug/health` — camera, preview, printer, disk, queue depths, temp/throttle
- `GET /debug/resources` — CPU, RSS, SoC temp, `vcgencmd get_throttled` sampled at 1 Hz

### 4.3 Resource sampler

Background task sampling CPU/RSS/temp/throttle into SQLite, timestamp-joinable with spans. Catches "the Pi thermally throttled at minute 90 and everything got slow" — which would otherwise look like a mysterious software regression.

### 4.4 Fault injection

Config-driven, honoured by mock backends *and* (where safe) real ones:

```yaml
faults:
  camera.disconnect_every_n: 50
  camera.download_timeout_pct: 2
  camera.slow_download_pct: 5
  printer.offline_after_n: 100
  upload.fail_pct: 30
  disk.simulate_full_at_pct: 95
```

### 4.5 Contract tests

`tests/contract/test_camera_backend.py` runs against `MockBackend` and, when `--hardware` is passed, `GphotoBackend`. Same assertions. This is what makes the mock trustworthy — if it drifts from real behaviour, the contract suite catches it.

---

## 5. The two-stage capture design

You want to move off JPEG size S. Agreed — but one correction to the mental model, because it changes the design:

**There is one USB pipe and one PTP session. Thumb and full-res downloads are strictly serial — they cannot run in parallel.** What we're actually overlapping is *USB transfer* against *CPU work, UI time, and the next countdown*. That's still a large win, and it's what makes bigger files viable.

### Per-shot sequence

```
[worker]  trigger_capture()
[worker]  wait for GP_EVENT_FILE_ADDED          ──► capture.trigger, ptp.wait_file_added
[worker]  file_get(GP_FILE_TYPE_PREVIEW)        ──► ptp.download_thumb
[app]     emit preview_ready ─────────────────────► SCREEN SHOWS IMAGE  ⏱ budget ends here
[worker]  file_get(GP_FILE_TYPE_NORMAL)         ──► ptp.download_full   (background)
[worker]  delete_file() if Save Dest = Card+PC
[app]     emit full_ready → pipeline → print / upload / archive
```

### Collage overlap

```
shot 1: trigger ─ thumb ─┬─ full download ──────────┐
                          └─ UI: show + countdown 2 ─┴─ shot 2: trigger ...
```

**Critical guard:** the next shutter must not fire while a download is in flight on the same handle. The state machine gates the end of the countdown on `camera.idle`. If the download overruns, the countdown holds at "1" for a moment rather than desyncing. Never queue a trigger behind a download.

This gives the **size ceiling formula** we'll measure in Phase 0:

```
max_jpeg_size = largest size where  ptp.download_full  <  countdown_duration − 1.0s safety
```

With a 5 s countdown and a measured PTP rate, that tells you directly whether M (4240×2832) or L (6000×4000) is reachable. Single-shot mode is more forgiving than collage since there's a review screen absorbing the time.

### Fallbacks if `GP_FILE_TYPE_PREVIEW` isn't supported on the a6400

1. Full download + **shrink-on-load** (libvips DCT scaling — decodes 6000×4000 → 1500×1000 at a fraction of full-decode cost). Simple, always works, but puts the full transfer on the critical path.
2. **RAW+JPEG, Save Dest: Card+PC.** gphoto pulls only the M-size JPEG (fast, good enough for print and web); RAW stays on the card as the high-res archive, ingested after the event. This is the option that best serves "I want bigger files" — worth benchmarking even if the thumb path works.
3. Grab the last go2rtc frame pre-flash as an instant placeholder, swap when the real image lands. Cheap trick, exposure won't match the flash. Last resort.

---

## 6. Phase 0 — hardware spike (do this first)

Standalone scripts in `tools/`. No application code. **Exit gate for starting Phase 1.**

### Camera — `tools/bench_camera.py`

- [ ] **T-C1** Baseline: `gphoto2 --capture-image-and-download` CLI, 10 shots, wall time each
- [x] **T-C2** Persistent `python-gphoto2` session, per-stage timing at size **S / M / L** — done via `tools/cam_test.py capture-full` / `soak`. L got a real n=10 sample; S/M are single-shot only so far (all three already sit far under budget — rerun with n=10+ each only if tightening confidence matters later)
- [x] **T-C3** Does `GP_FILE_TYPE_PREVIEW` work on the a6400? **No.** `download_preview()` returns nothing — confirmed via `tools/cam_test.py capture-preview-then-full`, which falls back to a direct full download cleanly. Moot anyway: full download is only ~13–50ms, faster than a preview round-trip would be.
- [ ] **T-C4** `trigger_capture` + `wait_for_event` vs `capture()` — measure both (only the `trigger_capture`+`wait_for_event` path measured so far)
- [x] **T-C5** PTP throughput per JPEG size → **no meaningful size ceiling on this body.** Download time barely grows with size (44ms → 48ms from S to L) and total shutter→downloadable stays under 1.1s worst-case even at L — see Measured table. MB/s not reported: at 5–50ms transfer times the number is dominated by fixed per-request overhead, not sustained throughput, so it isn't a meaningful ceiling estimate.
- [ ] **T-C6** RAW+JPEG with `Save Dest: Card+PC` — is only the JPEG pulled? How fast?
- [ ] **T-C7** Effect of `Still Img. Save Dest: PC Only` vs `Card+PC` on latency (currently running with `Save Dest: sdram`, the PC-only-equivalent setting — not yet compared against `card+sdram`)
- [x] **T-C8** Reconnect: unplug USB mid-session, measure recovery time. **~9.7s** full recovery via a plain "wait 2s, retry `reconnect()`" loop (3 attempts: disconnect raised `[-7] I/O problem`, two retries hit `[-105] Unknown model` while USB re-enumerated, third succeeded) — verified with a real capture afterward, not just a successful `connect()`. `uhubctl` power-cycle not tested (not installed; not needed given the plain retry loop already recovers this fast). Fault-injection caught a real bug in our own code along the way: `GphotoBackend.trigger_capture()` only wrapped the initial `trigger_capture()` call, not the `wait_for_event()` loop after it — a disconnect during the wait leaked a raw `gphoto2.GPhoto2Error` instead of our `CameraDisconnectedError`. Fixed (also hardened `connect()` and `download_full()` the same way). Separately noticed: restarting the test script right after a crash (one that skipped `disconnect()`/`camera.exit()`) caused one flaky `capture()` timeout on the very next attempt before recovering on its own — worth keeping in mind for the camera-worker's own crash-restart path in Phase 1 (T-1.6), though not reproduced as a hard failure.

**Measured** (2026-09-01, real a6400 via native `libgphoto2` on the Pi — Debian 13/aarch64, `libgphoto2` 2.5.31, `python-gphoto2` 2.6.4, manual mode, 1/4000s, f/3.5, ISO Auto — see note below):

| Test | S (3008×2000) | M (4240×2832) | L (6000×4000, n=10) |
|---|---|---|---|
| trigger → file added | 0.79s | 0.78s | avg 0.96s (min 0.93 / max 1.05) |
| full download | 0.04s | 0.04s | avg 0.013s (min 0.005 / max 0.048) |
| **total shutter → downloadable** | 0.84s | 0.83s | avg 0.975s (min 0.93 / max 1.07) |
| file size | 1834 KB | 1980 KB | avg ~3.5 MB |

All comfortably under the 3.0s exit criterion, with low run-to-run variance at L (~130ms spread over 10 shots) and zero PTP session drops or errors across the run.

**Where the ~1s actually goes** (debug-traced one L-size capture at the PTP event level via `gp_log_add_func` + per-event timestamps): the trigger round-trip itself is only ~40ms. The dominant cost is a ~800ms gap between the camera's "capture started" PTP event and it reporting anything else at all — no events, no data, nothing our `wait_for_event` loop could act on sooner. Confirmed this is *not* an artifact of our polling (each wait call returned well inside its window, not cut short) and *not* slow exposure (shutter speed was already locked to 1/4000s in manual mode — checked directly). This is in-camera JPEG encode + object-buffer time: a hardware/firmware floor for the a6400 over PTP, matching the "Sony's PTP stack is not one of the fast ones here" diagnosis in §2. It doesn't threaten the budget — it's just where the (still comfortable) ~1s goes.

**Outstanding from the preflight checklist (§3.4):** ISO is currently `Auto` — flagged there as something guests will notice shifting shot to shot. Not yet changed; do this before Phase 1 sign-off.

### Render — `tools/bench_render.py`

- [ ] **T-R1** pyvips: decode+resize to 1920px display variant, per source size
- [ ] **T-R2** Shrink-on-load vs full decode — quantify the difference
- [ ] **T-R3** Full 2×2 collage composite, border PNG + text, at 300 dpi → 1795×1205
- [ ] **T-R4** All three variants (print/web/thumb) end to end
- [ ] **T-R5** Peak RSS during T-R3 (watch out on an 8 GB Pi with 4 source images in flight)

### Preview — `tools/bench_preview.py`

- [x] **T-P1** go2rtc MJPEG `copy` at 1280×720 — pipeline confirmed working end-to-end (capture stick → go2rtc `exec:` ffmpeg passthrough → HTTP `/api/stream.mjpeg` consumer → real frames pulled and visually verified). Stick shows a brief color-bar test pattern for the first moment after signal lock — cosmetic, not a bug. **Not yet done: the full 30 min soak counting dropped/corrupt frames** — only a short (~8s) sample tested so far.
- [ ] **T-P2** Same at 640×480
- [ ] **T-P3** YUYV → MJPEG transcode — CPU cost and artifact rate vs `copy` — moot for now: confirmed via `v4l2-ctl --list-formats-ext` that the stick supports real MJPEG up to 1920×1080@60fps (YUYV on this stick tops out at 5fps at 1080p, exactly the bottleneck §4 warned about) — no reason to consider the transcode fallback unless `copy` misbehaves in the 30 min soak
- [ ] **T-P4** CPU cost of the app proxying go2rtc's MJPEG to a browser
- [ ] **T-P5** Latency: physical motion → pixels on screen
- [ ] **T-P6** HDMI blanking behaviour during capture — how many frames, does go2rtc recover cleanly?

**Note:** thin black bars visible on both sides of the captured frame — some aspect-ratio letterboxing between the camera's HDMI output and the requested 1280×720. Not investigated yet; revisit when doing kiosk UI layout (T-1.11).

### Contention — the test specific to your build

- [x] **T-X1** **Does preview streaming degrade PTP download speed?** No measurable effect. 10-shot L-size soaks compared: idle-preview baseline avg total 1.082s vs preview-actively-streaming avg total 1.067s — well within shot-to-shot noise (~30–80ms) for both runs, distributions fully overlap. Tested at 1280×720@15fps MJPEG preview bitrate; not re-tested at higher preview resolutions.
- [ ] **T-X2** Repeat with camera on USB2 port + stick on USB3 port vs both on USB2
- [ ] **T-X3** Godox recycle time at working power — does it keep up with a 4-shot collage? Check for underexposed frames.

### Soak

- [x] **T-S1** 300 consecutive captures via `tools/cam_test.py soak --shots 300 --size L --interval 1.0`. **300/300 succeeded, zero failures, zero disconnects.** trigger→file avg 1.044s (min 1.012 / max 1.353), total avg 1.068s (min 1.026 / max 1.377). No drift: first-10 avg 1.097s vs last-10 avg 1.069s — slightly faster if anything. `vcgencmd get_throttled` = `0x0` (no throttling at all), temp 43.8°C post-run. **Gap:** RSS memory wasn't sampled during the run, so memory growth isn't directly measured — 300 clean shots with stable timing is reassuring but not conclusive against a slow leak.

**Exit criteria:**
- [x] Shutter → displayable image **< 3.0 s** measured — max 1.377s across 300 shots at L, well inside budget
- [x] A decided JPEG size with a documented full-download time — L, ~1.07s avg total shutter→downloadable
- [ ] Preview stable for 30 min with acceptable artifacts — pipeline verified working (T-P1), but only ~8s sampled so far, not the full 30 min soak
- [x] Known, working camera reconnect procedure — ~9.7s auto-recovery via plain retry loop (T-C8)

---

## 7. Phase 1 — core capture loop

- [x] **T-1.1** Project skeleton, `uv`, ruff/mypy, justfile, CI
- [x] **T-1.2** Telemetry module — spans + SQLite sink (`telemetry/spans.py`, called from `web/session.py`), plus `GET /debug/traces` (per-capture waterfall) and `GET /debug/timings` (p50/p95/p99/max per span name) — `web/routers/debug.py`, live-verified against real capture data
- [x] **T-1.3** `CameraBackend` protocol + IPC message types — `camera/messages.py`, msgspec tagged unions over length-prefixed frames, TCP loopback rather than a literal UNIX socket (this dev machine's Python has no `socket.AF_UNIX`; TCP loopback behaves identically on the Pi, so no platform branching)
- [x] **T-1.4** `MockBackend` + fixtures + fault injection — camera fault knobs built directly into `MockBackend` and threaded through `MockCameraConfig`/`worker.py`'s CLI/`app.py`'s subprocess args: `disconnect_every_n` (simulates the Sony PTP-session-drop risk), `download_timeout_pct`, `slow_download_pct` (simulate flaky USB transfers). Printer/upload fault knobs from §4.4's sample not built — those backends don't exist yet (Phase 4)
- [x] **T-1.5** Contract test suite (green against mock) — still passing, now also exercised transitively through the worker's request/response mapping
- [x] **T-1.6** Camera worker process + async client — `camera/worker.py` (TCP server, single-threaded, one client at a time) + `camera/client.py` (asyncio, lock-serialized). "Restart" scoped narrowly per plan: the client reports "can't reach the worker" as `CameraDisconnectedError` rather than respawning it itself — actual process supervision (systemd `Restart=always`) is Phase 5 (T-5.1)
- [x] **T-1.7** `GphotoBackend` — implemented and validated against real a6400 hardware on the Pi throughout Phase 0 (300-shot soak, reconnect test, contention test all passed). `tests/contract/test_camera_backend.py` now parametrizes over `mock`/`gphoto`, the latter marked `hardware` (skipped by default via `pyproject.toml`'s existing `addopts = "-m 'not hardware'"`; run explicitly with `pytest -m hardware` on the Pi) — not yet actually run on the Pi through this specific suite, hardware validation so far was via `tools/cam_test.py`
- [x] **T-1.8** Session state machine: `IDLE → ARMED → COUNTDOWN → CAPTURING → REVIEW → PROCESSING → IDLE` — plus one addition beyond the original diagram: a direct `CAPTURING → IDLE` edge for a failed capture (a guest never "reviews" a camera error)
- [x] **T-1.9** WebSocket event bus, typed events — `core/events.py` (msgspec, JSON over the wire), `web/session.py`'s `SessionManager`, `web/routers/kiosk.py`'s `/ws` route
- [x] **T-1.10** MJPEG proxy endpoint — `preview/proxy.py` + `web/routers/preview.py`, relays go2rtc's stream verbatim (no decode/re-encode) with the real upstream `Content-Type`/boundary. "go2rtc supervision" scoped out: go2rtc already runs as its own systemd service on the Pi (confirmed working in Phase 0), so this app doesn't start/stop/monitor the go2rtc process itself, only proxies its stream
- [x] **T-1.11** Minimal kiosk UI: start → countdown over live preview → review — `frontend/src/routes/Kiosk.svelte`, driven entirely by the WS event stream, live preview via `<img src="/preview/stream">`. Ugly-but-functional, as specified
- [x] **T-1.12** Frontend reports `browser_decode` timing back over WS — the `/ws` route now parses inbound `{"type": "browser_decode", capture_id, duration_ms}` messages (`web/routers/kiosk.py`), `SessionManager.record_browser_decode()` records it as a `display.browser_decode` span via a new `telemetry.spans.record_duration()` helper (for durations measured elsewhere, not wrapped in `span()`'s context manager); `Kiosk.svelte`'s review `<img onload>` sends it
- [x] **T-1.13** SQLite schema + repos: sessions, captures — `storage/repos.py` (`SessionRepo`, `CaptureRepo`), wired into every state transition and capture in `SessionManager`. `spans` repo not separately wrapped (used directly via `telemetry/spans.py`, which was already a thin enough wrapper)
- [x] **T-1.14** `just bench-pi` — the base-scaffold recipe was stale (pointed at the never-implemented `tools/bench_camera.py` stub, assumed `uv` on the Pi, which was never installed there). Rewritten to run the real `tools/cam_test.py` over SSH; also added `just pull-pi-captures`. Both recipes' underlying commands verified live against the real Pi (`admin@192.168.188.44`)

**Done when:** single photo, end to end, on the Pi, with a waterfall showing every millisecond. **Status: 14/14 done.** Full capture loop (arm → countdown → capture → real JPEG served → review → dismiss) verified end to end on the dev laptop against the mock backend, with `/debug/traces`/`/debug/timings` verified live against real capture data. Real capture *timing* was independently proven on the Pi in Phase 0 (§6) via `tools/cam_test.py`. One honest gap for a future session: the FastAPI app itself (not just `tools/cam_test.py`) hasn't been run end to end on the Pi against the real `GphotoBackend` yet — only against `MockBackend` here on the laptop, and against `GphotoBackend` directly (not through the app) on the Pi.

---

## 8. Phase 2 — pipeline & templates

- [ ] **T-2.1** Template YAML schema + pydantic models + load-time validation
- [ ] **T-2.2** pyvips compositor: slots, fit modes, overlay PNGs, text with custom fonts
- [ ] **T-2.3** Variant rendering (print / web / thumb) with per-variant settings
- [ ] **T-2.4** Golden-image tests with perceptual diff tolerance
- [ ] **T-2.5** Collage modes driven by templates (2×2, 1+2, 3-strip)
- [ ] **T-2.6** Countdown gating on `camera.idle` + overlap verification in collage mode
- [ ] **T-2.7** `{event.*}` placeholder resolution
- [ ] **T-2.8** Render worker pool, bounded concurrency (don't let 4 collages fight for 4 cores)

---

## 9. Phase 3 — pages

### Landing / idle
- [ ] **T-3.1** Attract loop: configurable background image/video, event title, "Touch to start"
- [ ] **T-3.2** Mode selection if more than one capture mode is enabled
- [ ] **T-3.3** Idle timeout returns from any screen

### Gallery
- [ ] **T-3.4** Thumbnail grid, lazy loading, tap to enlarge
- [ ] **T-3.5** Per-event enable/disable toggle
- [ ] **T-3.6** Server-side gallery for the upload target (separate small app — decide: same codebase or standalone)

### Admin
- [ ] **T-3.7** Auth (PIN or long-press corner + token)
- [ ] **T-3.8** Event switching, event config editor
- [ ] **T-3.9** Template picker with live preview render
- [ ] **T-3.10** Live camera/printer/network/disk status
- [ ] **T-3.11** Test shot · camera reconnect · reprint last · clean shutdown
- [ ] **T-3.12** **Preflight check screen** — every hardware dependency, green/red per line
- [ ] **T-3.13** Timings dashboard (surfaces `/debug/timings` for non-dev use)

---

## 10. Phase 4 — delivery & printing

- [ ] **T-4.1** Durable job queue in SQLite (claim, retry with backoff, dead-letter)
- [ ] **T-4.2** Upload backends: `LocalDir`, `Sftp`, `S3` behind one interface
- [ ] **T-4.3** QR generation, unguessable session tokens
- [ ] **T-4.4** Offline behaviour: queue drains when connectivity returns; QR stays valid
- [ ] **T-4.5** Retention policy enforced in code
- [ ] **T-4.6** `NullPrinter` + `CupsBackend` behind one interface
- [ ] **T-4.7** Print queue, per-session limits, media tracking
- [ ] **T-4.8** Printer status gates the print button
- [ ] **T-4.9** Reprint from admin

---

## 11. Phase 5 — hardening

- [ ] **T-5.1** systemd units, `Restart=always`, watchdog
- [ ] **T-5.2** Log rotation, log level per module
- [ ] **T-5.3** `tools/soak.py` — multi-hour randomized guest flows + fault injection
- [ ] **T-5.4** Fault drill checklist, each with a defined expected behaviour:
  - [ ] Yank camera USB mid-countdown
  - [ ] Yank camera USB mid-download
  - [ ] Camera battery dies
  - [ ] Yank capture stick
  - [ ] Kill go2rtc
  - [ ] Network down for 20 minutes
  - [ ] Printer out of media
  - [ ] Disk 95% full
  - [ ] Hard power cut mid-capture → recovery on boot
  - [ ] `pkill -9` the camera worker
- [ ] **T-5.5** Read-only root / overlayfs or USB SSD boot
- [ ] **T-5.6** Tailscale remote access
- [ ] **T-5.7** Full-day dress rehearsal with real guests

---

## 12. First week — concrete order

1. **Day 1 (Pi):** T-C1, T-C2, T-C3. Three scripts, one afternoon. These decide whether the whole approach holds.
2. **Day 1 evening (laptop):** T-1.1 skeleton + T-1.2 telemetry, so Phase 0 numbers have somewhere to live.
3. **Day 2 (Pi):** T-C5, T-C6, T-X1, T-X2 → **pick your JPEG size**. This unblocks everything downstream.
4. **Day 2–3 (Pi):** T-R1..T-R5, T-P1..T-P3.
5. **Day 3 (Pi, overnight):** T-S1 soak.
6. **Day 4 onward (laptop):** T-1.3 through T-1.9 against the mock, with mock timings calibrated from the real numbers. Only touch the Pi again at T-1.7.

---

## 13. Open decisions

| # | Decision | Blocks | Status |
|---|---|---|---|
| D1 | JPEG size (S / M / L / RAW+JPEG) | Phase 2 | **Resolved: L.** No measurable latency cost vs S/M (§6 T-C5); set as `config/pi.yaml`'s default. RAW+JPEG (T-C6) untested, not needed to unblock this |
| D2 | Thumb-first vs full+shrink-on-load | Phase 1 | **Resolved: full+shrink.** a6400 has no PTP preview support (§6 T-C3) and full download is faster than a preview round-trip would be anyway |
| D3 | go2rtc `copy` vs transcode | Phase 1 | **Leaning `copy`** — pipeline works end-to-end, stick has real MJPEG hardware support (§6 T-P1/T-P3); still want the full 30 min soak before fully closing this out |
| D4 | Upload target (VPS vs S3-compatible) | Phase 4 | Open — build the abstraction first |
| D5 | Server gallery: same codebase or standalone? | Phase 3 | Open |
| D6 | Print sizes: 6×4 only, or strips too? | Phase 2 | Open — affects template schema |
| D7 | Touchscreen model + resolution | Phase 3 | Open — affects kiosk CSS |
