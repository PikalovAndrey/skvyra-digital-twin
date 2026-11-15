import { defineConfig } from '@playwright/test'

// Uses the locally installed Google Chrome (no browser download). WebGL runs on the real GPU via ANGLE.
export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5199/',
    channel: 'chrome',
    viewport: { width: 1440, height: 900 },
    launchOptions: { args: ['--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist'] },
    // the first-visit card is tested on its own (e2e/viewer.spec.ts "first visit card"); elsewhere it is already seen
    trace: 'retain-on-failure', // failures keep a trace + error context in test-results/ (no retries: a flaky test fails)
    storageState: { cookies: [], origins: [{ origin: 'http://127.0.0.1:5199', localStorage: [{ name: 'skvyra.welcomeSeen', value: '1' }] }] },
  },
  webServer: { command: 'npx vite --host 127.0.0.1', url: 'http://127.0.0.1:5199/', reuseExistingServer: true, timeout: 60_000 },
})
