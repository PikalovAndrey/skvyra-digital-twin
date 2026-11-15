// Camera-driven tile streaming. Lives outside React: tiles are added to / removed from `root` directly,
// so streaming never triggers React re-renders. Responsibilities:
//  * choose tiles + LOD per update (select.ts), keep the previous LOD visible until the new one arrives
//  * fetch with AbortController; abort requests that are no longer wanted; no duplicate in-flight loads
//  * LRU eviction under a byte budget with full GPU resource disposal
//  * retry failed tiles with exponential backoff; report failures instead of hiding them
//  * per-tile tree instancing, issue markers, data-quality recolouring and selection highlight
import { maybeGunzip } from './gz'
import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { mergeGeometries, mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js'
import type { BuildingMeta, ColorMode, Lod, Quality, TileEntry, TileIndex, TileMeta } from '../types'
import { MAT, QUALITY_COLORS, linear, terrainMaterial } from '../scene/materials'
import { chooseEvictions, selectTiles } from './select'

const BUDGET_MB: Record<Quality, number> = { low: 220, medium: 480, high: 900 }
const MAX_INFLIGHT = 6
// Upgrades to a finer LOD per update: adding several fresh LOD0 tiles in one frame uploads tens of MB of
// textures/geometry at once (measured 120-310 ms hitches after camera jumps); nearest tiles go first.
const MAX_UPGRADES_PER_UPDATE = 2
const BUILDING_PARTS = new Set(['walls', 'roofs', 'details'])

export type LayerKey = 'buildings' | 'roads' | 'water' | 'trees' | 'decorativeTrees' | 'terrain' | 'issues'

interface Loaded {
  key: string
  tile: TileEntry
  lod: Lod
  group: THREE.Group
  bytes: number
  lastUsed: number
  buildingMeshes: THREE.Mesh[]
  terrainMesh: THREE.Mesh | null
  baseColors: Map<THREE.Mesh, Float32Array | Uint16Array | Uint8Array>
}

interface TileExtras {
  meta: TileMeta
  trees: THREE.Group | null
  issues: THREE.Group | null
}

export interface StreamStats {
  active: number
  loading: number
  queued: number
  failed: number
  cachedMB: number
  transferredMB: number
  lodCounts: [number, number, number]
  missing: string[]
}

export class TileManager {
  readonly root = new THREE.Group()
  private loaded = new Map<string, Loaded>()
  private inflight = new Map<string, AbortController>()
  private failures = new Map<string, { count: number; retryAt: number; error: string }>()
  readonly shown = new Map<string, Lod>() // tile id -> displayed LOD (read by the minimap)
  private extras = new Map<string, TileExtras>()
  private metaInflight = new Map<string, Promise<TileMeta | null>>()
  private loader = new GLTFLoader()
  private frustum = new THREE.Frustum()
  private projScreen = new THREE.Matrix4()
  private box = new THREE.Box3()
  private transferred = 0
  private queued = 0 // wanted tiles whose desired LOD is neither loaded nor in flight
  readonly loadMs: number[] = [] // fetch + parse + prepare per tile (benchmarks)
  readonly parseMs: number[] = [] // main-thread part: glTF parse + material setup
  readonly treeMs: number[] = [] // main-thread tree instancing per tile (benchmarks)
  /** per-stage timings of each completed tile load (profiling: fetch, GLB decode, prepare, texture upload) */
  readonly stageMs: { fetch: number; decode: number; prepare: number; texture: number; bytes: number }[] = []
  /** per update(): wall time and number of tiles whose displayed LOD changed (activations) */
  readonly updateMs: { ms: number; activated: number; t: number }[] = []
  private clock = 0
  private colorMode: ColorMode = 'realistic'
  private farTrees = new Set<string>()
  private tileById: Map<string, TileEntry>
  private selected: { tile: string; fid: number } | null = null
  private layers: Record<LayerKey, boolean> = { buildings: true, roads: true, water: true, trees: true, decorativeTrees: true, terrain: true, issues: true }
  quality: Quality = 'medium'
  disposed = false
  renderer: THREE.WebGLRenderer | null = null // set by the scene: textures are uploaded as soon as a tile arrives

  constructor(readonly index: TileIndex, readonly baseUrl: string) {
    this.root.name = 'tiles'
    this.tileById = new Map(index.tiles.map((t) => [t.id, t]))
  }

  /** Call every few frames. Returns true if the scene graph changed. */
  update(camera: THREE.PerspectiveCamera): boolean {
    if (this.disposed) return false
    const t0 = performance.now()
    const before = this.activations
    const changed = this.updateInner(camera)
    this.updateMs.push({ ms: performance.now() - t0, activated: this.activations - before, t: t0 })
    if (this.updateMs.length > 4000) this.updateMs.splice(0, 2000)
    return changed
  }

  private activations = 0

  private updateInner(camera: THREE.PerspectiveCamera): boolean {
    this.clock++
    const cam = { x: camera.position.x, y: -camera.position.z, z: camera.position.y }
    this.projScreen.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse)
    this.frustum.setFromProjectionMatrix(this.projScreen)
    const inFrustum = (t: TileEntry) => {
      this.box.min.set(t.b[0], t.z[0] - 10, -t.b[3])
      this.box.max.set(t.b[2], t.z[1] + 60, -t.b[1])
      return this.frustum.intersectsBox(this.box)
    }
    const wants = selectTiles(this.index.tiles, cam, inFrustum, this.quality, this.shown)
    const wantedKeys = new Set(wants.map((w) => `${w.tile.id}:${w.lod}`))
    const wantedTiles = new Set(wants.map((w) => w.tile.id))
    let changed = false

    // 1. abort obsolete requests
    for (const [key, ctrl] of this.inflight) {
      if (!wantedKeys.has(key)) {
        ctrl.abort()
        this.inflight.delete(key)
      }
    }
    // 2. display: wanted LOD if loaded, else the closest loaded LOD as a placeholder
    const now = performance.now()
    let queued = 0
    let upgrades = 0
    for (const w of wants) {
      const id = w.tile.id
      let show: Loaded | undefined = this.loaded.get(`${id}:${w.lod}`)
      if (!show) {
        for (const l of [w.lod, 1, 2, 0] as Lod[]) {
          show = this.loaded.get(`${id}:${l}`)
          if (show) break
        }
      }
      if (show) {
        show.lastUsed = this.clock
        const cur = this.shown.get(id)
        if (cur !== show.lod) {
          const upgrade = cur !== undefined && show.lod < cur
          if (!upgrade || upgrades < MAX_UPGRADES_PER_UPDATE) {
            this.setShown(id, show.lod)
            changed = true
            if (upgrade) upgrades++
          } else queued++ // finer LOD is ready but waits for the next update
        }
      }
      if (!this.loaded.has(`${id}:${w.lod}`) && !this.inflight.has(`${id}:${w.lod}`)) {
        const f = this.failures.get(`${id}:${w.lod}`)
        const waiting = f && f.retryAt >= now
        if (!waiting && this.inflight.size < MAX_INFLIGHT) this.load(w.tile, w.lod)
        else if (!waiting) queued++
      }
    }
    this.queued = queued
    // tree detail by distance (hysteresis so a tile on the boundary does not flip every frame)
    for (const [id, lod] of this.shown) {
      if (lod !== 0) continue
      const t = this.tileById.get(id)
      if (!t) continue
      const dx = Math.max(t.b[0] - cam.x, 0, cam.x - t.b[2])
      const dy = Math.max(t.b[1] - cam.y, 0, cam.y - t.b[3])
      const d = Math.hypot(dx, dy, Math.max(0, cam.z - t.z[1]))
      const far = this.farTrees.has(id)
      if (!far && d > TREE_NEAR_M * 1.25) this.farTrees.add(id)
      else if (far && d < TREE_NEAR_M) this.farTrees.delete(id)
      else continue
      this.syncExtras(id)
    }
    for (const id of [...this.shown.keys()]) {
      if (!wantedTiles.has(id)) {
        this.setShown(id, null)
        changed = true
      }
    }
    // 3. evict least recently used beyond budget (displayed entries are never evicted)
    const keep = new Set([...this.shown].map(([id, lod]) => `${id}:${lod}`))
    const ev = chooseEvictions([...this.loaded.values()], BUDGET_MB[this.quality] * 1e6, keep)
    for (const key of ev) this.unload(key)
    return changed
  }

  private setShown(id: string, lod: Lod | null) {
    const prev = this.shown.get(id)
    if (prev !== undefined) {
      const l = this.loaded.get(`${id}:${prev}`)
      if (l) this.root.remove(l.group)
    }
    if (lod === null) {
      this.shown.delete(id)
    } else {
      const l = this.loaded.get(`${id}:${lod}`)!
      this.root.add(l.group)
      this.shown.set(id, lod)
      this.activations++
    }
    this.syncExtras(id)
  }

  private async load(tile: TileEntry, lod: Lod) {
    const key = `${tile.id}:${lod}`
    const ctrl = new AbortController()
    this.inflight.set(key, ctrl)
    const t0 = performance.now()
    try {
      const file = tile.files[String(lod)]
      const res = await fetch(`${this.baseUrl}tiles/${file.u}?v=${tile.hash}`, { signal: ctrl.signal })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const raw = await res.arrayBuffer()
      this.transferred += raw.byteLength
      const buf = await maybeGunzip(raw)
      const tp = performance.now()
      const gltf = await this.loader.parseAsync(buf, '')
      if (ctrl.signal.aborted || this.disposed) {
        disposeObject(gltf.scene)
        return
      }
      const td = performance.now()
      const entry = this.prepare(tile, lod, gltf.scene, buf.byteLength)
      const tq = performance.now()
      const map = (entry.terrainMesh?.material as THREE.MeshStandardMaterial | undefined)?.map
      if (map && this.renderer) this.renderer.initTexture(map) // upload now, spread over load completions
      const tt = performance.now()
      this.stageMs.push({ fetch: tp - t0, decode: td - tp, prepare: tq - td, texture: tt - tq, bytes: buf.byteLength })
      if (this.stageMs.length > 4000) this.stageMs.splice(0, 2000)
      this.parseMs.push(performance.now() - tp)
      this.loadMs.push(performance.now() - t0)
      this.loaded.set(key, entry)
      this.failures.delete(key)
      // building attributes / trees / issue markers: needed close up, for data colouring and for the few tiles with
      // issue markers; the overview (LOD2, realistic colours) renders without them (saves ~1 MB and 100+ requests)
      if (lod < 2 || this.colorMode !== 'realistic' || tile.n.issues > 0) void this.ensureMeta(tile)
    } catch (e) {
      if ((e as Error).name === 'AbortError') return
      const f = this.failures.get(key) ?? { count: 0, retryAt: 0, error: '' }
      f.count++
      f.retryAt = performance.now() + Math.min(60_000, 2000 * 2 ** f.count)
      f.error = String((e as Error).message ?? e)
      this.failures.set(key, f)
      console.warn(`tile ${key} failed (${f.count}x): ${f.error}`)
    } finally {
      if (this.inflight.get(key) === ctrl) this.inflight.delete(key)
    }
  }

  private prepare(tile: TileEntry, lod: Lod, scene: THREE.Object3D, bytes: number): Loaded {
    const group = new THREE.Group()
    group.name = `${tile.id}:lod${lod}`
    const entry: Loaded = { key: `${tile.id}:${lod}`, tile, lod, group, bytes: bytes * 1.4, lastUsed: this.clock, buildingMeshes: [], terrainMesh: null, baseColors: new Map() }
    const meshes: THREE.Mesh[] = []
    scene.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) meshes.push(o as THREE.Mesh)
    })
    for (const m of meshes) {
      const kind = m.name.replace(/_\d+$/, '').split('.')[0]
      const orig = m.material as THREE.MeshStandardMaterial
      if (kind === 'terrain') {
        m.material = terrainMaterial(orig.map)
        orig.map = null
        entry.terrainMesh = m
        m.receiveShadow = true
      } else if (kind === 'walls') {
        m.material = lod === 2 ? MAT.wallsPlain : MAT.walls
      } else if (kind in MAT) {
        m.material = MAT[kind as keyof typeof MAT]
      }
      orig.dispose()
      m.userData.kind = kind
      m.userData.tile = tile.id
      if (BUILDING_PARTS.has(kind)) {
        entry.buildingMeshes.push(m)
        m.castShadow = true
        m.receiveShadow = true
        const col = m.geometry.getAttribute('color') as THREE.BufferAttribute
        entry.baseColors.set(m, (col.array as Float32Array | Uint16Array | Uint8Array).slice())
      }
      m.matrixAutoUpdate = false
      m.updateMatrix()
      group.add(m)
    }
    group.matrixAutoUpdate = false
    this.applyLayerVisibility(group)
    this.recolorEntry(entry)
    return entry
  }

  private ensureMeta(tile: TileEntry): Promise<TileMeta | null> {
    if (this.extras.has(tile.id)) return Promise.resolve(this.extras.get(tile.id)!.meta)
    let p = this.metaInflight.get(tile.id)
    if (!p) {
      p = fetch(`${this.baseUrl}tiles/${tile.id}.json?v=${tile.hash}`)
        .then((r) => (r.ok ? (r.json() as Promise<TileMeta>) : null))
        .then((meta) => {
          if (meta && !this.disposed) {
            this.extras.set(tile.id, { meta, trees: null, issues: null })
            for (const l of this.loaded.values()) if (l.tile.id === tile.id) this.recolorEntry(l)
            this.syncExtras(tile.id)
          }
          return meta
        })
        .catch(() => null)
        .finally(() => this.metaInflight.delete(tile.id))
      this.metaInflight.set(tile.id, p)
    }
    return p
  }

  /** Trees for fine LODs, issue markers for any displayed LOD. */
  private syncExtras(id: string) {
    const ex = this.extras.get(id)
    if (!ex) return
    const lod = this.shown.get(id)
    const treeLimit: Lod = this.quality === 'high' ? 1 : 0
    const wantTrees = lod !== undefined && lod <= treeLimit
    const lite = lod === 1 || this.farTrees.has(id)
    if (ex.trees && wantTrees && ex.trees.userData.lite !== lite) {
      this.root.remove(ex.trees)
      disposeObject(ex.trees, true)
      ex.trees = null
    }
    if (wantTrees && !ex.trees) {
      const t0 = performance.now()
      ex.trees = buildTrees(ex.meta, lite, this.colorMode)
      this.treeMs.push(performance.now() - t0)
      if (ex.trees) ex.trees.userData.lite = lite
    }
    if (ex.trees) {
      if (wantTrees) this.root.add(ex.trees)
      else this.root.remove(ex.trees)
      ex.trees.visible = this.layers.trees
      for (const c of ex.trees.children) if (c.userData.decorative) c.visible = this.layers.decorativeTrees
    }
    if (!ex.issues && ex.meta.issues.length) ex.issues = buildIssues(ex.meta)
    if (ex.issues) {
      if (lod !== undefined) this.root.add(ex.issues)
      else this.root.remove(ex.issues)
      ex.issues.visible = this.layers.issues
    }
  }

  private unload(key: string) {
    const l = this.loaded.get(key)
    if (!l) return
    this.root.remove(l.group)
    disposeObject(l.group)
    this.loaded.delete(key)
    const id = l.tile.id
    if (![...this.loaded.keys()].some((k) => k.startsWith(`${id}:`))) {
      const ex = this.extras.get(id)
      if (ex) {
        for (const g of [ex.trees, ex.issues]) if (g) {
          this.root.remove(g)
          disposeObject(g, true)
        }
        this.extras.delete(id)
      }
    }
  }

  // ---------------------------------------------------------------------------------------------------
  // layers, colouring, picking
  // ---------------------------------------------------------------------------------------------------
  setLayers(layers: Record<LayerKey, boolean>) {
    this.layers = { ...layers }
    for (const l of this.loaded.values()) this.applyLayerVisibility(l.group)
    for (const id of this.extras.keys()) this.syncExtras(id)
  }

  private applyLayerVisibility(group: THREE.Group) {
    for (const c of group.children) {
      const k = c.userData.kind as string
      c.visible =
        BUILDING_PARTS.has(k) ? this.layers.buildings
          : k === 'decor_fence' ? this.layers.decorativeTrees // decorative yard layer (invented), switchable
          : k.startsWith('road') || k === 'bridge' || k === 'rail' ? this.layers.roads
            : k === 'water' ? this.layers.water
              : k === 'terrain' ? this.layers.terrain
                : true
    }
  }

  setQuality(q: Quality) {
    this.quality = q
    for (const id of this.extras.keys()) this.syncExtras(id)
  }

  setColorMode(mode: ColorMode) {
    this.colorMode = mode
    if (mode !== 'realistic') for (const id of this.shown.keys()) { const t = this.index.tiles.find((x) => x.id === id); if (t) void this.ensureMeta(t) }
    for (const l of this.loaded.values()) this.recolorEntry(l)
    for (const ex of this.extras.values()) if (ex.trees) recolorTrees(ex.trees, mode)
  }

  setSelection(sel: { tile: string; fid: number } | null) {
    const prev = this.selected
    this.selected = sel
    for (const l of this.loaded.values()) {
      if (l.tile.id === prev?.tile || l.tile.id === sel?.tile) this.recolorEntry(l)
    }
  }

  private recolorEntry(l: Loaded) {
    const meta = this.extras.get(l.tile.id)?.meta
    const palette = this.colorMode === 'realistic' || !meta ? null : paletteFor(meta.buildings, this.colorMode)
    const sel = this.selected?.tile === l.tile.id ? this.selected.fid : -1
    for (const m of l.buildingMeshes) {
      const col = m.geometry.getAttribute('color') as THREE.BufferAttribute
      const fid = m.geometry.getAttribute('_feature_id') as THREE.BufferAttribute | undefined
      const out = col.array as Float32Array | Uint16Array | Uint8Array
      const base = l.baseColors.get(m)!
      if (!palette && sel < 0) {
        out.set(base)
      } else {
        // colours may be stored normalized (16-bit, 4 components): scale and stride accordingly
        const n = col.itemSize
        const k = !col.normalized ? 1 : out instanceof Uint16Array ? 65535 : out instanceof Uint8Array ? 255 : 1
        for (let i = 0; i < col.count; i++) {
          const f = fid ? fid.getX(i) : -1
          const o = i * n
          if (f === sel) {
            out[o] = 1.0 * k; out[o + 1] = 0.75 * k; out[o + 2] = 0.05 * k
          } else if (palette && f >= 0 && palette[f]) {
            const p = palette[f]!
            out[o] = p[0] * k; out[o + 1] = p[1] * k; out[o + 2] = p[2] * k
          } else {
            out[o] = base[o]; out[o + 1] = base[o + 1]; out[o + 2] = base[o + 2]
          }
        }
      }
      col.needsUpdate = true
    }
  }

  /** Building / issue under a ray, if any. */
  /** Attributes of one building, loading its tile's metadata if it is not there yet (far tiles load it lazily). */
  async buildingMeta(tileId: string, fid: number): Promise<BuildingMeta | null> {
    const t = this.index.tiles.find((x) => x.id === tileId)
    const meta = t ? await this.ensureMeta(t) : null
    return meta?.buildings[fid] ?? null
  }

  /** Building / issue under a ray, if any (meta is null while the tile's metadata is still loading). */
  pick(raycaster: THREE.Raycaster): { type: 'building'; meta: BuildingMeta | null; tile: string; fid: number } | { type: 'issue'; tile: string; index: number } | null {
    const targets: THREE.Object3D[] = []
    for (const [id, lod] of this.shown) {
      const l = this.loaded.get(`${id}:${lod}`)
      if (l && this.layers.buildings) targets.push(...l.buildingMeshes)
      const ex = this.extras.get(id)
      if (ex?.issues && this.layers.issues) targets.push(...ex.issues.children)
    }
    const hit = raycaster.intersectObjects(targets, false)[0]
    if (!hit) return null
    const tile = hit.object.userData.tile as string
    if (hit.object.userData.issue !== undefined) return { type: 'issue', tile, index: hit.object.userData.issue }
    const fidAttr = (hit.object as THREE.Mesh).geometry.getAttribute('_feature_id')
    if (!fidAttr || hit.face == null) return null
    const fid = fidAttr.getX(hit.face.a)
    const ex = this.extras.get(tile)
    return ex ? (ex.meta.buildings[fid] ? { type: 'building', meta: ex.meta.buildings[fid], tile, fid } : null) : { type: 'building', meta: null, tile, fid }
  }

  /** Ground point under a ray (terrain only). */
  pickGround(raycaster: THREE.Raycaster): THREE.Vector3 | null {
    const targets: THREE.Object3D[] = []
    for (const [id, lod] of this.shown) {
      const t = this.loaded.get(`${id}:${lod}`)?.terrainMesh
      if (t) targets.push(t)
    }
    return raycaster.intersectObjects(targets, false)[0]?.point ?? null
  }

  issue(tile: string, i: number) {
    return this.extras.get(tile)?.meta.issues[i]
  }

  findBuilding(id: string): { meta: BuildingMeta; tile: string; fid: number } | null {
    for (const [tile, ex] of this.extras) {
      const fid = ex.meta.buildings.findIndex((b) => b.id === id)
      if (fid >= 0) return { meta: ex.meta.buildings[fid], tile, fid }
    }
    return null
  }

  stats(): StreamStats {
    const lodCounts: [number, number, number] = [0, 0, 0]
    for (const lod of this.shown.values()) lodCounts[lod]++
    let bytes = 0
    for (const l of this.loaded.values()) bytes += l.bytes
    const now = performance.now()
    return {
      active: this.shown.size,
      loading: this.inflight.size,
      queued: this.queued,
      failed: [...this.failures.values()].filter((f) => f.count >= 3 || f.retryAt > now).length,
      cachedMB: bytes / 1e6,
      transferredMB: this.transferred / 1e6,
      lodCounts,
      missing: this.index.failed.map((f) => f.id),
    }
  }

  dispose() {
    this.disposed = true
    for (const c of this.inflight.values()) c.abort()
    for (const k of [...this.loaded.keys()]) this.unload(k)
  }
}

