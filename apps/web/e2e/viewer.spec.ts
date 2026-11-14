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
