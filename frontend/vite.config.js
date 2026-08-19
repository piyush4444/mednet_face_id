import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
// Two entry points, two URLs: the admin SPA (index.html) and the
// entry-gate kiosk (kiosk.html — /kiosk.html in dev; nginx maps the
// kiosk hostname/path to it in production).
//
// The dev server proxies /api to the FastAPI backend so the browser talks to
// a single origin (localhost:5173) in development, exactly like production
// where nginx serves the SPA and API together. Same-origin is what lets the
// session cookie flow on fetch, MJPEG <img>, and the WebSocket handshake — a
// cross-origin dev setup would need CORS with credentials, incompatible with
// a wildcard origin. With this proxy the dev API base is the relative
// "/api/v1" (see frontend/.env).
export default defineConfig({
  plugins: [react(), tailwindcss(),],
  build: {
    rollupOptions: {
      input: {
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
        kiosk: fileURLToPath(new URL('./kiosk.html', import.meta.url)),
      },
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,               // proxy the /api/v1/ws/live WebSocket too
      },
    },
  },
})
