<!--
  Thumbnail grid of an event's photos. Per-event enable/disable toggle
  (photobooth-plan.md §7, IMPLEMENTATION_PLAN.md T-3.4/T-3.5).

  event_id discovery (temporary): read from the URL path, `/gallery/<event_id>`.
  App.svelte currently only routes on `path.startsWith('/gallery')`, so this
  component does its own second-level parse. This is a stand-in until the
  landing-page wave's event-info endpoint (if/when it exists) gives the
  frontend a single source of truth for "what event is this kiosk running" —
  at that point this should read from that shared source instead of the URL,
  the same way an admin-configured booth wouldn't want to depend on guests
  typing the right URL. See this task's report for the integration note.

  The backend gives a single generic 404 for both "no such event" and
  "gallery disabled for this event" (photobooth-plan.md §11 — don't reveal
  that a disabled gallery exists). This component matches that: one
  generic "not found" state, no attempt to distinguish the two.
-->
<script lang="ts">
  type Capture = {
    id: string
    created_at: string
    image_url: string
  }

  function eventIdFromPath(): string | null {
    const parts = window.location.pathname.split('/').filter(Boolean)
    // parts[0] === 'gallery'; parts[1], if present, is the event id.
    return parts[1] ?? null
  }

  const eventId = eventIdFromPath()

  let captures = $state<Capture[]>([])
  let loading = $state(true)
  let notFound = $state(false)
  let lightboxUrl = $state<string | null>(null)

  async function loadCaptures() {
    if (eventId === null) {
      loading = false
      notFound = true
      return
    }
    try {
      const response = await fetch(`/gallery/${eventId}/captures`)
      if (response.status === 404) {
        notFound = true
        return
      }
      if (!response.ok) {
        notFound = true
        return
      }
      captures = (await response.json()) as Capture[]
    } catch {
      notFound = true
    } finally {
      loading = false
    }
  }

  function openLightbox(url: string) {
    lightboxUrl = url
  }

  function closeLightbox() {
    lightboxUrl = null
  }

  function handleKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape') closeLightbox()
  }

  $effect(() => {
    loadCaptures()
  })
</script>

<svelte:window onkeydown={handleKeydown} />

<section class="gallery">
  <h1 class="heading">Gallery</h1>

  {#if loading}
    <p class="status">Loading…</p>
  {:else if notFound}
    <p class="status">Gallery not available for this event.</p>
  {:else if captures.length === 0}
    <p class="status">No photos yet.</p>
  {:else}
    <div class="grid">
      {#each captures as capture (capture.id)}
        <button class="thumb" onclick={() => openLightbox(capture.image_url)}>
          <img src={capture.image_url} alt="" loading="lazy" />
        </button>
      {/each}
    </div>
  {/if}
</section>

{#if lightboxUrl}
  <div class="lightbox" onclick={closeLightbox} role="presentation">
    <img src={lightboxUrl} alt="Full size capture" />
  </div>
{/if}

<style>
  .gallery {
    min-height: 100vh;
    padding: 1.5rem clamp(1rem, 4vw, 3rem);
    background: var(--color-bg);
    color: var(--color-ink);
  }

  .heading {
    font-family: var(--font-display);
    font-weight: 400;
    margin: 0 0 1.25rem;
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 0.75rem;
  }

  .thumb {
    padding: 0;
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    cursor: pointer;
    aspect-ratio: 1;
    overflow: hidden;
    border-radius: var(--radius-sm);
    box-shadow: var(--shadow-sm);
  }

  .thumb:hover img {
    transform: scale(1.04);
  }

  .thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    transition: transform 0.15s ease;
  }

  .status {
    color: var(--color-ink-muted);
  }

  .lightbox {
    position: fixed;
    inset: 0;
    background: rgba(20, 16, 12, 0.85);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    z-index: 10;
  }

  .lightbox img {
    max-width: 90vw;
    max-height: 90vh;
    object-fit: contain;
    border-radius: var(--radius);
    box-shadow: var(--shadow-md);
  }
</style>
