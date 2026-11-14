// Mapping review workbench (review.html): priority places where outlines are missing or unconfirmed.
// Shows source outlines, streets, reference points and missing-outline lines; lets a reviewer draw an outline,
// reject a candidate match and export a change set. Applying happens only through the CLI
// (`skvyra review apply changes.json` = validation + diff, `--yes` = write with backup; `skvyra review revert`).
import { useEffect, useMemo, useState } from 'react'
import { localToGeo, type Frame } from '../geo'
import { GAP_COLORS } from '../scene/materials'

type XY = [number, number]
interface Outline { sid: string; src: string; area: number; rings: XY[][]; confidence?: string }
interface Item {
  id: string; title: string; kind: 'landmark' | 'gap'; priority: number; wikidata?: string; address?: string
  points: { kind: string; xy: XY; note: string }[]; candidates: string[]; photos: string[]; photo_note?: string
  why_unconfirmed: string; needed: string; centre: XY; outlines: Outline[]; streets: { name: string | null; line: XY[] }[]
  gaps: { cls: string; conf: string; line: XY[] }[]
}
interface Doc { frame: Frame; items: Item[] }
type Change =
  | { op: 'add_outline'; feature: { type: 'Feature'; geometry: { type: 'Polygon'; coordinates: number[][][] }; properties: Record<string, unknown> } }
  | { op: 'reject_match'; wikidata: string; source_id: string; note: string }

const SRC_STYLE: Record<string, { stroke: string; fill: string; dash?: string }> = {
  osm: { stroke: '#2f6fd0', fill: '#3a7bd533' },
  ms_ml: { stroke: '#d97a1f', fill: '#e8913a33' },
  local: { stroke: '#1f9d55', fill: '#2e9e5b44' },
  local_approximate: { stroke: '#1f9d55', fill: 'none', dash: '3 2' },
}

// --- geometry checks (the CLI repeats them with GEOS before anything is written) ---------------------
const cross = (a: XY, b: XY, c: XY) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
function segX(a: XY, b: XY, c: XY, d: XY) {
  const d1 = cross(c, d, a), d2 = cross(c, d, b), d3 = cross(a, b, c), d4 = cross(a, b, d)
  return d1 * d2 < 0 && d3 * d4 < 0
}
export function selfIntersects(p: XY[]): boolean {
  const n = p.length
  for (let i = 0; i < n; i++) for (let j = i + 2; j < n; j++) {
    if (i === 0 && j === n - 1) continue
    if (segX(p[i], p[(i + 1) % n], p[j], p[(j + 1) % n])) return true
  }
  return false
}
export const area = (p: XY[]) => Math.abs(p.reduce((s, a, i) => s + a[0] * p[(i + 1) % p.length][1] - p[(i + 1) % p.length][0] * a[1], 0) / 2)
function inside(pt: XY, ring: XY[]) {
  let c = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j]
    if (yi > pt[1] !== yj > pt[1] && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) c = !c
  }
  return c
}
function overlapping(p: XY[], outlines: Outline[]) {
  return outlines.filter((o) => {
    const r = o.rings[0]
    return p.some((v) => inside(v, r)) || r.some((v) => inside(v, p)) ||
      p.some((a, i) => r.some((c, j) => segX(a, p[(i + 1) % p.length], c, r[(j + 1) % r.length])))
  }).map((o) => o.sid)
}

