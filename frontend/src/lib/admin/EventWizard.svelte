<!--
  "New Event" wizard: Template -> Basics -> Look -> Modes -> Review.
  Nothing is written to disk until the final "Create Event" step — an
  abandoned wizard leaves no orphaned event directory. See the approved
  event-tool plan for the full design rationale (scrim_color as a tinted
  dark backdrop hue, not a light/dark re-theme).
-->
<script lang="ts">
  import ModesEditor from './ModesEditor.svelte'
  import { pickContrastColor } from '../theme'

  interface CaptureMode {
    id: string
    label: string
    template: string
  }

  interface EventTemplatePreset {
    id: string
    label: string
    description: string
    scrim_color: string
    primary_color: string
    modes: CaptureMode[]
    vars_hint: Record<string, string>
  }

  let { onclose, oncreated }: { onclose: () => void; oncreated: (id: string) => void } = $props()

  let step = $state(1)

  let presets = $state<EventTemplatePreset[]>([])
  let presetsError = $state<string | null>(null)
  let selectedPresetId = $state<string | null>(null)

  let id = $state('')
  let idManual = $state(false)
  let title = $state('')
  let date = $state('')

  let backgroundFile = $state<File | null>(null)
  let logoFile = $state<File | null>(null)
  let primaryColor = $state('')
  let scrimColor = $state('')

  let modes = $state<CaptureMode[]>([])

  let creating = $state(false)
  let createError = $state<string | null>(null)

  async function loadPresets() {
    try {
      const res = await fetch('/admin/event-templates')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      presets = await res.json()
    } catch (err) {
      presetsError = err instanceof Error ? err.message : String(err)
    }
  }
  loadPresets()

  function slugify(text: string): string {
    return text
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/(^-|-$)/g, '')
  }

  function choosePreset(preset: EventTemplatePreset | null) {
    selectedPresetId = preset?.id ?? null
    primaryColor = preset?.primary_color ?? ''
    scrimColor = preset?.scrim_color ?? ''
    modes = preset ? preset.modes.map((m) => ({ ...m })) : []
    step = 2
  }

  function onTitleInput(value: string) {
    title = value
    if (!idManual) id = slugify(value)
  }

  function onIdInput(value: string) {
    idManual = true
    id = value
  }

  function canProceedFromBasics(): boolean {
    return id.trim().length > 0 && title.trim().length > 0
  }

  async function createEvent() {
    creating = true
    createError = null
    try {
      const createRes = await fetch('/admin/events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, title, date, based_on: selectedPresetId }),
      })
      const created = await createRes.json()
      if (!createRes.ok) throw new Error(created?.detail ?? `HTTP ${createRes.status}`)

      let backgroundImage: string = created.background_image ?? ''
      let logoImage: string = created.logo_image ?? ''

      if (backgroundFile) {
        const form = new FormData()
        form.append('kind', 'background')
        form.append('file', backgroundFile)
        const res = await fetch(`/admin/events/${encodeURIComponent(id)}/upload-image`, {
          method: 'POST',
          body: form,
        })
        const body = await res.json()
        if (!res.ok) throw new Error(body?.detail ?? `HTTP ${res.status}`)
        backgroundImage = body.background_image
      }

      if (logoFile) {
        const form = new FormData()
        form.append('kind', 'logo')
        form.append('file', logoFile)
        const res = await fetch(`/admin/events/${encodeURIComponent(id)}/upload-image`, {
          method: 'POST',
          body: form,
        })
        const body = await res.json()
        if (!res.ok) throw new Error(body?.detail ?? `HTTP ${res.status}`)
        logoImage = body.logo_image
      }

      const finalBody = {
        id,
        title,
        date,
        template: modes[0]?.template ?? created.template,
        modes,
        background_image: backgroundImage,
        logo_image: logoImage,
        theme: { primary_color: primaryColor, scrim_color: scrimColor },
        gallery_enabled: true,
        vars: created.vars ?? {},
        strings: {},
      }
      const putRes = await fetch(`/admin/events/${encodeURIComponent(id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(finalBody),
      })
      const final = await putRes.json()
      if (!putRes.ok) throw new Error(final?.detail ?? `HTTP ${putRes.status}`)

      oncreated(id)
    } catch (err) {
      createError = err instanceof Error ? err.message : String(err)
    } finally {
      creating = false
    }
  }
</script>

<div class="backdrop" role="presentation" onclick={onclose}>
  <div
    class="wizard"
    role="dialog"
    aria-modal="true"
    tabindex="-1"
    onclick={(e) => e.stopPropagation()}
    onkeydown={(e) => e.stopPropagation()}
  >
    <header>
      <h3>New Event</h3>
      <button type="button" class="close" onclick={onclose} aria-label="Close">✕</button>
    </header>

    <ol class="steps">
      {#each ['Template', 'Basics', 'Look', 'Modes', 'Review'] as label, i (label)}
        <li class:active={step === i + 1} class:done={step > i + 1}>{i + 1}. {label}</li>
      {/each}
    </ol>

    <div class="body">
      {#if step === 1}
        {#if presetsError}<p class="error">Failed to load templates: {presetsError}</p>{/if}
        <div class="preset-grid">
          {#each presets as preset (preset.id)}
            <button type="button" class="preset-card" onclick={() => choosePreset(preset)}>
              <span
                class="swatch"
                style={`background:${preset.scrim_color};`}
              >
                <span
                  class="swatch-accent"
                  style={`background:${preset.primary_color};color:${pickContrastColor(preset.primary_color)};`}
                >Aa</span>
              </span>
              <strong>{preset.label}</strong>
              <span class="desc">{preset.description}</span>
            </button>
          {/each}
          <button type="button" class="preset-card scratch" onclick={() => choosePreset(null)}>
            <span class="swatch blank"></span>
            <strong>Start from scratch</strong>
            <span class="desc">App defaults — amber accent, neutral backdrop.</span>
          </button>
        </div>
      {:else if step === 2}
        <label>
          Title
          <input
            type="text"
            value={title}
            oninput={(e) => onTitleInput(e.currentTarget.value)}
            placeholder="Anna & Ben's Wedding"
          />
        </label>
        <label>
          Event id (used in URLs — lowercase letters, digits, hyphens)
          <input type="text" value={id} oninput={(e) => onIdInput(e.currentTarget.value)} />
        </label>
        <label>
          Date
          <input type="text" bind:value={date} placeholder="2026-09-12" />
        </label>
        <div class="nav">
          <button type="button" onclick={() => (step = 1)}>Back</button>
          <button type="button" disabled={!canProceedFromBasics()} onclick={() => (step = 3)}>
            Next
          </button>
        </div>
      {:else if step === 3}
        <div class="image-field">
          <span class="image-label">Background image or video (optional)</span>
          <input
            type="file"
            accept="image/*,video/*"
            onchange={(e) => (backgroundFile = e.currentTarget.files?.[0] ?? null)}
          />
        </div>
        <div class="image-field">
          <span class="image-label">Logo (optional)</span>
          <input
            type="file"
            accept="image/*"
            onchange={(e) => (logoFile = e.currentTarget.files?.[0] ?? null)}
          />
        </div>
        <label>
          Accent color
          <div class="color-row">
            <input type="color" value={primaryColor || '#dc9c39'} oninput={(e) => (primaryColor = e.currentTarget.value)} />
            <input type="text" bind:value={primaryColor} placeholder="#dc9c39" />
          </div>
        </label>
        <label>
          Backdrop color (tinted dark scrim behind every guest screen)
          <div class="color-row">
            <input type="color" value={scrimColor || '#0f0c09'} oninput={(e) => (scrimColor = e.currentTarget.value)} />
            <input type="text" bind:value={scrimColor} placeholder="#0f0c09" />
          </div>
        </label>
        <div class="nav">
          <button type="button" onclick={() => (step = 2)}>Back</button>
          <button type="button" onclick={() => (step = 4)}>Next</button>
        </div>
      {:else if step === 4}
        <ModesEditor bind:modes />
        <div class="nav">
          <button type="button" onclick={() => (step = 3)}>Back</button>
          <button type="button" disabled={modes.length === 0} onclick={() => (step = 5)}>
            Next
          </button>
        </div>
      {:else if step === 5}
        <div class="review">
          <p><strong>{title}</strong> ({id})</p>
          <p>{date || 'no date set'}</p>
          <p>{modes.length} capture mode{modes.length === 1 ? '' : 's'}: {modes.map((m) => m.label).join(', ')}</p>
        </div>
        {#if createError}<p class="error">{createError}</p>{/if}
        <div class="nav">
          <button type="button" onclick={() => (step = 4)} disabled={creating}>Back</button>
          <button type="button" class="primary" onclick={createEvent} disabled={creating}>
            {creating ? 'Creating…' : 'Create Event'}
          </button>
        </div>
      {/if}
    </div>
  </div>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    background: color-mix(in srgb, var(--color-scrim) 70%, transparent);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 20;
  }

  .wizard {
    background: var(--color-surface);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-lg);
    padding: 1.25rem;
    width: min(36rem, 92vw);
    max-height: 88vh;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 1rem;
    color: var(--color-ink);
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  header h3 {
    margin: 0;
  }

  .close {
    background: none;
    border: none;
    color: var(--color-ink-muted);
    cursor: pointer;
    font-size: 1.1rem;
  }

  .steps {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    list-style: none;
    margin: 0;
    padding: 0;
    font-size: 0.8rem;
    color: var(--color-ink-muted);
  }

  .steps li {
    padding: 0.25rem 0.6rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--color-border);
  }

  .steps li.active {
    color: var(--color-ink);
    border-color: var(--color-primary);
  }

  .steps li.done {
    color: var(--color-success-fg);
  }

  .body {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  .body label {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    font-size: 0.9rem;
    color: var(--color-ink-muted);
  }

  .body input[type='text'] {
    font-size: 0.95rem;
    padding: 0.45rem 0.6rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-ink);
  }

  .preset-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(9.5rem, 1fr));
    gap: 0.75rem;
  }

  .preset-card {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    padding: 0.75rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius);
    background: var(--color-surface);
    color: var(--color-ink);
    cursor: pointer;
    text-align: left;
    transition:
      border-color 200ms var(--ease-spring),
      transform 150ms var(--ease-spring);
  }

  .preset-card:hover {
    border-color: var(--color-primary);
  }

  .preset-card:active {
    transform: scale(0.98);
  }

  .swatch {
    height: 3.5rem;
    border-radius: var(--radius-sm);
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .swatch.blank {
    background: var(--color-neutral-bg);
  }

  .swatch-accent {
    width: 2rem;
    height: 2rem;
    border-radius: var(--radius-sm);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.8rem;
    font-weight: 600;
  }

  .desc {
    font-size: 0.75rem;
    color: var(--color-ink-muted);
  }

  .image-field {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    padding: 0.6rem;
    border: 1px dashed var(--color-border);
    border-radius: var(--radius-sm);
  }

  .image-label {
    font-size: 0.85rem;
    color: var(--color-ink-muted);
  }

  .color-row {
    display: flex;
    gap: 0.5rem;
    align-items: center;
  }

  .color-row input[type='color'] {
    width: 2.5rem;
    height: 2.5rem;
    padding: 0.15rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
  }

  .color-row input[type='text'] {
    flex: 1;
  }

  .review p {
    margin: 0 0 0.4rem;
  }

  .nav {
    display: flex;
    justify-content: space-between;
  }

  .nav button {
    padding: 0.5rem 1.1rem;
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-ink);
    cursor: pointer;
  }

  .nav button:disabled {
    opacity: 0.4;
    cursor: default;
  }

  .nav button.primary {
    background: var(--color-primary);
    color: var(--color-primary-contrast);
    border: none;
  }

  .error {
    color: var(--color-danger-fg);
  }
</style>
