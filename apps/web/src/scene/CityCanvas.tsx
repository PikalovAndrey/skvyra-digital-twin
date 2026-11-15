import { maybeGunzip } from '../tiles/gz'
import { Environment, Line, MapControls, Sky } from '@react-three/drei'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import type { MapControls as MapControlsImpl } from 'three-stdlib'
import { localToGeo } from '../geo'
import { nearestStreet, type SearchItem } from '../search'
import { useStore, type GapStats } from '../store'
import { TileManager } from '../tiles/TileManager'
import type { TileIndex } from '../types'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { GAP_COLORS, MAT, terrainMaterial, uniforms } from './materials'

const SUN_DIR = new THREE.Vector3(-0.42, 0.62, 0.66).normalize() // afternoon sun from the south-west
const NIGHT = false

interface Props {
  index: TileIndex
  baseUrl: string
  streets: SearchItem[]
  onManager: (m: TileManager | null) => void
}

export function overviewPose(index: TileIndex) {
  const xs = index.boundary_local.map((p) => p[0])
  const ys = index.boundary_local.map((p) => p[1])
  const zs = index.boundary_local.map((p) => p[2])
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2
  const cy = (Math.min(...ys) + Math.max(...ys)) / 2
  const cz = zs.reduce((a, b) => a + b, 0) / zs.length
  const w = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys))
  return {
    target: new THREE.Vector3(cx, cz, -cy),
    position: new THREE.Vector3(cx + 0.05 * w, cz + 0.85 * w, -cy + 0.55 * w),
  }
}

export function CityCanvas(props: Props) {
  const quality = useStore((s) => s.quality)
  const dpr: [number, number] = quality === 'low' ? [1, 1] : quality === 'medium' ? [1, 1.5] : [1, 2]
  const pose = useMemo(() => overviewPose(props.index), [props.index])
  return (
    <Canvas
      dpr={dpr}
      shadows={quality !== 'low' ? 'soft' : false}
      gl={{ antialias: quality !== 'low', powerPreference: 'high-performance', logarithmicDepthBuffer: false }}
      camera={{ fov: 45, near: 2, far: 40000, position: pose.position.toArray() }}
      onCreated={({ gl }) => {
        gl.toneMapping = THREE.NeutralToneMapping // Khronos PBR Neutral: keeps material colours, soft highlight roll-off
        gl.toneMappingExposure = 1.0
        const el = gl.domElement
        el.addEventListener('webglcontextlost', (e) => {
          e.preventDefault() // allows restoration
          useStore.getState().set({ fatal: 'contextLost' })
        })
        el.addEventListener('webglcontextrestored', () => useStore.getState().set({ fatal: null }))
    ;(window as unknown as { __skvyraGl?: unknown }).__skvyraGl = gl // diagnostics (program count) for benchmarks
      }}
      data-testid="city-canvas"
    >
      <SceneContent {...props} />
    </Canvas>
  )
}

