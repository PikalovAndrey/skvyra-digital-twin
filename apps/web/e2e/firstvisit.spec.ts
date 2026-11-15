// First visit of a new user against the PRODUCTION build (not the dev server): network cost, first useful frame,
// time to the centre / lyceum / private sector, caching on a second visit, request duplicates.
//   npm run build && npx vite preview --port 4173 --strictPort &
//   FIRSTVISIT=1 NET=none|20mbit|5mbit VIEW=desktop|mobile npx playwright test firstvisit
// -> benchmarks/results/<date>-firstvisit-<net>-<view>.json, renders/firstvisit/<net>-<view>/*.png
// NET throttles bandwidth in Chrome (CDP). VIEW=mobile is a narrow touch viewport on this Mac's GPU - it is NOT a
// phone GPU test.
import { test, type CDPSession } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'

test.skip(!process.env.FIRSTVISIT, 'set FIRSTVISIT=1 (and serve the production build, see the header)')

const URL_ = process.env.DEMO_URL ?? 'http://127.0.0.1:4173/'
const NET = process.env.NET ?? 'none'
const VIEW = process.env.VIEW ?? 'desktop'
const KBPS: Record<string, number> = { '20mbit': 20000, '5mbit': 5000 }
type W = { __skvyra?: { stats(): { active: number; loading: number; queued: number; failed: number; cachedMB: number; lodCounts?: number[] } }; __skvyraStore?: { getState(): { set(p: object): void; stats: { lodCounts: number[] } } } }

