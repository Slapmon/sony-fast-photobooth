<!--
  Attract loop / landing screen (IMPLEMENTATION_PLAN.md T-3.1,
  photobooth-plan.md §7 "Landing / idle screen"). Shown while the session is
  IDLE: the event's branded background+logo (EventBackground.svelte) with
  title/date over it. The guest's actual choices (capture mode, gallery)
  live in BottomNav.svelte, rendered by Kiosk.svelte alongside this — kept
  separate so the exact same nav bar can also appear on the review screen
  and the gallery page without duplicating this component's background
  logic there.
-->
<script lang="ts">
  import EventBackground, { type BackgroundInfo } from './EventBackground.svelte'
  import type { EventThemeInfo } from './theme'

  export interface EventMode {
    id: string
    label: string
  }

  export interface EventInfo extends BackgroundInfo {
    event_id: string
    title: string
    date: string
    modes: EventMode[]
    theme: EventThemeInfo
    strings: Record<string, string>
    idle_timeout_s: number
  }

  const { event }: { event: EventInfo | null } = $props()
</script>

<div class="attract">
  <EventBackground info={event} />

  <div class="content">
    {#if event?.title}
      <h1 class="title rise-in" style="animation-delay: 60ms">{event.title}</h1>
    {/if}
    {#if event?.date}
      <p class="date rise-in" style="animation-delay: 140ms">{event.date}</p>
    {/if}
    <p class="cta rise-in" style="animation-delay: 220ms">
      {event?.strings.attract_cta ?? 'Touch a button below to start'}
    </p>
  </div>
</div>

<style>
  .attract {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    color: #fff;
  }

  .content {
    position: relative;
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.75rem;
    text-align: center;
    padding: 2rem;
    padding-bottom: 10rem; /* keeps title/date clear of BottomNav */
    box-sizing: border-box;
  }

  .title {
    font-size: 3.5rem;
    margin: 0;
    font-weight: 400;
    letter-spacing: -0.01em;
    font-family: var(--font-display);
    text-shadow: 0 2px 20px rgba(0, 0, 0, 0.35);
  }

  .date {
    font-size: 1.35rem;
    margin: 0;
    opacity: 0.9;
    letter-spacing: 0.01em;
    text-shadow: 0 1px 8px rgba(0, 0, 0, 0.3);
  }

  .cta {
    margin-top: 2.5rem;
    font-size: 0.85rem;
    opacity: 0.7;
    letter-spacing: 0.14em;
    text-transform: uppercase;
  }
</style>
