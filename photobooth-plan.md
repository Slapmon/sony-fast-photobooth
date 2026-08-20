# Photobooth Software — Planning Document

**Status:** Planning / pre-implementation
**Hardware baseline:** Raspberry Pi 4 · Sony a6400 (USB 2.0 multi-port) · Godox flash + trigger · clean HDMI → USB capture stick · iPad kiosk · router
**Goal shift:** one-off wedding build → rentable product with optional printing

---

## 1. Goals

### Primary goals

| # | Goal | Success criterion |
|---|---|---|
| G1 | **Cut shutter-to-preview latency** | ≤ 2.5 s from shutter fire to image visible on screen (was 10–20 s) |
| G2 | **Never block the guest** | UI stays responsive during download, processing, print, upload. No spinner longer than 2 s. |
| G3 | **Rentable, unattended operation** | Survives a 6-hour event with zero operator intervention; auto-recovers from camera/printer/network faults |
| G4 | **Printing** | Optional print path via CUPS, ≤ 20 s to physical print, queued not blocking |
| G5 | **Per-event configurability** | New event = one config file + asset folder. No code changes, no rebuild. |

### Secondary goals

- Offline-tolerant: booth works fully with no internet; uploads drain later.
- Content pipeline (borders, text, layouts) shared between print and web output.
- Legally shippable in Germany (GDPR — see §11).

### Explicit non-goals for v1

Keep these out or the project will not ship:

- Video / boomerang / GIF modes
- Green screen / background replacement
- Face detection, beautify filters, AR props
- Multi-camera
- Guest accounts, social sharing integrations
- Cloud-hosted admin panel

---

## 2. Diagnosis — why the old stack was slow

This matters more than the feature list, because it determines the architecture. Best assessment of where your 10–20 s went, roughly in order of magnitude:

**1. Process spawn + camera re-initialization per shot (~1–3 s, sometimes more)**
`photoboothproject` is PHP and shells out to the `gphoto2` CLI. Every invocation re-enumerates USB, loads the camlib, and re-does the PTP handshake. This is a well-documented cost — the libgphoto2 maintainers have confirmed roughly the first second of a `--capture-image-and-download` is pure init, and that keeping the connection alive removes it. On a Pi 4 with a Sony body it's typically worse than on a desktop.

**2. libgphoto2's post-capture object polling (~1–4 s)**
After the shutter fires, the PTP driver polls the camera asking "is there a new object yet?" on a fixed interval. On several bodies this poll interval, not the actual transfer, dominates the wall time — people have measured effective transfer rates of ~8 MB/s where the raw port reads were running at ~75 MB/s. Sony's PTP stack is not one of the fast ones here.

**3. Image processing in PHP/GD or ImageMagick (~2–6 s)**
Full decode → resize → re-encode of a multi-megapixel JPEG on Pi 4 ARM cores. GD is single-threaded and does full-resolution decode even when the output is 1/4 size. ImageMagick is worse on memory.

**4. Delivery to the iPad (~0.5–2 s)**
Serving a large JPEG over HTTP and letting Safari decode it at full resolution, then scale it in the DOM. Nobody pre-sizes for the actual display.

**5. Serialized pipeline**
Capture → download → move → resize → thumbnail → collage → display, all in sequence, all in one PHP request. Nothing overlaps.

**Conclusion:** your instinct is right. gphoto2 is not the problem; *how it's being invoked* is most of the problem, and the rest is naive image processing. A rewrite can realistically get this to 2–3 s without exotic tricks.

---

## 3. Camera control — the key decision

### 3.1 Sony Camera Remote SDK: not available for your body

Worth checking before committing to gphoto2, and the answer is unfortunately no. Sony's official Camera Remote SDK — which they explicitly market at photobooth developers, and which ships ARMv8 Linux binaries — supports these APS-C bodies: **ILCE-6700 only**. The a6400 (ILCE-6400) is **not** on the supported device list.

