<!--
  GUI replacement for the old "paste JSON into a textarea" vars editor
  (EventConfig.vars: dict[str, str]) — free-form {event.<key>} placeholders
  used by template text overlays (e.g. couple, hashtag, company).
-->
<script lang="ts">
  let { vars = $bindable({}) }: { vars: Record<string, string> } = $props()

  interface Row {
    key: string
    value: string
  }

  let rows = $state<Row[]>(Object.entries(vars).map(([key, value]) => ({ key, value })))

  function sync() {
    const next: Record<string, string> = {}
    for (const row of rows) {
      if (row.key.trim()) next[row.key.trim()] = row.value
    }
    vars = next
  }

  function addRow() {
    rows = [...rows, { key: '', value: '' }]
  }

  function removeRow(index: number) {
    rows = rows.filter((_, i) => i !== index)
    sync()
  }

  function onKeyInput(index: number, value: string) {
    rows[index].key = value
    sync()
  }

  function onValueInput(index: number, value: string) {
    rows[index].value = value
    sync()
  }
</script>

<div class="vars-editor">
  {#if rows.length === 0}
    <p class="empty">No placeholder variables set.</p>
  {/if}
  {#each rows as row, index (index)}
    <div class="var-row">
      <input
        type="text"
        placeholder="placeholder name (e.g. couple)"
        value={row.key}
        oninput={(e) => onKeyInput(index, e.currentTarget.value)}
      />
      <input
        type="text"
        placeholder="value"
        value={row.value}
        oninput={(e) => onValueInput(index, e.currentTarget.value)}
      />
      <button type="button" class="remove" onclick={() => removeRow(index)} title="Remove">✕</button>
    </div>
  {/each}
  <button type="button" class="add" onclick={addRow}>+ Add variable</button>
</div>

<style>
  .vars-editor {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }

  .empty {
    font-size: 0.85rem;
    color: var(--color-ink-muted);
    margin: 0;
  }

  .var-row {
    display: flex;
    gap: 0.4rem;
  }

  .var-row input {
    flex: 1;
    font-size: 0.9rem;
    padding: 0.4rem 0.5rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-ink);
  }

  .var-row button.remove {
    width: 2rem;
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-danger-fg);
    cursor: pointer;
  }

  .add {
    align-self: flex-start;
    padding: 0.4rem 0.75rem;
    border: 1px dashed var(--color-border-strong);
    border-radius: var(--radius-sm);
    background: none;
    color: var(--color-ink);
    cursor: pointer;
  }
</style>
