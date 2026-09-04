<!--
  Test shot · camera reconnect · clean shutdown (IMPLEMENTATION_PLAN.md
  T-3.11) · reprint (T-4.9).
-->
<script lang="ts">
  let testShotBusy = $state(false)
  let testShotResult = $state<string | null>(null)
  let testShotImageUrl = $state<string | null>(null)

  let reconnectBusy = $state(false)
  let reconnectResult = $state<string | null>(null)

  let resetBusy = $state(false)
  let resetResult = $state<string | null>(null)

  let restartBusy = $state(false)
  let restartResult = $state<string | null>(null)
  let restartConfirming = $state(false)

  let shutdownBusy = $state(false)
  let shutdownResult = $state<string | null>(null)

  // T-4.9: simplest possible UI — a capture_id text field. A "pick from
  // recent captures" list would be nicer but is out of scope here.
  let reprintCaptureId = $state('')
  let reprintBusy = $state(false)
  let reprintOk = $state(false)
  let reprintResult = $state<string | null>(null)

  async function runTestShot() {
    testShotBusy = true
    testShotResult = null
    testShotImageUrl = null
    try {
      const res = await fetch('/admin/actions/test-shot', { method: 'POST' })
      const body = await res.json()
      if (!res.ok) throw new Error(body?.detail ?? `HTTP ${res.status}`)
      testShotResult = `Captured ${body.capture_id} (${body.width}×${body.height}).`
      testShotImageUrl = body.image_url
    } catch (err) {
      testShotResult = `Failed: ${err instanceof Error ? err.message : String(err)}`
    } finally {
      testShotBusy = false
    }
  }

  async function runReconnect() {
    reconnectBusy = true
    reconnectResult = null
    try {
      const res = await fetch('/admin/actions/reconnect-camera', { method: 'POST' })
      const body = await res.json()
      if (!res.ok) throw new Error(body?.detail ?? `HTTP ${res.status}`)
      reconnectResult = 'Camera reconnected.'
    } catch (err) {
      reconnectResult = `Failed: ${err instanceof Error ? err.message : String(err)}`
    } finally {
      reconnectBusy = false
    }
  }

  async function runReset() {
    resetBusy = true
    resetResult = null
    try {
      const res = await fetch('/admin/actions/reset-session', { method: 'POST' })
      const body = await res.json()
      if (!res.ok) throw new Error(body?.detail ?? `HTTP ${res.status}`)
      resetResult = 'Session reset — booth returned to the attract screen.'
    } catch (err) {
      resetResult = `Failed: ${err instanceof Error ? err.message : String(err)}`
    } finally {
      resetBusy = false
    }
  }

  async function runRestart() {
    if (!restartConfirming) {
      restartConfirming = true
      return
    }
    restartConfirming = false
    restartBusy = true
    restartResult = null
    try {
      const res = await fetch('/admin/actions/restart-app', { method: 'POST' })
      const body = await res.json()
      if (!res.ok) throw new Error(body?.detail ?? `HTTP ${res.status}`)
      restartResult = 'Restarting… the app will be back in a few seconds.'
    } catch (err) {
      restartResult = `Failed: ${err instanceof Error ? err.message : String(err)}`
    } finally {
      restartBusy = false
    }
  }

  async function runShutdown() {
    shutdownBusy = true
    shutdownResult = null
    try {
      const res = await fetch('/admin/actions/shutdown-camera', { method: 'POST' })
      const body = await res.json()
      if (!res.ok) throw new Error(body?.detail ?? `HTTP ${res.status}`)
      shutdownResult = 'Booth returned to idle and camera disconnected.'
    } catch (err) {
      shutdownResult = `Failed: ${err instanceof Error ? err.message : String(err)}`
    } finally {
      shutdownBusy = false
    }
  }

  async function runReprint() {
    const captureId = reprintCaptureId.trim()
    if (!captureId) return
    reprintBusy = true
    reprintResult = null
    try {
      const res = await fetch(`/admin/actions/reprint/${encodeURIComponent(captureId)}`, {
        method: 'POST',
      })
      const body = await res.json()
      if (!res.ok) throw new Error(body?.detail ?? `HTTP ${res.status}`)
      reprintOk = true
      reprintResult = 'Reprint job submitted.'
    } catch (err) {
      reprintOk = false
      reprintResult = `Failed: ${err instanceof Error ? err.message : String(err)}`
    } finally {
      reprintBusy = false
    }
  }
