import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  // Never discover the parent project's production .env.
  envDir: './.offline-no-env',
  server: { host: '127.0.0.1', port: 5173, strictPort: true, fs: { strict: true } },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true },
  build: { sourcemap: false, chunkSizeWarningLimit: 700 },
  test: { environment: 'jsdom', setupFiles: ['./tests/setup.ts'], clearMocks: true },
})
