import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Generated tiles are served straight from the pipeline output (assets/generated): no copying in dev.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  publicDir: '../../assets/generated',
  base: './',
  server: { port: 5199, strictPort: true },
  preview: { port: 5198, strictPort: true },
  build: { chunkSizeWarningLimit: 1500, copyPublicDir: true },
  test: { include: ['src/**/*.test.ts'], environment: 'node' },
})
