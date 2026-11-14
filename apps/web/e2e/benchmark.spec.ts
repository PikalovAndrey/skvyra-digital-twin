// Reproducible performance scenario: `BENCH=1 npx playwright test benchmark` (writes benchmarks/results/*.json).
// Static viewpoints (median / p95 / p99 frame time) + a continuous low-altitude flight across the town that
// forces tile streaming while moving + tile load latency + memory over time.
import { test, type Page } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import os from 'node:os'

test.skip(!process.env.BENCH, 'set BENCH=1 to run the benchmark')
// uncapped frame rate so frame times measure real cost instead of the 60 Hz vsync limit
test.use({ launchOptions: { args: ['--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist', '--disable-gpu-vsync', '--disable-frame-rate-limit'] } })

type Ring = { buf: Float32Array; n: number }
interface W {
  __skvyraStore: { getState(): { stats: Record<string, unknown>; set(p: object): void } }
  __skvyra: { stats(): { active: number; loading: number; queued: number }; loadMs: number[]; parseMs: number[] }
  __skvyraFrames: Ring
}

const pct = (a: number[], q: number) => {
  const s = [...a].sort((x, y) => x - y)
  return s.length ? s[Math.min(s.length - 1, Math.floor((q / 100) * s.length))] : 0
}
const summary = (a: number[]) => ({ n: a.length, mean: +(a.reduce((x, y) => x + y, 0) / Math.max(1, a.length)).toFixed(2), p50: +pct(a, 50).toFixed(2), p95: +pct(a, 95).toFixed(2), p99: +pct(a, 99).toFixed(2), max: +Math.max(0, ...a).toFixed(2) })

async function frames(page: Page, ms: number): Promise<number[]> {
  const start = await page.evaluate(() => (window as unknown as W).__skvyraFrames.n)
  await page.waitForTimeout(ms)
  return page.evaluate((s) => {
    const r = (window as unknown as W).__skvyraFrames
    const out: number[] = []
    for (let i = Math.max(s, r.n - r.buf.length); i < r.n; i++) out.push(r.buf[i % r.buf.length])
    return out
  }, start)
}

test('benchmark: city route', async ({ page }) => {
  test.setTimeout(400_000)
  const t0 = Date.now()
  await page.goto('/?clean=1')
  const idle = async () => {
    await page.evaluate(() => ((window as unknown as { __i?: number }).__i = undefined))
    await page.waitForTimeout(300)
    await page.waitForFunction(() => {
      const w = window as unknown as W & { __i?: number }
      const s = w.__skvyra?.stats()
      if (!s || !s.active || s.loading || s.queued) return (w.__i = undefined), false
      w.__i ??= performance.now()
      return performance.now() - w.__i > 800
    }, null, { timeout: 120_000, polling: 200 })
  }
  await idle()
  const firstIdleMs = Date.now() - t0
  const heap = async () => (await page.evaluate(() => (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory?.usedJSHeapSize ?? 0)) / 1e6
  const set = (p: object) => page.evaluate((x) => (window as unknown as W).__skvyraStore.getState().set(x), p)
  const samples: Record<string, unknown>[] = []
  const still = async (label: string) => {
    await idle()
    const f = await frames(page, 3000)
    const st = await page.evaluate(() => (window as unknown as W).__skvyraStore.getState().stats)
    samples.push({ label, frameMs: summary(f), calls: st.calls, triangles: st.triangles, lodCounts: st.lodCounts, heapMB: +(await heap()).toFixed(1), transferredMB: st.transferredMB })
  }
  const fly = (x: number, y: number, dist: number, pitch = 48) => set({ flyTo: { x, y, dist, pitch, t: Date.now() } }).then(() => page.waitForTimeout(1600))
  for (const q of ['medium', 'high'] as const) {
    await set({ quality: q })
    await set({ resetView: Date.now() }); await page.waitForTimeout(1600); await still(`overview (${q})`)
    await fly(150, 120, 350); await still(`centre 350 m (${q})`)
    await fly(-700, -500, 90); await still(`south-west houses 90 m (${q})`)
    await fly(150, 120, 300, 10); await still(`centre low angle 10° (${q})`)
  }
  // continuous flight: 18 s straight across town at 120 m altitude (~2.8 km), streaming while moving
  await set({ quality: 'medium' })
  await idle() // let the quality switch (shadow map size, LOD distances) settle before measuring the flight
  await page.waitForTimeout(1000)
  const start = await page.evaluate(() => (window as unknown as W).__skvyraFrames.n)
  const loadsBefore = await page.evaluate(() => (window as unknown as W).__skvyra.loadMs.length)
  const heapTrace: number[] = []
  await page.evaluate(() => {
    const w = window as unknown as W & { __flying?: boolean }
    const t0 = performance.now()
    w.__flying = true
    const step = () => {
      const k = Math.min(1, (performance.now() - t0) / 18000)
      const x = -1500 + 2800 * k, y = -900 + 1700 * k
      w.__skvyraStore.getState().set({ view: { camera: [x - 150, y - 150, 330], target: [x + 200, y + 200, 200], t: performance.now() } })
      if (k < 1) requestAnimationFrame(step); else w.__flying = false
    }
    requestAnimationFrame(step)
  })
  for (let i = 0; i < 9; i++) { await page.waitForTimeout(2000); heapTrace.push(+(await heap()).toFixed(1)) }
  await page.waitForFunction(() => !(window as unknown as { __flying?: boolean }).__flying, null, { timeout: 30_000 })
  const flightFrames = await page.evaluate((s) => {
    const r = (window as unknown as W).__skvyraFrames
    const out: number[] = []
    for (let i = Math.max(s, r.n - r.buf.length); i < r.n; i++) out.push(r.buf[i % r.buf.length])
    return out
  }, start)
  const loads = await page.evaluate((b) => ({ all: (window as unknown as W).__skvyra.loadMs.slice(b), parse: (window as unknown as W).__skvyra.parseMs.slice(b) }), loadsBefore)
  const allLoads = await page.evaluate(() => ({ all: (window as unknown as W).__skvyra.loadMs, parse: (window as unknown as W).__skvyra.parseMs }))
  const gl = await page.evaluate(() => {
    const c = document.createElement('canvas').getContext('webgl2')!
    const ext = c.getExtension('WEBGL_debug_renderer_info')
    return ext ? c.getParameter(ext.UNMASKED_RENDERER_WEBGL) : c.getParameter(c.RENDERER)
  })
  const result = {
    date: new Date().toISOString(), machine: `${os.cpus()[0].model}, ${os.cpus().length} cores, ${(os.totalmem() / 2 ** 30).toFixed(0)} GB`,
    gpu: gl, viewport: page.viewportSize(), browser: 'Chrome (Playwright, channel chrome, ANGLE Metal, vsync off)',
    firstIdleMs, samples,
    flight: { frameMs: summary(flightFrames), hitchesOver50ms: flightFrames.map((v, i) => [i, +v.toFixed(1)]).filter(([, v]) => v > 50), tileLoadsDuringFlight: loads.all.length, loadMs: summary(loads.all), parseMs: summary(loads.parse), heapMBTrace: heapTrace },
    allTileLoads: { loadMs: summary(allLoads.all), parseMs: summary(allLoads.parse) },
  }
  mkdirSync('../../benchmarks/results', { recursive: true })
  writeFileSync(`../../benchmarks/results/${result.date.slice(0, 19).replace(/[:T]/g, '-')}.json`, JSON.stringify(result, null, 2))
  console.log(JSON.stringify({ firstIdleMs, flight: result.flight, allTileLoads: result.allTileLoads }, null, 1))
})
