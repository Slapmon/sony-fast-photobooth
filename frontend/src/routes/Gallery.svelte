<!--
  Thumbnail grid of an event's photos. Per-event enable/disable toggle
  (photobooth-plan.md §7, IMPLEMENTATION_PLAN.md T-3.4/T-3.5). Shares the
  same branded backdrop (EventBackground) and persistent bottom nav
  (BottomNav) as the kiosk's attract/review screens — a guest shouldn't
  have to relearn the controls just because this is a different route.

  event_id discovery (temporary): read from the URL path, `/gallery/<event_id>`.
  App.svelte currently only routes on `path.startsWith('/gallery')`, so this
  component does its own second-level parse.

  The backend gives a single generic 404 for both "no such event" and
  "gallery disabled for this event" (photobooth-plan.md §11 — don't reveal
  that a disabled gallery exists). This component matches that: one
  generic "not found" state, no attempt to distinguish the two.
-->
<script lang="ts">
  import BottomNav, { type NavMode } from '../lib/BottomNav.svelte'
  import EventBackground, { type BackgroundInfo } from '../lib/EventBackground.svelte'
  import { themeStyle, type EventThemeInfo } from '../lib/theme'

  type Capture = {
    id: string
    created_at: string
    image_url: string
  }

  type GalleryInfo = BackgroundInfo & {
    title: string
    theme: EventThemeInfo
    modes: NavMode[]
    strings: Record<string, string>
  }

  function eventIdFromPath(): string | null {
    const parts = window.location.pathname.split('/').filter(Boolean)
    // parts[0] === 'gallery'; parts[1], if present, is the event id.
    return parts[1] ?? null
  }

  const eventId = eventIdFromPath()

  let captures = $state<Capture[]>([])
  let info = $state<GalleryInfo | null>(null)
  let loading = $state(true)
  let notFound = $state(false)
  let lightboxUrl = $state<string | null>(null)

  async function loadGallery() {
    if (eventId === null) {
      loading = false
      notFound = true
      return
    }
    try {
      const [capturesRes, infoRes] = await Promise.all([
        fetch(`/gallery/${eventId}/captures`),
        fetch(`/gallery/${eventId}/info`),
      ])
      if (!capturesRes.ok || !infoRes.ok) {
        notFound = true
        return
      }
      captures = (await capturesRes.json()) as Capture[]
      info = (await infoRes.json()) as GalleryInfo
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

  function handleStart(modeId: string) {
    // No shared session state with the kiosk route (this is a separate
    // page load) — hand the choice off via a query param Kiosk.svelte
    // auto-starts on load, so it's still a one-tap action for the guest.
    window.location.href = `/?mode=${encodeURIComponent(modeId)}`
  }

  function handleGallery() {
    // Already here — a no-op, the button just reads as "active".
  }

  $effect(() => {
    loadGallery()
  })
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="page" style={themeStyle(info?.theme)}>
  <EventBackground info={info} />

  <section class="gallery">
    <h1 class="heading">
      {info?.title
        ? `${info.title} — ${info.strings.gallery_word ?? 'Gallery'}`
        : (info?.strings.gallery_word ?? 'Gallery')}
    </h1>

    {#if loading}
      <p class="status">{info?.strings.gallery_loading ?? 'Loading…'}</p>
    {:else if notFound}
      <p class="status">
        {info?.strings.gallery_not_available ?? 'Gallery not available for this event.'}
      </p>
    {:else if captures.length === 0}
      <p class="status">{info?.strings.gallery_empty ?? 'No photos yet.'}</p>
    {:else}
      <div class="grid">
        {#each captures as capture, i (capture.id)}
          <button
            class="thumb rise-in"
            style="animation-delay: {Math.min(i * 40, 480)}ms"
            onclick={() => openLightbox(capture.image_url)}
          >
            <img src={capture.image_url} alt="" loading="lazy" />
          </button>
        {/each}
      </div>
    {/if}
  </section>

  {#if info}
    <BottomNav
      modes={info.modes}
      onStart={handleStart}
      onGallery={handleGallery}
      galleryLabel={info.strings.gallery_word ?? 'Gallery'}
      galleryActive
    />
  {/if}
</div>

{#if lightboxUrl}
  <div class="lightbox" onclick={closeLightbox} role="presentation">
    <div class="photo-frame">
      <img src={lightboxUrl} alt="Full size capture" />
    </div>
  </div>
{/if}

<style>
  .page {
    position: relative;
    min-height: 100vh;
    background: #000;
  }

  .gallery {
    position: relative;
    min-height: 100vh;
    padding: 1.5rem clamp(1rem, 4vw, 3rem) 8rem;
    box-sizing: border-box;
    color: #fff;
  }

  .heading {
    font-family: var(--font-display);
    font-weight: 400;
    font-size: 2.25rem;
    letter-spacing: -0.01em;
    margin: 0 0 1.5rem;
    text-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 0.75rem;
  }

  .thumb {
    padding: 0;
    border: 1px solid rgba(255, 255, 255, 0.2);
    background: rgba(0, 0, 0, 0.3);
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
    transition: transform 200ms var(--ease-spring);
  }

  .status {
    color: #fff;
    opacity: 0.85;
    text-shadow: 0 1px 4px rgba(0, 0, 0, 0.5);
  }

  .lightbox {
    position: fixed;
    inset: 0;
    background: color-mix(in srgb, var(--color-scrim) 85%, transparent);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    z-index: 10;
  }

  .lightbox .photo-frame {
    max-width: 90vw;
    max-height: 90vh;
    animation: rise-in 220ms var(--ease-spring) both;
  }

  .lightbox .photo-frame img {
    max-width: calc(90vw - 1rem);
    max-height: calc(90vh - 1rem);
    object-fit: contain;
  }
</style>
