<!--
  Timings dashboard (IMPLEMENTATION_PLAN.md T-3.13) — purely a frontend
  surface for the existing GET /debug/timings (built in Phase 1). No new
  backend work.
-->
<script lang="ts">
  interface Timing {
    count: number
    p50: number
    p95: number
    p99: number
    max: number
  }

  let timings = $state<Record<string, Timing>>({})
  let loading = $state(true)
  let error = $state<string | null>(null)

  async function refresh() {
    loading = true
    error = null
    try {
      const res = await fetch('/debug/timings')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      timings = await res.json()
    } catch (err) {
      error = err instanceof Error ? err.message : String(err)
    } finally {
      loading = false
    }
  }

  function fmt(ms: number): string {
    return ms.toFixed(1)
  }

  refresh()
</script>

<section class="timings-panel">
  <button class="primary" onclick={refresh} disabled={loading}
    >{loading ? 'Loading…' : 'Refresh'}</button
  >

  {#if error}
    <p class="error-text">Failed to load timings: {error}</p>
  {:else if Object.keys(timings).length === 0}
    <p class="muted">No span data yet.</p>
  {:else}
    <table class="card">
      <thead>
        <tr>
          <th>Span</th>
          <th>Count</th>
          <th>p50 (ms)</th>
          <th>p95 (ms)</th>
          <th>p99 (ms)</th>
          <th>max (ms)</th>
        </tr>
      </thead>
      <tbody>
        {#each Object.entries(timings) as [name, t] (name)}
          <tr>
            <td>{name}</td>
            <td>{t.count}</td>
            <td>{fmt(t.p50)}</td>
            <td>{fmt(t.p95)}</td>
            <td>{fmt(t.p99)}</td>
            <td>{fmt(t.max)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</section>

<style>
  .timings-panel {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    color: var(--color-ink);
  }

  .muted {
    color: var(--color-ink-muted);
    margin: 0;
  }

  table {
    border-collapse: collapse;
    width: 100%;
    padding: 0;
    overflow: hidden;
  }

  th,
  td {
    text-align: left;
    padding: 0.5rem 1rem;
    border-bottom: 1px solid var(--color-border);
  }

  th {
    color: var(--color-ink-muted);
    font-weight: 600;
    font-size: 0.85rem;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    background: var(--color-surface-alt);
  }

  tr:last-child td {
    border-bottom: none;
  }
</style>
