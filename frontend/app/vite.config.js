import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 3000,
    // Vite rejects unrecognized Host headers by default (anti DNS-rebinding).
    // Wildcarded rather than a fixed hostname because Cloudflare's free
    // Quick Tunnel assigns a new random *.trycloudflare.com subdomain on
    // every restart.
    allowedHosts: ['.trycloudflare.com'],
    proxy: {
      '/api': {
        target: process.env.VITE_API_URL || 'http://backend:8000',
        rewrite: path => path.replace(/^\/api/, ''),
        changeOrigin: true,
      },
      // Per-turn generated image/audio/video -- served as static files by
      // nginx (see nginx.conf's /media/ location), not by the backend.
      // Without this, <img>/<video> tags resolve /media/* against the Vite
      // dev server itself (404) when the app is opened on :3000 directly
      // instead of through nginx on :80.
      '/media': {
        target: process.env.VITE_MEDIA_URL || 'http://nginx:80',
        changeOrigin: true,
      }
    }
  }
})
