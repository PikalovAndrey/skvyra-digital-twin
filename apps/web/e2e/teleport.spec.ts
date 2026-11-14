// Camera-jump benchmark: `BENCH=1 npx playwright test teleport` -> benchmarks/results/<ts>-teleport.json.
// Runs with the browser's normal vsync / frame pacing on purpose: with `--disable-gpu-vsync` (used by
// benchmark.spec.ts to expose raw frame cost) the CPU queues frames far ahead of the GPU and the first texture upload
// after a jump then waits for that whole backlog (0.4-1.2 s measured) - an artefact users never see.
import { test } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'

test.skip(!process.env.BENCH, 'set BENCH=1 to run the benchmark')

type Ring = { buf: Float32Array; n: number }
type W = { __skvyraStore: { getState(): { set(p: object): void } }; __skvyra: { stats(): { active: number; loading: number; queued: number }; stageMs: { fetch: number; decode: number; prepare: number; texture: number }[] }; __skvyraFrames: Ring; __i?: number }
const pct = (a: number[], q: number) => { const s = [...a].sort((x, y) => x - y); return s.length ? +s[Math.min(s.length - 1, Math.floor((q / 100) * s.length))].toFixed(1) : 0 }

test('benchmark: camera jumps (vsync on)', async ({ page }) => {
  test.setTimeout(1_800_000)
  await page.goto('/?clean=1')
  await page.waitForFunction(() => !!(window as unknown as W).__skvyra)
  const idle = async () => { await page.waitForTimeout(300); await page.waitForFunction(() => { const w = window as unknown as W; const s = w.__skvyra.stats(); if (s.loading || s.queued || !s.active) return (w.__i = undefined), false; w.__i ??= performance.now(); return performance.now() - w.__i > 800 }, null, { timeout: 120_000, polling: 200 }) }
  await idle()
  // 50 deterministic spots over the town and its edges: first visit = cold (nothing loaded or cached), second = warm
  let seed = 20261114
  const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648)
  const n = Number(process.env.JUMPS || 50)
  const spots = Array.from({ length: n }, () => [Math.round(-1600 + 3400 * rnd()), Math.round(-1600 + 3200 * rnd())])
  const jumps: { spot: number[]; round: number; maxFrameMs: number; over50: number; tiles: number; textureMaxMs: number }[] = []
  for (const round of [1, 2]) {
    for (const [x, y] of spots) {
      const r = await page.evaluate(async ([x, y]) => {
        const w = window as unknown as W
        const sm = w.__skvyra.stageMs ?? [], n0 = w.__skvyraFrames.n, s0 = sm.length
        w.__skvyraStore.getState().set({ view: { camera: [x - 120, y - 160, 330], target: [x, y, 205], t: Date.now() } })
        await new Promise((res) => setTimeout(res, 2500))
        const f: number[] = []
        for (let i = n0 + 1; i < w.__skvyraFrames.n; i++) f.push(w.__skvyraFrames.buf[i % w.__skvyraFrames.buf.length])
        const st = (w.__skvyra.stageMs ?? []).slice(s0)
        return { maxFrameMs: Math.max(0, ...f), over50: f.filter((v) => v > 50).length, tiles: st.length, textureMaxMs: Math.max(0, ...st.map((s) => s.texture)) }
      }, [x, y])
      jumps.push({ spot: [x, y], round, ...r })
      await idle()
    }
  }
  const stat = (js: typeof jumps) => { const m = js.map((j) => j.maxFrameMs); return { n: m.length, p50: pct(m, 50), p95: pct(m, 95), p99: pct(m, 99), max: Math.max(...m), over50: m.filter((v) => v > 50).length } }
  const result = { date: new Date().toISOString(), mode: 'vsync on (normal browser pacing)', jumps,
    worstFrameAfterJumpMs: stat(jumps), cold: stat(jumps.filter((j) => j.round === 1)), warm: stat(jumps.filter((j) => j.round === 2)) }
  mkdirSync('../../benchmarks/results', { recursive: true })
  writeFileSync(`../../benchmarks/results/${result.date.slice(0, 19).replace(/[:T]/g, '-')}-teleport.json`, JSON.stringify(result, null, 2))
  console.log(JSON.stringify({ all: result.worstFrameAfterJumpMs, cold: result.cold, warm: result.warm }))
})
