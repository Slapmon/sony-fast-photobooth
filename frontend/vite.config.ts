import { defineConfig, type ProxyOptions } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// Dev-only proxy to the FastAPI backend (just dev / uvicorn on :8000) — the
// kiosk page calls /session/*, /ws, /preview/stream and /captures/* as
// same-origin paths so the same code works unproxied once built and served
// by the backend itself.
//
// /admin and /gallery are special: those path prefixes are used BOTH as the
// SPA's own client-side route (App.svelte's path-based switch renders
// Admin.svelte/Gallery.svelte for them) AND as the backend's API prefix
// (e.g. POST /admin/login, GET /gallery/{id}/captures). A plain proxy entry
// would intercept the browser's top-level page-load request for /admin
// itself and hand back the backend's raw JSON instead of index.html. Real
// browser navigations send `Accept: text/html`; this app's own fetch()
// calls never do — so bypass proxying (serve index.html locally) whenever
// the request is asking for a page, and only proxy real API calls.
const bypassPageNavigation: ProxyOptions['bypass'] = (req) => {
  if (req.headers.accept?.includes('text/html')) return '/index.html'
}

export default defineConfig({
  plugins: [svelte()],
  server: {
    proxy: {
      '/session': 'http://127.0.0.1:8000',
      '/preview': 'http://127.0.0.1:8000',
      '/captures': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
      '/debug': 'http://127.0.0.1:8000',
      '/gallery': { target: 'http://127.0.0.1:8000', bypass: bypassPageNavigation },
      '/admin': { target: 'http://127.0.0.1:8000', bypass: bypassPageNavigation },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
})
