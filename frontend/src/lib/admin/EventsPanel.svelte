<!--
  Event switching + config editor (IMPLEMENTATION_PLAN.md T-3.8).

  Lists events from GET /admin/events, lets the operator pick one to view/edit
  via GET+PUT /admin/events/{id}, and activate it via
  POST /admin/events/{id}/activate. Background/logo images are uploaded via
  POST /admin/events/{id}/upload-image (multipart) — that endpoint saves the
  file AND updates the event's background_image/logo_image field immediately,
  no separate Save step for the image itself (Save still applies to every
  other field below, including the theme color).

  IMPORTANT UX caveat surfaced directly in this panel (see this task's report
  for the full explanation): activating an event here updates the attract
  loop and template-preview's "active event" immediately, but an
  already-armed/in-progress guest session was constructed against whatever
  event was active at app startup and will NOT pick up the switch until the
  app restarts. This is flagged inline so an operator isn't surprised
  mid-event.
-->
<script lang="ts">
  interface EventSummary {
    id: string
    title?: string
    date?: string
    template?: string
    gallery_enabled?: boolean
    error?: string
  }

  interface CaptureMode {
    id: string
    label: string
    template: string
  }

  interface EventTheme {
    primary_color: string
  }

  interface EventConfig {
    id: string
    title: string
    date: string
    template: string
    modes: CaptureMode[]
    background_image: string
    logo_image: string
    theme: EventTheme
    gallery_enabled: boolean
    vars: Record<string, string>
    strings: Record<string, string>
  }

  let events = $state<EventSummary[]>([])
  let loading = $state(true)
  let loadError = $state<string | null>(null)

  let selectedId = $state<string | null>(null)
  let editing = $state<EventConfig | null>(null)
  let editingVarsText = $state('')
  let editingModesText = $state('')
  let editingStringsText = $state('')
  let editError = $state<string | null>(null)
  let saveStatus = $state<string | null>(null)
  let activateStatus = $state<string | null>(null)

  let uploadingBackground = $state(false)
  let uploadingLogo = $state(false)
  let uploadError = $state<string | null>(null)

  async function loadEvents() {
    loading = true
    loadError = null
    try {
      const res = await fetch('/admin/events')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      events = await res.json()
    } catch (err) {
      loadError = err instanceof Error ? err.message : String(err)
    } finally {
      loading = false
    }
  }

  async function selectEvent(id: string) {
    selectedId = id
    editing = null
    editError = null
    saveStatus = null
    uploadError = null
    try {
      const res = await fetch(`/admin/events/${encodeURIComponent(id)}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const config: EventConfig = await res.json()
      editing = config
      editingVarsText = JSON.stringify(config.vars ?? {}, null, 2)
      editingModesText = JSON.stringify(config.modes ?? [], null, 2)
      editingStringsText = JSON.stringify(config.strings ?? {}, null, 2)
    } catch (err) {
      editError = err instanceof Error ? err.message : String(err)
    }
  }

  async function saveEvent() {
    if (!editing) return
    saveStatus = 'Saving…'
    editError = null
    let vars: Record<string, string>
    try {
      vars = JSON.parse(editingVarsText || '{}')
    } catch {
      editError = 'vars must be valid JSON (an object of string keys/values)'
      saveStatus = null
      return
    }
    let modes: CaptureMode[]
    try {
      modes = JSON.parse(editingModesText || '[]')
    } catch {
      editError = 'modes must be valid JSON (an array of {id, label, template})'
      saveStatus = null
      return
    }
    let strings: Record<string, string>
    try {
      strings = JSON.parse(editingStringsText || '{}')
    } catch {
      editError = 'strings must be valid JSON (an object of string keys/values)'
      saveStatus = null
      return
    }
    const body = { ...editing, vars, modes, strings }
    try {
      const res = await fetch(`/admin/events/${encodeURIComponent(editing.id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      editing = await res.json()
      saveStatus = 'Saved.'
      await loadEvents()
    } catch (err) {
      editError = err instanceof Error ? err.message : String(err)
      saveStatus = null
    }
  }

  async function activateEvent(id: string) {
    activateStatus = 'Activating…'
    try {
      const res = await fetch(`/admin/events/${encodeURIComponent(id)}/activate`, {
        method: 'POST',
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      activateStatus = `Activated "${id}". Note: an in-progress guest session won't see this until the app restarts (see panel note above).`
    } catch (err) {
      activateStatus = `Failed: ${err instanceof Error ? err.message : String(err)}`
    }
  }

  async function uploadImage(kind: 'background' | 'logo', input: HTMLInputElement) {
    const file = input.files?.[0]
    if (!editing || !file) return
    if (kind === 'background') uploadingBackground = true
    else uploadingLogo = true
    uploadError = null
    try {
      const form = new FormData()
      form.append('kind', kind)
      form.append('file', file)
      const res = await fetch(`/admin/events/${encodeURIComponent(editing.id)}/upload-image`, {
        method: 'POST',
        body: form,
      })
      const responseBody = await res.json()
      if (!res.ok) throw new Error(responseBody?.detail ?? `HTTP ${res.status}`)
      editing = responseBody as EventConfig
      await loadEvents()
    } catch (err) {
      uploadError = err instanceof Error ? err.message : String(err)
    } finally {
      uploadingBackground = false
      uploadingLogo = false
      input.value = ''
    }
  }

  loadEvents()
</script>

<section class="events-panel">
  <p class="note">
    Activating an event here takes effect immediately for the attract loop and template preview.
    It does <strong>not</strong> retroactively affect an already-running app process's guest
    capture flow — that only picks up the new event on the next app restart.
  </p>

  {#if loading}
    <p>Loading events…</p>
  {:else if loadError}
    <p class="error">Failed to load events: {loadError}</p>
  {:else if events.length === 0}
    <p>No events found under the configured events directory.</p>
  {:else}
    <ul class="event-list">
      {#each events as ev (ev.id)}
        <li class:selected={ev.id === selectedId}>
          <button class="event-row" onclick={() => selectEvent(ev.id)}>
            <strong>{ev.id}</strong>
            {#if ev.error}
              <span class="error">— failed to load: {ev.error}</span>
            {:else}
              <span>{ev.title} · {ev.date} · {ev.template}</span>
            {/if}
          </button>
          <button class="activate" onclick={() => activateEvent(ev.id)}>Activate</button>
        </li>
      {/each}
    </ul>
  {/if}

  {#if activateStatus}
    <p class="status">{activateStatus}</p>
  {/if}

  {#if editing}
    <form class="editor" onsubmit={(e) => { e.preventDefault(); saveEvent() }}>
      <h3>Edit "{editing.id}"</h3>
      <label>
        Title
        <input type="text" bind:value={editing.title} />
      </label>
      <label>
        Date
        <input type="text" bind:value={editing.date} />
      </label>
      <label>
        Template (legacy fallback — used only if no modes are listed below)
        <input type="text" bind:value={editing.template} />
      </label>
      <label>
        Guest-facing capture modes (JSON array of {'{id, label, template}'} — each becomes a
        button at the bottom of the attract/review/gallery screens, e.g. <code
          >[&#123;"id":"single","label":"Single Photo","template":"single.yaml"&#125;]</code
        >)
        <textarea rows="5" bind:value={editingModesText}></textarea>
      </label>

      <div class="image-field">
        <span class="image-label">Background image or video</span>
        {#if editing.background_image}
          <img class="image-preview" src={`/session/event/background?_r=${Date.now()}`} alt="" />
        {/if}
        <input
          type="file"
          accept="image/*,video/*"
          disabled={uploadingBackground}
          onchange={(e) => uploadImage('background', e.currentTarget)}
        />
        {#if uploadingBackground}<span class="muted">Uploading…</span>{/if}
      </div>

      <div class="image-field">
        <span class="image-label">Logo (shown centered over the background)</span>
        {#if editing.logo_image}
          <img class="image-preview logo" src={`/session/event/logo?_r=${Date.now()}`} alt="" />
        {/if}
        <input
          type="file"
          accept="image/*"
          disabled={uploadingLogo}
          onchange={(e) => uploadImage('logo', e.currentTarget)}
        />
        {#if uploadingLogo}<span class="muted">Uploading…</span>{/if}
      </div>

      {#if uploadError}<p class="error">{uploadError}</p>{/if}

      <label>
        Accent color (buttons, highlights — leave blank for the app default)
        <div class="color-row">
          <input
            type="color"
            value={editing.theme.primary_color || '#dc9c39'}
            oninput={(e) => (editing!.theme.primary_color = e.currentTarget.value)}
          />
          <input
            type="text"
            placeholder="#dc9c39 (blank = default)"
            bind:value={editing.theme.primary_color}
          />
        </div>
      </label>

      <label>
        Background image (relative path — set automatically by the upload above)
        <input type="text" bind:value={editing.background_image} />
      </label>
      <label class="checkbox">
        <input type="checkbox" bind:checked={editing.gallery_enabled} />
        Gallery enabled
      </label>
      <label>
        Vars (JSON object)
        <textarea rows="5" bind:value={editingVarsText}></textarea>
      </label>
      <label>
        Guest-facing text overrides (JSON object — for other languages, e.g. <code
          >&#123;"attract_cta":"Berühre einen Knopf","gallery_word":"Galerie"&#125;</code
        >. Any key you leave out uses the English default. Known keys: attract_cta,
        gallery_word, capturing_label, print_button, print_button_busy, qr_caption,
        gallery_loading, gallery_empty, gallery_not_available.)
        <textarea rows="6" bind:value={editingStringsText}></textarea>
      </label>
      <button type="submit">Save</button>
      {#if saveStatus}<span class="status">{saveStatus}</span>{/if}
      {#if editError}<span class="error">{editError}</span>{/if}
    </form>
  {/if}
</section>

<style>
  .events-panel {
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

  .event-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }

  .event-list li {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    padding: 0.25rem;
    background: var(--color-surface);
  }

  .event-list li.selected {
    border-color: var(--color-primary);
    box-shadow: 0 0 0 1px var(--color-primary);
  }

  .event-row {
    flex: 1;
    text-align: left;
    background: none;
    border: none;
    cursor: pointer;
    padding: 0.5rem;
    display: flex;
    gap: 0.5rem;
    align-items: baseline;
    color: var(--color-ink);
  }

  .event-row span:not(.error) {
    color: var(--color-ink-muted);
  }

  .activate {
    padding: 0.45rem 0.85rem;
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-ink);
    font-weight: 500;
    transition:
      background-color 200ms var(--ease-spring),
      transform 150ms var(--ease-spring);
  }

  .activate:hover {
    background: var(--color-surface-alt);
  }

  .activate:active {
    transform: scale(0.96);
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

  .editor h3 {
    margin: 0;
  }

  .editor label {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    font-size: 0.9rem;
    color: var(--color-ink-muted);
  }

  .editor label.checkbox {
    flex-direction: row;
    align-items: center;
    color: var(--color-ink);
  }

  .editor input[type='text'],
  .editor textarea {
    font-size: 0.95rem;
    padding: 0.45rem 0.6rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-ink);
  }

  .image-field {
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

  .image-preview {
    max-width: 100%;
    max-height: 8rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--color-border);
    align-self: flex-start;
    object-fit: contain;
    background: var(--color-surface-alt);
  }

  .image-preview.logo {
    max-height: 5rem;
  }

  .muted {
    font-size: 0.85rem;
    color: var(--color-ink-muted);
  }

  .color-row {
    display: flex;
    gap: 0.5rem;
    align-items: center;
  }

  .color-row input[type='color'] {
    width: 2.5rem;
    height: 2.5rem;
    padding: 0.15rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
  }

  .color-row input[type='text'] {
    flex: 1;
  }

  .editor button[type='submit'] {
    align-self: flex-start;
    padding: 0.5rem 1.25rem;
    border: none;
    border-radius: var(--radius-sm);
    background: var(--color-primary);
    color: var(--color-primary-contrast);
    font-weight: 500;
    transition:
      background-color 200ms var(--ease-spring),
      transform 150ms var(--ease-spring);
  }

  .editor button[type='submit']:hover {
    background: var(--color-primary-hover);
  }

  .editor button[type='submit']:active {
    transform: scale(0.97);
  }

  .error {
    color: var(--color-danger-fg);
  }

  .status {
    color: var(--color-success-fg);
  }
</style>