*Implication:* If you ever replace the body, an **a6700** unlocks the official SDK, which would very likely be faster and dramatically more robust than libgphoto2 for tethered repetitive capture (that's exactly the workload Sony built it for). Worth keeping in the back of your mind as a future hardware option, not a v1 dependency.

For now: **libgphoto2, but used properly.**

### 3.2 Use libgphoto2 as a library, not a CLI

Non-negotiable design rule: **one long-lived process owns the camera handle for the entire session.**

- `python-gphoto2` (SWIG bindings, actively maintained) or the Rust `gphoto2` crate.
- Open the camera once at boot. Never close it between shots.
- The camera handle lives in a **dedicated single-threaded worker process**, not in the web server. libgphoto2 is not safe to call concurrently on one camera, and its calls are blocking — they must never sit inside an async event loop.
- Communication with the rest of the app: a local socket / message queue with a strict command protocol (`ARM`, `CAPTURE`, `GET_STATUS`, `RECONNECT`).

Expected saving: 1–3 s per shot, immediately.

### 3.3 The two-stage download idea

Your proposal — pull a small preview first, full-res in the background — is sound in principle, but I'd flag it as **build the seam, decide with data.**

Do this: make the display path and the archive path two separate consumers of a capture event, so *either* can feed the screen. Then measure.

Then check, in the Phase 0 benchmark:

- Does the a6400 respond to `GP_FILE_TYPE_PREVIEW` (PTP `GetThumb`) at all, and how fast? Sony's PTP support is inconsistent here. If it returns a ~1616×1080 embedded JPEG in <300 ms, you win big.
- **How fast is a small full JPEG anyway?** Set the camera to **JPEG size S (3008×2000, ~6 MP)** — plenty for a 6×4 print at 300 dpi (1800×1200) and more than enough for web. That's ~2–3 MB. If PTP delivers that in ~1.2 s, the preview trick saves you maybe 800 ms for a meaningful complexity cost.

My guess: with a small JPEG plus **shrink-on-load** (JPEG DCT scaling — decoding 3008×2000 directly to 1504×1000 costs a fraction of a full decode), the simple path already hits your target and the two-stage download becomes an optimization you may not need. Keep the architecture able to do it; don't build it in week one.

### 3.4 Sony-specific settings to lock down

Configure once, verify in a preflight check on every boot:

- `USB Connection: PC Remote`
- `PC Remote Cxn Method: USB` · `Still Img. Save Dest: PC Only` (avoids a redundant SD write and the associated wait)
- **Auto Power OFF Temp / Power Save: OFF** — a sleeping camera is the #1 mid-event failure
- Full manual: M mode, fixed shutter/aperture/ISO, fixed WB (flash/Kelvin, never AWB — guests will notice colour shifting between shots)
- **Manual focus, pre-focused at the booth's fixed subject distance.** More reliable than AF-C in a dark room, and removes AF acquisition time from the shutter path.
- JPEG only (no RAW), size S, quality Fine
- Dummy battery / AC coupler (NP-FW50) — no battery swaps mid-event

---

## 4. Live preview

Keep the HDMI → USB capture approach. It's the right call and it has an architectural benefit you may not have noticed: **preview runs on a completely separate data path from capture**, so live view keeps running while gphoto is busy downloading over USB. With gphoto-based liveview you'd have to stop and restart preview around every shot.

Requirements and gotchas:

- **The capture stick must support MJPEG output**, not just YUYV. 1080p30 YUYV is ~124 MB/s — impossible over USB 2.0, so a YUYV-only stick silently drops you to 720p at low fps. MJPEG lets you pass frames through with near-zero CPU.
- **Do not decode/re-encode.** Read MJPEG frames from V4L2 and serve them straight as `multipart/x-mixed-replace` to an `<img>` tag. No OpenCV, no ffmpeg transcode. This is the difference between ~2% and ~60% CPU on a Pi 4.
- **Port placement:** put the capture stick on a **USB 3 port** and the camera on a **USB 2 port**. Even though the stick is a USB 2 device, this puts it on a separate host controller path and stops the two from fighting over the same 480 Mbit bus.
- **Handle HDMI blanking:** the a6400's HDMI output blanks/freezes for a moment during exposure. Freeze the last good frame in the UI rather than showing a black flash.
- Consider WebRTC only if MJPEG latency proves unacceptable. It almost certainly won't for a countdown.

---

## 5. Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Raspberry Pi                                            │
│                                                          │
│  ┌────────────────┐   IPC    ┌────────────────────────┐  │
│  │ camera-worker  │◄────────►│  app  (HTTP + WS)      │  │
│  │ (owns gphoto   │          │  session state machine │  │
│  │  handle, 1 thr)│          │  event bus             │  │
│  └────────────────┘          └───────┬────────────────┘  │
│         │ capture events              │                  │
│         ▼                             │                  │
│  ┌────────────────┐                   │                  │
│  │ pipeline pool  │  render/compose   │                  │
│  │ (libvips)      │  → display / print/ web variants     │
│  └───────┬────────┘                   │                  │
│          │                            │                  │
│  ┌───────▼────────┐  ┌─────────────┐  │  ┌────────────┐  │
│  │ upload worker  │  │ print worker│  │  │ preview    │  │
│  │ (retry queue)  │  │ (CUPS)      │  │  │ MJPEG relay│  │
│  └────────────────┘  └─────────────┘  │  └────────────┘  │
│                                       │                  │
│  ┌────────────────────────────────────▼───────────────┐  │
│  │ SQLite (WAL): sessions, images, jobs, config       │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
                       │ WebSocket + HTTP
                       ▼
              Touchscreen kiosk (Chromium)
```

**Principles:**

1. **Camera handle is owned by exactly one process, forever.**
2. **Everything after the shutter is a job, not a request.** Capture emits an event; display, print, upload, and archive are independent consumers. A failed print must never block an upload.
3. **The UI is driven by a WebSocket event stream**, not polling. The state machine (`IDLE → ARMED → COUNTDOWN → CAPTURING → REVIEW → PROCESSING → IDLE`) lives server-side and is the single source of truth.
4. **Working files in tmpfs**, finals on persistent storage. Saves SD card wear and is faster.
5. **Every queue is persisted in SQLite** so a crash or power loss doesn't lose a guest's photos or pending uploads.

### Latency budget (target)

| Stage | Target |
|---|---|
| Shutter fire → file available on camera | 0.3–0.8 s |
| PTP download (S JPEG, ~2.5 MB, persistent session) | 0.8–1.5 s |
| Write to tmpfs | <50 ms |
| Display variant via shrink-on-load + resize (libvips) | 150–300 ms |
| Push to browser + decode + swap | 200–400 ms |
| **Total shutter → on screen** | **≈ 1.5–3.0 s** |

Full-res archive, overlay compositing, print render, and upload all happen *after* this, invisibly.

---

## 6. Software stack recommendation

| Layer | Recommendation | Why |
|---|---|---|
| Language | **Python 3.11+** | Most mature libgphoto2 bindings; fast enough because the hot paths are in C libraries |
| Web framework | **FastAPI + uvicorn** | Native async, first-class WebSockets, tiny footprint |
| Camera | **python-gphoto2**, isolated process | See §3.2 |
| Image processing | **pyvips (libvips)** — *not* Pillow, *not* ImageMagick | 3–10× faster and far lower memory; shrink-on-load for JPEG; NEON-optimized on ARM |
| Database | **SQLite (WAL mode)** | Zero-ops, transactional job queues, trivially backed up |
| Frontend | **Svelte + Vite** (or plain TS) | Small bundle, fast on modest hardware; no need for React's weight here |
| Preview | V4L2 MJPEG passthrough → HTTP multipart | §4 |
| Printing | **CUPS + Gutenprint** | Industry standard; good dye-sub support |
| Supervision | **systemd** units, `Restart=always`, watchdog timer | Boring and reliable |
| Remote support | **Tailscale** | SSH into a rented booth from anywhere without port forwarding |

**Alternative worth a thought:** Rust (axum + the `gphoto2` crate + `libvips` bindings) gives you a single static binary, trivial deployment, and no Python environment to break on a rented device. Slower to write, less mature camera bindings. If you expect to maintain this for years and deploy to multiple boxes, it's defensible. I'd still start in Python and port the camera worker later if it proves flaky.

---

## 7. Feature specification

### v1 (must ship)

**Capture modes**
- Single photo
- Collage (2×2, 1+2, 3-strip — driven by layout templates, not hardcoded)
- Countdown 3–10 s (configurable), large on-screen numerals, live preview behind
- Post-capture review screen: image + "Print" / "Get photo" / "Done" / auto-dismiss timer

**Gallery**
- Grid of the event's photos, tap to enlarge
- Optional: hide gallery entirely (some clients want it, some don't)

**Delivery**
- QR code on the review screen → per-session or per-image link
- Background upload with **persistent retry queue** — a failed upload retries, it does not vanish
- Replace plain FTP with **SFTP or S3-compatible** (Backblaze B2 / Cloudflare R2 / self-hosted MinIO). FTP is plaintext and stateful; SFTP is a drop-in mental model with none of the pain.

**Printing**
- Print button on review screen, queued to CUPS
- **Per-session print limit** (configurable, e.g. 1 or 2) — otherwise one group burns 40 sheets
- Printer status surfaced: media remaining, paper out, error → hide the print button rather than letting guests queue into a dead printer

**Templates / layouts**
- Declarative layout definitions (see §8)
- Border/frame PNG with alpha, text overlays with custom font/colour/position
- Same template renders both a 300 dpi print variant and a web variant

**Landing / idle screen**
- Configurable background image or video, event title, "Touch to start"
- Attract loop when idle

**Admin**
- Hidden entry (5-second press in a screen corner, or `/admin` from a laptop on the booth's network)
- Test shot · camera reconnect · printer status · reprint last · upload queue status · disk usage · event switch · clean shutdown

### v2 (nice to have, design for but don't build)

- Boomerang / GIF mode
- Email delivery in addition to QR
- Client-facing web dashboard for the rented event
- Guest name/message capture ("guestbook" mode)
- Multiple print sizes (2×6 strips)

---

## 8. Template / layout system

Design this properly once and every future "can you add a border with the couple's names?" request becomes a config file, not a code change.

```yaml
# templates/collage-2x2.yaml
name: "Classic 2x2"
canvas:
  width_mm: 152
  height_mm: 102
  dpi: 300           # → 1795 × 1205 px
  background: "#ffffff"
slots:
  - { x: 40,  y: 40,  w: 840, h: 560, fit: cover }
  - { x: 915, y: 40,  w: 840, h: 560, fit: cover }
  - { x: 40,  y: 635, w: 840, h: 560, fit: cover }
  - { x: 915, y: 635, w: 840, h: 560, fit: cover }
overlays:
  - { type: image, src: "assets/frame.png", x: 0, y: 0, w: 1795, h: 1205 }
  - { type: text,  content: "{event.couple} · {event.date}"
      font: "assets/GreatVibes.ttf", size: 64, color: "#3a3a3a"
      anchor: bottom-center, y_offset: -30 }
variants:
  print: { dpi: 300, format: jpeg, quality: 95 }
  web:   { long_edge: 2000, format: jpeg, quality: 85 }
  thumb: { long_edge: 400,  format: jpeg, quality: 75 }
```

- Rendered with libvips composite operations — fast, low memory.
- `{event.*}` placeholders resolved from the event config, so one template serves every wedding.
- **Validate templates at load time**, with a preview render in admin, so you find a broken font path in the workshop and not at 8pm on a Saturday.

An event is then: `events/mueller-2026-09-12/` containing `event.yaml`, chosen template refs, a logo, a background image, and branding colours.

---

## 9. Printing

**Printer recommendation.** For rental you want a **dye-sublimation** printer, not inkjet — no drying, no clogged heads from sitting idle for three weeks, fixed cost per print, water-resistant output.

Realistic candidates with good Linux/Gutenprint support:

- **DNP DS-RX1HS** — the rental industry workhorse. ~12–13 s per 6×4, ~700 prints per roll. Used units are the sweet spot on price.
- **DNP DS620A** — more sizes, slightly slower, newer.
- **Citizen CX-02** — comparable, sometimes cheaper used.
- *Avoid* Canon Selphy for rental: ~47 s per print and much higher consumable cost per sheet, though it's fine as a cheap proof-of-concept.

Check current used pricing locally — it moves a lot.

**Software handling:**
- Print jobs go to a queue, never inline with the UI.
- Poll CUPS for job/printer state; expose it in admin and use it to gate the print button.
- Track prints per session *and* per event, with a remaining-media estimate.
- **Test the exact printer/Gutenprint combination before buying** if you can — dye-sub Linux support is good but not universal, and colour profiles matter.
- Build a "reprint" admin function. Someone will drop a print in a drink.

---

## 10. Hardware & rental hardening

### Changes I'd recommend to the current build

| Current | Recommendation | Reason |
|---|---|---|
| iPad in kiosk mode | **10" HDMI touchscreen driven by the Pi directly** | Removes a network hop from the preview path, removes a €500 device from your rental risk, removes iOS updates breaking kiosk mode, removes charging. Trade-off: some CPU for local Chromium. |
| Phone hotspot | **4G/LTE router** (GL.iNet or Teltonika) with its own SIM | A guest's phone is not infrastructure |
| SD card boot | **USB SSD boot**, or read-only root with overlayfs | Power-cut SD corruption is the single most common Pi field failure |
| Pi 4 | Pi 4 8GB is fine; **Pi 5 if you want headroom** | You're targeting efficient software, so Pi 4 should hold. Pi 5 gives margin for v2 features. |
| — | **Add active cooling** | A wooden box with a Pi, a capture stick and a camera gets hot. Thermal throttling will undo your latency work. |
| — | **UPS / supercap hat with clean shutdown** | Someone will trip the cable |
| — | **Physical start button** (optional, alongside touch) | More intuitive, and a fallback if the touchscreen fails |

### Fault handling — this is what makes it rentable

Each of these needs a defined, tested behaviour:

- Camera disconnects mid-event → detect, attempt reconnect loop, show a friendly "one moment" screen, alert admin. Sony bodies are known to drop the PTP session after prolonged tethering; assume it *will* happen.
- Camera sleeps / battery dies → preflight check on boot plus periodic heartbeat
- Flash not recycled → enforce a minimum inter-shot interval; consider detecting underexposed frames
- Printer out of media → gate the button, keep the booth working
- Network down → everything still works, uploads queue
- Disk fills → alert threshold at 80%, refuse to start an event below a floor
- Pi reboots mid-event → sessions and queues survive (they're in SQLite), booth returns to idle screen automatically

### Pre-event checklist (automate it)

A single "preflight" screen in admin that checks: camera connected · camera settings match expected profile · test shot succeeds within budget · flash fires · capture device streaming · printer online with media · disk free · network reachable · time synced. Green/red per line. This is what turns a nervous setup into a two-minute one.

---

## 11. Legal & privacy (Germany / GDPR)

Once you rent this out, you're processing other people's biometric-adjacent personal data commercially. Worth getting right early — it's cheap now and expensive later.

- **Role:** you're likely a *processor* (Auftragsverarbeiter) for the client, who is the controller. You need an **AVV / DPA contract** with each renter. Templates exist; a lawyer review once is worth it.
- **On-booth notice:** a visible privacy notice (physical sign + a line on the idle screen) explaining that photos are taken, stored, and uploaded, and to where.
- **Retention:** automatic deletion after a defined window (e.g. 30 days), enforced in code, not by hand. Document it.
- **Hosting:** keep the upload target **in the EU** (Hetzner, Cloudflare R2 EU jurisdiction, self-hosted). Avoid US-only providers.
- **Access control:** gallery links should be **unguessable tokens**, not sequential IDs. Don't let anyone enumerate someone else's wedding.
- **Transport security:** HTTPS everywhere; SFTP/S3 instead of plain FTP.
- **Deletion request path:** a documented way for a guest to ask for their photo to be removed.
- Minors will be photographed at these events. Handle it via the client's consent process; don't build anything that stores identity.

---

## 12. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Sony PTP session drops after N shots | High | High | Watchdog + auto-reconnect; consider USB port power-cycle via `uhubctl` (verify support on your Pi model); test with a 300-shot soak run |
| a6400 doesn't support fast PTP thumbnail fetch | Medium | Low | Fall back to small JPEG + shrink-on-load; the target is reachable either way |
| Capture stick is YUYV-only | Medium | Medium | Verify with `v4l2-ctl --list-formats-ext` before designing around it |
| Latency target not met even with persistent session | Low-Medium | High | **Phase 0 benchmark answers this before you write the app** |
| Printer colour/driver issues on Linux | Medium | Medium | Buy a model with confirmed Gutenprint support; test before committing |
| Pi thermal throttling in a sealed wooden box | Medium | Medium | Active cooling + vents; monitor temp and log it |
| Scope creep into v2 features | High | High | The non-goals list in §1 is a contract with yourself |

---

## 13. Roadmap

### Phase 0 — Benchmark spike *(do this first, ~1 weekend)*

**Do not write application code yet.** Write throwaway scripts on the real hardware and produce a table of measured numbers. This phase either validates the whole plan or changes it.

Measure:
1. `gphoto2` CLI cold invocation, full capture-and-download — your baseline
2. Persistent python-gphoto2 session: shutter → file in memory, 20 consecutive shots, at JPEG sizes L / M / S
3. `GP_FILE_TYPE_PREVIEW` availability and latency on the a6400
4. pyvips: decode+resize S JPEG → 1920px display variant, and → 1800×1200 print variant
5. Full composite render of a 2×2 collage with border + text at 300 dpi
6. V4L2 formats and CPU cost of MJPEG passthrough at 1080p
7. **Soak test:** 300 shots back to back. Does the camera drop? Does memory grow? Does the Pi throttle?

**Exit criterion:** a measured shutter → display path under 3 s, and a clear picture of where the remaining time goes.

### Phase 1 — Core loop
Camera worker process · state machine · WebSocket event bus · single photo · countdown with live preview · review screen · local storage. **Ugly UI is fine.** The goal is proving the latency in a real app.

### Phase 2 — Composition
Template engine · collage modes · borders and text overlays · print/web/thumb variant rendering.

### Phase 3 — Delivery
Gallery · QR codes · upload worker with persistent retry queue · server-side gallery page · retention policy.

### Phase 4 — Printing
CUPS integration · print queue · limits · status surfacing · reprint.

### Phase 5 — Configurability
Event config format · landing page theming · admin panel · preflight check screen · template preview.

### Phase 6 — Rental hardening
Fault injection testing (yank the camera cable, pull the network, fill the disk, kill power) · systemd supervision · logging and log rotation · Tailscale remote access · documentation and a physical setup checklist · full-day dress rehearsal.

**Ship gate:** you can hand the box to someone who has never seen it, with a one-page instruction sheet, and it works.

---

## 14. Open decisions

Things I'd want your call on before Phase 1:

1. **Touchscreen vs iPad** — I lean strongly toward a Pi-driven HDMI touchscreen for rental (§10), but you already own the iPad and know it works.
2. **Python vs Rust** — Python to ship faster, Rust for a single deployable binary. My default: Python.
3. **Upload target** — self-hosted VPS (full control, more ops) vs S3-compatible (less ops, per-GB cost). Both fine; pick one and build the abstraction so it's swappable.
4. **Gallery on the booth screen** — some renters want it, some consider it a privacy problem. Make it a per-event toggle.
5. **Local Wi-Fi download vs cloud** — letting guests download over the booth's local Wi-Fi is instant but knocks them off mobile data. Cloud is slower but frictionless. Suggest: cloud primary, QR works later.
6. **Print sizes** — 6×4 only in v1, or 2×6 strips too? Strips affect the template system, so decide before Phase 2.
