<!--
  Hidden admin surface: preflight checks, test shot, camera reconnect,
  printer/upload status, event switching (photobooth-plan.md §7,
  IMPLEMENTATION_PLAN.md T-3.7..T-3.13).

  T-3.7 (this file, today): auth gate only. Not authenticated -> PIN entry.
  Authenticated -> a minimal "shell" wrapping the real admin content.

  INTEGRATION POINT for T-3.8 onward: everything that renders once logged in
  goes inside the `{#if authenticated}...{/if}` block below, replacing the
  placeholder <section class="placeholder">. A `logout()` action is already
  wired to a button in the shell header — later waves don't need to build
  their own. Keep new admin sub-views as separate components imported and
  rendered inside that same block (e.g. <EventSwitcher />, <TemplatePicker
  />) rather than restructuring this file's auth flow.
-->
<script lang="ts">
  import ActionsPanel from '../lib/admin/ActionsPanel.svelte'
  import EventsPanel from '../lib/admin/EventsPanel.svelte'
  import PreflightPanel from '../lib/admin/PreflightPanel.svelte'
  import StatusPanel from '../lib/admin/StatusPanel.svelte'
  import TemplatesPanel from '../lib/admin/TemplatesPanel.svelte'
  import TimingsPanel from '../lib/admin/TimingsPanel.svelte'

  type Tab = 'events' | 'templates' | 'status' | 'actions' | 'preflight' | 'timings'

  const TABS: { id: Tab; label: string }[] = [
    { id: 'events', label: 'Events' },
    { id: 'templates', label: 'Templates' },
    { id: 'status', label: 'Status' },
    { id: 'actions', label: 'Actions' },
    { id: 'preflight', label: 'Preflight' },
    { id: 'timings', label: 'Timings' },
  ]

  let activeTab = $state<Tab>('events')

  let authenticated = $state<boolean | null>(null) // null = still checking
  let pin = $state('')
  let error = $state<string | null>(null)
  let submitting = $state(false)

  async function checkSession() {
    try {
      const res = await fetch('/admin/session')
      const body = await res.json()
      authenticated = Boolean(body.authenticated)
    } catch {
      // Network hiccup on load: treat as unauthenticated rather than stuck
      // on a spinner — the PIN screen is a safe fallback either way.
      authenticated = false
    }
  }

  async function login() {
    error = null
    submitting = true
    try {
      const res = await fetch('/admin/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pin }),
      })
      if (!res.ok) {
        error = 'Incorrect PIN'
        pin = ''
        return
      }
      authenticated = true
      pin = ''
    } catch {
      error = 'Could not reach the server'
    } finally {
      submitting = false
    }
  }

  async function logout() {
    await fetch('/admin/logout', { method: 'POST' })
    authenticated = false
  }

  function onKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter' && pin && !submitting) login()
  }

  checkSession()
</script>

{#if authenticated === null}
  <section class="checking">Checking session…</section>
{:else if !authenticated}
  <section class="login">
    <h1>Admin</h1>
    <p>Enter the PIN to continue.</p>
    <input
      type="password"
      inputmode="numeric"
      autocomplete="off"
      bind:value={pin}
      onkeydown={onKeydown}
      disabled={submitting}
      placeholder="PIN"
      aria-label="Admin PIN"
    />
    <button onclick={login} disabled={submitting || !pin}>
      {submitting ? 'Checking…' : 'Log in'}
    </button>
    {#if error}
      <p class="error">{error}</p>
    {/if}
  </section>
{:else}
  <section class="shell">
    <header>
      <h1>Admin</h1>
      <button onclick={logout}>Log out</button>
    </header>
    <nav class="tabs">
      {#each TABS as tab (tab.id)}
        <button class:active={activeTab === tab.id} onclick={() => (activeTab = tab.id)}>
          {tab.label}
        </button>
      {/each}
    </nav>

    <div class="tab-content">
      {#if activeTab === 'events'}
        <EventsPanel />
      {:else if activeTab === 'templates'}
        <TemplatesPanel />
      {:else if activeTab === 'status'}
        <StatusPanel />
      {:else if activeTab === 'actions'}
        <ActionsPanel />
      {:else if activeTab === 'preflight'}
        <PreflightPanel />
      {:else if activeTab === 'timings'}
        <TimingsPanel />
      {/if}
    </div>
  </section>
{/if}

<style>
  .checking,
  .login,
  .shell {
    min-height: 100vh;
    box-sizing: border-box;
    padding: 2rem;
    background: var(--color-bg);
    color: var(--color-ink);
  }

  .checking {
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--color-ink-muted);
  }

  .login {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.75rem;
  }

  .login h1 {
    margin: 0;
  }

  .login p {
    color: var(--color-ink-muted);
    margin: 0 0 0.5rem;
  }

  .login input {
    font-size: 1.5rem;
    letter-spacing: 0.3rem;
    padding: 0.6rem 1rem;
    text-align: center;
    width: 10rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
  }

  .login button {
    font-size: 1.1rem;
    padding: 0.6rem 1.75rem;
    border: none;
    border-radius: var(--radius-sm);
    background: var(--color-primary);
    color: var(--color-primary-contrast);
  }

  .login button:hover:not(:disabled) {
    background: var(--color-primary-hover);
  }

  .login button:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .error {
    color: var(--color-danger-fg);
    margin: 0;
  }

  .shell header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid var(--color-border);
    padding-bottom: 1rem;
    margin-bottom: 1rem;
  }

  .shell header h1 {
    margin: 0;
  }

  .shell header button {
    padding: 0.4rem 1rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-ink);
  }

  .shell header button:hover {
    background: var(--color-surface-alt);
  }

  .tabs {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    border-bottom: 1px solid var(--color-border);
    padding-bottom: 0.75rem;
    margin-bottom: 1.25rem;
  }

  .tabs button {
    padding: 0.5rem 1.1rem;
    border: 1px solid var(--color-border);
    border-radius: 999px;
    background: var(--color-surface);
    color: var(--color-ink);
    font-size: 0.95rem;
  }

  .tabs button:hover:not(.active) {
    background: var(--color-surface-alt);
  }

  .tabs button.active {
    background: var(--color-primary);
    border-color: var(--color-primary);
    color: var(--color-primary-contrast);
  }

  .tab-content {
    max-width: 60rem;
  }
</style>
