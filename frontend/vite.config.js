import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
// The dev server proxies /api to the FastAPI backend so the browser talks to
// a single origin (localhost:5173) in development, exactly like production
// where nginx serves the SPA and API together. Same-origin is what lets the
// session cookie flow on fetch, MJPEG <img>, and the WebSocket handshake — a
// cross-origin dev setup would need CORS with credentials, incompatible with
// a wildcard origin. With this proxy the dev API base is the relative
// "/api/v1" (see frontend/.env).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')

  return {
    base: env.VITE_BASE_PATH || '/',
    plugins: [react(), tailwindcss(),],
    build: {
      rollupOptions: {
        input: fileURLToPath(new URL('./index.html', import.meta.url)),
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
  }
})
