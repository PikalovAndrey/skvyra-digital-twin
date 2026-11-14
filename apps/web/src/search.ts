// Search over streets, landmarks and addresses with Ukrainian <-> Latin spellings.
// Names and queries are both folded to a loose Latin key (KMU 2010 transliteration, then
// y/i, kh/h, ts/c ... merged), so "Skvira", "Skvyra" and "Сквира" all match each other.

export interface SearchItem {
  name: string
  name_en?: string
  kind: string // 'street' | 'building' | 'amenity=school' ...
  x: number
  y: number
  bb?: [number, number, number, number]
  id?: string
  parts?: number[][] // street polylines, flat local x,y pairs, for hover lookup
}

const MAP: Record<string, string> = {
  а: 'a', б: 'b', в: 'v', г: 'h', ґ: 'g', д: 'd', е: 'e', є: 'ie', ж: 'zh', з: 'z', и: 'y', і: 'i', ї: 'i',
  й: 'i', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f', х: 'kh',
  ц: 'ts', ч: 'ch', ш: 'sh', щ: 'shch', ь: '', ю: 'iu', я: 'ia', ъ: '', ы: 'y', э: 'e', ё: 'io', "'": '', '’': '', 'ʼ': '',
}

export function translit(s: string): string {
  let out = ''
  for (const ch of s.toLowerCase()) out += MAP[ch] ?? ch
  return out
}

/** Loose phonetic key: tolerant to the usual transliteration variants. */
export function foldKey(s: string): string {
  return translit(s)
    .replace(/\b(vulytsia|vul|street|st|provulok|prov|lane|ploshcha|square)\b\.?/g, ' ')
    .replace(/shch/g, 'sch')
    .replace(/kh/g, 'h')
    .replace(/zh/g, 'z')
    .replace(/ts/g, 'c')
    .replace(/ch/g, 'c')
    .replace(/sh/g, 's')
    .replace(/[yij]+/g, 'i')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

export interface Indexed extends SearchItem {
  key: string
}

export function buildIndex(items: SearchItem[]): Indexed[] {
  return items.map((it) => ({ ...it, key: foldKey(`${it.name} ${it.name_en ?? ''}`) }))
}

export function search(index: Indexed[], query: string, limit = 12): Indexed[] {
  const q = foldKey(query)
  if (!q) return []
  const words = q.split(' ')
  const scored: [number, Indexed][] = []
  for (const it of index) {
    if (!words.every((w) => it.key.includes(w))) continue
    let score = it.key.startsWith(q) ? 0 : it.key.split(' ').some((w) => w.startsWith(words[0])) ? 1 : 2
    if (it.kind !== 'street') score += 0.5
    scored.push([score + it.key.length / 1000, it])
  }
  return scored.sort((a, b) => a[0] - b[0]).slice(0, limit).map((s) => s[1])
}

/** Nearest street (polyline) to a local point within maxDist metres. */
export function nearestStreet(items: SearchItem[], x: number, y: number, maxDist = 25): SearchItem | null {
  let best: SearchItem | null = null
  let bestD = maxDist
  for (const it of items) {
    if (!it.parts || !it.bb) continue
    if (x < it.bb[0] - maxDist || x > it.bb[2] + maxDist || y < it.bb[1] - maxDist || y > it.bb[3] + maxDist) continue
    for (const p of it.parts) {
      for (let i = 0; i + 3 < p.length; i += 2) {
        const d = segDist(x, y, p[i], p[i + 1], p[i + 2], p[i + 3])
        if (d < bestD) {
          bestD = d
          best = it
        }
      }
    }
  }
  return best
}

function segDist(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax
  const dy = by - ay
  const l2 = dx * dx + dy * dy
  const t = l2 > 0 ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / l2)) : 0
  return Math.hypot(px - ax - t * dx, py - ay - t * dy)
}
