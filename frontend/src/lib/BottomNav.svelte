<!--
  Persistent bottom nav: one button per guest-facing capture mode, plus a
  Gallery button. Used on the kiosk's attract and review screens (replacing
  the old "Done" button on review — picking a mode from review starts a new
  session directly instead) and on the standalone gallery page. Rendering
  the exact same bar everywhere is the point: a guest never has to relearn
  where the controls are.
-->
<script lang="ts">
  export interface NavMode {
    id: string
    label: string
  }

  const {
    modes,
    onStart,
    onGallery,
    galleryActive = false,
    galleryLabel = 'Gallery',
  }: {
    modes: NavMode[]
    onStart: (modeId: string) => void
    onGallery: () => void
    galleryActive?: boolean
    galleryLabel?: string
  } = $props()
</script>

<nav class="bottom-nav">
  {#each modes as mode, i (mode.id)}
    <button
      class="mode-btn rise-in"
      style="animation-delay: {i * 70}ms"
      onclick={() => onStart(mode.id)}
    >
      {mode.label}
    </button>
  {/each}
  <button
    class="gallery-btn rise-in"
    style="animation-delay: {modes.length * 70}ms"
    class:active={galleryActive}
    onclick={onGallery}
  >
    {galleryLabel}
  </button>
</nav>

<style>
  .bottom-nav {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 5;
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 1rem;
    padding: 1.5rem 2rem calc(1.5rem + env(safe-area-inset-bottom, 0px));
    background: linear-gradient(to top, rgba(0, 0, 0, 0.6) 0%, rgba(0, 0, 0, 0) 100%);
  }

  .mode-btn,
  .gallery-btn {
    font-size: 1.25rem;
    font-weight: 600;
    padding: 0.85rem 2.25rem;
    border: none;
    border-radius: var(--radius);
    cursor: pointer;
    transition:
      background-color 200ms var(--ease-spring),
      transform 150ms var(--ease-spring),
      border-color 200ms var(--ease-spring);
  }

  .mode-btn:active,
  .gallery-btn:active {
    transform: scale(0.96);
  }

  .mode-btn {
    background: var(--color-primary);
    color: var(--color-primary-contrast);
    box-shadow: var(--shadow-md);
  }

  .mode-btn:hover {
    background: var(--color-primary-hover);
  }

  .gallery-btn {
    background: rgba(255, 255, 255, 0.08);
    color: #fff;
    border: 1.5px solid rgba(255, 255, 255, 0.55);
  }

  .gallery-btn:hover,
  .gallery-btn.active {
    background: rgba(255, 255, 255, 0.18);
    border-color: #fff;
  }
</style>
