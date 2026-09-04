<!--
  Guest-facing capture flow: idle -> armed -> countdown -> capturing -> review.
  Driven entirely by the server's WebSocket event stream (photobooth-plan.md
  §5: "the UI is driven by a WebSocket event stream, not polling" — the
  `state` below just mirrors whatever StateChanged last said, it's never
  set optimistically). Ugly is fine for Phase 1 (IMPLEMENTATION_PLAN.md §7)
  — the point is proving the capture flow and its timing work end to end.

  Phase 3 (IMPLEMENTATION_PLAN.md §9) layers the attract loop (T-3.1) and an
  idle timeout (T-3.3) on top of that same server-driven `sessionState`.
  IDLE renders AttractScreen.svelte (branded background+logo+title/date);
  BottomNav.svelte is rendered here, alongside it, on both the idle AND
  review screens — the guest's mode/gallery choices are a persistent bar,
  not a one-off "Done" button (see BottomNav's own header comment). Picking
  a mode from review dismisses the current session and starts the new one
  directly. From any other screen, activity listeners below auto-dismiss
  back to IDLE after a configurable stretch of no guest input, fetched from
  GET /session/event's idle_timeout_s.
-->
<script lang="ts">
  import AttractScreen, { type EventInfo } from '../lib/AttractScreen.svelte'
  import BottomNav from '../lib/BottomNav.svelte'
  import { themeStyle } from '../lib/theme'

  type SessionState = 'idle' | 'armed' | 'countdown' | 'capturing' | 'review' | 'processing'

  type ServerEvent =
    | {
        type: 'StateChanged'
        session_id: string
        state: SessionState
        share_token?: string | null
      }
    | { type: 'CountdownStarted'; session_id: string; duration_s: number }
    | { type: 'PreviewReady'; session_id: string; capture_id: string; image_url: string }
    | { type: 'FullImageReady'; session_id: string; capture_id: string; image_url: string }
    | { type: 'CaptureFailed'; session_id: string; message: string }

  type PrinterStatus = { status: string; detail: string }

  let sessionState = $state<SessionState>('idle')
  let countdownValue = $state(0)
  let reviewImageUrl = $state<string | null>(null)
  let reviewCaptureId = $state<string | null>(null)
  let errorMessage = $state<string | null>(null)
  let imageSetAt = 0

  // T-4.3/T-4.8: set from StateChanged's share_token once the guest's shots
  // finish uploading and land on REVIEW (web/session.py's
  // `_issue_share_token_and_enqueue_uploads`). printerStatus gates the
  // print button — a guest never sees a print button that can't work.
  let shareToken = $state<string | null>(null)
  let printerStatus = $state<PrinterStatus | null>(null)
  let printBusy = $state(false)
  let printMessage = $state<string | null>(null)
  let printMessageOk = $state(false)

  let eventInfo = $state<EventInfo | null>(null)
  let autoStartedFromQuery = false

  // T-3.3: default matches KioskConfig.idle_timeout_s's pydantic default
  // (src/photobooth/config/models.py) until GET /session/event's response
  // overwrites it with the configured value.
  let idleTimeoutS = $state(60)

  // Held at component scope (not inside the $effect closure) so the review
  // <img>'s onload handler below can reach it to report decode timing.
  let socket: WebSocket | undefined

  function handleEvent(event: ServerEvent) {
    switch (event.type) {
      case 'StateChanged':
        sessionState = event.state
        if (event.state === 'armed') {
          reviewImageUrl = null
          errorMessage = null
          shareToken = null
          printerStatus = null
          printMessage = null
        }
        if (event.state === 'review') {
          shareToken = event.share_token ?? null
        }
        break
      case 'CountdownStarted':
        startCountdown(event.duration_s)
        break
      case 'FullImageReady':
        // The a6400 has no PTP preview support (confirmed against real
        // hardware, IMPLEMENTATION_PLAN.md §6 T-C3), so FullImageReady is
        // what actually lands the guest's photo — PreviewReady is handled
        // too, for backends that do have one, but isn't relied on here.
        reviewImageUrl = event.image_url
        reviewCaptureId = event.capture_id
        imageSetAt = performance.now()
        break
      case 'PreviewReady':
        if (reviewImageUrl === null) {
          reviewImageUrl = event.image_url
          reviewCaptureId = event.capture_id
          imageSetAt = performance.now()
        }
        break
      case 'CaptureFailed':
        errorMessage = event.message
        break
    }
  }

  function reportDecodeTime() {
    // IMPLEMENTATION_PLAN.md §4.1's mandatory display.browser_decode span —
    // the one stage of the latency budget only the browser can measure.
    if (socket?.readyState !== WebSocket.OPEN || reviewCaptureId === null) return
    socket.send(
      JSON.stringify({
        type: 'browser_decode',
        capture_id: reviewCaptureId,
        duration_ms: performance.now() - imageSetAt,
      })
    )
  }

  let countdownTimer: ReturnType<typeof setInterval> | undefined

  function startCountdown(durationS: number) {
    clearInterval(countdownTimer)
    const deadline = Date.now() + durationS * 1000
    const tick = () => {
      const remainingS = (deadline - Date.now()) / 1000
      countdownValue = Math.max(0, Math.ceil(remainingS))
      if (remainingS <= 0) clearInterval(countdownTimer)
    }
    tick()
    countdownTimer = setInterval(tick, 100)
  }

  async function startSession(modeId: string) {
    errorMessage = null
    const armed = await fetch(`/session/arm?mode_id=${encodeURIComponent(modeId)}`, {
      method: 'POST',
    })
    if (!armed.ok) {
      errorMessage = `could not start (${armed.status})`
      return
    }
    const captured = await fetch('/session/capture', { method: 'POST' })
    if (!captured.ok) {
      errorMessage = `capture failed (${captured.status})`
    }
  }

  async function dismiss() {
    await fetch('/session/dismiss', { method: 'POST' })
  }

  // T-4.8: fetched fresh on entering REVIEW so the print button reflects
  // the printer's live state (media out, offline, etc), not a stale value
  // from earlier in the session.
  async function loadPrinterStatus() {
    printerStatus = null
    try {
      const response = await fetch('/session/printer-status')
      if (response.ok) printerStatus = (await response.json()) as PrinterStatus
    } catch {
      printerStatus = null
    }
  }

  async function printPhoto() {
    if (reviewCaptureId === null) return
    printBusy = true
    printMessage = null
    try {
      const response = await fetch('/session/print', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ capture_id: reviewCaptureId }),
      })
      const body = await response.json()
      if (!response.ok) {
        printMessageOk = false
        printMessage = body?.detail ?? `Print failed (${response.status})`
      } else {
        const remaining = body.remaining as number
        printMessageOk = true
        printMessage = `Printing… ${remaining} print${remaining === 1 ? '' : 's'} left.`
      }
    } catch {
      printMessageOk = false
      printMessage = 'Print failed.'
    } finally {
      printBusy = false
    }
  }

  async function loadEventInfo() {
    const response = await fetch('/session/event')
    if (!response.ok) return
    eventInfo = (await response.json()) as EventInfo
    idleTimeoutS = eventInfo.idle_timeout_s
  }

  // BottomNav's single onStart handler works from BOTH the attract screen
  // (sessionState 'idle') and the review screen ('review' — replaces the
  // old standalone "Done" button entirely, see this file's header comment).
  // From review, the state machine only allows REVIEW -> {PROCESSING,IDLE},
  // never straight to ARMED, so dismiss() first and only then arm the new
  // mode — dismiss()'s fetch already awaits the server completing that
  // transition before resolving, so the follow-up arm is never racing it.
  async function handleStart(modeId: string) {
    if (sessionState === 'review') {
      await dismiss()
    }
    void startSession(modeId)
  }

  // Mirrors handleStart's own review-guard above: leaving for the gallery is
  // a full page navigation (no unmount cleanup runs the WS through
  // /session/dismiss), so a session sitting in REVIEW would otherwise stay
  // stranded there server-side — REVIEW only allows -> {PROCESSING,IDLE},
  // never straight back to ARMED — and the guest's next arm() 409s the
  // moment they return via a gallery mode button (bug: "after visiting the
  // gallery I can not start the new capture process").
  async function handleGallery() {
    const eventId = eventInfo?.event_id
    if (!eventId) return
    if (sessionState === 'review') {
      await dismiss()
    }
    window.location.href = `/gallery/${encodeURIComponent(eventId)}`
  }

  // T-3.3: from any screen other than the attract loop itself, no
  // pointer/touch/key activity for `idleTimeoutS` returns to IDLE. The
  // attract loop is excluded on purpose — it IS the idle state, timing out
  // of it would be timing out of nothing.
  let idleTimer: ReturnType<typeof setTimeout> | undefined

  function resetIdleTimer() {
    clearTimeout(idleTimer)
    if (sessionState === 'idle') return
    idleTimer = setTimeout(() => {
      void dismiss()
    }, idleTimeoutS * 1000)
  }

  $effect(() => {
    // Reads `sessionState` (and `idleTimeoutS`), so this re-runs whenever
    // either changes — e.g. re-arming the timer fresh on every state
    // transition, and clearing it once back at idle.
    resetIdleTimer()
  })

  $effect(() => {
    const activityEvents = ['pointerdown', 'touchstart', 'keydown'] as const
    const onActivity = () => resetIdleTimer()
    for (const type of activityEvents) window.addEventListener(type, onActivity)
    return () => {
      for (const type of activityEvents) window.removeEventListener(type, onActivity)
    }
  })

  $effect(() => {
    void loadEventInfo()
  })

  // Lets the gallery page's BottomNav (a separate route/component, no
  // shared session state) start a mode in one tap via `/?mode=<id>` rather
  // than bouncing the guest back to the attract screen to tap again.
  $effect(() => {
    if (autoStartedFromQuery || eventInfo === null || sessionState !== 'idle') return
    const mode = new URLSearchParams(window.location.search).get('mode')
    if (mode) {
      autoStartedFromQuery = true
      history.replaceState(null, '', window.location.pathname)
      void handleStart(mode)
    }
  })

  $effect(() => {
    if (sessionState === 'review') void loadPrinterStatus()
  })

  $effect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout>
    let cancelled = false

    const connect = () => {
      socket = new WebSocket(`ws://${location.host}/ws`)
      socket.onmessage = (ev) => handleEvent(JSON.parse(ev.data) as ServerEvent)
      socket.onclose = () => {
        if (!cancelled) reconnectTimer = setTimeout(connect, 1000)
      }
    }
    connect()

    return () => {
      cancelled = true
      clearTimeout(reconnectTimer)
      clearInterval(countdownTimer)
      clearTimeout(idleTimer)
      socket?.close()
    }
  })