test('first visit', async ({ browser }) => {
  test.setTimeout(600_000)
  const mobile = VIEW === 'mobile'
  const ctx = await browser.newContext(mobile ? { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true } : { viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()
  const cdp: CDPSession = await ctx.newCDPSession(page)
  await cdp.send('Network.enable')
  if (KBPS[NET]) await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 40, downloadThroughput: (KBPS[NET] * 1000) / 8, uploadThroughput: 2_500_000 / 8 })
  const reqs = new Map<string, { url: string; t: number; bytes: number; cache: boolean }>()
  let t0 = 0
  cdp.on('Network.requestWillBeSent', (e) => reqs.set(e.requestId, { url: e.request.url, t: Date.now() - t0, bytes: 0, cache: false }))
  cdp.on('Network.requestServedFromCache', (e) => { const r = reqs.get(e.requestId); if (r) r.cache = true })
  cdp.on('Network.loadingFinished', (e) => { const r = reqs.get(e.requestId); if (r) r.bytes = e.encodedDataLength })
  const dir = `../../renders/firstvisit/${NET}-${VIEW}`
  mkdirSync(dir, { recursive: true })
  const bytesUntil = (ms: number) => [...reqs.values()].filter((r) => r.t <= ms).reduce((a, r) => a + r.bytes, 0) / 1e6
  const stats = () => page.evaluate(() => { const w = window as unknown as W; return w.__skvyra ? { ...w.__skvyra.stats(), lod: w.__skvyraStore!.getState().stats.lodCounts } : null })

  t0 = Date.now()
  await page.goto(URL_)
  const marks: Record<string, number | null> = { firstTile: null, firstIdle: null }
  const shots = [2000, 5000, 10000]
  const trace: { t: number; active: number; loading: number; queued: number; lod: number[]; mb: number; cachedMB: number }[] = []
  let idleSince: number | null = null
  while (Date.now() - t0 < 60_000) {
    const t = Date.now() - t0
    const s = await stats()
    if (s) {
      trace.push({ t, active: s.active, loading: s.loading, queued: s.queued, lod: s.lod, mb: +bytesUntil(t).toFixed(2), cachedMB: +s.cachedMB.toFixed(1) })
      if (s.active > 0 && marks.firstTile === null) marks.firstTile = t
      if (s.active > 0 && !s.loading && !s.queued) { idleSince ??= t; if (marks.firstIdle === null && t - idleSince > 1000) marks.firstIdle = idleSince } else idleSince = null
    }
    if (shots.length && t >= shots[0]) await page.screenshot({ path: `${dir}/t${shots.shift()! / 1000}s.png` })
    await page.waitForTimeout(250)
  }
  const visit1 = [...reqs.values()]
  const at = { s10: bytesUntil(10_000), s30: bytesUntil(30_000), s60: bytesUntil(60_000) }

  // places: time until the streamer is idle again and the bytes it took
  const places: Record<string, { ms: number; mb: number; failedTiles: number | null; cachedMB: number | null }> = {}
  for (const [name, x, y, dist, pitch] of [['centre', 150, 120, 350, 48], ['lyceum', -815, 705, 250, 35], ['private sector', -700, -500, 150, 40]] as const) {
    const before = [...reqs.values()].reduce((a, r) => a + r.bytes, 0)
    const ts = Date.now()
    await page.evaluate((p) => (window as unknown as W).__skvyraStore!.getState().set({ flyTo: { ...p, t: Date.now() } }), { x, y, dist, pitch })
    await page.waitForTimeout(400)
    let since: number | null = null
    while (Date.now() - ts < 120_000) {
      const s = await stats()
      if (s && !s.loading && !s.queued) { since ??= Date.now(); if (Date.now() - since > 1000) break } else since = null
      await page.waitForTimeout(200)
    }
    const ps = await stats()
    places[name] = { failedTiles: ps?.failed ?? null, cachedMB: ps ? +ps.cachedMB.toFixed(1) : null, ms: (since ?? Date.now()) - ts, mb: +(([...reqs.values()].reduce((a, r) => a + r.bytes, 0) - before) / 1e6).toFixed(2) }
    await page.screenshot({ path: `${dir}/${name.replace(' ', '-')}.png` })
  }
  const all = [...reqs.values()]
  const byUrl = new Map<string, number>()
  for (const r of all) byUrl.set(r.url, (byUrl.get(r.url) ?? 0) + 1)
  const dupes = [...byUrl.entries()].filter(([, n]) => n > 1)
  const kind = (u: string) => (/\.lod(\d)\.glb/.exec(u)?.[1] ? `lod${/\.lod(\d)\.glb/.exec(u)![1]}` : /\.js($|\?)/.test(u) ? 'js' : /\.css/.test(u) ? 'css' : /tiles\/t[^/]*\.json/.test(u) ? 'tile-meta' : u.split('/').pop()!.split('?')[0])
  const byKind: Record<string, { n: number; mb: number }> = {}
  for (const r of all) { const k = kind(r.url); byKind[k] ??= { n: 0, mb: 0 }; byKind[k].n++; byKind[k].mb = +(byKind[k].mb + r.bytes / 1e6).toFixed(3) }
  const initial = visit1.filter((r) => /\.(js|css|html)(\?|$)|\/$|index\.json|search\.json/.test(r.url)).reduce((a, r) => a + r.bytes, 0) / 1e6

  // second visit (same browser cache): what the network still has to carry
  const n1 = reqs.size
  t0 = Date.now()
  await page.reload()
  await page.waitForTimeout(15_000)
  const second = [...reqs.values()].slice(n1)
  const result = {
    date: new Date().toISOString(), url: URL_, net: NET, view: VIEW, note: VIEW === 'mobile' ? 'narrow touch viewport emulated on a desktop GPU - not a phone GPU test' : undefined,
    firstTileMs: marks.firstTile, firstUsefulFrameMs: marks.firstIdle, initialHtmlJsCssIndexMB: +initial.toFixed(3),
    transferredMB: { first10s: +at.s10.toFixed(2), first30s: +at.s30.toFixed(2), first60s: +at.s60.toFixed(2) }, places,
    totalRequests: all.length, totalMB: +(all.reduce((a, r) => a + r.bytes, 0) / 1e6).toFixed(2), byKind, duplicateUrls: dupes.slice(0, 20),
    secondVisit15s: { requests: second.length, fromCache: second.filter((r) => r.cache).length, networkMB: +(second.reduce((a, r) => a + r.bytes, 0) / 1e6).toFixed(3) },
    trace: trace.filter((_, i) => i % 4 === 0),
  }
  mkdirSync('../../benchmarks/results', { recursive: true })
  writeFileSync(`../../benchmarks/results/${result.date.slice(0, 19).replace(/[:T]/g, '-')}-firstvisit-${NET}-${VIEW}.json`, JSON.stringify(result, null, 1))
  console.log(JSON.stringify({ ...result, trace: undefined }, null, 1))
  await ctx.close()
})
