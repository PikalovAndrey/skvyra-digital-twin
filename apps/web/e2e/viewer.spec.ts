import { expect, test, type Page } from '@playwright/test'

const SHOTS = 'e2e-results'

interface Probe {
  shown: Map<string, number>
  stats(): { active: number; loading: number; queued: number; failed: number; lodCounts: number[] }
}

/** Waits until no tile is loading for 1.5 s in a row (a single idle sample can fall between batches). */
async function streamingIdle(page: Page, minActive = 1) {
  await page.evaluate(() => ((window as unknown as { __idleSince?: number }).__idleSince = undefined))
  await page.waitForTimeout(300) // at least one streaming update after the last camera change
  await page.waitForFunction(
    (min) => {
      const w = window as unknown as { __skvyra?: Probe; __idleSince?: number }
      const s = w.__skvyra?.stats()
      if (!s || s.active < min || s.loading > 0 || s.queued > 0) {
        w.__idleSince = undefined
        return false
      }
      w.__idleSince ??= performance.now()
      return performance.now() - w.__idleSince > 800
    },
    minActive,
    { timeout: 90_000, polling: 250 },
  )
}

const stats = (page: Page) =>
  page.evaluate(() => (window as unknown as { __skvyra: Probe }).__skvyra.stats())

test('user journey: overview -> street -> building -> quality -> data mode -> overview', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
  page.on('response', (r) => r.status() >= 400 && errors.push(`${r.status()} ${r.url()}`))

  // 1-2. launch and see the whole city at overview LOD, without loading everything at full detail
  await page.goto('/')
  await expect(page.getByTestId('city-canvas')).toBeVisible()
  await streamingIdle(page, 40)
  const ov = await stats(page)
  expect(ov.lodCounts[0]).toBeLessThan(ov.active) // overview is not all LOD0
  await page.screenshot({ path: `${SHOTS}/1-overview.png` })

  // 3. navigate to a mapped street via search (Latin spelling of a Ukrainian name)
  await page.getByTestId('search-input').fill('Shevchenka')
  await page.getByTestId('search-result').first().click()
  await page.waitForTimeout(1800)
  await streamingIdle(page)
  await page.screenshot({ path: `${SHOTS}/2-street.png` })

  // 4. zoom toward buildings: close-up LOD streams in
  const canvas = page.getByTestId('city-canvas')
  const box = (await canvas.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  for (let i = 0; i < 12; i++) {
    await page.mouse.wheel(0, -400)
    await page.waitForTimeout(120)
  }
  await page.waitForTimeout(800)
  await streamingIdle(page)
  const near = await stats(page)
  expect(near.lodCounts[0]).toBeGreaterThan(0)
  await page.screenshot({ path: `${SHOTS}/2b-zoomed.png` })

  // 5-6. select a building; metadata with provenance is shown
  let selected = false
  for (const [fx, fy] of [[0.5, 0.5], [0.45, 0.55], [0.55, 0.45], [0.5, 0.6], [0.4, 0.5], [0.6, 0.55], [0.35, 0.6], [0.65, 0.4]]) {
    await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy)
    if (await page.getByTestId('info-panel').isVisible()) {
      selected = true
      break
    }
  }
  expect(selected).toBe(true)
  const info = page.getByTestId('info-panel')
  await expect(info).toContainText(/osm:|ms:/)
  await expect(info).toContainText(/(OpenStreetMap|Microsoft)/)
  await page.screenshot({ path: `${SHOTS}/3-selected.png` })

  // 7. switch quality
  await page.getByTestId('tb-settings').click()
  await page.getByTestId('quality').locator('[data-value=high]').click()
  await streamingIdle(page)
  await page.getByTestId('quality').locator('[data-value=low]').click()
  await streamingIdle(page)

  // 8. data-quality mode with legend
  await page.getByTestId('tb-view').click()
  await page.getByTestId('color-mode').locator('[data-value=geometry]').click()
  await expect(page.getByTestId('legend')).toBeVisible()
  await page.waitForTimeout(500)
  await page.screenshot({ path: `${SHOTS}/4-data-quality.png` })

  // 9. back to overview
  await page.getByTestId('overview').click()
  await page.waitForTimeout(1800)
  await streamingIdle(page, 40)
  await page.screenshot({ path: `${SHOTS}/5-back-overview.png` })

  const final = await stats(page)
  expect(final.failed).toBe(0)
  expect(errors.filter((e) => !/Download the React DevTools/.test(e))).toEqual([])
})

