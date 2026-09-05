<!--
  Event switching + config editor (IMPLEMENTATION_PLAN.md T-3.8) + the
  create/duplicate/delete event tool (see the approved event-tool plan).

  Lists events from GET /admin/events, lets the operator pick one to view/edit
  via GET+PUT /admin/events/{id}, and activate it via
  POST /admin/events/{id}/activate. Background/logo images are uploaded via
  POST /admin/events/{id}/upload-image (multipart) — that endpoint saves the
  file AND updates the event's background_image/logo_image field immediately,
  no separate Save step for the image itself (Save still applies to every
  other field below, including the theme colors).

  "New Event" opens EventWizard (template presets + guided setup). Duplicate
  and Delete are per-row actions; Delete is blocked (409 from the backend)
  for the currently active event — active_event_id is read from the
  existing public GET /session/event (there's no admin-scoped equivalent),
  best-effort only: if that fetch fails, Delete just stays enabled for every
  row rather than blocking the whole panel.

  IMPORTANT UX caveat surfaced directly in this panel (see this task's report
  for the full explanation): activating an event here updates the attract
  loop and template-preview's "active event" immediately AND persists it to
  the config file on disk, but the actual guest capture flow's own
  active-event reference is still frozen at app-startup time — it does NOT
  pick up the switch until the app restarts (Actions tab -> Restart app).
  This is flagged inline so an operator isn't surprised mid-event.
-->
<script lang="ts">
  import EventWizard from './EventWizard.svelte'
  import ModesEditor from './ModesEditor.svelte'
  import VarsEditor from './VarsEditor.svelte'
  import StringsEditor from './StringsEditor.svelte'

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
    scrim_color: string
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
    countdown_s: number
    include_logo_in_prints: boolean
    gallery_enabled: boolean
    vars: Record<string, string>
    strings: Record<string, string>
  }

  let events = $state<EventSummary[]>([])
  let loading = $state(true)
  let loadError = $state<string | null>(null)
  let activeEventId = $state<string | null>(null)

  let selectedId = $state<string | null>(null)
  let editing = $state<EventConfig | null>(null)
  let editError = $state<string | null>(null)
  let saveStatus = $state<string | null>(null)
  let activateStatus = $state<string | null>(null)
  let rowActionError = $state<string | null>(null)

  let uploadingBackground = $state(false)
  let uploadingLogo = $state(false)
  let uploadError = $state<string | null>(null)

  let showWizard = $state(false)
  let duplicatingId = $state<string | null>(null)
  let duplicateNewId = $state('')
  let duplicateNewTitle = $state('')

  async function loadEvents() {
    loading = true
    loadError = null
    try {
      const res = await fetch('/admin/events')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      events = await res.json()
      try {
        const activeRes = await fetch('/session/event')
        if (activeRes.ok) {
          const active = await activeRes.json()
          activeEventId = active.event_id ?? null
        }
      } catch {
        // best-effort only — active-event indication (disabling Delete on
        // it) is a nice-to-have here, not load-bearing for the rest of the
        // panel
      }
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
      if (!config.theme) config.theme = { primary_color: '', scrim_color: '' }
      editing = config
    } catch (err) {
      editError = err instanceof Error ? err.message : String(err)
    }
  }

  async function saveEvent() {
    if (!editing) return
    saveStatus = 'Saving…'
    editError = null
    try {
      const res = await fetch(`/admin/events/${encodeURIComponent(editing.id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(editing),
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
      activeEventId = id
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

  function onWizardCreated(id: string) {
    showWizard = false
    loadEvents()
    selectEvent(id)
  }

  function startDuplicate(ev: EventSummary) {
    duplicatingId = ev.id
    duplicateNewId = `${ev.id}-copy`
    duplicateNewTitle = ev.title ? `${ev.title} (copy)` : ''
    rowActionError = null
  }

  async function confirmDuplicate() {
    if (!duplicatingId) return
    try {
      const res = await fetch(`/admin/events/${encodeURIComponent(duplicatingId)}/duplicate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_id: duplicateNewId, new_title: duplicateNewTitle }),
      })
      const body = await res.json()
      if (!res.ok) throw new Error(body?.detail ?? `HTTP ${res.status}`)
      duplicatingId = null
      await loadEvents()
      await selectEvent(body.id)
    } catch (err) {
      rowActionError = err instanceof Error ? err.message : String(err)
    }
  }

  async function deleteEvent(id: string) {
    if (!confirm(`Delete event "${id}"? This cannot be undone.`)) return
    rowActionError = null
    try {
      const res = await fetch(`/admin/events/${encodeURIComponent(id)}`, { method: 'DELETE' })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body?.detail ?? `HTTP ${res.status}`)
      }
      if (selectedId === id) {
        selectedId = null
        editing = null
      }
      await loadEvents()
    } catch (err) {
      rowActionError = err instanceof Error ? err.message : String(err)
    }
  }

  loadEvents()
