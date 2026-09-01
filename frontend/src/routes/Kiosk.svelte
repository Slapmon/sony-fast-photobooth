<!--
  Guest-facing capture flow: idle -> armed -> countdown -> capturing -> review.
  Driven entirely by the server's WebSocket event stream (photobooth-plan.md
  §5: "the UI is driven by a WebSocket event stream, not polling" — the
  `state` below just mirrors whatever StateChanged last said, it's never
  set optimistically). Ugly is fine for Phase 1 (IMPLEMENTATION_PLAN.md §7)
  — the point is proving the capture flow and its timing work end to end.
-->
<script lang="ts">
  type SessionState = 'idle' | 'armed' | 'countdown' | 'capturing' | 'review' | 'processing'

  type ServerEvent =
    | { type: 'StateChanged'; session_id: string; state: SessionState }
    | { type: 'CountdownStarted'; session_id: string; duration_s: number }
    | { type: 'PreviewReady'; session_id: string; capture_id: string; image_url: string }
    | { type: 'FullImageReady'; session_id: string; capture_id: string; image_url: string }
    | { type: 'CaptureFailed'; session_id: string; message: string }

  let sessionState = $state<SessionState>('idle')
  let countdownValue = $state(0)
  let reviewImageUrl = $state<string | null>(null)
  let reviewCaptureId = $state<string | null>(null)
  let errorMessage = $state<string | null>(null)
  let imageSetAt = 0

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

  async function startSession() {
    errorMessage = null
    const armed = await fetch('/session/arm', { method: 'POST' })
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
      socket?.close()
    }
  })
</script>

<div class="stage">
  <img class="preview" src="/preview/stream" alt="" />

  {#if sessionState === 'idle'}
    <button class="overlay tap-to-start" onclick={startSession}>Touch to start</button>
  {:else if sessionState === 'countdown'}
    <div class="overlay countdown">{countdownValue}</div>
  {:else if sessionState === 'capturing'}
    <div class="overlay">Capturing…</div>
  {:else if sessionState === 'review' && reviewImageUrl}
    <div class="overlay review">
      <img src={reviewImageUrl} alt="Your capture" onload={reportDecodeTime} />
      <button onclick={dismiss}>Done</button>
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
    color: #fff;
    background: rgba(0, 0, 0, 0.35);
    font-size: 2rem;
    border: none;
  }

  .tap-to-start {
    cursor: pointer;
    font-size: 3rem;
  }

  .countdown {
    font-size: 10rem;
    font-weight: 700;
  }

  .review img {
    max-width: 80vw;
    max-height: 70vh;
    object-fit: contain;
  }

  .review button {
    font-size: 1.5rem;
    padding: 0.75rem 2rem;
  }

  .error {
    position: absolute;
    bottom: 1rem;
    left: 50%;
    transform: translateX(-50%);
    background: #b00020;
    color: #fff;
    padding: 0.5rem 1rem;
    border-radius: 0.25rem;
  }
</style>