test('Ukrainian is default and English switch works', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('search-input')).toHaveAttribute('placeholder', /Пошук/)
  await page.getByTestId('lang').click()
  await expect(page.getByTestId('search-input')).toHaveAttribute('placeholder', /Search/)
})

test('missing tile requests degrade gracefully', async ({ page }) => {
  await page.route(/t\+0\+0\.lod\d\.glb/, (r) => r.fulfill({ status: 503, body: 'down' }))
  await page.goto('/')
  await streamingIdle(page, 40)
  // the rest of the city still streams; the failure is counted, not hidden
  await page.getByTestId('search-input').fill('Сквира')
  await page.getByTestId('search-result').first().click()
  await page.waitForTimeout(2500)
  const s = await stats(page)
  expect(s.active).toBeGreaterThan(5)
  expect(s.failed).toBeGreaterThan(0)
})

test('stress: rapid camera jumps, repeated zoom, resize, quality toggles - no errors, bounded memory', async ({ page }) => {
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
  await page.goto('/')
  await streamingIdle(page, 40)
  type St = { getState(): { set(p: object): void; stats: { heapMB: number | null } } }
  const jump = (x: number, y: number, d: number) =>
    page.evaluate(([x, y, d]) => (window as unknown as { __skvyraStore: St }).__skvyraStore.getState().set({ flyTo: { x, y, dist: d, t: Date.now() } }), [x, y, d])
  const heaps: number[] = []
  for (let round = 0; round < 4; round++) {
    // rapid jumps: most requests become obsolete before they finish and must be aborted, not duplicated
    for (let i = 0; i < 12; i++) {
      await jump(((i * 937) % 5000) - 2500, ((i * 613) % 4000) - 2000, 80 + (i % 4) * 150)
      await page.waitForTimeout(150)
    }
    const box = (await page.getByTestId('city-canvas').boundingBox())!
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
    for (let i = 0; i < 10; i++) await page.mouse.wheel(0, i % 2 ? 900 : -900)
    await page.setViewportSize(round % 2 ? { width: 1440, height: 900 } : { width: 800, height: 1000 })
    await page.evaluate((q) => (window as unknown as { __skvyraStore: St }).__skvyraStore.getState().set({ quality: q }), ['low', 'high', 'medium', 'high'][round])
    await page.evaluate(() => (window as unknown as { __skvyraStore: St }).__skvyraStore.getState().set({ resetView: Date.now() }))
    await page.waitForTimeout(1600)
    await streamingIdle(page, 40)
    heaps.push(await page.evaluate(() => (window as unknown as { __skvyraStore: St }).__skvyraStore.getState().stats.heapMB ?? 0))
  }
  const s = await stats(page)
  expect(s.failed).toBe(0)
  expect(errors).toEqual([])
  // after repeated full cycles the heap must plateau (tile cache is budgeted), not grow per round
  console.log('heap per round (MB):', heaps.map((h) => h.toFixed(0)).join(', '))
  expect(Math.max(...heaps)).toBeLessThan(600)
  expect(heaps[3]).toBeLessThan(heaps[1] * 1.5)
})

test('fly mode moves the camera with the keyboard', async ({ page }) => {
  await page.goto('/')
  await streamingIdle(page, 40)
  await page.getByTestId('tb-settings').click()
  await page.getByTestId('camera-mode').locator('[data-value=fly]').click()
  const pos = () => page.evaluate(() => (window as unknown as { __skvyraCam: { position: { x: number; y: number; z: number } } }).__skvyraCam.position).then((p) => ({ ...p }))
  const before = await pos()
  await page.getByTestId('city-canvas').click({ position: { x: 700, y: 450 } })
  await page.keyboard.down('KeyW')
  await page.waitForTimeout(800)
  await page.keyboard.up('KeyW')
  const after = await pos()
  expect(Math.hypot(after.x - before.x, after.y - before.y, after.z - before.z)).toBeGreaterThan(10)
})

