<!--
  Preflight check screen (IMPLEMENTATION_PLAN.md T-3.12, photobooth-plan.md
  §10 "Pre-event checklist"). Renders GET /debug/health's flat named
  checklist, green/red/gray per line.
-->
<script lang="ts">
  import StatusRow from './StatusRow.svelte'

  interface HealthCheck {
    name: string
    status: string
    detail?: string
  }

  let checks = $state<HealthCheck[]>([])
  let loading = $state(true)
  let error = $state<string | null>(null)

  async function refresh() {
    loading = true
    error = null
    try {
      const res = await fetch('/debug/health')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      checks = await res.json()
    } catch (err) {
      error = err instanceof Error ? err.message : String(err)
    } finally {
      loading = false
    }
  }

  refresh()
</script>

<section class="preflight-panel">
  <button class="primary" onclick={refresh} disabled={loading}
    >{loading ? 'Running…' : 'Run preflight'}</button
  >

  {#if error}
    <p class="error-text">Failed to run preflight: {error}</p>
  {:else if checks.length > 0}
    <ul class="rows card">
      {#each checks as check (check.name)}
        <StatusRow name={check.name} status={check.status} detail={check.detail} />
      {/each}
    </ul>
  {/if}
</section>

<style>
  .preflight-panel {
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
