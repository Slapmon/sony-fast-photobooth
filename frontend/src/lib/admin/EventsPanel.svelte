<!--
  Event switching + config editor (IMPLEMENTATION_PLAN.md T-3.8).

  Lists events from GET /admin/events, lets the operator pick one to view/edit
  via GET+PUT /admin/events/{id}, and activate it via
  POST /admin/events/{id}/activate.

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

  interface EventConfig {
    id: string
    title: string
    date: string
    template: string
    modes: CaptureMode[]
    background_image: string
    gallery_enabled: boolean
    vars: Record<string, string>
  }

  let events = $state<EventSummary[]>([])
  let loading = $state(true)
  let loadError = $state<string | null>(null)

  let selectedId = $state<string | null>(null)
  let editing = $state<EventConfig | null>(null)
  let editingVarsText = $state('')
  let editingModesText = $state('')
  let editError = $state<string | null>(null)
  let saveStatus = $state<string | null>(null)
  let activateStatus = $state<string | null>(null)

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
    try {
      const res = await fetch(`/admin/events/${encodeURIComponent(id)}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const config: EventConfig = await res.json()
      editing = config
      editingVarsText = JSON.stringify(config.vars ?? {}, null, 2)
      editingModesText = JSON.stringify(config.modes ?? [], null, 2)
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
    const body = { ...editing, vars, modes }
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
        button on the attract screen, e.g. <code>[&#123;"id":"single","label":"Single
        Photo","template":"single.yaml"&#125;]</code>)
        <textarea rows="5" bind:value={editingModesText}></textarea>
      </label>
      <label>
        Background image (relative path)
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
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-ink);
  }

  .activate:hover {
    background: var(--color-surface-alt);
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

  .editor button[type='submit'] {
    align-self: flex-start;
    padding: 0.5rem 1.25rem;
    border: none;
    border-radius: var(--radius-sm);
    background: var(--color-primary);
    color: var(--color-primary-contrast);
  }

  .editor button[type='submit']:hover {
    background: var(--color-primary-hover);
  }

  .error {
    color: var(--color-danger-fg);
  }

  .status {
    color: var(--color-success-fg);
  }
</style>
