<!--
  Attract loop / landing screen (IMPLEMENTATION_PLAN.md T-3.1,
  photobooth-plan.md §7 "Landing / idle screen"). Shown while the session is
  IDLE. Renders the active event's background (image or looping video) with
  title/date overlaid, plus explicit buttons: one per guest-facing capture
  mode (e.g. "Single Photo", "Collage" — GET /session/event's `modes`) and
  one that goes straight to the event's gallery. No implicit "touch anywhere
  to start" and no separate mode-select screen — every choice is a real,
  visible button on this one screen.
-->
<script lang="ts">
  export interface EventMode {
    id: string
    label: string
  }

  export interface EventInfo {
    event_id: string
    title: string
    date: string
    background_image_url: string | null
    modes: EventMode[]
    idle_timeout_s: number
  }

  const {
    event,
    onStart,
    onGallery,
  }: { event: EventInfo | null; onStart: (modeId: string) => void; onGallery: () => void } =
    $props()

  const VIDEO_EXTENSIONS = ['.mp4', '.webm', '.mov', '.m4v']

  function isVideo(url: string): boolean {
    const lower = url.toLowerCase()
    return VIDEO_EXTENSIONS.some((ext) => lower.endsWith(ext))
  }
</script>

<div class="attract">
  {#if event?.background_image_url}
    {#if isVideo(event.background_image_url)}
      <video
        class="background"
        src={event.background_image_url}
        autoplay
        muted
        loop
        playsinline
      ></video>
    {:else}
      <img class="background" src={event.background_image_url} alt="" />
    {/if}
  {/if}

  <div class="scrim"></div>

  <div class="content">
    {#if event?.title}
      <h1 class="title">{event.title}</h1>
    {/if}
    {#if event?.date}
      <p class="date">{event.date}</p>
    {/if}

    <div class="buttons">
      {#each event?.modes ?? [] as mode (mode.id)}
        <button class="mode-btn" onclick={() => onStart(mode.id)}>{mode.label}</button>
      {/each}
      <button class="gallery-btn" onclick={onGallery}>Gallery</button>
    </div>
  </div>
</div>

<style>
  .attract {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    background: #000;
    color: #fff;
    overflow: hidden;
  }

  .background {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  .scrim {
    position: absolute;
    inset: 0;
    background: linear-gradient(to bottom, rgba(0, 0, 0, 0.25) 0%, rgba(0, 0, 0, 0.6) 100%);
  }

  .content {
    position: relative;
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.75rem;
    text-align: center;
    padding: 2rem;
    box-sizing: border-box;
  }

  .title {
    font-size: 3.25rem;
    margin: 0;
    font-weight: 400;
    font-family: var(--font-display);
  }

  .date {
    font-size: 1.5rem;
    margin: 0;
    opacity: 0.85;
  }

  .buttons {
    margin-top: 2.5rem;
    display: flex;
    flex-wrap: wrap;
    gap: 1.25rem;
    justify-content: center;
  }

  .mode-btn,
  .gallery-btn {
    font-size: 1.75rem;
    padding: 1rem 2.75rem;
    border: none;
    border-radius: 999px;
    cursor: pointer;
  }

  .mode-btn {
    background: var(--color-primary);
    color: var(--color-primary-contrast);
    animation: pulse 2s ease-in-out infinite;
  }

  .mode-btn:hover {
    background: var(--color-primary-hover);
  }

  .gallery-btn {
    background: transparent;
    color: #fff;
    border: 2px solid rgba(255, 255, 255, 0.7);
  }

  .gallery-btn:hover {
    background: rgba(255, 255, 255, 0.15);
  }

  @keyframes pulse {
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0.55;
    }
  }
</style>
