import { describe, expect, it } from 'vitest'
import type { Lod, TileEntry } from '../types'
import { chooseEvictions, selectTiles, tileDistance } from './select'

const tile = (ix: number, iy: number): TileEntry => ({
  id: `t${ix}${iy}`, ix, iy, b: [ix * 500, iy * 500, ix * 500 + 500, iy * 500 + 500], z: [190, 210],
  n: { buildings: 1, trees: 0, issues: 0 }, hash: 'h',
  files: { '0': { u: 'a', s: 1 }, '1': { u: 'b', s: 1 }, '2': { u: 'c', s: 1 } },
})
const grid = Array.from({ length: 21 }, (_, i) => Array.from({ length: 21 }, (_, j) => tile(i - 10, j - 10))).flat()
const all = () => true

describe('tile selection', () => {
  it('distance is zero inside a tile at terrain height', () => {
    expect(tileDistance(tile(0, 0), 250, 250, 200)).toBe(0)
  })
  it('chooses finer LOD near the camera and coarser far away', () => {
    const w = selectTiles(grid, { x: 250, y: 250, z: 400 }, all, 'medium', new Map())
    const byId = new Map(w.map((x) => [x.tile.id, x]))
    expect(byId.get('t00')!.lod).toBe(0)
    expect(byId.get('t40')!.lod).toBe(1)
    expect(byId.get('t90')!.lod).toBe(2)
  })
  it('never selects tiles beyond the view distance', () => {
    const w = selectTiles(grid, { x: -20000, y: 0, z: 300 }, all, 'medium', new Map())
    expect(w).toHaveLength(0)
  })
  it('hidden tiles fall back to LOD2 and load after visible ones', () => {
    const w = selectTiles(grid, { x: 250, y: 250, z: 400 }, (t) => t.ix >= 0, 'medium', new Map())
    expect(w.find((x) => x.tile.id === 't-10')!.lod).toBe(2)
    const firstHidden = w.findIndex((x) => !x.visible)
    expect(w.slice(firstHidden).every((x) => !x.visible)).toBe(true)
  })
  it('applies hysteresis to avoid LOD flicker', () => {
    const t = [tile(0, 0)]
    // 680 m from the tile: beyond the 650 m LOD0 limit
    const cam = { x: 500 + 680, y: 250, z: 200 }
    expect(selectTiles(t, cam, all, 'medium', new Map())[0].lod).toBe(1)
    expect(selectTiles(t, cam, all, 'medium', new Map<string, Lod>([['t00', 0]]))[0].lod).toBe(0)
  })
})

describe('eviction', () => {
  it('evicts least recently used first and respects keep', () => {
    const e = [
      { key: 'a', bytes: 100, lastUsed: 1 },
      { key: 'b', bytes: 100, lastUsed: 2 },
      { key: 'c', bytes: 100, lastUsed: 3 },
    ]
    expect(chooseEvictions(e, 150, new Set())).toEqual(['a', 'b'])
    expect(chooseEvictions(e, 150, new Set(['a']))).toEqual(['b', 'c'])
    expect(chooseEvictions(e, 1000, new Set())).toEqual([])
  })
})