function paletteFor(buildings: BuildingMeta[], mode: ColorMode): ([number, number, number] | null)[] {
  return buildings.map((b) => {
    if (mode === 'geometry') return linear(QUALITY_COLORS[b.conf])
    if (b.hM === 'override') return linear(QUALITY_COLORS.hOverride)
    if (b.hM === 'type_default') return linear(QUALITY_COLORS.hDefault)
    if (b.hM.startsWith('ghsl')) return linear(QUALITY_COLORS.hGhsl)
    return linear(QUALITY_COLORS.hSource) // OSM height / levels, photo estimate of a landmark
  })
}

// ------------------------------------------------------------------------------------------------------
// trees and issue markers
// ------------------------------------------------------------------------------------------------------
const TREE_NEAR_M = 220 // full-detail trees within this distance of the camera (tile bbox), lite beyond
let protos: THREE.BufferGeometry[] | null = null
let protosLite: THREE.BufferGeometry[] | null = null

function coloured(g: THREE.BufferGeometry, hex: string) {
  const geo = g.index ? g.toNonIndexed() : g
  const c = linear(hex)
  const arr = new Float32Array(geo.getAttribute('position').count * 3)
  for (let i = 0; i < arr.length; i += 3) arr.set(c, i)
  geo.setAttribute('color', new THREE.BufferAttribute(arr, 3))
  geo.deleteAttribute('uv')
  return geo
}

