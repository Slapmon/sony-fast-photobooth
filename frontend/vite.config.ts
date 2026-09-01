import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// Dev-only proxy to the FastAPI backend (just dev / uvicorn on :8000) — the
// kiosk page calls /session/*, /ws, /preview/stream and /captures/* as
// same-origin paths so the same code works unproxied once built and served
// by the backend itself.
export default defineConfig({
  plugins: [svelte()],
  server: {
    proxy: {
      '/session': 'http://127.0.0.1:8000',
      '/preview': 'http://127.0.0.1:8000',
      '/captures': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
})
