<!--
  GUI replacement for the old "paste JSON into a textarea" modes editor
  (EventConfig.modes: list[CaptureMode]). Each row is one guest-facing
  button (attract screen bottom nav) driving one template. `id` auto-slugs
  from the label as the operator types; editing the id field directly opts
  that row out of auto-slugging (e.g. to match an existing `?mode=` link
  already shared with guests).
-->
<script lang="ts">
  interface CaptureMode {
    id: string
    label: string
    template: string
  }

  interface TemplateOption {
    name: string
    title?: string
    slot_count?: number
    error?: string
  }

  let { modes = $bindable([]) }: { modes: CaptureMode[] } = $props()

  let templates = $state<TemplateOption[]>([])
  let loadError = $state<string | null>(null)
  let manualIds = $state<Set<number>>(new Set())

  async function loadTemplates() {
    try {
      const res = await fetch('/admin/templates')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      templates = await res.json()
    } catch (err) {
      loadError = err instanceof Error ? err.message : String(err)
    }
  }
  loadTemplates()

  function slugify(label: string): string {
    return (
      label
        .toLowerCase()
        .trim()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/(^-|-$)/g, '') || 'mode'
    )
  }

  function addMode() {
    modes = [...modes, { id: '', label: '', template: templates[0]?.name ?? '' }]
  }

  function removeMode(index: number) {
    modes = modes.filter((_, i) => i !== index)
    manualIds = new Set([...manualIds].filter((i) => i !== index).map((i) => (i > index ? i - 1 : i)))
  }

  function moveUp(index: number) {
    if (index === 0) return
    const next = modes.slice()
    ;[next[index - 1], next[index]] = [next[index], next[index - 1]]
    modes = next
  }

  function moveDown(index: number) {
    if (index === modes.length - 1) return
    const next = modes.slice()
    ;[next[index + 1], next[index]] = [next[index], next[index + 1]]
    modes = next
  }

  function onLabelInput(index: number, value: string) {
    const next = modes.slice()
    const auto = !manualIds.has(index)
    next[index] = { ...next[index], label: value, id: auto ? slugify(value) : next[index].id }
    modes = next
  }

  function onIdInput(index: number, value: string) {
    manualIds = new Set(manualIds).add(index)
    const next = modes.slice()
    next[index] = { ...next[index], id: value }
    modes = next
  }

  function onTemplateInput(index: number, value: string) {
    const next = modes.slice()
    next[index] = { ...next[index], template: value }
    modes = next
  }
</script>

<div class="modes-editor">
  {#if loadError}<p class="error">Failed to load templates: {loadError}</p>{/if}
  {#if modes.length === 0}
    <p class="empty">No capture modes yet — add at least one button for guests to tap.</p>
  {/if}
  {#each modes as mode, index (index)}
    <div class="mode-row">
      <div class="mode-fields">
        <label>
          Label
          <input
            type="text"
            value={mode.label}
            oninput={(e) => onLabelInput(index, e.currentTarget.value)}
            placeholder="Single Photo"
          />
        </label>
        <label>
          Template
          <select value={mode.template} onchange={(e) => onTemplateInput(index, e.currentTarget.value)}>
            {#each templates as t (t.name)}
              <option value={t.name}>{t.title ?? t.name}</option>
            {/each}
          </select>
        </label>
        <label class="id-field">
          id
          <input
            type="text"
            value={mode.id}
            oninput={(e) => onIdInput(index, e.currentTarget.value)}
          />
        </label>
      </div>
      <div class="mode-actions">
        <button type="button" onclick={() => moveUp(index)} disabled={index === 0} title="Move up">
          ↑
        </button>
        <button
          type="button"
          onclick={() => moveDown(index)}
          disabled={index === modes.length - 1}
          title="Move down"
        >
          ↓
        </button>
        <button type="button" class="remove" onclick={() => removeMode(index)} title="Remove">
          ✕
        </button>
      </div>
    </div>
  {/each}
  <button type="button" class="add" onclick={addMode}>+ Add mode</button>
</div>

<style>
  .modes-editor {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .empty {
    font-size: 0.85rem;
    color: var(--color-ink-muted);
    margin: 0;
  }

  .mode-row {
    display: flex;
    gap: 0.5rem;
    align-items: flex-end;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    padding: 0.6rem;
    background: var(--color-surface);
  }

  .mode-fields {
    flex: 1;
    display: grid;
    grid-template-columns: 1.3fr 1.3fr 1fr;
    gap: 0.5rem;
  }

  .mode-fields label,
  .id-field {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    font-size: 0.8rem;
    color: var(--color-ink-muted);
  }

  .mode-fields input,
  .mode-fields select {
    font-size: 0.9rem;
    padding: 0.4rem 0.5rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-ink);
  }

  .mode-actions {
    display: flex;
    gap: 0.25rem;
  }

  .mode-actions button {
    width: 2rem;
    height: 2rem;
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-ink);
    cursor: pointer;
  }

  .mode-actions button:disabled {
    opacity: 0.4;
    cursor: default;
  }

  .mode-actions button.remove {
    color: var(--color-danger-fg);
  }

  .add {
    align-self: flex-start;
    padding: 0.45rem 0.85rem;
    border: 1px dashed var(--color-border-strong);
    border-radius: var(--radius-sm);
    background: none;
    color: var(--color-ink);
    cursor: pointer;
  }

  .error {
    color: var(--color-danger-fg);
    font-size: 0.85rem;
  }
</style>
