<!--
  Delivery (upload) target configuration: where a guest's finished photo
  gets uploaded to, and what URL the review screen's QR code links guests
  to once it's there. See src/photobooth/config/models.py's DeliveryConfig
  and web/routers/admin.py's GET/PUT /admin/delivery.

  Secrets (password, private key) are never round-tripped back to this
  panel — GET only reports whether one is set (password_set/private_key_set
  booleans). The password field always starts blank; leaving it blank on
  Save keeps whatever is already saved. A private key is set separately via
  file upload, not through this form's Save.

  Changes here are persisted to config/pi.yaml immediately, but the actual
  delivery worker (built once at app startup) only picks them up after
  Restart App (Actions tab) — same convention as event activation.
-->
<script lang="ts">
  interface DeliverySftp {
    host: string
    port: number
    username: string
    remote_path: string
    password_set: boolean
    private_key_set: boolean
  }

  interface DeliveryInfo {
    backend: 'local' | 'sftp' | 's3'
    sftp: DeliverySftp
    public_base_url: string
  }

  let info = $state<DeliveryInfo | null>(null)
  let loading = $state(true)
  let loadError = $state<string | null>(null)

  let backend = $state<'local' | 'sftp'>('local')
  let host = $state('')
  let port = $state(22)
  let username = $state('')
  let password = $state('')
  let remotePath = $state('')
  let publicBaseUrl = $state('')

  let saveStatus = $state<string | null>(null)
  let saveError = $state<string | null>(null)

  let uploadingKey = $state(false)
  let keyUploadError = $state<string | null>(null)

  let testBusy = $state(false)
  let testResult = $state<{ ok: boolean; detail: string } | null>(null)

  async function load() {
    loading = true
    loadError = null
    try {
      const res = await fetch('/admin/delivery')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const body = (await res.json()) as DeliveryInfo
      info = body
      backend = body.backend === 's3' ? 'local' : body.backend
      host = body.sftp.host
      port = body.sftp.port
      username = body.sftp.username
      remotePath = body.sftp.remote_path
      publicBaseUrl = body.public_base_url
      password = ''
    } catch (err) {
      loadError = err instanceof Error ? err.message : String(err)
    } finally {
      loading = false
    }
  }

  async function save() {
    saveStatus = 'Saving…'
    saveError = null
    try {
      const res = await fetch('/admin/delivery', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          backend,
          sftp: {
            host,
            port,
            username,
            password: password || null,
            remote_path: remotePath,
          },
          public_base_url: publicBaseUrl,
        }),
      })
      const body = await res.json()
      if (!res.ok) throw new Error(body?.detail ?? `HTTP ${res.status}`)
      info = body as DeliveryInfo
      password = ''
      saveStatus = 'Saved. Restart App (Actions tab) to apply it to the running delivery worker.'
    } catch (err) {
      saveError = err instanceof Error ? err.message : String(err)
      saveStatus = null
    }
  }

  async function uploadKey(input: HTMLInputElement) {
    const file = input.files?.[0]
    if (!file) return
    uploadingKey = true
    keyUploadError = null
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch('/admin/delivery/upload-key', { method: 'POST', body: form })
      const body = await res.json()
      if (!res.ok) throw new Error(body?.detail ?? `HTTP ${res.status}`)
      info = body as DeliveryInfo
    } catch (err) {
      keyUploadError = err instanceof Error ? err.message : String(err)
    } finally {
      uploadingKey = false
      input.value = ''
    }
  }

  async function testConnection() {
    testBusy = true
    testResult = null
    try {
      const res = await fetch('/admin/actions/test-delivery', { method: 'POST' })
      const body = await res.json()
      testResult = { ok: Boolean(body.ok), detail: body.detail ?? '' }
    } catch (err) {
      testResult = { ok: false, detail: err instanceof Error ? err.message : String(err) }
    } finally {
      testBusy = false
    }
  }

  load()
</script>

