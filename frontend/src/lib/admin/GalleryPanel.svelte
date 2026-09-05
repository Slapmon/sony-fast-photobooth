<!--
  Admin gallery management: pick an event, browse its captures, delete
  individual photos. Uses GET /admin/events/{id}/captures — an admin-scoped
  listing that (unlike the guest-facing GET /gallery/{id}/captures) ignores
  EventConfig.gallery_enabled, since an operator must be able to review and
  delete photos even for an event whose public gallery is turned off.

  Deletion (DELETE /admin/captures/{id}) removes the local file + DB row
  only — it does not reach into a remote SFTP/S3 delivery target the photo
  may already have been uploaded to (see admin.py's delete_capture_action
  docstring for why that's out of scope here).
-->
<script lang="ts">
  interface EventSummary {
    id: string
    title?: string
    error?: string
  }

  interface Capture {
    id: string
    created_at: string
    image_url: string
  }

  let events = $state<EventSummary[]>([])
  let loadingEvents = $state(true)
  let eventsError = $state<string | null>(null)

  let selectedEventId = $state<string | null>(null)
  let captures = $state<Capture[]>([])
  let loadingCaptures = $state(false)
  let capturesError = $state<string | null>(null)

  let deletingId = $state<string | null>(null)
  let deleteError = $state<string | null>(null)

  let lightboxUrl = $state<string | null>(null)

  async function loadEvents() {
    loadingEvents = true
    eventsError = null
    try {
      const res = await fetch('/admin/events')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      events = await res.json()
    } catch (err) {
      eventsError = err instanceof Error ? err.message : String(err)
    } finally {
      loadingEvents = false
    }
  }

  async function selectEvent(eventId: string) {
    selectedEventId = eventId
    captures = []
    capturesError = null
    deleteError = null
    loadingCaptures = true
    try {
      const res = await fetch(`/admin/events/${encodeURIComponent(eventId)}/captures`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      captures = await res.json()
    } catch (err) {
      capturesError = err instanceof Error ? err.message : String(err)
    } finally {
      loadingCaptures = false
    }
  }

  async function deleteCapture(captureId: string) {
    if (!confirm('Delete this photo? This cannot be undone.')) return
    deletingId = captureId
    deleteError = null
    try {
      const res = await fetch(`/admin/captures/${encodeURIComponent(captureId)}`, {
        method: 'DELETE',
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body?.detail ?? `HTTP ${res.status}`)
      }
      captures = captures.filter((c) => c.id !== captureId)
    } catch (err) {
      deleteError = err instanceof Error ? err.message : String(err)
    } finally {
      deletingId = null
    }
  }

  function openLightbox(url: string) {
    lightboxUrl = url
  }

  function closeLightbox() {
    lightboxUrl = null
  }

  loadEvents()
</script>

<section class="gallery-panel">
  {#if loadingEvents}
    <p>Loading events…</p>
  {:else if eventsError}
    <p class="error">Failed to load events: {eventsError}</p>
  {:else if events.length === 0}
    <p>No events found.</p>
  {:else}
    <div class="event-picker">
      {#each events as ev (ev.id)}
        <button
          class="event-btn"
          class:active={ev.id === selectedEventId}
          onclick={() => selectEvent(ev.id)}
        >
          {ev.title || ev.id}
        </button>
      {/each}
    </div>

    {#if selectedEventId}
      {#if loadingCaptures}
        <p>Loading photos…</p>
      {:else if capturesError}
        <p class="error">Failed to load photos: {capturesError}</p>
      {:else if captures.length === 0}
        <p class="muted">No photos yet for this event.</p>
      {:else}
        {#if deleteError}<p class="error">{deleteError}</p>{/if}
        <div class="grid">
          {#each captures as capture (capture.id)}
            <div class="thumb-card">
              <button class="thumb" onclick={() => openLightbox(capture.image_url)}>
                <img src={capture.image_url} alt="" loading="lazy" />
              </button>
              <button
                class="delete-btn"
                disabled={deletingId === capture.id}
                onclick={() => deleteCapture(capture.id)}
              >
                {deletingId === capture.id ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          {/each}
        </div>
      {/if}
    {/if}
  {/if}
</section>

{#if lightboxUrl}
  <div class="lightbox" onclick={closeLightbox} role="presentation">
    <img src={lightboxUrl} alt="Full size capture" />
  </div>
{/if}

<style>
  .gallery-panel {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    color: var(--color-ink);
  }

  .event-picker {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .event-btn {
    padding: 0.5rem 1rem;
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-pill);
    background: var(--color-surface);
    color: var(--color-ink);
    cursor: pointer;
    transition:
      background-color 200ms var(--ease-spring),
      color 200ms var(--ease-spring);
  }

  .event-btn:hover {
    background: var(--color-surface-alt);
  }

  .event-btn.active {
    background: var(--color-primary);
    color: var(--color-primary-contrast);
    border-color: transparent;
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 0.75rem;
  }

  .thumb-card {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }

  .thumb {
    padding: 0;
    border: 1px solid var(--color-border);
    background: var(--color-surface-alt);
    cursor: pointer;
    aspect-ratio: 1;
    overflow: hidden;
    border-radius: var(--radius-sm);
    box-shadow: var(--shadow-sm);
  }

  .thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }

  .delete-btn {
    padding: 0.4rem 0.6rem;
    border: 1px solid var(--color-danger-fg);
    border-radius: var(--radius-sm);
    background: none;
    color: var(--color-danger-fg);
    cursor: pointer;
    font-size: 0.85rem;
  }

  .delete-btn:disabled {
    opacity: 0.5;
    cursor: default;
  }

  .muted {
    color: var(--color-ink-muted);
  }

  .error {
    color: var(--color-danger-fg);
  }

  .lightbox {
    position: fixed;
    inset: 0;
    background: color-mix(in srgb, var(--color-scrim) 85%, transparent);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    z-index: 20;
  }

  .lightbox img {
    max-width: 90vw;
    max-height: 90vh;
    object-fit: contain;
    border-radius: var(--radius-sm);
  }
</style>
