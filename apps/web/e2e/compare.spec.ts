// Viewer screenshots from the fixed viewpoints in blender/presets/viewpoints.json.
// COMPARE=<label> npx playwright test compare  -> renders/compare/<label>/web-<viewpoint>.png
import { test } from '@playwright/test'
import { mkdirSync, readFileSync } from 'node:fs'

test.skip(!process.env.COMPARE, 'set COMPARE=<label>')

const vps = JSON.parse(readFileSync('../../blender/presets/viewpoints.json', 'utf8')).viewpoints as Record<string, { camera: number[]; target: number[] }>
type W = { __skvyraStore: { getState(): { set(p: object): void } }; __skvyra: { stats(): { active: number; loading: number; queued: number } }; __idle?: number }

test('compare screenshots', async ({ page }) => {
  test.setTimeout(400_000)
  const dir = `../../renders/compare/${process.env.COMPARE}`
  mkdirSync(dir, { recursive: true })
  await page.setViewportSize({ width: 1600, height: 900 })
  await page.goto('/?clean=1')
  await page.waitForFunction(() => !!(window as unknown as W).__skvyra)
  await page.evaluate(() => (window as unknown as W).__skvyraStore.getState().set({ quality: 'high' }))
  const only = process.env.VPS ? new Set(process.env.VPS.split(',')) : null
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
  for (const [name, vp] of Object.entries(vps)) {
    if (only && !only.has(name)) continue
    await page.evaluate((v) => {
      const w = window as unknown as W
      w.__idle = undefined // a stale idle timestamp from the previous viewpoint must not count
      w.__skvyraStore.getState().set({ view: { camera: v.camera, target: v.target, t: Date.now() } })
    }, vp)
    await page.waitForTimeout(400) // let the streamer run at least one update for the new camera
    await page.waitForFunction(() => {
      const w = window as unknown as W
      const s = w.__skvyra.stats()
      if (s.loading || s.queued || !s.active) return (w.__idle = undefined), false
      w.__idle ??= performance.now()
      return performance.now() - w.__idle > 1200
    }, null, { timeout: 120_000, polling: 200 })
    await page.screenshot({ path: `${dir}/web-${name}.png` })
  }
  if (errors.length) throw new Error(`browser errors: ${errors.slice(0, 3).join(' | ')}`)
})
