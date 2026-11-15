// Smoke test of a DEPLOYED demo (Cloudflare Pages preview behind Cloudflare Access). Read-only: GET requests only.
//   SMOKE_URL=https://preview.<project>.pages.dev/ npx playwright test smoke --headed
// Access: a browser window opens; sign in there (e-mail code), the test continues by itself (up to 5 min).
//   or service token: CF_ACCESS_CLIENT_ID=... CF_ACCESS_CLIENT_SECRET=... (no window needed)
// Options: SMOKE_COMMIT=<sha> (default: apps/web/dist-demo/version.json), SMOKE_PROD_URL=https://<project>.pages.dev/
// (must NOT serve the demo), SMOKE_ACCESS=0 / SMOKE_PREVIEW=0 for a local run (`wrangler pages dev`).
// Desktop and phone runs are Chrome on this Mac: the mobile part is a narrow touch viewport, not a real phone.
import { expect, test, type APIRequestContext, type Browser, type BrowserContext, type Page } from '@playwright/test'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync } from 'node:fs'
import { gunzipSync } from 'node:zlib'
import { PLACES } from '../src/places'

const URL_ = (process.env.SMOKE_URL ?? '').replace(/\/?$/, '/')
const ACCESS = process.env.SMOKE_ACCESS !== '0'
const PREVIEW = process.env.SMOKE_PREVIEW !== '0'
const DIST = 'dist-demo'
const OUT = '../../renders/smoke'
const token = process.env.CF_ACCESS_CLIENT_ID
  ? { 'CF-Access-Client-Id': process.env.CF_ACCESS_CLIENT_ID, 'CF-Access-Client-Secret': process.env.CF_ACCESS_CLIENT_SECRET ?? '' }
  : undefined
const expectedCommit = process.env.SMOKE_COMMIT ?? (existsSync(`${DIST}/version.json`) ? JSON.parse(readFileSync(`${DIST}/version.json`, 'utf8')).commit : '')
type W = { __skvyra?: { stats(): { active: number; loading: number; queued: number; failed: number; gaps?: number; fillVisible?: boolean | null } } }

test.skip(!process.env.SMOKE_URL, 'set SMOKE_URL to the deployed preview (see the header)')
// no trace / video: they would store the Access session cookie and headers on disk
test.use({ trace: 'off', video: 'off' })
test.describe.configure({ mode: 'serial' })
test.setTimeout(420_000)

let auth: Awaited<ReturnType<BrowserContext['storageState']>> | undefined
const problems: string[] = []
const check = (ok: boolean, what: string) => { if (!ok) problems.push(what); console.log(`${ok ? 'ok  ' : 'FAIL'} ${what}`) }

async function newContext(browser: Browser, mobile = false) {
  return browser.newContext({
    ...(mobile ? { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true } : { viewport: { width: 1440, height: 900 } }),
    storageState: auth ?? { cookies: [], origins: [] }, // fresh localStorage on the deployed origin: the welcome card shows
    extraHTTPHeaders: token,
  })
}

async function idle(page: Page, minActive = 1) {
  await page.waitForTimeout(400)
  await page.waitForFunction((min) => {
    const s = (window as unknown as W).__skvyra?.stats()
    const w = window as unknown as { __idle?: number }
    if (!s || s.active < min || s.loading || s.queued) return (w.__idle = undefined), false
    w.__idle ??= performance.now()
    return performance.now() - w.__idle > 1000
  }, minActive, { timeout: 180_000, polling: 250 })
  return page.evaluate(() => (window as unknown as W).__skvyra!.stats())
}