function SceneContent({ index, baseUrl, streets, onManager }: Props) {
  const { scene, camera, gl } = useThree()
  const quality = useStore((s) => s.quality)
  const layers = useStore((s) => s.layers)
  const colorMode = useStore((s) => s.colorMode)
  const selection = useStore((s) => s.selection)
  const cameraMode = useStore((s) => s.cameraMode)
  const manager = useMemo(() => new TileManager(index, baseUrl), [index, baseUrl])
  const controls = useRef<MapControlsImpl>(null)
  const loadingFill = useRef<THREE.Object3D | null>(null) // coarse ground under the tiles, shown until they load
  const sun = useRef<THREE.DirectionalLight>(null)
  const pose = useMemo(() => overviewPose(index), [index])
  const zmin = Math.min(...index.tiles.map((t) => t.z[0]))

  // satellite-coloured surroundings outside the reconstructed tiles (context only, not pickable)
  useEffect(() => {
    if (!index.context) return
    const ctrl = new AbortController()
    let root: THREE.Object3D | null = null
    fetch(`${baseUrl}tiles/${index.context.u}?v=${encodeURIComponent(index.generated_at)}`, { signal: ctrl.signal })
      .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(`context ${r.status}`))))
      .then(maybeGunzip)
      .then((buf) => new GLTFLoader().parseAsync(buf, ''))
      .then((g) => {
        if (ctrl.signal.aborted) return
        g.scene.traverse((o) => {
          const m = o as THREE.Mesh
          if (!m.isMesh) return
          const orig = m.material as THREE.MeshStandardMaterial
          m.material = terrainMaterial(orig.map)
          m.receiveShadow = false
        })
        root = g.scene
        root.name = 'context (Sentinel-2, not reconstructed)'
        loadingFill.current = root.getObjectByName('context_under') ?? null
        scene.add(root)
      })
      .catch((e) => (e as Error).name !== 'AbortError' && console.warn('context ground unavailable:', e))
    return () => {
      ctrl.abort()
      if (root) {
        scene.remove(root)
        root.traverse((o) => {
          const m = o as THREE.Mesh
          if (m.isMesh) {
            m.geometry.dispose()
            ;(m.material as THREE.MeshStandardMaterial).map?.dispose()
            ;(m.material as THREE.Material).dispose()
          }
        })
      }
    }
  }, [index, baseUrl, scene])

  // shader warm-up: compile every shared material once at start-up, so the first close-up does not stall
  useEffect(() => {
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3))
    g.setAttribute('color', new THREE.Float32BufferAttribute([1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1], 4))
    g.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1], 2))
    g.setAttribute('_style', new THREE.Float32BufferAttribute([1, 1, 1], 1))
    g.setAttribute('_feature_id', new THREE.Float32BufferAttribute([0, 0, 0], 1))
    const warm = new THREE.Group()
    for (const m of [...Object.values(MAT), terrainMaterial(null)]) {
      const mesh = new THREE.Mesh(g, m)
      mesh.castShadow = mesh.receiveShadow = true
      mesh.frustumCulled = false
      warm.add(mesh)
    }
    scene.add(warm)
    let alive = true
    gl.compileAsync(scene, camera).catch(() => undefined).finally(() => {
      if (!alive) return
      scene.remove(warm)
      g.dispose()
    })
    return () => {
      alive = false
      scene.remove(warm)
    }
  }, [gl, scene, camera])

  useEffect(() => {
    manager.renderer = gl
    scene.add(manager.root)
    onManager(manager)
    return () => {
      scene.remove(manager.root)
      manager.dispose()
      onManager(null)
    }
  }, [manager, scene, onManager, gl])

  useEffect(() => {
    ;(window as unknown as { __skvyraCam?: THREE.Camera }).__skvyraCam = camera
  }, [camera])
  useEffect(() => manager.setQuality(quality), [manager, quality])
  useEffect(() => manager.setLayers(layers), [manager, layers])
  useEffect(() => {
    manager.setColorMode(colorMode)
    uniforms.uWindows.value = colorMode === 'realistic' ? 1 : 0
  }, [manager, colorMode])
  useEffect(() => {
    manager.setSelection(selection?.type === 'building' ? { tile: selection.tile, fid: manager.findBuilding(selection.meta.id)?.fid ?? -1 } : null)
  }, [manager, selection])

  useEffect(() => {
    scene.fog = new THREE.Fog('#c3d3e0', quality === 'low' ? 4000 : 7000, quality === 'high' ? 22000 : 15000) // light haze only far away
    return () => {
      scene.fog = null
    }
  }, [scene, quality])

  // --- streaming + stats --------------------------------------------------------------------------
  const acc = useRef({ frames: 0, t: performance.now(), last: 0, ms: 0 })
  const ring = useMemo(() => ({ buf: new Float32Array(32768), n: 0, prev: 0 }), [])
  useEffect(() => {
    // published in an effect: StrictMode runs useMemo twice and would expose the discarded instance
    ;(window as unknown as { __skvyraFrames?: unknown }).__skvyraFrames = ring // frame-time percentiles (benchmarks)
  }, [ring])
  useFrame((state, dt) => {
    uniforms.uTime.value += dt
    const a = acc.current
    const nowF = performance.now()
    if (ring.prev) ring.buf[ring.n++ % ring.buf.length] = nowF - ring.prev
    ring.prev = nowF
    a.frames++
    a.ms = a.ms * 0.9 + dt * 1000 * 0.1
    if (a.frames % 6 === 0) manager.update(camera as THREE.PerspectiveCamera)
    // keep the shadow camera around the area being looked at
    const target = controls.current?.target ?? pose.target
    if (sun.current) {
      const dist = camera.position.distanceTo(target)
      const half = THREE.MathUtils.clamp(dist * 0.6, 150, 1600)
      const sc = sun.current.shadow.camera as THREE.OrthographicCamera
      if (Math.abs(sc.right - half) > 1) {
        sc.left = sc.bottom = -half
        sc.right = sc.top = half
        sc.updateProjectionMatrix()
      }
      sun.current.position.copy(target).addScaledVector(SUN_DIR, 3000)
      sun.current.target.position.copy(target)
      sun.current.target.updateMatrixWorld()
    }
    const now = performance.now()
    if (now - a.t > 500) {
      const s = manager.stats()
      if (loadingFill.current && s.active > 0 && !s.loading && !s.queued) {
        loadingFill.current.visible = false // first tiles are in: the coarse fill would only poke through them
        loadingFill.current = null
      }
      const mem = (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory
      useStore.getState().set({
        stats: {
          fps: (a.frames * 1000) / (now - a.t), frameMs: a.ms, calls: state.gl.info.render.calls,
          triangles: state.gl.info.render.triangles, activeTiles: s.active, loading: s.loading, queued: s.queued,
          failed: s.failed, cachedMB: s.cachedMB, heapMB: mem ? mem.usedJSHeapSize / 1e6 : null,
          transferredMB: s.transferredMB, lodCounts: s.lodCounts,
        },
      })
      a.frames = 0
      a.t = now
    }
  })

  // --- picking & pointer readout --------------------------------------------------------------------
  useEffect(() => {
    const el = gl.domElement
    const ray = new THREE.Raycaster()
    const ndc = new THREE.Vector2()
    let down: { x: number; y: number } | null = null
    let lastMove = 0
    const toNdc = (e: PointerEvent) => {
      const r = el.getBoundingClientRect()
      ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1)
      ray.setFromCamera(ndc, camera)
    }
    const onDown = (e: PointerEvent) => (down = { x: e.clientX, y: e.clientY })
    const onUp = (e: PointerEvent) => {
      if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 5) return
      toNdc(e)
      const hit = manager.pick(ray)
      const set = useStore.getState().set
      if (!hit) set({ selection: null })
      else if (hit.type === 'building' && hit.meta) set({ selection: { type: 'building', meta: hit.meta, tile: hit.tile } })
      else if (hit.type === 'building') void manager.buildingMeta(hit.tile, hit.fid).then((m) => m && set({ selection: { type: 'building', meta: m, tile: hit.tile } }))
      else {
        const issue = manager.issue(hit.tile, hit.index)
        if (issue) set({ selection: { type: 'issue', issue, tile: hit.tile } })
      }
    }
    const onMove = (e: PointerEvent) => {
      const now = performance.now()
      if (now - lastMove < 80) return
      lastMove = now
      toNdc(e)
      const p = manager.pickGround(ray)
      if (!p) return useStore.getState().set({ pointer: null })
      const [lon, lat] = localToGeo(index.frame, p.x, -p.z)
      const st = nearestStreet(streets, p.x, -p.z)
      useStore.getState().set({ pointer: { lon, lat, elev: p.y, street: st?.name ?? null } })
    }
    el.addEventListener('pointerdown', onDown)
    el.addEventListener('pointerup', onUp)
    el.addEventListener('pointermove', onMove)
    return () => {
      el.removeEventListener('pointerdown', onDown)
      el.removeEventListener('pointerup', onUp)
      el.removeEventListener('pointermove', onMove)
    }
  }, [gl, camera, manager, index, streets])

  const boundaryPts = useMemo(
    () => index.boundary_local.map(([x, y, z]) => new THREE.Vector3(x, z + 2.5, -y)),
    [index],
  )
  const sunPos = useMemo(() => SUN_DIR.clone().multiplyScalar(1000).toArray(), [])

  return (
    <>
      <Sky sunPosition={sunPos} turbidity={3} rayleigh={1.1} mieCoefficient={0.004} mieDirectionalG={0.8} distance={30000} />
      {/* reflections (glass, water, metal roofs) and soft sky light come from the same procedural sky */}
      <Environment frames={1} resolution={128} environmentIntensity={0.55}>
        <Sky sunPosition={sunPos} turbidity={3} rayleigh={1.1} mieCoefficient={0.004} mieDirectionalG={0.8} />
      </Environment>
      <hemisphereLight args={['#dfe9f3', '#5d5a42', NIGHT ? 0.1 : 0.45]} />
      <directionalLight
        ref={sun}
        intensity={3.1}
        color="#fff1dc"
        castShadow={quality !== 'low'}
        shadow-mapSize={quality === 'high' ? [4096, 4096] : [2048, 2048]}
        shadow-bias={-0.0004}
        shadow-normalBias={0.6}
        shadow-camera-near={10}
        shadow-camera-far={7000}
      />
      <mesh rotation-x={-Math.PI / 2} position={[pose.target.x, zmin - 4, pose.target.z]} receiveShadow={false} name="horizon (not reconstructed)">
        <planeGeometry args={[60000, 60000]} />
        <meshStandardMaterial color="#8a8a5c" roughness={1} />
      </mesh>
      {layers.boundary && <Line points={boundaryPts} color="#d6453d" lineWidth={2.5} dashed dashSize={20} gapSize={12} />}
      {layers.gaps && <QualityGaps baseUrl={baseUrl} />}
      {cameraMode === 'orbit' ? (
        <MapControls
          ref={controls}
          makeDefault
          target={pose.target}
          minDistance={12}
          maxDistance={16000}
          maxPolarAngle={1.48}
          enableDamping
          dampingFactor={0.12}
          screenSpacePanning={false}
          zoomToCursor
        />
      ) : (
        <FlyControls />
      )}
      <CameraDirector manager={manager} controls={controls} index={index} />
    </>
  )
}