</script>

<section class="actions-panel">
  <div class="action card">
    <button class="primary" onclick={runTestShot} disabled={testShotBusy}>
      {testShotBusy ? 'Capturing…' : 'Test shot'}
    </button>
    <p class="hint">
      Fires a real capture directly, out-of-band from the guest flow. Avoid running this while a
      guest session is mid-countdown or mid-capture.
    </p>
    {#if testShotResult}<p class="result">{testShotResult}</p>{/if}
    {#if testShotImageUrl}
      <img class="preview" src={testShotImageUrl} alt="Test shot result" />
    {/if}
  </div>

  <div class="action card">
    <button class="secondary" onclick={runReconnect} disabled={reconnectBusy}>
      {reconnectBusy ? 'Reconnecting…' : 'Reconnect camera'}
    </button>
    {#if reconnectResult}<p class="result">{reconnectResult}</p>{/if}
  </div>

  <div class="action card">
    <button class="secondary" onclick={runReset} disabled={resetBusy}>
      {resetBusy ? 'Resetting…' : 'Reset stuck session'}
    </button>
    <p class="hint">
      Returns a frozen or stuck guest session to the attract screen without touching the camera
      connection or restarting the app. Use this first if a guest reports the booth won't start a
      new photo.
    </p>
    {#if resetResult}<p class="result">{resetResult}</p>{/if}
  </div>

  <div class="action card">
    <button class="primary" onclick={runRestart} disabled={restartBusy}>
      {restartBusy
        ? 'Restarting…'
        : restartConfirming
          ? 'Click again to confirm restart'
          : 'Restart app'}
    </button>
    <p class="hint">
      Restarts the whole app process — needed after creating or activating a new event so the
      actual guest capture flow (not just the attract screen) picks it up. Takes a few seconds;
      the booth is briefly unavailable during the restart.
    </p>
    {#if restartResult}<p class="result">{restartResult}</p>{/if}
  </div>

  <div class="action card">
    <button class="secondary" onclick={runShutdown} disabled={shutdownBusy}>
      {shutdownBusy ? 'Shutting down…' : 'Clean shutdown (end of event)'}
    </button>
    <p class="hint">
      Returns the booth to idle and cleanly disconnects the camera for teardown. This does
      <strong>not</strong> power off the Pi or stop the app.
    </p>
    {#if shutdownResult}<p class="result">{shutdownResult}</p>{/if}
  </div>

  <div class="action card">
    <label for="reprint-capture-id" class="hint">Reprint by capture ID</label>
    <input
      id="reprint-capture-id"
      type="text"
      bind:value={reprintCaptureId}
      placeholder="e.g. 3f9a2c1e8b7d4f0a"
    />
    <button
      class="primary"
      onclick={runReprint}
      disabled={reprintBusy || !reprintCaptureId.trim()}
    >
      {reprintBusy ? 'Reprinting…' : 'Reprint'}
    </button>
    <p class="hint">
      Bypasses the guest's per-session print limit — use this when a print is ruined or dropped.
      Requires a printer backend to be configured.
    </p>
    {#if reprintResult}
      <p class:success-text={reprintOk} class:error-text={!reprintOk}>{reprintResult}</p>
    {/if}
  </div>
</section>

<style>
  .actions-panel {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    color: var(--color-ink);
  }

  .action {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    align-items: flex-start;
  }

  .hint {
    font-size: 0.85rem;
    color: var(--color-ink-muted);
    margin: 0;
  }

  .result {
    margin: 0;
    font-weight: 600;
  }

  .preview {
    max-width: 20rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    box-shadow: var(--shadow-sm);
  }

  input[type='text'] {
    font-size: 0.95rem;
    padding: 0.45rem 0.6rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-ink);
    width: 100%;
    max-width: 20rem;
  }
</style>
