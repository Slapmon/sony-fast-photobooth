<!--
  Template picker with live preview render (IMPLEMENTATION_PLAN.md T-3.9).

  Lists templates from GET /admin/templates; clicking one calls
  POST /admin/templates/{name}/preview, which renders the "web" variant
  against the currently-active event using repeated sample fixture imagery,
  and displays the returned JPEG inline.
-->
<script lang="ts">
  interface TemplateSummary {
    name: string
    title?: string
    slot_count?: number
    error?: string
  }

  let templates = $state<TemplateSummary[]>([])
  let loading = $state(true)
  let loadError = $state<string | null>(null)

  let selectedName = $state<string | null>(null)
  let previewUrl = $state<string | null>(null)
  let previewLoading = $state(false)
  let previewError = $state<string | null>(null)

  async function loadTemplates() {
    loading = true
    loadError = null
    try {
      const res = await fetch('/admin/templates')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      templates = await res.json()
    } catch (err) {
      loadError = err instanceof Error ? err.message : String(err)
    } finally {
      loading = false
    }
  }

  async function renderPreview(name: string) {
    selectedName = name
    previewLoading = true
    previewError = null
    if (previewUrl) URL.revokeObjectURL(previewUrl)
    previewUrl = null
    try {
      const res = await fetch(`/admin/templates/${encodeURIComponent(name)}/preview`, {
        method: 'POST',
      })
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        throw new Error(body?.detail ?? `HTTP ${res.status}`)
      }
      const blob = await res.blob()
      previewUrl = URL.createObjectURL(blob)
    } catch (err) {
      previewError = err instanceof Error ? err.message : String(err)
    } finally {
      previewLoading = false
    }
  }

  loadTemplates()
</script>

<section class="templates-panel">
  {#if loading}
    <p class="muted">Loading templates…</p>
  {:else if loadError}
    <p class="error-text">Failed to load templates: {loadError}</p>
  {:else if templates.length === 0}
    <p class="muted">No templates found under templates/.</p>
  {:else}
    <ul class="template-grid">
      {#each templates as tpl (tpl.name)}
        <li>
          <button
            class="template-card"
            class:selected={tpl.name === selectedName}
            disabled={Boolean(tpl.error)}
            onclick={() => renderPreview(tpl.name)}
          >
            <strong>{tpl.name}</strong>
            {#if tpl.error}
              <span class="error-text">{tpl.error}</span>
            {:else}
              <span class="muted">{tpl.title} · {tpl.slot_count} slot(s)</span>
            {/if}
          </button>
        </li>
      {/each}
    </ul>
  {/if}

  {#if previewLoading}
    <p class="muted">Rendering preview…</p>
  {:else if previewError}
    <p class="error-text">Preview failed: {previewError}</p>
  {:else if previewUrl}
    <img class="preview" src={previewUrl} alt="Rendered preview of {selectedName}" />
  {/if}
</section>

<style>
  .templates-panel {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    color: var(--color-ink);
  }

  .muted {
    color: var(--color-ink-muted);
    margin: 0;
  }

  .template-grid {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr));
    gap: 0.6rem;
  }

  .template-card {
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    text-align: left;
    padding: 0.85rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-ink);
  }

  .template-card:hover:not(:disabled) {
    background: var(--color-surface-alt);
  }

  .template-card.selected {
    border-color: var(--color-primary);
    box-shadow: 0 0 0 1px var(--color-primary);
  }

  .template-card:disabled {
    cursor: not-allowed;
    opacity: 0.6;
  }

  .preview {
    max-width: 100%;
    max-height: 32rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius);
    box-shadow: var(--shadow-sm);
  }
</style>
