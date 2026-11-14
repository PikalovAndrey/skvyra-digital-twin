import { create } from 'zustand'
import type { BuildingMeta, ColorMode, Issue, Quality, TileIndex } from './types'
import type { Lang } from './i18n'

export interface Stats {
  fps: number
  frameMs: number
  calls: number
  triangles: number
  activeTiles: number
  loading: number
  queued: number
  failed: number
  cachedMB: number
  heapMB: number | null
  transferredMB: number
  lodCounts: [number, number, number]
}

export interface Pointer {
  lon: number
  lat: number
  elev: number
  street: string | null
}

export type Selection =
  | { type: 'building'; meta: BuildingMeta; tile: string }
  | { type: 'issue'; issue: Issue; tile: string }
  | null

interface State {
  lang: Lang
  quality: Quality
  colorMode: ColorMode
  cameraMode: 'orbit' | 'fly'
  flySpeed: number
  layers: { buildings: boolean; roads: boolean; water: boolean; trees: boolean; decorativeTrees: boolean; terrain: boolean; issues: boolean; boundary: boolean }
  index: TileIndex | null
  fatal: string | null
  stats: Stats
  pointer: Pointer | null
  selection: Selection
  showStats: boolean
  minimap: boolean
  flyTo: { x: number; y: number; dist?: number; pitch?: number; t: number } | null // pitch: degrees below horizon
  resetView: number
  view: { camera: [number, number, number]; target: [number, number, number]; t: number } | null // exact local-frame camera (comparison renders)
  set: (p: Partial<State>) => void
  toggleLayer: (k: keyof State['layers']) => void
}

export const useStore = create<State>((set) => ({
  lang: 'uk',
  quality: 'medium',
  colorMode: 'realistic',
  cameraMode: 'orbit',
  flySpeed: 40,
  layers: { buildings: true, roads: true, water: true, trees: true, decorativeTrees: true, terrain: true, issues: true, boundary: true },
  index: null,
  fatal: null,
  stats: { fps: 0, frameMs: 0, calls: 0, triangles: 0, activeTiles: 0, loading: 0, queued: 0, failed: 0, cachedMB: 0, heapMB: null, transferredMB: 0, lodCounts: [0, 0, 0] },
  pointer: null,
  selection: null,
  showStats: false,
  minimap: true,
  flyTo: null,
  resetView: 0,
  view: null,
  set: (p) => set(p),
  toggleLayer: (k) => set((s) => ({ layers: { ...s.layers, [k]: !s.layers[k] } })),
}))