function treeProtos(lite = false): THREE.BufferGeometry[] {
  const cached = lite ? protosLite : protos
  if (cached) return cached
  // unit trees: height 1, crown diameter 1 -> scaled per instance (h, 2r). Crowns are clusters of several
  // blobs in two greens (sunlit top, darker inner) with smooth normals, so they read as foliage, not facets.
  // The lite set (one blob per crown, ~5x fewer triangles) is used beyond TREE_NEAR_M and in LOD1 tiles.
  const trunk = (r = 0.06, h = 0.42) => coloured(new THREE.CylinderGeometry(r * 0.65, r, h, 6).translate(0, h / 2, 0), '#4a3626')
  const blob = (r: number, x: number, y: number, z: number, hex: string, sy = 0.85, detail = 1) =>
    coloured(new THREE.IcosahedronGeometry(r, detail).scale(1, sy, 1).translate(x, y, z), hex)
  const smooth = (g: THREE.BufferGeometry) => {
    const m = mergeVertices(g, 1e-4)
    m.computeVertexNormals()
    return m
  }
  if (lite) {
    protosLite = [
      smooth(mergeGeometries([trunk(), blob(0.42, 0, 0.66, 0, '#3f6b2b', 0.85)])!),
      smooth(mergeGeometries([trunk(0.05), coloured(new THREE.ConeGeometry(0.45, 0.85, 7).translate(0, 0.55, 0), '#25492d')])!),
      smooth(mergeGeometries([trunk(0.05, 0.38), blob(0.45, 0, 0.6, 0, '#587d37', 0.72)])!),
      smooth(coloured(new THREE.CylinderGeometry(0.3, 0.5, 1, 6).translate(0, 0.5, 0), '#2a4a29')),
      smooth(blob(0.5, 0, 0.4, 0, '#4b6f30', 0.8, 0)),
    ]
    return protosLite
  }
  const decid = smooth(mergeGeometries([trunk(), blob(0.33, 0, 0.68, 0, '#3d6a2a'), blob(0.27, 0.19, 0.58, 0.07, '#47722f'),
    blob(0.26, -0.17, 0.6, -0.11, '#355e26'), blob(0.24, 0.04, 0.86, 0.06, '#4f7c35'), blob(0.22, -0.06, 0.55, 0.2, '#3a6428'),
    blob(0.2, 0.12, 0.74, -0.18, '#456e2e')])!)
  const conif = smooth(mergeGeometries([trunk(0.05), coloured(new THREE.ConeGeometry(0.45, 0.5, 9).translate(0, 0.42, 0), '#23452c'),
    coloured(new THREE.ConeGeometry(0.36, 0.45, 9).translate(0, 0.66, 0), '#27502f'), coloured(new THREE.ConeGeometry(0.24, 0.36, 9).translate(0, 0.88, 0), '#2c5733')])!)
  const fruit = smooth(mergeGeometries([trunk(0.05, 0.38), blob(0.36, 0, 0.6, 0, '#557a35', 0.72), blob(0.25, 0.22, 0.55, -0.06, '#5d823a', 0.72),
    blob(0.23, -0.2, 0.58, 0.12, '#4e7330', 0.72), blob(0.2, 0.02, 0.76, 0.16, '#628a3e', 0.72)])!)
  const thuja = smooth(mergeGeometries([coloured(new THREE.CylinderGeometry(0.05, 0.06, 0.1, 5).translate(0, 0.05, 0), '#4a3626'),
    coloured(new THREE.SphereGeometry(0.5, 10, 12).translate(0, 0.5, 0), '#2a4a29')])!)
  const shrub = smooth(mergeGeometries([blob(0.45, 0, 0.4, 0, '#4b6f30', 0.8), blob(0.32, 0.25, 0.32, 0.1, '#56793a', 0.8),
    blob(0.3, -0.22, 0.34, -0.12, '#426528', 0.8)])!)
  protos = [decid, conif, fruit, thuja, shrub]
  return protos
}

