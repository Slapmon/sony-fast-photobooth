<!--
  GUI replacement for the old "paste JSON into a textarea" strings editor
  (EventConfig.strings: dict[str, str]) — sparse UI-text overrides layered
  onto DEFAULT_UI_STRINGS (config/event.py). One labeled input per known
  key rather than free-text JSON: the key list is fixed and small, and a
  blank field means "inherit the English default" exactly like the
  backend's resolved_strings() merge.
-->
<script lang="ts">
  let { strings = $bindable({}) }: { strings: Record<string, string> } = $props()

  // Mirrors DEFAULT_UI_STRINGS in src/photobooth/config/event.py exactly —
  // key, English default (shown as placeholder), and a short description.
  const KNOWN_STRINGS: { key: string; default: string; hint: string }[] = [
    { key: 'attract_cta', default: 'Touch a button below to start', hint: 'Attract screen call to action' },
    { key: 'gallery_word', default: 'Gallery', hint: 'Gallery button/heading' },
    { key: 'capturing_label', default: 'Capturing…', hint: 'Shown while a shot is being taken' },
    { key: 'print_button', default: 'Print', hint: 'Print button label' },
    { key: 'print_button_busy', default: 'Printing…', hint: 'Print button label while printing' },
    { key: 'qr_caption', default: 'Scan to get your photo', hint: 'Caption under the QR code' },
    { key: 'gallery_loading', default: 'Loading…', hint: 'Gallery page while fetching captures' },
    { key: 'gallery_empty', default: 'No photos yet.', hint: 'Gallery page with zero captures' },
    {
      key: 'gallery_not_available',
      default: 'Gallery not available for this event.',
      hint: 'Shown when gallery_enabled is off',
    },
  ]

  function onInput(key: string, value: string) {
    const next = { ...strings }
    if (value.trim()) next[key] = value
    else delete next[key]
    strings = next
  }
</script>

<div class="strings-editor">
  {#each KNOWN_STRINGS as item (item.key)}
    <label>
      <span class="label-text">{item.hint}</span>
      <input
        type="text"
        placeholder={item.default}
        value={strings[item.key] ?? ''}
        oninput={(e) => onInput(item.key, e.currentTarget.value)}
      />
    </label>
  {/each}
</div>

<style>
  .strings-editor {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  label {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    font-size: 0.8rem;
    color: var(--color-ink-muted);
  }

  .label-text {
    font-size: 0.8rem;
  }

  input {
    font-size: 0.9rem;
    padding: 0.4rem 0.5rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-ink);
  }
</style>