<section class="delivery-panel">
  {#if loading}
    <p>Loading delivery settings…</p>
  {:else if loadError}
    <p class="error">Failed to load: {loadError}</p>
  {:else}
    <p class="note">
      Save persists these settings to disk immediately, but the running delivery worker only
      picks them up after <strong>Restart App</strong> (Actions tab).
    </p>

    <form class="editor" onsubmit={(e) => { e.preventDefault(); save() }}>
      <label>
        Delivery backend
        <select bind:value={backend}>
          <option value="local">Local (saved on this Pi only)</option>
          <option value="sftp">SFTP (upload to a remote server)</option>
        </select>
      </label>

      {#if backend === 'sftp'}
        <label>
          Host
          <input type="text" bind:value={host} placeholder="sftp.example.com" />
        </label>
        <div class="row">
          <label>
            Port
            <input type="number" bind:value={port} min="1" max="65535" />
          </label>
          <label>
            Username
            <input type="text" bind:value={username} />
          </label>
        </div>
        <label>
          Password
          <input
            type="password"
            bind:value={password}
            placeholder={info?.sftp.password_set ? 'saved — leave blank to keep it' : '(none saved)'}
          />
        </label>
        <div class="key-field">
          <span class="image-label">
            Private key {info?.sftp.private_key_set ? '(a key is currently saved)' : '(none saved)'}
          </span>
          <input
            type="file"
            disabled={uploadingKey}
            onchange={(e) => uploadKey(e.currentTarget)}
          />
          {#if uploadingKey}<span class="muted">Uploading…</span>{/if}
          {#if keyUploadError}<p class="error">{keyUploadError}</p>{/if}
        </div>
        <label>
          Remote path
          <input type="text" bind:value={remotePath} placeholder="/uploads" />
        </label>
      {/if}

      <label>
        Public delivery URL (leave blank while testing on the venue Wi-Fi)
        <input
          type="text"
          bind:value={publicBaseUrl}
          placeholder="https://photos.example.com"
        />
        <span class="hint">
          Once set, the review screen's QR code links guests directly to
          <code>{(publicBaseUrl || 'https://photos.example.com').replace(/\/$/, '')}/&lt;photo-id&gt;.jpg</code>
          — that server just needs to serve whatever the backend above uploads into it over HTTP(S);
          it does not need to run this app. Blank: the QR falls back to this app's own share page,
          which only works on the booth's own Wi-Fi.
        </span>
      </label>

      <div class="actions-row">
        <button type="submit">Save</button>
        <button type="button" onclick={testConnection} disabled={testBusy}>
          {testBusy ? 'Testing…' : 'Test Connection'}
        </button>
      </div>

      {#if saveStatus}<span class="status">{saveStatus}</span>{/if}
      {#if saveError}<span class="error">{saveError}</span>{/if}
      {#if testResult}
        <p class:status={testResult.ok} class:error={!testResult.ok}>
          {testResult.ok ? '✓ ' : '✗ '}{testResult.detail}
        </p>
      {/if}
    </form>
  {/if}
</section>

<style>
  .delivery-panel {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    color: var(--color-ink);
  }

  .note {
    font-size: 0.9rem;
    background: var(--color-warning-bg);
    color: var(--color-warning-fg);
    border: 1px solid var(--color-warning-fg);
    padding: 0.75rem;
    border-radius: var(--radius-sm);
    margin: 0;
  }

  .editor {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius);
    padding: 1rem;
    max-width: 32rem;
    background: var(--color-surface);
  }

  .editor label {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    font-size: 0.9rem;
    color: var(--color-ink-muted);
  }

  .editor input[type='text'],
  .editor input[type='password'],
  .editor input[type='number'],
  .editor select {
    font-size: 0.95rem;
    padding: 0.45rem 0.6rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-ink);
  }

  .row {
    display: flex;
    gap: 0.75rem;
  }

  .row label {
    flex: 1;
  }

  .hint {
    font-size: 0.8rem;
    color: var(--color-ink-muted);
  }

  .hint code {
    word-break: break-all;
  }

  .key-field {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    padding: 0.75rem;
    border: 1px dashed var(--color-border);
    border-radius: var(--radius-sm);
  }

  .image-label {
    font-size: 0.9rem;
    color: var(--color-ink-muted);
  }

  .muted {
    font-size: 0.85rem;
    color: var(--color-ink-muted);
  }

  .actions-row {
    display: flex;
    gap: 0.6rem;
  }

  .actions-row button {
    padding: 0.5rem 1.25rem;
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-ink);
    font-weight: 500;
    cursor: pointer;
    transition:
      background-color 200ms var(--ease-spring),
      transform 150ms var(--ease-spring);
  }

  .actions-row button[type='submit'] {
    border: none;
    background: var(--color-primary);
    color: var(--color-primary-contrast);
  }

  .actions-row button[type='submit']:hover {
    background: var(--color-primary-hover);
  }

  .actions-row button:hover {
    background: var(--color-surface-alt);
  }

  .actions-row button:active {
    transform: scale(0.97);
  }

  .error {
    color: var(--color-danger-fg);
  }

  .status {
    color: var(--color-success-fg);
  }
</style>
