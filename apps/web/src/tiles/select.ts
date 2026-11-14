// Pure tile/LOD selection: given camera position, frustum test and quality, decide which LOD each
// tile should show. Kept free of three.js state so it can be unit-tested.
import type { Lod, Quality, TileEntry } from '../types'

export const LOD_DIST: Record<Quality, [number, number, number]> = {
  // max distance (m) for LOD0, LOD1, and for showing the tile at all (LOD2)
  low: [350, 1100, 7000],
  medium: [650, 1800, 9000],
  high: [1000, 2800, 12000],
}

/** Distance from point (local x, y, z) to the tile's 3D bounding box. */
export function tileDistance(t: TileEntry, x: number, y: number, z: number): number {
  const dx = Math.max(t.b[0] - x, 0, x - t.b[2])
  const dy = Math.max(t.b[1] - y, 0, y - t.b[3])
  const dz = Math.max(t.z[0] - z, 0, z - t.z[1] - 30) // +30 m for buildings above terrain max
  return Math.hypot(dx, dy, dz)
}

export interface Want {
  tile: TileEntry
  lod: Lod
  dist: number
  visible: boolean
}

/**
 * @param current  LOD currently displayed per tile id (for hysteresis: avoid flicker at thresholds)
 * Tiles outside the frustum still get LOD2 if close, so turning the camera never shows holes nearby.
 */
export function selectTiles(
  tiles: TileEntry[],
  cam: { x: number; y: number; z: number },
  inFrustum: (t: TileEntry) => boolean,
  quality: Quality,
  current: Map<string, Lod>,
): Want[] {
  const [d0, d1, d2] = LOD_DIST[quality]
  const out: Want[] = []
  for (const t of tiles) {
    const dist = tileDistance(t, cam.x, cam.y, cam.z)
    if (dist > d2) continue
    const visible = inFrustum(t)
    const cur = current.get(t.id)
    const h = (lim: number, lod: Lod) => (cur !== undefined && cur <= lod ? lim * 1.15 : lim) // keep finer LOD a bit longer
    let lod: Lod = dist < h(d0, 0) ? 0 : dist < h(d1, 1) ? 1 : 2
    if (!visible) lod = 2 // off-screen tiles: cheapest representation only
    if (!t.files[String(lod)]) {
      // graceful fallback: a missing LOD file uses the nearest coarser (else finer) one that exists
      const avail = Object.keys(t.files).map(Number).sort()
      if (!avail.length) continue
      lod = (avail.find((l) => l >= lod) ?? avail[avail.length - 1]) as Lod
    }
    out.push({ tile: t, lod, dist, visible })
  }
  // load order: visible before hidden, then nearest first
  return out.sort((a, b) => Number(b.visible) - Number(a.visible) || a.dist - b.dist)
}

/** Least-recently-used eviction under a byte budget; never evicts entries in `keep`. */
export function chooseEvictions(
  entries: { key: string; bytes: number; lastUsed: number }[],
  budget: number,
  keep: Set<string>,
): string[] {
  let total = entries.reduce((s, e) => s + e.bytes, 0)
  const out: string[] = []
  for (const e of [...entries].sort((a, b) => a.lastUsed - b.lastUsed)) {
    if (total <= budget) break
    if (keep.has(e.key)) continue
    out.push(e.key)
    total -= e.bytes
  }
  return out
}