test('data-quality colouring is not black (regression: 16-bit vertex colours)', async ({ page }) => {
  await page.goto('/?clean=1')
  await streamingIdle(page, 40)
  await page.evaluate(() => (window as unknown as { __skvyraStore: { getState(): { set(p: object): void } } }).__skvyraStore.getState().set({ colorMode: 'geometry', flyTo: { x: 150, y: 120, dist: 250, t: Date.now() } }))
  await page.waitForTimeout(1800)
  await streamingIdle(page)
  // sample the building colour attribute directly: OSM buildings must be blue, ML ones orange, never ~0
  const sample = await page.evaluate(() => {
    const m = (window as unknown as { __skvyra: { root: { traverse(f: (o: { userData: { kind?: string }; geometry?: { getAttribute(n: string): { getX(i: number): number; getY(i: number): number; getZ(i: number): number; count: number } } }) => void): void } } }).__skvyra
    let maxSum = 0
    m.root.traverse((o) => {
      if (o.userData.kind === 'walls' && o.geometry) {
        const c = o.geometry.getAttribute('color')
        for (let i = 0; i < Math.min(c.count, 500); i++) maxSum = Math.max(maxSum, c.getX(i) + c.getY(i) + c.getZ(i))
      }
    })
    return maxSum
  })
  expect(sample).toBeGreaterThan(0.3)
})

test('data-quality layer: probable missing outlines, off by default, with statistics', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))
  await page.goto('/')
  await streamingIdle(page, 40)
  await expect(page.getByTestId('gaps-panel')).toHaveCount(0) // off by default
  await page.evaluate(() => (window as unknown as { __skvyraStore: { getState(): { toggleLayer(k: string): void } } }).__skvyraStore.getState().toggleLayer('gaps'))
  const panel = page.getByTestId('gaps-panel')
  await expect(panel).toContainText('km')
  await expect(panel).toContainText('%')
  expect(errors).toEqual([])
})

test.describe('first visit card', () => {
  test.use({ storageState: { cookies: [], origins: [] } }) // a new visitor: empty localStorage

  test('explains the model, offers places, remembers it was seen, reopens with ?', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(String(e)))
    await page.goto('/')
    const card = page.getByTestId('welcome')
    await expect(card).toBeVisible()
    await expect(card).toContainText('Сквира')
    await expect(card).toContainText('не фотографічна копія')
    const cam = () => page.evaluate(() => (window as unknown as { __skvyraCamera: { x: number; y: number } }).__skvyraCamera)
    await page.getByTestId('welcome-lyceum').click()
    await expect(card).toBeHidden()
    await page.waitForTimeout(1800) // fly animation 1.4 s
    const c = await cam()
    expect(Math.hypot(c.x - -760, c.y - 600)).toBeLessThan(30) // at the lyceum camera
    // places menu: synagogue
    await page.getByTestId('tb-places').click()
    await page.getByTestId('place-synagogue').click()
    await page.waitForTimeout(1800)
    const s = await cam()
    expect(Math.hypot(s.x - 412.9, s.y - 110.3)).toBeLessThan(30)
    // seen once: not shown again after a reload, "?" reopens it
    await page.reload()
    await page.waitForTimeout(1500)
    await expect(page.getByTestId('welcome')).toBeHidden()
    await page.getByTestId('help').click()
    await expect(page.getByTestId('welcome')).toBeVisible()
    await page.getByTestId('welcome-go').click()
    await expect(page.getByTestId('welcome')).toBeHidden()
    expect(errors).toEqual([])
  })
})

test('overview loads tile metadata lazily, a far building still opens its card', async ({ page }) => {
  const metaReqs: string[] = []
  page.on('request', (r) => /\/tiles\/t[^/]*\.json/.test(r.url()) && metaReqs.push(r.url()))
  await page.goto('/')
  await streamingIdle(page, 50)
  const s = await stats(page)
  const withIssues = await page.evaluate(() => (window as unknown as { __skvyraStore: { getState(): { index: { tiles: { n: { issues: number } }[] } } } })
    .__skvyraStore.getState().index.tiles.filter((t) => t.n.issues > 0).length)
  expect(s.lodCounts[2]).toBeGreaterThan(50)
  expect(metaReqs.length).toBeLessThanOrEqual(withIssues + s.lodCounts[0] + s.lodCounts[1]) // not one per tile
  const b = await page.evaluate(async () => {
    const w = window as unknown as { __skvyra: { buildingMeta(t: string, f: number): Promise<{ id: string } | null> }; __skvyraStore: { getState(): { index: { tiles: { id: string; n: { buildings: number } }[] } } } }
    const t = w.__skvyraStore.getState().index.tiles.find((x) => x.n.buildings > 10)!
    return w.__skvyra.buildingMeta(t.id, 0)
  })
  expect(b?.id).toBeTruthy()
})