/** Smooth fly-to (search results, overview, reset) and ground clamping. */
function CameraDirector({ manager, controls, index }: { manager: TileManager; controls: React.RefObject<MapControlsImpl | null>; index: TileIndex }) {
  const { camera } = useThree()
  const flyTo = useStore((s) => s.flyTo)
  const resetView = useStore((s) => s.resetView)
  const view = useStore((s) => s.view)
  const anim = useRef<{ fromP: THREE.Vector3; toP: THREE.Vector3; fromT: THREE.Vector3; toT: THREE.Vector3; t0: number; dur: number } | null>(null)
  const down = useMemo(() => new THREE.Raycaster(), [])
  const frameNo = useRef(0)

  const start = (toT: THREE.Vector3, toP: THREE.Vector3) => {
    const fromT = controls.current?.target.clone() ?? toT.clone()
    anim.current = { fromP: camera.position.clone(), toP, fromT, toT, t0: performance.now(), dur: 1400 }
  }

  useEffect(() => {
    if (!flyTo) return
    const zs = index.tiles.find((t) => flyTo.x >= t.b[0] && flyTo.x < t.b[2] && flyTo.y >= t.b[1] && flyTo.y < t.b[3])?.z ?? [200, 200]
    const gz = (zs[0] + zs[1]) / 2
    const d = flyTo.dist ?? 260
    const toT = new THREE.Vector3(flyTo.x, gz, -flyTo.y)
    const p = THREE.MathUtils.degToRad(flyTo.pitch ?? 48)
    start(toT, toT.clone().add(new THREE.Vector3(-0.38 * Math.cos(p), Math.sin(p), 0.92 * Math.cos(p)).multiplyScalar(d)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flyTo])

  useEffect(() => {
    if (!view) return
    anim.current = null
    const [cx, cy, cz] = view.camera
    const [tx, ty, tz] = view.target
    const tgt = new THREE.Vector3(tx, tz, -ty)
    if (view.fly) return start(tgt, new THREE.Vector3(cx, cz, -cy))
    camera.position.set(cx, cz, -cy)
    if (controls.current) {
      controls.current.target.copy(tgt)
      controls.current.update()
    } else camera.lookAt(tgt)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view])

  useEffect(() => {
    if (!resetView) return
    const p = overviewPose(index)
    start(p.target, p.position)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetView])

  useFrame(() => {
    const a = anim.current
    if (a) {
      const k = Math.min(1, (performance.now() - a.t0) / a.dur)
      const e = k < 0.5 ? 4 * k ** 3 : 1 - (-2 * k + 2) ** 3 / 2 // easeInOutCubic
      camera.position.lerpVectors(a.fromP, a.toP, e)
      if (controls.current) {
        controls.current.target.lerpVectors(a.fromT, a.toT, e)
        controls.current.update()
      } else camera.lookAt(new THREE.Vector3().lerpVectors(a.fromT, a.toT, e))
      if (k >= 1) anim.current = null
    }
    // never go below ground (raycast every 4th frame: it scans every visible terrain mesh)
    if ((frameNo.current = (frameNo.current + 1) % 4) !== 0) return
    down.set(new THREE.Vector3(camera.position.x, camera.position.y + 2000, camera.position.z), new THREE.Vector3(0, -1, 0))
    const g = manager.pickGround(down)
    if (g && camera.position.y < g.y + 4) camera.position.y = g.y + 4
  })
  return null
}

function FlyControls() {
  const { camera, gl } = useThree()
  const keys = useRef(new Set<string>())
  const euler = useMemo(() => new THREE.Euler(0, 0, 0, 'YXZ').setFromQuaternion(camera.quaternion), [camera])
  useEffect(() => {
    const el = gl.domElement
    let drag: { x: number; y: number } | null = null
    const kd = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === 'INPUT') return
      keys.current.add(e.code)
    }
    const ku = (e: KeyboardEvent) => keys.current.delete(e.code)
    const pd = (e: PointerEvent) => (drag = { x: e.clientX, y: e.clientY })
    const pu = () => (drag = null)
    const pm = (e: PointerEvent) => {
      if (!drag) return
      euler.y -= (e.clientX - drag.x) * 0.0035
      euler.x = THREE.MathUtils.clamp(euler.x - (e.clientY - drag.y) * 0.0035, -1.5, 1.5)
      camera.quaternion.setFromEuler(euler)
      drag = { x: e.clientX, y: e.clientY }
    }
    const wheel = (e: WheelEvent) => {
      const s = useStore.getState()
      s.set({ flySpeed: THREE.MathUtils.clamp(s.flySpeed * (e.deltaY > 0 ? 0.85 : 1.18), 5, 600) })
    }
    window.addEventListener('keydown', kd)
    window.addEventListener('keyup', ku)
    el.addEventListener('pointerdown', pd)
    window.addEventListener('pointerup', pu)
    window.addEventListener('pointermove', pm)
    el.addEventListener('wheel', wheel, { passive: true })
    const blur = () => keys.current.clear()
    window.addEventListener('blur', blur)
    return () => {
      window.removeEventListener('keydown', kd)
      window.removeEventListener('keyup', ku)
      el.removeEventListener('pointerdown', pd)
      window.removeEventListener('pointerup', pu)
      window.removeEventListener('pointermove', pm)
      el.removeEventListener('wheel', wheel)
      window.removeEventListener('blur', blur)
    }
  }, [camera, gl, euler])
  const v = useMemo(() => new THREE.Vector3(), [])
  useFrame((_, dt) => {
    const k = keys.current
    if (!k.size) return
    const speed = useStore.getState().flySpeed * (k.has('ShiftLeft') || k.has('ShiftRight') ? 4 : 1) * Math.min(dt, 0.1)
    v.set(
      (k.has('KeyD') ? 1 : 0) - (k.has('KeyA') ? 1 : 0),
      (k.has('KeyE') ? 1 : 0) - (k.has('KeyQ') ? 1 : 0),
      (k.has('KeyS') ? 1 : 0) - (k.has('KeyW') ? 1 : 0),
    )
    if (!v.lengthSq()) return
    v.normalize().multiplyScalar(speed)
    const upDown = v.y
    v.y = 0
    v.applyQuaternion(camera.quaternion)
    camera.position.add(v)
    camera.position.y += upDown
  })
  return null
}