/** Instance colour: per-tree tint (avoids the "copy-paste forest" look) or, in the geometry-source mode, the
 *  provenance colour: OSM-mapped, canopy height model (Meta/WRI, real crowns), rule-based / decorative. */
function treeTint(t: TileMeta['trees'][number], hsh: number, mode: ColorMode, col: THREE.Color) {
  if (mode === 'geometry') {
    const hex = t[6] === 0 ? QUALITY_COLORS.source : t[6] === 3 ? QUALITY_COLORS.treeModel : QUALITY_COLORS.inferred
    const [r, g, b] = linear(hex)
    // the tree material multiplies the (dark green) vertex colours: divide by a typical leaf colour (#3d6a2a,
    // linear) so the crown shows the provenance colour
    return col.setRGB(r / 0.047, g / 0.144, b / 0.023)
  }
  return col.setRGB(0.82 + 0.3 * hsh, 0.85 + 0.25 * ((hsh * 7.3) % 1), 0.75 + 0.3 * ((hsh * 3.1) % 1))
}

function recolorTrees(g: THREE.Group, mode: ColorMode) {
  const col = new THREE.Color()
  for (const c of g.children) {
    const im = c as THREE.InstancedMesh
    const rows = im.userData.rows as TileMeta['trees']
    rows.forEach((t, i) => im.setColorAt(i, treeTint(t, Math.abs(Math.sin(t[0] * 12.9898 + t[1] * 78.233) * 43758.5453) % 1, mode, col)))
    if (im.instanceColor) im.instanceColor.needsUpdate = true
  }
}

