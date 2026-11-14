import { useCallback, useEffect, useMemo, useState } from 'react'
import * as THREE from 'three'
import { DICTS, useT } from './i18n'
import { buildIndex, type SearchItem } from './search'
import { CityCanvas } from './scene/CityCanvas'
import { useStore } from './store'
import type { TileManager } from './tiles/TileManager'
import type { TileIndex } from './types'
import { Attribution, Banner, ControlPanel, InfoPanel, Legend, LoadingBar, Minimap, PointerReadout, SearchBox, StatsOverlay } from './ui/Panels'

const BASE = import.meta.env.BASE_URL
const CLEAN = new URLSearchParams(location.search).has('clean') // canvas only, for comparison screenshots

function webglAvailable(): boolean {
  try {
    const c = document.createElement('canvas')
    return !!c.getContext('webgl2')
  } catch {
    return false
  }
}

export default function App() {
  const t = useT()
  const fatal = useStore((s) => s.fatal)
  const lang = useStore((s) => s.lang)
  const set = useStore((s) => s.set)
  const [index, setIndex] = useState<TileIndex | null>(null)
  const [items, setItems] = useState<SearchItem[]>([])
  const [manager, setManager] = useState<TileManager | null>(null)
  const onManager = useCallback((m: TileManager | null) => {
    setManager(m)
    ;(window as unknown as { __skvyra?: unknown; __skvyraStore?: unknown }).__skvyra = m // e2e tests & benchmarks
    ;(window as unknown as { __skvyraStore?: unknown }).__skvyraStore = useStore
  }, [])

  useEffect(() => {
    if (!webglAvailable()) return set({ fatal: 'webglError' })
    const ctrl = new AbortController()
    Promise.all([
      fetch(`${BASE}tiles/index.json`, { signal: ctrl.signal }).then((r) => {
        if (!r.ok) throw new Error(`index ${r.status}`)
        return r.json() as Promise<TileIndex>
      }),
      fetch(`${BASE}search.json`, { signal: ctrl.signal }).then((r) => (r.ok ? (r.json() as Promise<SearchItem[]>) : [])),
    ])
      .then(([idx, s]) => {
        setIndex(idx)
        setItems(s)
        set({ index: idx })
      })
      .catch((e) => {
        if ((e as Error).name !== 'AbortError') {
          console.error(e)
          set({ fatal: 'indexError' })
        }
      })
    return () => ctrl.abort()
  }, [set])

  useEffect(() => {
    document.documentElement.lang = lang
    document.title = DICTS[lang].title
  }, [lang])

  const searchIndex = useMemo(() => buildIndex(items), [items])
  const streets = useMemo(() => items.filter((i) => i.kind === 'street'), [items])

  if (fatal) {
    return (
      <div className="flex h-full items-center justify-center bg-slate-950 p-6 text-slate-100" data-testid="fatal">
        <div className="max-w-md space-y-3 text-center">
          <div className="text-lg font-medium">{t('title')}</div>
          <p className="text-sm text-slate-300">{t(fatal as 'webglError')}</p>
          <button className="rounded-lg bg-sky-600 px-4 py-2 text-sm" onClick={() => location.reload()}>{t('reload')}</button>
        </div>
      </div>
    )
  }
  if (!index) return <div className="flex h-full items-center justify-center bg-slate-950 text-slate-300">{t('loading')}…</div>

  return (
    <div className="relative h-full w-full overflow-hidden bg-slate-900">
      <CityCanvas index={index} baseUrl={BASE} streets={streets} onManager={onManager} />
      <CameraProbe />
      {CLEAN ? null : <LoadingBar />}
      <div className={`pointer-events-none absolute inset-0 flex flex-col justify-between p-4 ${CLEAN ? 'hidden' : ''}`}>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="pointer-events-auto flex flex-col gap-2">
            <div className="flex items-center gap-3">
              <div className="rounded-xl bg-slate-900/80 px-3 py-1.5 text-slate-100 shadow-xl backdrop-blur-md" title={t('subtitle')}>
                <div className="text-sm font-semibold">{t('title')}</div>
              </div>
              <button
                className="rounded-lg bg-slate-900/80 px-2 py-1 text-xs text-slate-200 shadow"
                onClick={() => set({ lang: lang === 'uk' ? 'en' : 'uk' })}
                data-testid="lang"
              >
                {lang === 'uk' ? 'EN' : 'УКР'}
              </button>
            </div>
            <SearchBox index={searchIndex} />
            <ControlPanel />
          </div>
          <div className="flex flex-col items-end gap-2">
            <InfoPanel index={index} />
            <StatsOverlay />
          </div>
        </div>
        <div className="flex items-end justify-between gap-3">
          <div className="flex max-w-sm flex-col items-start gap-2">
            <Legend />
            <Banner index={index} />
            <PointerReadout />
          </div>
          <div className="flex flex-col items-end gap-2">
            <Minimap index={index} manager={manager} />
            <Attribution index={index} />
          </div>
        </div>
      </div>
    </div>
  )
}

/** Publishes the camera ground position for the minimap without React re-renders. */
function CameraProbe() {
  useEffect(() => {
    let raf = 0
    const v = new THREE.Vector3()
    const tick = () => {
      raf = requestAnimationFrame(tick)
      const cam = (window as unknown as { __skvyraCam?: THREE.Camera }).__skvyraCam
      if (!cam) return
      cam.getWorldDirection(v)
      ;(window as unknown as { __skvyraCamera?: unknown }).__skvyraCamera = { x: cam.position.x, y: -cam.position.z, dir: Math.atan2(v.x, -v.z) }
    }
    tick()
    return () => cancelAnimationFrame(raf)
  }, [])
  return null
}