// Loading fill (coarse satellite ground under the tiles): whenever a tile in view has nothing loaded yet, the fill must
// be visible, so the flat horizon plane never shows through, and hidden when nothing is missing. Scenario on a slow
// network: start at one edge before the overview is in, move to the far edge (tiles never loaded), overview, centre
// (LOD changes: the coarser LOD stays as placeholder, no gap).
test('loading fill covers every gap: early jump, return to overview, LOD change', async ({ page, context }) => {
  test.setTimeout(240_000)
  const cdp = await context.newCDPSession(page)
  await cdp.send('Network.enable')
  await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 40, downloadThroughput: 20e6 / 8, uploadThroughput: 2.5e6 / 8 })
  const samples: { t: string; gaps: number; fill: boolean | null; loading: number }[] = []
  const sample = async (t: string, ms: number) => {
    const end = Date.now() + ms
    while (Date.now() < end) {
      const s = await page.evaluate(() => (window as unknown as { __skvyra?: { stats(): { gaps: number; fillVisible: boolean | null; loading: number } } }).__skvyra?.stats())
      if (s) samples.push({ t, gaps: s.gaps, fill: s.fillVisible, loading: s.loading })
      await page.waitForTimeout(100)
    }
  }
  await page.goto('/')
  await page.waitForFunction(() => typeof (window as unknown as { __skvyra?: { stats(): { fillVisible: boolean | null } } }).__skvyra?.stats().fillVisible === 'boolean', null, { timeout: 60_000 })
  // low quality (tiles are wanted within 7 km only), instant jump to the south-west edge before the overview is in, wait
  // there (first "everything loaded" moment with only part of the town loaded), then fly to the north-east edge
  const view = (camera: number[], target: number[], fly: boolean) => page.evaluate((v) => (window as unknown as { __skvyraStore: { getState(): { set(p: object): void } } })
    .__skvyraStore.getState().set({ view: { ...v, t: Date.now() } }), { camera, target, fly })
  await page.evaluate(() => (window as unknown as { __skvyraStore: { getState(): { set(p: object): void } } }).__skvyraStore.getState().set({ quality: 'low' }))
  await view([-3300, -3300, 600], [-3000, -3000, 200], false)
  await sample('south-west', 1500)
  await streamingIdle(page)
  await sample('south-west idle', 600)
  await view([3300, 2800, 600], [3000, 2500, 200], true)
  await sample('north-east', 4000)
  await streamingIdle(page)
  await sample('north-east idle', 600)
  await page.getByTestId('tb-places').click()
  await page.getByTestId('places').getByRole('button').last().click() // "whole town from above"
  await sample('overview', 2500)
  await page.screenshot({ path: `${SHOTS}/loading-fill-overview-return.png` })
  await streamingIdle(page, 50)
  await sample('overview idle', 600)
  await page.getByTestId('tb-places').click()
  await page.getByTestId('place-centre').click()
  await sample('centre', 3000)
  await streamingIdle(page)
  await sample('centre idle', 600)
  const uncovered = samples.filter((s) => s.gaps > 0 && s.fill === false)
  const summary = Object.fromEntries([...new Set(samples.map((s) => s.t))].map((t) => {
    const ss = samples.filter((s) => s.t === t)
    return [t, { n: ss.length, maxGaps: Math.max(...ss.map((s) => s.gaps)), uncovered: ss.filter((s) => s.gaps > 0 && s.fill === false).length, fillShownWithoutGaps: ss.filter((s) => s.gaps === 0 && s.fill).length }]
  }))
  console.log(JSON.stringify(summary))
  expect(samples.some((s) => s.t === 'north-east' && s.gaps > 0)).toBe(true) // gaps after the first idle moment
  expect(uncovered).toEqual([])
  expect(samples.filter((s) => s.t.endsWith('idle')).every((s) => s.gaps === 0 && s.fill === false)).toBe(true) // hidden once all is in
})
