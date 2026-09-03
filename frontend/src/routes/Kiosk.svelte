<!--
  Guest-facing capture flow: idle -> armed -> countdown -> capturing -> review.
  Driven entirely by the server's WebSocket event stream (photobooth-plan.md
  §5: "the UI is driven by a WebSocket event stream, not polling" — the
  `state` below just mirrors whatever StateChanged last said, it's never
  set optimistically). Ugly is fine for Phase 1 (IMPLEMENTATION_PLAN.md §7)
  — the point is proving the capture flow and its timing work end to end.

  Phase 3 (IMPLEMENTATION_PLAN.md §9) layers the attract loop (T-3.1) and an
  idle timeout (T-3.3) on top of that same server-driven `sessionState`.
  IDLE renders AttractScreen.svelte directly — every guest choice (which
  capture mode, or "go to the gallery instead") is a real button on that one
  screen, there's no separate mode-select step or implicit "touch anywhere"
  affordance (see AttractScreen.svelte's own header comment). From any
  *other* screen, activity listeners below auto-dismiss back to IDLE after a
  configurable stretch of no guest input, fetched from GET /session/event's
  idle_timeout_s.
-->
<script lang="ts">
  import AttractScreen, { type EventInfo } from '../lib/AttractScreen.svelte'

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

  function handleStart(modeId: string) {
    void startSession(modeId)
  }

  function handleGallery() {
    const eventId = eventInfo?.event_id
    if (eventId) window.location.href = `/gallery/${encodeURIComponent(eventId)}`
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

<div class="stage">
  <img class="preview" src="/preview/stream" alt="" />

  {#if sessionState === 'idle'}
    <AttractScreen event={eventInfo} onStart={handleStart} onGallery={handleGallery} />
  {:else if sessionState === 'countdown'}
    <div class="overlay countdown">{countdownValue}</div>
  {:else if sessionState === 'capturing'}
    <div class="overlay">Capturing…</div>
  {:else if sessionState === 'review' && reviewImageUrl}
    <div class="overlay review">
      <img src={reviewImageUrl} alt="Your capture" onload={reportDecodeTime} />
      <div class="review-actions">
        {#if printerStatus?.status === 'green'}
          <button onclick={printPhoto} disabled={printBusy}>
            {printBusy ? 'Printing…' : 'Print'}
          </button>
        {/if}
        <button onclick={dismiss}>Done</button>
      </div>
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
          <span>Scan to get your photo</span>
        </div>
      {/if}
    </div>
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

  .countdown {
    font-size: 10rem;
    font-weight: 700;
    font-family: var(--font-display);
  }

  /* Direct-child selectors — .qr-corner below also contains an <img> and a
     button-shaped affordance, and must not inherit the capture photo's
     sizing or the "Done"/"Print" pill styling. */
  .review > img {
    max-width: 80vw;
    max-height: 70vh;
    object-fit: contain;
    border-radius: var(--radius);
    box-shadow: var(--shadow-md);
  }

  .review-actions {
    display: flex;
    gap: 1rem;
  }

  .review-actions button {
    font-size: 1.3rem;
    padding: 0.75rem 2.5rem;
    border: none;
    border-radius: 999px;
    background: var(--color-primary);
    color: var(--color-primary-contrast);
  }

  .review-actions button:hover:not(:disabled) {
    background: var(--color-primary-hover);
  }

  .review-actions button:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .print-message {
    background: var(--color-surface);
    padding: 0.5rem 1.1rem;
    border-radius: var(--radius-sm);
    font-size: 1rem;
    margin: 0;
  }

  /* Small corner placement (T-4.3's frontend half) — the guest's own photo
     stays the focus of the review screen, the QR code is a secondary
     affordance for taking it with them. */
  .qr-corner {
    position: absolute;
    bottom: 1.25rem;
    right: 1.25rem;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.25rem;
    background: var(--color-surface);
    padding: 0.5rem;
    border-radius: var(--radius-sm);
    box-shadow: var(--shadow-md);
  }

  .qr-corner img {
    width: 6rem;
    height: 6rem;
  }

  .qr-corner span {
    font-size: 0.7rem;
    color: var(--color-ink-muted);
  }

  .error {
    position: absolute;
    bottom: 1.5rem;
    left: 50%;
    transform: translateX(-50%);
    background: var(--color-danger-dot);
    color: #fff;
    padding: 0.6rem 1.25rem;
    border-radius: var(--radius-sm);
    box-shadow: var(--shadow-md);
  }
</style>
