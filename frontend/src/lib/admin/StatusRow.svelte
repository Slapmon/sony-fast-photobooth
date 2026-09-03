<!--
  Shared "one row per check" renderer used by StatusPanel (T-3.10) and
  PreflightPanel (T-3.12) — both surface the same
  `{status: "green"|"red"|"gray"|"not_configured"|"not_available", detail}`
  shape, just from different endpoints (/admin/status vs /debug/health).
-->
<script lang="ts">
  const {
    name,
    status,
    detail,
  }: { name: string; status: string; detail?: string } = $props()

  const DOT_COLOR: Record<string, string> = {
    green: 'var(--color-success-dot)',
    red: 'var(--color-danger-dot)',
    gray: 'var(--color-neutral-dot)',
    not_configured: 'var(--color-neutral-dot)',
    not_available: 'var(--color-neutral-dot)',
  }

  function color(s: string): string {
    return DOT_COLOR[s] ?? 'var(--color-neutral-dot)'
  }
</script>

<li class="row">
  <span class="dot" style="background:{color(status)}" aria-hidden="true"></span>
  <span class="name">{name}</span>
  <span class="detail">{detail ?? status}</span>
</li>

<style>
  .row {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.55rem 0;
    border-bottom: 1px solid var(--color-border);
  }

  .dot {
    width: 0.75rem;
    height: 0.75rem;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .name {
    font-weight: 600;
    min-width: 12rem;
    color: var(--color-ink);
  }

  .detail {
    color: var(--color-ink-muted);
    font-size: 0.9rem;
  }
</style>