</script>

<section class="events-panel">
  <p class="note">
    Activating an event here takes effect immediately for the attract loop and template preview.
    It does <strong>not</strong> retroactively affect the running app process's actual guest
    capture flow — use the Actions tab's <strong>Restart app</strong> button after activating a
    new event to make sure captures use it too.
  </p>

  <div class="toolbar">
    <button type="button" class="new-event" onclick={() => (showWizard = true)}>+ New Event</button>
  </div>

  {#if rowActionError}<p class="error">{rowActionError}</p>{/if}

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
          <button class="secondary" onclick={() => startDuplicate(ev)}>Duplicate</button>
          <button
            class="secondary danger"
            disabled={ev.id === activeEventId}
            title={ev.id === activeEventId ? 'Cannot delete the active event' : ''}
            onclick={() => deleteEvent(ev.id)}
          >
            Delete
          </button>
        </li>
        {#if duplicatingId === ev.id}
          <li class="duplicate-form">
            <label>
              New id
              <input type="text" bind:value={duplicateNewId} />
            </label>
            <label>
              New title
              <input type="text" bind:value={duplicateNewTitle} />
            </label>
            <button type="button" class="activate" onclick={confirmDuplicate}>Create copy</button>
            <button type="button" class="secondary" onclick={() => (duplicatingId = null)}>Cancel</button>
          </li>
        {/if}
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
        Countdown before each shot (seconds)
        <input
          type="number"
          min="1"
          max="15"
          step="0.5"
          bind:value={editing.countdown_s}
        />
      </label>

      <fieldset>
        <legend>Capture modes</legend>
        <ModesEditor bind:modes={editing.modes} />
      </fieldset>

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

      <label class="checkbox">
        <input type="checkbox" bind:checked={editing.include_logo_in_prints} />
        Include logo in the bottom-right corner of printed/delivered collage and strip photos
      </label>

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
        Backdrop color (tinted dark scrim behind every guest screen — leave blank for the app
        default)
        <div class="color-row">
          <input
            type="color"
            value={editing.theme.scrim_color || '#0f0c09'}
            oninput={(e) => (editing!.theme.scrim_color = e.currentTarget.value)}
          />
          <input
            type="text"
            placeholder="#0f0c09 (blank = default)"
            bind:value={editing.theme.scrim_color}
          />
        </div>
      </label>

      <label class="checkbox">
        <input type="checkbox" bind:checked={editing.gallery_enabled} />
        Gallery enabled
      </label>

      <fieldset>
        <legend>Placeholder variables ({'{event.<key>}'} in template text)</legend>
        <VarsEditor bind:vars={editing.vars} />
      </fieldset>

      <fieldset>
        <legend>Guest-facing text overrides (for other languages)</legend>
        <StringsEditor bind:strings={editing.strings} />
      </fieldset>

      <button type="submit">Save</button>
      {#if saveStatus}<span class="status">{saveStatus}</span>{/if}
      {#if editError}<span class="error">{editError}</span>{/if}
    </form>
  {/if}
</section>

{#if showWizard}
  <EventWizard onclose={() => (showWizard = false)} oncreated={onWizardCreated} />
{/if}

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

  .toolbar {
    display: flex;
    justify-content: flex-end;
  }

  .new-event {
    padding: 0.55rem 1.1rem;
    border: none;
    border-radius: var(--radius-sm);
    background: var(--color-primary);
    color: var(--color-primary-contrast);
    font-weight: 500;
    cursor: pointer;
    transition:
      background-color 200ms var(--ease-spring),
      transform 150ms var(--ease-spring);
  }

  .new-event:hover {
    background: var(--color-primary-hover);
  }

  .new-event:active {
    transform: scale(0.97);
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

  .duplicate-form {
    flex-wrap: wrap;
    padding: 0.6rem !important;
  }

  .duplicate-form label {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    font-size: 0.8rem;
    color: var(--color-ink-muted);
  }

  .duplicate-form input {
    font-size: 0.9rem;
    padding: 0.35rem 0.5rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-ink);
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

  .activate,
  .secondary {
    padding: 0.45rem 0.85rem;
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

  .activate:hover,
  .secondary:hover {
    background: var(--color-surface-alt);
  }

  .activate:active,
  .secondary:active {
    transform: scale(0.96);
  }

  .secondary.danger {
    color: var(--color-danger-fg);
  }

  .secondary:disabled {
    opacity: 0.4;
    cursor: default;
  }

  .editor {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius);
    padding: 1rem;
    max-width: 36rem;
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

  .editor input[type='text'] {
    font-size: 0.95rem;
    padding: 0.45rem 0.6rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-ink);
  }

  .editor fieldset {
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    padding: 0.75rem;
  }

  .editor legend {
    font-size: 0.85rem;
    color: var(--color-ink-muted);
    padding: 0 0.3rem;
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
