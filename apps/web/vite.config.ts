import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Generated tiles are served straight from the pipeline output (assets/generated): no copying in dev.
// `--mode demo` (make demo): the public viewer only - no review workbench page; scripts/pack_demo.py then removes
// internal files, gzips the tiles and writes the cache headers.
export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss()],
  publicDir: '../../assets/generated',
  base: './',
  server: { port: 5199, strictPort: true },
  preview: { port: 5198, strictPort: true },
  build: {
    chunkSizeWarningLimit: 1500, copyPublicDir: true, outDir: mode === 'demo' ? 'dist-demo' : 'dist',
    rollupOptions: { input: (mode === 'demo' ? { main: 'index.html' } : { main: 'index.html', review: 'review.html' }) as Record<string, string> },
  },
  test: { include: ['src/**/*.test.ts'], environment: 'node' },
}))