export default function ReviewApp() {
  const [doc, setDoc] = useState<Doc | null>(null)
  const [sel, setSel] = useState(0)
  const [draft, setDraft] = useState<XY[]>([])
  const [drawing, setDrawing] = useState(false)
  const [changes, setChanges] = useState<Change[]>([])
  const [form, setForm] = useState({ confidence: 'low', method: '', url: '', license: '', author: '', note: '', replaces: '' })
  const [zoom, setZoom] = useState(1)
  useEffect(() => {
    fetch('./review.json').then((r) => r.json()).then(setDoc).catch(() => setDoc(null))
  }, [])
  const it = doc?.items[sel]
  const R = 130 / zoom
  const view = it ? `${it.centre[0] - R} ${-it.centre[1] - R} ${2 * R} ${2 * R}` : '0 0 1 1'
  const problems = useMemo(() => {
    if (!it || draft.length < 3) return []
    const out: string[] = []
    if (selfIntersects(draft)) out.push('самоперетин контуру')
    const a = area(draft)
    if (a < 6 || a > 20000) out.push(`неправдоподібна площа ${a.toFixed(1)} м²`)
    const repl = form.replaces.split(',').map((s) => s.trim()).filter(Boolean)
    const ov = overlapping(draft, it.outlines).filter((s) => !repl.includes(s))
    if (ov.length) out.push(`перекриває наявні контури: ${ov.join(', ')} (вкажіть їх у «замінює» або виправте контур)`)
    if (!form.method) out.push('вкажіть метод отримання контуру')
    return out
  }, [draft, it, form])
  if (!doc || !it) return <div className="p-6 text-slate-300">Завантаження review.json… (створюється командою <code>make build</code>)</div>

  const toGeo = (p: XY) => localToGeo(doc.frame, p[0], p[1]).map((v) => +v.toFixed(7))
  const addOutline = () => {
    const ring = [...draft, draft[0]].map(toGeo)
    const repl = form.replaces.split(',').map((s) => s.trim()).filter(Boolean)
    setChanges([...changes, { op: 'add_outline', feature: { type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] }, properties: {
      id: `local:${it.id}`, status: 'approximate', confidence: form.confidence, method: form.method, date: new Date().toISOString().slice(0, 10),
      author: form.author || undefined, note: form.note || undefined, wikidata: it.wikidata,
      evidence: form.url ? [{ url: form.url, license: form.license, author: form.author }] : [],
      replaces: repl.length ? repl.map((s) => ({ source_id: s })) : undefined } } }])
    setDraft([]); setDrawing(false)
  }
  const download = () => {
    const blob = new Blob([JSON.stringify({ version: 1, changes }, null, 1)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob); a.download = 'skvyra-review-changes.json'; a.click()
  }
  const svgPt = (e: React.MouseEvent<SVGSVGElement>): XY => {
    const s = e.currentTarget, pt = s.createSVGPoint()
    pt.x = e.clientX; pt.y = e.clientY
    const p = pt.matrixTransform(s.getScreenCTM()!.inverse())
    return [+p.x.toFixed(2), +(-p.y).toFixed(2)]
  }
  const poly = (r: XY[]) => r.map(([x, y]) => `${x},${-y}`).join(' ')
  const field = 'w-full rounded bg-slate-800 px-2 py-1 text-xs'
  return (
    <div className="flex h-screen bg-slate-950 text-slate-200" data-testid="review">
      <aside className="w-[25rem] shrink-0 overflow-y-auto border-r border-slate-800 p-3 text-xs">
        <h1 className="mb-1 text-sm font-semibold">Перевірка контурів — центр Сквири</h1>
        <p className="mb-2 text-slate-400">Нічого не записується звідси: набір змін застосовує <code>skvyra review apply</code> (перевірка й попередній перегляд), відкат — <code>skvyra review revert</code>. Не створюйте контур за однією точкою чи 10-м land cover.</p>
        {doc.items.map((x, i) => (
          <button key={x.id} onClick={() => { setSel(i); setDraft([]); setDrawing(false) }} data-testid={`item-${x.id}`}
            className={`mb-1 block w-full rounded px-2 py-1 text-left ${i === sel ? 'bg-sky-900' : 'bg-slate-900 hover:bg-slate-800'}`}>
            {x.priority}. {x.title}
          </button>
        ))}
        <div className="mt-3 space-y-1.5" data-testid="item-detail">
          {it.address && <div><b>Адреса:</b> {it.address}</div>}
          <div><b>Чому не підтверджено:</b> {it.why_unconfirmed}</div>
          <div><b>Що потрібно:</b> {it.needed}</div>
          {it.photo_note && <div><b>Фото:</b> {it.photo_note}</div>}
          {it.photos.map((u) => <a key={u} className="block truncate text-sky-300 underline" href={u} target="_blank" rel="noreferrer">{decodeURI(u)}</a>)}
          {it.candidates.map((c) => (
            <div key={c} className="flex items-center gap-2"><span>Кандидат <code>{c}</code></span>
              {it.wikidata && <button className="rounded bg-rose-900 px-2" onClick={() => setChanges([...changes, { op: 'reject_match', wikidata: it.wikidata!, source_id: c, note: `rejected in review: ${it.why_unconfirmed.slice(0, 120)}` }])}>Відхилити зіставлення</button>}
            </div>
          ))}
        </div>
        <div className="mt-3 rounded border border-slate-800 p-2">
          <div className="mb-1 font-semibold">Локальний контур</div>
          {!drawing ? <button className="rounded bg-slate-700 px-2 py-1" onClick={() => { setDrawing(true); setDraft([]) }}>Креслити (клік — вершина)</button>
            : <span className="text-amber-300">Клікайте вершини на карті ({draft.length})</span>}
          {draft.length > 0 && <button className="ml-2 rounded bg-slate-700 px-2 py-1" onClick={() => setDraft(draft.slice(0, -1))}>↶ вершина</button>}
          <div className="mt-2 grid grid-cols-2 gap-1">
            <div className={`${field} text-slate-400`} title="Підтвердження - окрема зміна approve_outline: хто перевірив, на якій підставі (обміри, ліцензовані знімки, офіційний документ, відкритий датасет) і що звірено">приблизний; підтвердження - окремо (approve_outline)</div>
            <select className={field} value={form.confidence} onChange={(e) => setForm({ ...form, confidence: e.target.value })}>
              <option value="low">впевненість: низька</option><option value="medium">середня</option><option value="high">висока</option>
            </select>
            <input className={`${field} col-span-2`} placeholder="метод (напр. GNSS-обхід, OSM-обведення ліцензованого орто)" value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value })} />
            <input className={`${field} col-span-2`} placeholder="посилання на легальне джерело / фото" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} />
            <input className={field} placeholder="ліцензія" value={form.license} onChange={(e) => setForm({ ...form, license: e.target.value })} />
            <input className={field} placeholder="автор" value={form.author} onChange={(e) => setForm({ ...form, author: e.target.value })} />
            <input className={`${field} col-span-2`} placeholder="замінює (source_id через кому, лише явно)" value={form.replaces} onChange={(e) => setForm({ ...form, replaces: e.target.value })} />
            <input className={`${field} col-span-2`} placeholder="примітка" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
          </div>
          {draft.length >= 3 && (problems.length
            ? <ul className="mt-1 list-disc pl-4 text-rose-300" data-testid="draft-problems">{problems.map((p) => <li key={p}>{p}</li>)}</ul>
            : <button className="mt-2 rounded bg-emerald-800 px-2 py-1" onClick={addOutline} data-testid="add-outline">Додати до набору змін</button>)}
        </div>
        <div className="mt-3 rounded border border-slate-800 p-2" data-testid="changes">
          <div className="mb-1 font-semibold">Набір змін ({changes.length}) — попередній перегляд</div>
          {changes.map((c, i) => (
            <div key={i} className="flex gap-2">
              <span className="flex-1">{c.op === 'add_outline' ? `+ контур ${c.feature.properties.id} (${c.feature.properties.status}, ${c.feature.properties.confidence})` : `× відхилити ${c.wikidata} → ${c.source_id}`}</span>
              <button className="text-slate-400" onClick={() => setChanges(changes.filter((_, j) => j !== i))}>скасувати</button>
            </div>
          ))}
          <button disabled={!changes.length} className="mt-2 rounded bg-sky-800 px-2 py-1 disabled:opacity-40" onClick={download}>Завантажити набір змін (JSON)</button>
        </div>
      </aside>
      <main className="relative flex-1">
        <svg viewBox={view} className="h-full w-full bg-slate-900" data-testid="review-map"
          onWheel={(e) => setZoom((z) => Math.min(8, Math.max(0.5, z * (e.deltaY < 0 ? 1.15 : 1 / 1.15))))}
          onClick={(e) => drawing && setDraft([...draft, svgPt(e)])}>
          {it.streets.map((s, i) => <polyline key={i} points={poly(s.line)} fill="none" stroke="#64748b" strokeWidth={5 / zoom} strokeLinecap="round" />)}
          {it.streets.map((s, i) => s.name && <text key={`t${i}`} x={s.line[Math.floor(s.line.length / 2)][0]} y={-s.line[Math.floor(s.line.length / 2)][1]} fontSize={5 / zoom} fill="#cbd5e1">{s.name}</text>)}
          {it.gaps.map((g, i) => <polyline key={`g${i}`} points={poly(g.line)} fill="none" strokeWidth={2 / zoom}
            stroke={g.cls === 'suspect_gap' ? (g.conf === 'medium' ? GAP_COLORS.suspect_gap_medium : GAP_COLORS.suspect_gap_low) : g.cls === 'insufficient' ? GAP_COLORS.insufficient : GAP_COLORS.no_signs} />)}
          {it.outlines.map((o) => {
            const st = SRC_STYLE[o.src] ?? SRC_STYLE.osm
            const cand = it.candidates.includes(o.sid)
            return <polygon key={o.sid} points={poly(o.rings[0])} fill={st.fill} stroke={cand ? '#f43f5e' : st.stroke} strokeWidth={(cand ? 1.6 : 0.6) / zoom} strokeDasharray={st.dash}>
              <title>{`${o.sid} · ${o.src} · ${o.area} м²`}</title></polygon>
          })}
          {it.points.map((p, i) => p.kind === 'camera'
            ? <polygon key={`p${i}`} points={`${p.xy[0]},${-p.xy[1] - 2.5} ${p.xy[0] - 2},${-p.xy[1] + 1.5} ${p.xy[0] + 2},${-p.xy[1] + 1.5}`} fill="#22c55e"><title>{p.note}</title></polygon>
            : <circle key={`p${i}`} cx={p.xy[0]} cy={-p.xy[1]} r={2.2} fill="#fde047" stroke="#000" strokeWidth={0.4}><title>{p.note}</title></circle>)}
          {draft.length > 0 && <polygon points={poly(draft)} fill="#fbbf2433" stroke="#fbbf24" strokeWidth={0.8 / zoom} />}
          <g transform={`translate(${it.centre[0] - R + 8 / zoom},${-it.centre[1] + R - 8 / zoom})`}>
            <line x1={0} y1={0} x2={20} y2={0} stroke="#e2e8f0" strokeWidth={1 / zoom} /><text y={-2 / zoom} fontSize={5 / zoom} fill="#e2e8f0">20 м · пн ↑</text>
          </g>
        </svg>
        <div className="absolute bottom-2 right-2 rounded bg-slate-900/90 p-2 text-[11px]">
          <div><span style={{ color: SRC_STYLE.osm.stroke }}>■</span> OSM · <span style={{ color: SRC_STYLE.ms_ml.stroke }}>■</span> Microsoft ML · <span style={{ color: SRC_STYLE.local.stroke }}>■</span> локальний (пунктир — приблизний) · <span className="text-rose-400">□</span> кандидат</div>
          <div><span className="text-yellow-300">●</span> точка WLM/Wikidata · <span className="text-green-400">▲</span> геотег камери · лінії — ймовірні прогалини</div>
        </div>
      </main>
    </div>
  )
}