test('access and HTTP delivery', async ({ browser, playwright }) => {
  mkdirSync(OUT, { recursive: true })
  if (ACCESS) {
    // without credentials the preview must not be readable
    const anon = await playwright.request.newContext()
    const r = await anon.get(URL_, { maxRedirects: 0 })
    const loc = (r.headers()['location'] ?? '').split('?')[0] // no query: Access metadata
    check([302, 303, 401, 403].includes(r.status()) && (r.status() >= 400 || /cloudflareaccess\.com/.test(loc)), `anonymous request is stopped by Access (${r.status()} ${loc.slice(0, 60)})`)
    await anon.dispose()
    if (!token) {
      const ctx = await browser.newContext()
      const page = await ctx.newPage()
      await page.goto(URL_)
      console.log('>>> sign in to Cloudflare Access in the opened window (waiting up to 5 min)')
      await page.waitForURL((u) => u.href.startsWith(URL_), { timeout: 300_000 })
      auth = await ctx.storageState()
      await ctx.close()
    }
  }
  const ctx = await newContext(browser)
  const req: APIRequestContext = ctx.request
  const get = (path: string, headers: Record<string, string> = {}, maxRedirects = 5) => req.get(new URL(path, URL_).href, { headers, maxRedirects })

  const v = await get('version.json', { 'Cache-Control': 'no-cache' })
  const ver = v.ok() ? await v.json() : {}
  check(v.ok() && ver.commit === expectedCommit && ver.dirty === false, `version.json = ${String(ver.commit).slice(0, 7)} (expected ${expectedCommit.slice(0, 7)}, dirty ${ver.dirty})`)

  const home = await get('')
  check(home.ok() && /text\/html/.test(home.headers()['content-type'] ?? '') && (await home.text()).includes('<div id="root">'), 'index page 200, text/html')
  if (PREVIEW) check(/noindex/.test(home.headers()['x-robots-tag'] ?? ''), `preview has X-Robots-Tag: noindex (${home.headers()['x-robots-tag']})`)

  const idx = await get('tiles/index.json')
  check(idx.ok() && /no-cache/.test(idx.headers()['cache-control'] ?? ''), `tiles/index.json no-cache (${idx.headers()['cache-control']})`)
  const index = await idx.json()
  const tile = index.tiles.find((t: { files: Record<string, unknown> }) => t.files['2'])
  for (const [path, file] of [[`tiles/${tile.files['2'].u}?v=${tile.hash}`, tile.files['2'].u], [`tiles/${index.context.u}?v=x`, index.context.u]] as const) {
    const r = await get(path, { 'Accept-Encoding': 'gzip, br, zstd' })
    const body = await r.body() // any transport encoding is already removed here, as in a browser
    const h = r.headers()
    const local = existsSync(`${DIST}/tiles/${file}`) ? readFileSync(`${DIST}/tiles/${file}`) : null
    let glb = ''
    try { glb = gunzipSync(body).subarray(0, 4).toString('latin1') } catch { glb = 'not gzip' }
    check(r.ok() && body[0] === 0x1f && body[1] === 0x8b && glb === 'glTF', `${file}: gzip file inside, glTF after one inflate (${h['content-type']}, content-encoding ${h['content-encoding'] ?? 'none'})`)
    if (local) check(createHash('sha256').update(body).digest('hex') === createHash('sha256').update(local).digest('hex'), `${file}: bytes identical to the local package`)
    check(/max-age=86400/.test(h['cache-control'] ?? ''), `${file}: Cache-Control ${h['cache-control']}`)
    if (h['etag']) {
      const again = await get(path, { 'If-None-Match': h['etag'] })
      check(again.status() === 304, `${file}: revalidation 304 (${again.status()})`)
    }
  }
  const js = /assets\/[^"]+\.js/.exec(await home.text())?.[0]
  if (js) {
    const r = await get(js)
    check(r.ok() && /immutable/.test(r.headers()['cache-control'] ?? ''), `${js}: immutable`)
  }
  for (const path of ['tiles/missing.lod2.glb', 'review.html', 'review', 'review.json', 'blender/', '_headers', 'scripts/pack_demo.py', '.git/config', 'data/overrides/footprints.geojson']) {
    const r = await get(path)
    const t = r.status() === 200 ? await r.text() : ''
    check(r.status() !== 200 && !t.includes('<div id="root">'), `${path}: not served (${r.status()})`)
  }
  if (process.env.SMOKE_PROD_URL) {
    const r = await ctx.request.get(process.env.SMOKE_PROD_URL, { maxRedirects: 0 })
    const t = r.status() === 200 ? await r.text() : ''
    check(!t.includes('<div id="root">'), `production URL does not serve the demo (${r.status()})`)
  }
  await ctx.close()
  expect(problems).toEqual([])
})

for (const mobile of [false, true]) {
  test(mobile ? 'phone viewport: welcome, places, synagogue' : 'desktop: overview, lyceum, synagogue', async ({ browser }) => {
    const ctx = await newContext(browser, mobile)
    const page = await ctx.newPage()
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(String(e)))
    page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
    const tag = mobile ? 'phone' : 'desktop'
    await page.goto(URL_)
    await expect(page.getByTestId('welcome')).toBeVisible({ timeout: 60_000 })
    await page.screenshot({ path: `${OUT}/${tag}-welcome.png` })
    await page.getByTestId('welcome-go').click()
    let s = await idle(page, 50)
    check(s.failed === 0 && (s.gaps ?? 0) === 0, `${tag}: overview ${s.active} tiles, ${s.failed} failed`)
    await page.screenshot({ path: `${OUT}/${tag}-overview.png` })
    for (const place of mobile ? ['synagogue'] : ['lyceum', 'synagogue']) {
      await page.getByTestId('tb-places').click()
      await page.getByTestId(`place-${place}`).click()
      const [x, y] = PLACES.find((p) => p.id === place)!.camera // wait until the flight has arrived, then for streaming
      await page.waitForFunction(([px, py]) => {
        const c = (window as unknown as { __skvyraCamera?: { x: number; y: number } }).__skvyraCamera
        return !!c && Math.hypot(c.x - px, c.y - py) < 15
      }, [x, y], { timeout: 60_000, polling: 200 })
      s = await idle(page)
      check(s.failed === 0 && (s.gaps ?? 0) === 0, `${tag}: ${place} loaded, ${s.failed} failed`)
      await page.screenshot({ path: `${OUT}/${tag}-${place}.png` })
    }
    if (mobile) {
      const w = await page.evaluate(() => document.documentElement.scrollWidth)
      check(w <= 390, `phone: no horizontal scroll (${w} px)`)
    }
    check(errors.length === 0, `${tag}: no page errors (${errors.slice(0, 3).join(' | ')})`)
    await ctx.close()
    expect(problems).toEqual([])
  })
}
