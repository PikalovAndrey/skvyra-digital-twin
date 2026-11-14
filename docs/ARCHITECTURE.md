# Architecture

```
skvyra-digital-twin/
  config/city.json          geographic + build configuration (no geography hard-coded in code)
  pipeline/skvyra/          Python package: acquire, boundary, osm, normalize, audit, geo, terrain,
                            heights (GHSL evidence), landmarks, buildings, surfaces, mesh (GLB writer),
                            tiles, overrides, cli
  config/landmarks.json     listed buildings: Wikidata coordinates + photo-estimated attributes
  blender/presets/          viewpoints.json: fixed cameras shared by Blender and the viewer (before/after)
  blender/scripts/          build_scene.py (headless scene builder + renderer)
  apps/web/                 React + TypeScript (strict) + Vite + three.js / React Three Fiber + drei,
                            zustand, Tailwind; Vitest unit tests, Playwright e2e + benchmark
  data/raw|processed        source data + manifest | normalized GeoJSON + reports   (git-ignored)
  data/overrides/           manual corrections (kept in git)
  assets/generated/         tiles, search index, .blend files                     (git-ignored)
  tests/                    pytest unit + integration, synthetic fixture generator
  benchmarks/results/       benchmark JSON (kept as evidence)
  docs/                     this documentation + diagnostic images
```

The suggested `packages/shared-types`, `geo-core` split was not needed: the only shared contract is the
tile format (`apps/web/src/types.ts` mirrors `tiles.py`), and the geo maths exists once per language
with cross-language tests (pyproj fixtures in `tests/fixtures/utm_points.json`).

## Key decisions

| Decision | Why |
|---|---|
| Meshes are generated in Python and written as GLB directly (own ~100-line writer) | Deterministic, unit-testable without Blender, ~5 s for the whole town; Blender consumes the same GLBs, so the web and cinematic outputs never diverge. Blender remains the place for materials, lighting, landmarks and renders. |
| OSM via Overpass, not a country `.pbf` | 2 MB vs ~1–1.5 GB (estimate, not downloaded) for the same data at this scale |
| UTM 35N + fixed local origin | metric processing; float32-safe GPU coordinates; origin stored in config so ids/coords are stable |
| One GLB per tile per LOD, 500 m tiles | 119 tiles; overview LOD totals 18 MB, max LOD0 tile 2.4 MB; small enough for fine-grained streaming |
| Feature id as a vertex attribute | picking = one raycast + attribute lookup; recolouring for data-quality modes without extra draw calls |
| Streaming manager outside React | tiles are added to the scene graph imperatively; React only renders panels, so streaming never re-renders UI |
| Shared materials (one per kind) | constant shader/material count regardless of tile count; terrain is the only per-tile material (own texture) |
| Ground detail as per-tile textures, roads as meshes only at LOD0/1 | overview stays cheap, close-ups stay crisp |
| Overrides keyed by stable source id + anchor | corrections survive data refreshes; conflicts are reported |
| Roofs = min of planes, regions by half-plane clipping; complex plans split into rectangles | one robust code path for 6 roof families, L/T/U plans and extensions, testable (roof area = footprint area) |
| Facade / roof detail procedural in shaders, driven by a per-vertex `_STYLE` id | no texture atlas to stream; consistent in web and Blender; patterns fade with distance (no moiré) |
| Satellite colour only as ground texture; GHSL only as height evidence with confidence | uses open data where it is valid, never as geometry or as a measurement |
| No post-processing pass (N8AO tried: incompatible with three r187 render targets) | baked contact shadows in the ground texture + shader wall darkening instead |

## Viewer data flow

```
index.json + search.json ──► App ──► CityCanvas (R3F)
                                      ├─ TileManager (select.ts: frustum + distance LOD + hysteresis)
                                      │    fetch(AbortController) → GLTFLoader.parse → shared materials
                                      │    LRU budget eviction → dispose geometry/textures
                                      │    tile JSON → trees (InstancedMesh), issue markers, metadata
                                      ├─ picking (raycast → _feature_id → building metadata)
                                      └─ camera: MapControls (orbit/pan/zoom-to-cursor) | fly (WASD)
zustand store ◄──── stats every 500 ms, pointer readout (≤ 12 Hz), selection
```

## Error handling

- Pipeline: actionable one-line errors; quarantine instead of silent drops; failed tiles listed and the
  stage exits non-zero; offline mode and corrupted-cache detection.
- Viewer: WebGL2 check before creating the canvas; GPU context loss/restoration handled with a message;
  failed tile requests retried with exponential backoff, counted and shown ("тайлів не завантажилося");
  missing LOD files fall back to the nearest available LOD; tiles missing from the dataset are listed
  from `index.json["failed"]` and drawn red on the minimap; obsolete requests are aborted; no duplicate
  in-flight loads.
