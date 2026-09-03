<!--
  Live camera/preview/disk/network/printer status (IMPLEMENTATION_PLAN.md
  T-3.10). Polls GET /admin/status on mount and via a manual refresh button
  (no auto-polling loop — an admin operator refreshing on demand is enough
  for v1, keeps this simple).
-->
<script lang="ts">
  import StatusRow from './StatusRow.svelte'

  interface Check {
    status: string
    detail?: string
  }

  interface StatusResponse {
    camera: Check
    preview: Check
    disk: Check
    network: Check
    printer: Check
  }

  let status = $state<StatusResponse | null>(null)
  let loading = $state(true)
  let error = $state<string | null>(null)

  async function refresh() {
    loading = true
    error = null
    try {
      const res = await fetch('/admin/status')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      status = await res.json()
    } catch (err) {
      error = err instanceof Error ? err.message : String(err)
    } finally {
      loading = false
    }
  }

  refresh()
</script>

<section class="status-panel">
  <button class="primary" onclick={refresh} disabled={loading}
    >{loading ? 'Refreshing…' : 'Refresh'}</button
  >

  {#if error}
    <p class="error-text">Failed to load status: {error}</p>
  {:else if status}
    <ul class="rows card">
      <StatusRow name="Camera" status={status.camera.status} detail={status.camera.detail} />
      <StatusRow
        name="Preview stream"
        status={status.preview.status}
        detail={status.preview.detail}
      />
      <StatusRow name="Disk" status={status.disk.status} detail={status.disk.detail} />
      <StatusRow name="Network" status={status.network.status} detail={status.network.detail} />
      <StatusRow name="Printer" status={status.printer.status} detail={status.printer.detail} />
    </ul>
  {/if}
</section>

<style>
  .status-panel {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  .rows {
    list-style: none;
    margin: 0;
    padding: 0.25rem 1rem;
  }

  .rows :global(li:last-child) {
    border-bottom: none;
  }
</style>
