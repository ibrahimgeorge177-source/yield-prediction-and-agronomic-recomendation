import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// The API base URL is baked in at build time (VITE_API_BASE_URL) and can still be
// overridden at run time from the Settings panel, which writes to localStorage —
// useful when the same build is pointed at a staging API.
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg', 'icons/icon-192.png', 'icons/icon-512.png'],
      manifest: {
        name: 'Kenya Maize Yield Forecast',
        short_name: 'Maize Yield',
        description:
          'District-level maize yield forecasts and agronomic recommendations for Kenya, ' +
          'from the One Acre Fund MEL survey (2016-2020).',
        theme_color: '#0A1317',
        background_color: '#0A1317',
        display: 'standalone',
        orientation: 'portrait-primary',
        start_url: '/',
        scope: '/',
        icons: [
          { src: 'icons/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icons/icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: 'icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        navigateFallback: 'index.html',
        runtimeCaching: [
          {
            // Reference data changes rarely and makes the app useful offline:
            // districts, levers, the input schema and the model summary.
            urlPattern: /\/api\/v1\/(reference|model)\/.*/i,
            handler: 'StaleWhileRevalidate',
            options: {
              cacheName: 'api-reference',
              expiration: { maxEntries: 30, maxAgeSeconds: 60 * 60 * 24 * 7 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            urlPattern: /^https:\/\/fonts\.(googleapis|gstatic)\.com\/.*/i,
            handler: 'CacheFirst',
            options: {
              cacheName: 'google-fonts',
              expiration: { maxEntries: 20, maxAgeSeconds: 60 * 60 * 24 * 365 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
        ],
      },
    }),
  ],
  server: { port: 5173, host: true },
  preview: { port: Number(process.env.PORT) || 4173, host: true },
  build: { outDir: 'dist', sourcemap: false, chunkSizeWarningLimit: 900 },
})
