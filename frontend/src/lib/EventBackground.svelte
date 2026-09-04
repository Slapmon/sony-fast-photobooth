<!--
  Shared branded backdrop: an event's background image/video with an
  optional centered logo on top. Used behind the attract screen and the
  gallery page — anywhere a guest should see the event's own look, not the
  live camera preview (that's specific to the active capture flow, see
  Kiosk.svelte).
-->
<script lang="ts">
  export interface BackgroundInfo {
    background_image_url: string | null
    logo_image_url: string | null
  }

  const { info }: { info: BackgroundInfo | null } = $props()

  const VIDEO_EXTENSIONS = ['.mp4', '.webm', '.mov', '.m4v']

  function isVideo(url: string): boolean {
    const lower = url.toLowerCase()
    return VIDEO_EXTENSIONS.some((ext) => lower.endsWith(ext))
  }
</script>

<div class="backdrop">
  {#if info?.background_image_url}
    {#if isVideo(info.background_image_url)}
      <video
        class="background"
        src={info.background_image_url}
        autoplay
        muted
        loop
        playsinline
      ></video>
    {:else}
      <img class="background" src={info.background_image_url} alt="" />
    {/if}
  {/if}

  <div class="scrim"></div>

  {#if info?.logo_image_url}
    <img class="logo" src={info.logo_image_url} alt="" />
  {/if}
</div>

<style>
  .backdrop {
    position: absolute;
    inset: 0;
    background: #000;
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

  .logo {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    max-width: min(40vw, 20rem);
    max-height: min(40vh, 20rem);
    object-fit: contain;
    filter: drop-shadow(0 4px 16px rgba(0, 0, 0, 0.4));
  }
</style>
