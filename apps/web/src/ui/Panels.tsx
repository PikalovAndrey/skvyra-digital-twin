import { useEffect, useMemo, useRef, useState } from 'react'
import { formatLatLon } from '../geo'
import { useT, type Key } from '../i18n'
import { search, type Indexed } from '../search'
import { useStore } from '../store'
import { GAP_COLORS, QUALITY_COLORS } from '../scene/materials'
import type { TileManager } from '../tiles/TileManager'
import type { ColorMode, Quality, TileIndex } from '../types'

const panel = 'pointer-events-auto rounded-xl border border-white/10 bg-slate-900/80 text-slate-100 shadow-xl backdrop-blur-md'

export function SearchBox({ index }: { index: Indexed[] }) {
  const t = useT()
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const results = useMemo(() => search(index, q), [index, q])
  const go = (r: Indexed) => {
    const s = useStore.getState()
    const dist = r.kind === 'street' && r.bb ? Math.max(180, Math.min(1500, Math.max(r.bb[2] - r.bb[0], r.bb[3] - r.bb[1]) * 0.9)) : 160
    s.set({ flyTo: { x: r.x, y: r.y, dist, t: Date.now() }, selection: null })
    setQ(r.name)
    setOpen(false)
  }
  return (
    <div className="relative w-full sm:w-80">
      <input
        data-testid="search-input"
        value={q}
        onChange={(e) => {
          setQ(e.target.value)
          setOpen(true)
          setActive(0)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') setActive((a) => Math.min(a + 1, results.length - 1))
          if (e.key === 'ArrowUp') setActive((a) => Math.max(a - 1, 0))
          if (e.key === 'Enter' && results[active]) go(results[active])
          if (e.key === 'Escape') setOpen(false)
        }}
        placeholder={t('search')}
        aria-label={t('search')}
        className="w-full rounded-lg border border-white/10 bg-slate-950/70 px-3 py-2 text-sm text-slate-100 placeholder-slate-400 outline-none focus:border-sky-400"
      />
      {open && q.trim() && (
        <ul className="absolute z-20 mt-1 max-h-80 w-full overflow-auto rounded-lg border border-white/10 bg-slate-900/95 py-1 text-sm shadow-2xl" role="listbox">
          {results.length === 0 && <li className="px-3 py-2 text-slate-400">{t('noResults')}</li>}
          {results.map((r, i) => (
            <li key={`${r.name}-${r.x}-${r.y}`} role="option" aria-selected={i === active}>
              <button
                data-testid="search-result"
                className={`flex w-full items-baseline justify-between gap-2 px-3 py-1.5 text-left hover:bg-sky-500/20 ${i === active ? 'bg-sky-500/20' : ''}`}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => go(r)}
              >
                <span className="truncate">{r.name}</span>
                <span className="shrink-0 text-xs text-slate-400">{kindLabel(r.kind)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function kindLabel(kind: string) {
  if (kind === 'street') return '🛣'
  if (kind === 'building') return '🏠'
  return kind.split('=')[1]?.replace(/_/g, ' ') ?? kind
}

function Seg<T extends string>({ value, options, onChange, label, testid, vertical }: { value: T; options: [T, string][]; onChange: (v: T) => void; label: string; testid?: string; vertical?: boolean }) {
  return (
    <div>
      <div className="mb-1 text-[11px] uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`flex overflow-hidden rounded-lg border border-white/10 ${vertical ? 'flex-col' : ''}`} role="radiogroup" aria-label={label} data-testid={testid}>
        {options.map(([v, l]) => (
          <button
            key={v}
            role="radio"
            aria-checked={value === v}
            data-value={v}
            onClick={() => onChange(v)}
            className={`flex-1 px-2 py-1 text-xs ${value === v ? 'bg-sky-500 text-white' : 'bg-slate-950/50 text-slate-300 hover:bg-slate-800'}`}
          >
            {l}
          </button>
        ))}
      </div>
    </div>
  )
}

type Pop = 'layers' | 'view' | 'settings' | null

/** Compact toolbar: every control lives in a small popover, all closed by default. */
export function ControlPanel() {
  const t = useT()
  const s = useStore()
  const [pop, setPop] = useState<Pop>(null)
  const btn = (id: Exclude<Pop, null>, icon: string, label: string) => (
    <button
      data-testid={`tb-${id}`}
      aria-expanded={pop === id}
      title={label}
      onClick={() => setPop(pop === id ? null : id)}
      className={`flex h-8 items-center gap-1 rounded-lg px-2 text-xs ${pop === id ? 'bg-sky-500 text-white' : 'bg-slate-900/80 text-slate-200 hover:bg-slate-800'}`}
    >
      <span aria-hidden>{icon}</span>
      <span className="hidden sm:inline">{label}</span>
    </button>
  )
  return (
    <div className="pointer-events-auto relative">
      <div className="flex gap-1 rounded-xl bg-slate-900/60 p-1 shadow-lg backdrop-blur-md">
        {btn('layers', '▤', t('layers'))}
        {btn('view', '◐', t('colorMode'))}
        {btn('settings', '⚙', t('quality'))}
        <button
          data-testid="overview"
          title={t('overview')}
          className="flex h-8 items-center rounded-lg bg-slate-900/80 px-2 text-xs text-slate-200 hover:bg-slate-800"
          onClick={() => s.set({ resetView: Date.now(), selection: null })}
        >
          ⌂<span className="ml-1 hidden sm:inline">{t('overview')}</span>
        </button>
      </div>
      {pop && (
        <div className={`${panel} absolute left-0 top-11 z-20 w-72 max-w-[calc(100vw-32px)] space-y-3 p-3 text-sm`}>
          {pop === 'layers' && (
            <div className="grid grid-cols-1 gap-1" data-testid="layers">
              {(Object.keys(s.layers) as (keyof typeof s.layers)[]).map((k) => (
                <label key={k} className="flex cursor-pointer items-center gap-2 text-xs">
                  <input type="checkbox" checked={s.layers[k]} onChange={() => s.toggleLayer(k)} className="accent-sky-500" />
                  {t(`layer.${k}` as Key)}
                </label>
              ))}
            </div>
          )}
          {pop === 'view' && (
            <Seg<ColorMode>
              label={t('colorMode')}
              value={s.colorMode}
              onChange={(v) => s.set({ colorMode: v })}
              testid="color-mode"
              vertical
              options={[['realistic', t('colorMode.realistic')], ['geometry', t('colorMode.geometry')], ['height', t('colorMode.height')]]}
            />
          )}
          {pop === 'settings' && (
            <>
              <Seg<Quality>
                label={t('quality')}
                value={s.quality}
                onChange={(v) => s.set({ quality: v })}
                testid="quality"
                options={[['low', t('quality.low')], ['medium', t('quality.medium')], ['high', t('quality.high')]]}
              />
              <Seg<'orbit' | 'fly'>
                label={t('camera')}
                value={s.cameraMode}
                onChange={(v) => s.set({ cameraMode: v })}
                testid="camera-mode"
                options={[['orbit', t('camera.orbit')], ['fly', t('camera.fly')]]}
              />
              {s.cameraMode === 'fly' && (
                <div>
                  <label className="text-[11px] uppercase tracking-wide text-slate-400">{t('flySpeed')}: {Math.round(s.flySpeed)} {t('m')}/s</label>
                  <input type="range" min={5} max={600} value={s.flySpeed} onChange={(e) => s.set({ flySpeed: Number(e.target.value) })} className="w-full accent-sky-500" />
                  <p className="text-[11px] text-slate-400">{t('flyHelp')}</p>
                </div>
              )}
              <button className="w-full rounded-lg bg-slate-800 px-2 py-1.5 text-xs hover:bg-slate-700" onClick={() => s.set({ resetView: Date.now(), cameraMode: 'orbit', flySpeed: 40 })}>
                {t('reset')}
              </button>
              <div className="flex gap-3 text-xs">
                <label className="flex items-center gap-1"><input type="checkbox" checked={s.showStats} onChange={() => s.set({ showStats: !s.showStats })} className="accent-sky-500" data-testid="toggle-stats" />{t('stats')}</label>
                <label className="flex items-center gap-1"><input type="checkbox" checked={s.minimap} onChange={() => s.set({ minimap: !s.minimap })} className="accent-sky-500" />{t('minimap')}</label>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function GapPanel() {
  const t = useT()
  const st = useStore((s) => s.gapStats)
  const pct = (v: number) => `${(v * 100).toFixed(1)} %`
  const rows: [string, Key, string][] = st
    ? [[GAP_COLORS.suspect_gap_medium, 'gaps.suspectMedium', `${pct(st.suspect_gap_share)} · ${st.suspect_gap_km} km`], [GAP_COLORS.suspect_gap_low, 'gaps.suspectLow', ''],
       [GAP_COLORS.insufficient, 'gaps.insufficient', `${pct(st.insufficient_share)} · ${st.insufficient_km} km`],
       [GAP_COLORS.no_signs, 'gaps.noSigns', `${pct(st.no_signs_share)} · ${st.no_signs_km} km`]]
    : []
  return (
    <div className={`${panel} max-w-[19rem] px-2.5 py-2 text-[11px]`} data-testid="gaps-panel">
      <div className="mb-0.5 font-medium">{t('gaps.title')}</div>
      {st ? (
        <>
          <div className="text-slate-300">{t('gaps.checked')}: {st.residential_street_km} km · {t('gaps.covered')}: {pct(st.covered_share)}</div>
          {rows.map(([c, k, v]) => (
            <div key={k} className="flex items-center gap-2 py-0.5">
              <span className="inline-block h-1.5 w-4 rounded-sm" style={{ background: c }} />
              <span>{t(k)}</span>
              <span className="ml-auto text-slate-400">{v}</span>
            </div>
          ))}
          <div className="mt-1 text-slate-400">{t('gaps.note')}</div>
        </>
      ) : (
        <div className="text-slate-400">…</div>
      )}
    </div>
  )
}

export function Legend() {
  const t = useT()
  const mode = useStore((s) => s.colorMode)
  const gaps = useStore((s) => s.layers.gaps)
  if (mode === 'realistic') return gaps ? <GapPanel /> : null
  const rows: [string, Key][] =
    mode === 'geometry'
      ? [[QUALITY_COLORS.verified, 'legend.verified'], [QUALITY_COLORS.source, 'legend.source'], [QUALITY_COLORS.inferred, 'legend.inferred'], [QUALITY_COLORS.missing, 'legend.missing'], [QUALITY_COLORS.treeModel, 'legend.treeModel']]
      : [[QUALITY_COLORS.hOverride, 'legend.hOverride'], [QUALITY_COLORS.hSource, 'legend.hSource'], [QUALITY_COLORS.hGhsl, 'legend.hGhsl'], [QUALITY_COLORS.hDefault, 'legend.hDefault'], [QUALITY_COLORS.missing, 'legend.missing']]
  return (
    <>
    {gaps && <GapPanel />}
    <div className={`${panel} px-2.5 py-2 text-[11px]`} data-testid="legend">
      <div className="mb-0.5 font-medium">{t('legend')}</div>
      {rows.map(([c, k]) => (
        <div key={k} className="flex items-center gap-2 py-0.5">
          <span className="inline-block h-3 w-3 rounded-sm" style={{ background: c }} />
          {t(k)}
        </div>
      ))}
    </div>
    </>
  )
}

export function InfoPanel({ index }: { index: TileIndex }) {
  const t = useT()
  const sel = useStore((s) => s.selection)
  const set = useStore((s) => s.set)
  if (!sel) return null
  const rows: [Key, React.ReactNode][] = []
  if (sel.type === 'building') {
    const b = sel.meta
    rows.push(
      ['f.name', b.name], ['f.addr', b.addr],
      ['f.type', <>{t(`kind.${b.kind}` as Key)} <Hint>{b.type ? `building=${b.type}` : t(`kindM.${b.kindM}` as Key)}</Hint></>],
      ['f.height', <>~{b.h.toFixed(1)} {t('m')} <Hint>{t(`hM.${b.hM}` as Key)}{b.hC ? ` · ${t('f.confidenceShort')}: ${t(`hC.${b.hC}` as Key)}` : ''}</Hint></>],
      ['f.ghsl', b.ghsl?.anbh != null ? <>{b.ghsl.anbh.toFixed(1)} {t('m')} <Hint>{t('ghslNote')}{b.ghsl.share != null ? ` · ${Math.round(b.ghsl.share * 100)}%` : ''}</Hint></> : null],
      ['f.levels', b.lv != null ? <>{b.lv} <Hint>{b.hM === 'source_levels' || b.hM === 'override' ? t('observed') : t('inferred')}</Hint></> : null],
      ['f.roof', <>{t(`roof.${b.roof}` as Key)}{b.parts ? ` (${b.parts} ${t('roofParts')})` : ''}{b.roofMat ? `, ${t(`roofMat.${b.roofMat}` as Key)}` : ''} <Hint>{t(`roofM.${b.roofM}` as Key)}</Hint></>],
      ['f.landmark', b.landmark ? <span>{b.landmark.name}{b.landmark.match === 'probable' ? <Hint> ({t('landmark.probable')})</Hint> : null}{b.landmark.heritage && b.landmark.heritage.status !== 'unknown' ? ` · ${t(`heritage.${b.landmark.heritage.status}` as Key)}` : ''}<br />{b.landmark.identity_audit ? <Hint>{b.landmark.identity_audit}<br /></Hint> : null}{b.landmark.heritage?.reported_status ? <Hint>{t('heritage.reported')}: {b.landmark.heritage.reported_status} ({b.landmark.heritage.checked})<br /></Hint> : null}<Hint>{b.landmark.evidence}</Hint>{(b.landmark.photos ?? []).map((u, i) => <a key={u} className="ml-1 text-sky-300 underline" href={u} target="_blank" rel="noreferrer">{t('photo')} {i + 1}</a>)}{b.landmark.license ? <Hint> {b.landmark.license}</Hint> : null}{' '}<a className="text-sky-300 underline" href={`https://www.wikidata.org/wiki/${b.landmark.wikidata}`} target="_blank" rel="noreferrer">{b.landmark.wikidata}</a></span> : null],
      ['f.area', `${b.area.toFixed(0)} ${t('m2')}`],
      ['f.confidence', <Conf c={b.conf} />],
      ['f.source', t(`src.${b.src}` as Key)],
      ['f.updated', b.ts?.slice(0, 10)],
      ['f.coords', formatLatLon(b.ll[0], b.ll[1])],
      ['f.id', <code>{b.id}</code>],
      ['f.sid', b.src === 'osm' ? <a className="text-sky-300 underline" href={`https://www.openstreetmap.org/${b.sid.replace('osm:', '').split('#')[0]}`} target="_blank" rel="noreferrer">{b.sid}</a> : <code>{b.sid}</code>],
      ['f.note', b.note],
    )
  } else {
    const i = sel.issue
    rows.push(['f.name', i.name], ['f.reason', i.reason], ['f.sid', <code>{i.sid}</code>])
  }
  return (
    <div className={`${panel} w-80 max-w-[calc(100vw-32px)] p-3 text-sm`} data-testid="info-panel">
      <div className="mb-2 flex items-center justify-between">
        <div className="font-medium">{sel.type === 'building' ? t('building') : t('dataIssue')}</div>
        <button onClick={() => set({ selection: null })} className="rounded px-2 text-slate-400 hover:bg-slate-800" aria-label={t('close')}>✕</button>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        {rows.filter(([, v]) => v != null && v !== '').map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-slate-400">{t(k)}</dt>
            <dd className="break-words [overflow-wrap:anywhere]">{v}</dd>
          </div>
        ))}
      </dl>
      <div className="mt-2 text-[10px] text-slate-500">
        {sel.type === 'building' && sel.meta.src === 'ms_ml'
          ? 'Building footprint © Microsoft, CDLA-Permissive-2.0'
          : index.sources['osm.json']?.license}
      </div>
    </div>
  )
}

const Hint = ({ children }: { children: React.ReactNode }) => <span className="text-[10px] text-slate-400">({children})</span>

function Conf({ c }: { c: 'verified' | 'source' | 'inferred' }) {
  const t = useT()
  return (
    <span className="inline-flex items-center gap-1">
      <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: QUALITY_COLORS[c] }} />
      {t(`conf.${c}` as Key)}
    </span>
  )
}

export function PointerReadout() {
  const t = useT()
  const p = useStore((s) => s.pointer)
  if (!p) return null
  return (
    <div className={`${panel} px-3 py-2 font-mono text-[11px] leading-5`} data-testid="pointer-readout">
      <div>{formatLatLon(p.lon, p.lat)}</div>
      <div>{t('elevation')}: {p.elev.toFixed(1)} {t('m')}</div>
      {p.street && <div className="font-sans">{t('street')}: {p.street}</div>}
    </div>
  )
}

export function StatsOverlay() {
  const t = useT()
  const s = useStore((st) => st.stats)
  const show = useStore((st) => st.showStats)
  if (!show) return null
  return (
    <div className={`${panel} px-3 py-2 font-mono text-[11px] leading-5`} data-testid="stats">
      <div>FPS {s.fps.toFixed(0)} · {s.frameMs.toFixed(1)} ms</div>
      <div>draw calls {s.calls} · tris {(s.triangles / 1e6).toFixed(2)}M</div>
      <div>{t('tiles')} {s.activeTiles} (L0 {s.lodCounts[0]} / L1 {s.lodCounts[1]} / L2 {s.lodCounts[2]})</div>
      <div>loading {s.loading} · queued {s.queued} · failed {s.failed}</div>
      <div>cache {s.cachedMB.toFixed(0)} MB · net {s.transferredMB.toFixed(1)} MB</div>
      {s.heapMB != null && <div>JS heap {s.heapMB.toFixed(0)} MB</div>}
    </div>
  )
}

export function LoadingBar() {
  const t = useT()
  const loading = useStore((s) => s.stats.loading + s.stats.queued)
  const active = useStore((s) => s.stats.activeTiles)
  if (!loading) return null
  return (
    <div className="pointer-events-none absolute inset-x-0 top-0 z-30" data-testid="loading">
      <div className="h-0.5 w-full animate-pulse bg-sky-400" />
      <div className="absolute right-3 top-2 rounded bg-slate-900/80 px-2 py-0.5 text-[11px] text-slate-200">{t('loading')} · {loading} / {active} {t('tiles')}</div>
    </div>
  )
}

export function Minimap({ index, manager }: { index: TileIndex; manager: TileManager | null }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const show = useStore((s) => s.minimap)
  const geom = useMemo(() => {
    const xs = index.tiles.flatMap((t) => [t.b[0], t.b[2]])
    const ys = index.tiles.flatMap((t) => [t.b[1], t.b[3]])
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys)
    return { x0, y0, s: 130 / Math.max(x1 - x0, y1 - y0), w: x1 - x0, h: y1 - y0 }
  }, [index])
  useEffect(() => {
    if (!show) return
    let raf = 0
    let last = 0
    const draw = (now = 0) => {
      raf = requestAnimationFrame(draw)
      if (now - last < 100) return // 10 Hz is plenty for a minimap
      last = now
      const c = ref.current
      if (!c || !manager) return
      const g = c.getContext('2d')!
      const { x0, y0, s, h } = geom
      const X = (x: number) => (x - x0) * s
      const Y = (y: number) => (y0 + h - y) * s
      g.clearRect(0, 0, c.width, c.height)
      const shown = manager.shown
      for (const t of index.tiles) {
        const lod = shown.get(t.id)
        g.fillStyle = lod === 0 ? '#38bdf8aa' : lod === 1 ? '#38bdf855' : lod === 2 ? '#38bdf822' : '#ffffff08'
        g.fillRect(X(t.b[0]), Y(t.b[3]), (t.b[2] - t.b[0]) * s - 0.5, (t.b[3] - t.b[1]) * s - 0.5)
      }
      for (const f of index.failed) {
        const t = index.tiles.find((tt) => tt.id === f.id)
        if (t) {
          g.fillStyle = '#d63b3b'
          g.fillRect(X(t.b[0]), Y(t.b[3]), (t.b[2] - t.b[0]) * s, (t.b[3] - t.b[1]) * s)
        }
      }
      g.strokeStyle = '#f87171'
      g.lineWidth = 1.2
      g.beginPath()
      index.boundary_local.forEach(([x, y], i) => (i ? g.lineTo(X(x), Y(y)) : g.moveTo(X(x), Y(y))))
      g.stroke()
      const cam = (window as unknown as { __skvyraCamera?: { x: number; y: number; dir: number } }).__skvyraCamera
      if (cam) {
        g.fillStyle = '#fde047'
        g.beginPath()
        g.arc(X(cam.x), Y(cam.y), 3.5, 0, Math.PI * 2)
        g.fill()
        g.strokeStyle = '#fde047'
        g.beginPath()
        g.moveTo(X(cam.x), Y(cam.y))
        g.lineTo(X(cam.x) + Math.sin(cam.dir) * 14, Y(cam.y) - Math.cos(cam.dir) * 14)
        g.stroke()
      }
    }
    draw()
    return () => cancelAnimationFrame(raf)
  }, [show, manager, index, geom])
  if (!show) return null
  return (
    <div className={`${panel} hidden p-2 sm:block`}>
      <canvas
        ref={ref}
        width={Math.ceil(geom.w * geom.s)}
        height={Math.ceil(geom.h * geom.s)}
        className="cursor-crosshair"
        data-testid="minimap"
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect()
          const x = geom.x0 + (e.clientX - r.left) / geom.s
          const y = geom.y0 + geom.h - (e.clientY - r.top) / geom.s
          useStore.getState().set({ flyTo: { x, y, dist: 500, t: Date.now() } })
        }}
      />
    </div>
  )
}

export function Banner({ index }: { index: TileIndex }) {
  const t = useT()
  const [dismissed, setDismissed] = useState(typeof window !== 'undefined' && window.innerWidth < 900)
  const failed = useStore((s) => s.stats.failed)
  return (
    <div className="space-y-2">
      {dismissed ? (
        <button className={`${panel} px-2 py-1 text-[11px] text-amber-100`} onClick={() => setDismissed(false)} title={t('accuracyNote')}>⚠</button>
      ) : (
        <div className={`${panel} flex max-w-sm items-start gap-2 p-2 text-[11px] text-amber-100`} data-testid="accuracy-note">
          <span>⚠</span>
          <span>{t('accuracyNote')}</span>
          <button onClick={() => setDismissed(true)} className="text-slate-400" aria-label={t('close')}>✕</button>
        </div>
      )}
      {failed > 0 && <div className={`${panel} p-2 text-[11px] text-red-300`}>⚠ {failed} {t('failedTiles')}</div>}
      {index.failed.length > 0 && <div className={`${panel} p-2 text-[11px] text-red-300`}>⚠ {index.failed.length} {t('missingTiles')}</div>}
    </div>
  )
}

export function Attribution({ index }: { index: TileIndex }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  return (
    <div className="pointer-events-auto text-right text-[10px] text-slate-200 [text-shadow:0_1px_2px_#000]">
      <button onClick={() => setOpen(!open)} className="underline decoration-dotted">
        © OpenStreetMap contributors (ODbL) · Microsoft Building Footprints (CDLA-Permissive-2.0) · Copernicus WorldDEM-30 · Copernicus Sentinel data 2026 · GHSL (CC BY 4.0) · Trees: Meta & WRI Canopy Height Maps v2 (CC BY 4.0) · ESA WorldCover 2021 (CC BY 4.0)
      </button>
      {open && (
        <div className={`${panel} mt-1 max-w-md p-3 text-left text-[11px]`}>
          <div className="mb-1 font-medium">{t('sourcesTitle')}</div>
          <p className="mb-2 text-slate-300">{t('aboutText')}</p>
          {Object.entries(index.sources).map(([k, v]) => (
            <div key={k} className="mb-1">
              <div className="text-slate-200">{v.source ?? k}</div>
              <div className="text-slate-400">{v.license}</div>
              <div className="text-slate-500">{v.source_timestamp ? `data: ${v.source_timestamp}` : ''} {v.retrieved_at ? `· retrieved ${v.retrieved_at.slice(0, 10)}` : ''}</div>
            </div>
          ))}
          <div className="text-slate-500">index {index.generated_at.slice(0, 16)} · code {index.code_version}</div>
        </div>
      )}
    </div>
  )
}
