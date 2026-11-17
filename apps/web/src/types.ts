import type { Frame } from './geo'

export interface TileEntry {
  id: string
  ix: number
  iy: number
  b: [number, number, number, number] // local x0, y0, x1, y1
  z: [number, number]
  n: { buildings: number; trees: number; issues: number }
  files: Record<string, { u: string; s: number }>
  hash: string
}

export interface TileIndex {
  version: number
  generated_at: string
  code_version: string
  frame: Frame
  tile_size: number
  lods: Record<string, { terrain_step_m: number; texture_px: number }>
  boundary_local: [number, number, number][] // x, y, terrain z (LOD0)
  tiles: TileEntry[]
  failed: { id: string; error: string }[]
  sources: Record<string, { source?: string; license?: string; source_timestamp?: string; retrieved_at?: string; url?: string }>
  counts: Record<string, number>
  context?: { u: string; s: number; b: [number, number, number, number] } | null // satellite-coloured surroundings
}

export interface BuildingMeta {
  id: string
  sid: string
  src: 'osm' | 'ms_ml' | 'local'
  local?: { confidence: string; method: string; date: string; author?: string; note?: string; evidence?: { url: string; license?: string; author?: string }[] }
  conf: 'verified' | 'source' | 'inferred'
  kind: string
  kindM: string
  type?: string
  h: number
  eave: number
  hM: 'override' | 'source_height' | 'photo_estimate' | 'source_levels' | 'ghsl_dominant' | 'ghsl_neighbourhood' | 'ghsl_small_cell' | 'type_default'
  hC?: 'high' | 'medium' | 'low' | 'lowest'
  roofMat?: string
  facade?: string
  parts?: number
  inb?: boolean
  poi?: string[]
  ghsl?: { anbh?: number; share?: number }
  landmark?: { name: string; wikidata?: string; evidence?: string; photos?: string[]; license?: string; match_distance_m?: number; match?: string; identity_audit?: string; heritage?: { status: 'registered_monument' | 'newly_identified' | 'not_registered' | 'unknown'; reported_status?: string; checked?: string } }
  lv?: number
  roof: string
  roofM: 'override' | 'source_tag' | 'photo_estimate' | 'inferred'
  name?: string
  addr?: string
  ll: [number, number]
  area: number
  ts?: string
  flags?: string[]
  note?: string
  ml_conf?: number
}

export interface Issue {
  sid: string
  reason: string
  x: number
  y: number
  z: number
  name?: string
}

export interface TileMeta {
  id: string
  hash: string
  bounds: [number, number, number, number]
  buildings: BuildingMeta[]
  trees: [number, number, number, number, number, number, number, number?, number?][] // x y z h r kind src (0 OSM, 1 land-cover rule, 2 decorative, 3 canopy model) conf (0 rule, 1 low, 2 medium) dateIdx
  tree_dates?: string[] // imagery dates of canopy-model trees
  issues: Issue[]
}

export type Lod = 0 | 1 | 2
export type Quality = 'low' | 'medium' | 'high'
export type ColorMode = 'realistic' | 'geometry' | 'height'