function buildTrees(meta: TileMeta, lite = false, mode: ColorMode = 'realistic'): THREE.Group | null {
  if (!meta.trees.length) return null
  const g = new THREE.Group()
  g.name = `${meta.id}:trees`
  const m4 = new THREE.Matrix4()
  const q = new THREE.Quaternion()
  const up = new THREE.Vector3(0, 1, 0)
  for (const decorative of [false, true]) {
    for (const kind of [0, 1, 2, 3, 4]) {
      const rows = meta.trees.filter((t) => t[5] === kind && (t[6] === 2) === decorative)
      if (!rows.length) continue
      const im = new THREE.InstancedMesh(treeProtos(lite)[kind], MAT.tree, rows.length)
      const col = new THREE.Color()
      rows.forEach(([x, y, z, h, r], i) => {
        const hsh = Math.abs(Math.sin(x * 12.9898 + y * 78.233) * 43758.5453) % 1
        q.setFromAxisAngle(up, hsh * 6.283)
        m4.compose(new THREE.Vector3(x, z - 0.2, -y), q, new THREE.Vector3(2 * r, h, 2 * r))
        im.setMatrixAt(i, m4)
        im.setColorAt(i, treeTint(rows[i], hsh, mode, col))
      })
      im.userData.rows = rows
      im.computeBoundingSphere()
      im.castShadow = true
      im.userData.decorative = decorative
      im.userData.shared = true
      g.add(im)
    }
  }
  return g
}