</script>

<div class="stage" style={themeStyle(eventInfo?.theme)}>
  <img class="preview" src="/preview/stream" alt="" />

  {#if sessionState === 'idle'}
    <AttractScreen event={eventInfo} />
  {:else if sessionState === 'countdown'}
    <div class="overlay countdown">{countdownValue}</div>
  {:else if sessionState === 'capturing'}
    <div class="overlay">{eventInfo?.strings.capturing_label ?? 'Capturing…'}</div>
  {:else if sessionState === 'review' && reviewImageUrl}
    <div class="overlay review">
      <div class="photo-frame rise-in">
        <img src={reviewImageUrl} alt="Your capture" onload={reportDecodeTime} />
      </div>

      {#if printerStatus?.status === 'green'}
        <div class="review-toolbar">
          <button onclick={printPhoto} disabled={printBusy}>
            {printBusy
              ? (eventInfo?.strings.print_button_busy ?? 'Printing…')
              : (eventInfo?.strings.print_button ?? 'Print')}
          </button>
        </div>
      {/if}
      {#if printMessage}
        <p
          class="print-message"
          class:success-text={printMessageOk}
          class:error-text={!printMessageOk}
        >
          {printMessage}
        </p>
      {/if}
      {#if shareToken}
        <div class="qr-corner">
          <img src={`/s/${shareToken}/qr.png`} alt="Scan with your phone to keep this" />
          <span>{eventInfo?.strings.qr_caption ?? 'Scan to get your photo'}</span>
        </div>
      {/if}
    </div>
  {/if}

  {#if sessionState === 'idle' || sessionState === 'review'}
    <BottomNav
      modes={eventInfo?.modes ?? []}
      onStart={handleStart}
      onGallery={handleGallery}
      galleryLabel={eventInfo?.strings.gallery_word ?? 'Gallery'}
    />
  {/if}

  {#if errorMessage}
    <div class="error">{errorMessage}</div>
  {/if}
</div>

<style>
  .stage {
    position: relative;
    width: 100vw;
    height: 100vh;
    background: #000;
    overflow: hidden;
  }

  .preview {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  .overlay {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 1rem;
    color: var(--color-overlay-ink);
    background: var(--color-overlay-bg);
    font-size: 2rem;
    border: none;
  }

  /* LCD exposure-counter treatment (this app's design signature — see
     app.css's header comment) at full display scale: an analog camera's
     digital frame counter, not a plain fullscreen number. */
  .countdown {
    font-size: 11rem;
    font-weight: 600;
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    color: var(--color-primary);
    text-shadow:
      0 0 40px color-mix(in srgb, var(--color-primary) 70%, transparent),
      0 4px 30px rgba(0, 0, 0, 0.5);
  }

  .review .photo-frame {
    max-width: 88vw;
    max-height: 76vh;
  }

  .review .photo-frame img {
    max-width: calc(88vw - 1rem);
    max-height: calc(76vh - 1rem);
    object-fit: contain;
  }

  .review-toolbar {
    position: absolute;
    top: 1.25rem;
    right: 1.25rem;
  }

  .review-toolbar button {
    font-size: 1.1rem;
    font-weight: 500;
    padding: 0.6rem 1.85rem;
    border: none;
    border-radius: var(--radius);
    background: var(--color-primary);
    color: var(--color-primary-contrast);
    box-shadow: var(--shadow-md);
    transition:
      background-color 200ms var(--ease-spring),
      transform 150ms var(--ease-spring);
  }

  .review-toolbar button:hover:not(:disabled) {
    background: var(--color-primary-hover);
  }

  .review-toolbar button:active:not(:disabled) {
    transform: scale(0.96);
  }

  .review-toolbar button:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .print-message {
    position: absolute;
    top: 4.5rem;
    right: 1.25rem;
    background: var(--color-surface);
    padding: 0.5rem 1.1rem;
    border-radius: var(--radius-sm);
    font-size: 1rem;
    margin: 0;
    max-width: 20rem;
  }

  /* Small corner placement (T-4.3's frontend half) — the guest's own photo
     stays the focus of the review screen, the QR code is a secondary
     affordance for taking it with them. Sits above BottomNav, not behind
     it. */
  .qr-corner {
    position: absolute;
    bottom: 7rem;
    right: 1.25rem;
    z-index: 6;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.4rem;
    background: #f7f3ea;
    padding: 0.85rem;
    border-radius: var(--radius);
    box-shadow: var(--shadow-lg);
  }

  .qr-corner img {
    width: 12rem;
    height: 12rem;
  }

  .qr-corner span {
    font-size: 0.8rem;
    color: #5c5342;
    text-align: center;
  }

  .error {
    position: absolute;
    bottom: 7rem;
    left: 50%;
    transform: translateX(-50%);
    z-index: 6;
    background: var(--color-danger-dot);
    color: #fff;
    padding: 0.6rem 1.25rem;
    border-radius: var(--radius-sm);
    box-shadow: var(--shadow-md);
  }
</style>