/** Data-quality overlay: street lines where outlines are probably incomplete. Lines only - never buildings. */
type GapRun = { cls: 'suspect_gap' | 'insufficient' | 'no_signs'; conf: string; pts: [number, number, number][] }
function QualityGaps({ baseUrl }: { baseUrl: string }) {
  const [runs, setRuns] = useState<GapRun[] | null>(null)
  useEffect(() => {
    const ctrl = new AbortController()
    fetch(`${baseUrl}quality_gaps.json`, { signal: ctrl.signal, cache: 'no-cache' })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: { stats: GapStats; runs: GapRun[] }) => {
        setRuns(d.runs)
        useStore.getState().set({ gapStats: d.stats })
      })
      .catch(() => undefined)
    return () => ctrl.abort()
  }, [baseUrl])
  const groups = useMemo(() => {
    const g: Record<string, THREE.Vector3[]> = {}
    for (const r of runs ?? []) {
      const key = r.cls === 'suspect_gap' ? `suspect_gap_${r.conf}` : r.cls
      const arr = (g[key] ??= [])
      for (let i = 0; i + 1 < r.pts.length; i++) {
        const [a, b] = [r.pts[i], r.pts[i + 1]]
        arr.push(new THREE.Vector3(a[0], a[2], -a[1]), new THREE.Vector3(b[0], b[2], -b[1]))
      }
    }
    return g
  }, [runs])
  return (
    <group name="quality: probable missing outlines (not buildings)">
      {Object.entries(groups).map(([k, pts]) => (
        <Line key={k} points={pts} segments color={GAP_COLORS[k as keyof typeof GAP_COLORS]} lineWidth={k.startsWith('suspect') ? 5 : 3}
          dashed={k === 'insufficient'} dashSize={6} gapSize={4} />
      ))}
    </group>
  )
}