const issueGeo = new THREE.ConeGeometry(2.5, 8, 8).rotateX(Math.PI).translate(0, 8, 0)

function buildIssues(meta: TileMeta): THREE.Group {
  const g = new THREE.Group()
  g.name = `${meta.id}:issues`
  meta.issues.forEach((it, i) => {
    const m = new THREE.Mesh(issueGeo, MAT.issue)
    m.position.set(it.x, it.z, -it.y)
    m.userData = { issue: i, tile: meta.id, shared: true }
    g.add(m)
  })
  return g
}

function disposeObject(o: THREE.Object3D, keepShared = false) {
  o.traverse((c) => {
    const m = c as THREE.Mesh
    if (!m.isMesh) return
    if (keepShared && c.userData.shared && !(c as THREE.InstancedMesh).isInstancedMesh) return
    if (!(c as THREE.InstancedMesh).isInstancedMesh && !c.userData.shared) m.geometry.dispose()
    if ((c as THREE.InstancedMesh).isInstancedMesh) (c as THREE.InstancedMesh).dispose()
    const mats = Array.isArray(m.material) ? m.material : [m.material]
    for (const mat of mats) {
      if (Object.values(MAT).includes(mat as never)) continue // shared materials live forever
      const map = (mat as THREE.MeshStandardMaterial).map
      map?.dispose()
      mat.dispose()
    }
  })
}
