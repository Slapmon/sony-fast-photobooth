<script lang="ts">
  // Minimal path-based switch for now — one Vite app, three routes
  // (kiosk / gallery / admin) per IMPLEMENTATION_PLAN.md §2. Revisit with a
  // real router once admin auth (T-3.7) needs guarding.
  import Kiosk from './routes/Kiosk.svelte'
  import Gallery from './routes/Gallery.svelte'
  import Admin from './routes/Admin.svelte'

  const path = window.location.pathname
</script>

{#if path.startsWith('/gallery')}
  <Gallery />
{:else if path.startsWith('/admin')}
  <Admin />
{:else}
  <Kiosk />
{/if}
