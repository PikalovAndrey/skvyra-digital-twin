# Progress — 2026-11-14

## Iteration 7 — Authenticity 3.2 (2026-11-14)

Heritage statuses restructured with official sources (all `unknown` officially; reported statuses kept
separately); zemstvo and Rylskyi identities checked → not confirmed, not modelled, probable matches labelled in the
viewer; camera-jump stall traced to the uncapped benchmark mode (normal pacing: p95 ~33 ms), new jump benchmark;
missing-outline quality layer with statistics (probable gaps 11.9 % of residential streets); Overture checked.
Tests 124 / 34 / tsc 0 / e2e 7, QA 0 defects.

## Iteration 6 — Authenticity 3.1 (2026-11-14)

Synagogue rebuilt from 30+ Commons photos on its real OSM outline (three blocks, stepped attic, Star-of-David
gable window, stone plinth and stairs); KartaView licence verified (CC BY-SA 4.0) but the Skvyra sequence is
deleted and unattributable → not used; decorative fences kept off photo-based landmarks; tree build time
recorded in the benchmark. Tests 123 / 34 / tsc 0 / e2e 6, QA 0 defects.

## Iteration 5 — Realism & Authenticity 3.0 (2026-11-14)

| Phase | Result |
|---|---|
| 0 Data discovery | CHM v2 usable (2015/2011 imagery, model-fill plateaus found); WorldCover 2021 as a newer check; Sentinel-2 composite rejected; Panoramax 0, KartaView without licence, Mapillary needs a token; GlobalBuildingAtlas tile 1.02 GB → not downloaded |
| 1 Real vegetation | pilot (residential quarter + Парк культури) → city-wide: 23,514 of 50,569 trees from observed data; three trust masks; provenance colouring in the viewer |
| 2 Landmarks | lyceum corner tower (photo estimate); kostel / zemstvo / Babaev: not reliably reproducible from the available evidence (documented) |
| 3 Performance | distance-based tree detail; paired benchmarks; CPU-throttled profile added to `make bench` |
| 4 Ground | multi-date composite tested, not adopted |
| 5 Integration | 16 before/after pairs (`docs/img/vf3/`), QA 0 defects, 122 Python tests, 34 vitest, tsc 0, e2e 6 passed, production build OK |

Also fixed: a fresh clone failed at `make acquire` (gitignored `data/raw` missing); a flaky unit test (test HTTP
server did not read POST bodies → random TCP resets).


## Iteration 4 — Visual Fidelity 2.0 (same day)

| Item | Status | Evidence |
|---|---|---|
| Before screenshots, 13 fixed viewpoints incl. house close-up, street level, pond | Done | `renders/compare/vf2-before` |
| Prototype quarter first (4 tiles), then whole city | Done | `renders/compare/vf2-proto` |
| Eaves overhang + fascia, doors on bay boundaries, PBR relief for roofs/walls, frames, sills | Implemented, visually verified | `docs/img/vf2/web-house-close.jpg`, test `test_eaves_overhang_extends_roof_not_walls` |
| Multi-scale ground, asphalt, shoulders, gravel | Implemented, visually verified | `docs/img/vf2/web-street-ground.jpg` |
| Trees/shrubs, decorative yard layer (switchable, labelled invented) | Implemented, visually verified | `web-street-ground`, `web-house-close` |
| Water: flat ponds at shoreline level, shore colour, ripples, sky reflection; outline check vs OSM | Verified | `make qa` water figures, `web-pond-shore` |
| Lighting: sky environment, Neutral tone mapping | Visually verified | all vf2 pairs |
| Compact toolbar UI, all functions kept | Verified by e2e (popover flows) | `viewer.spec.ts` |
| Performance after changes, 3 runs, percentiles | Measured | PERFORMANCE.md |
| Regression found + fixed: data-quality colouring black with 16-bit colours | Verified | e2e "data-quality colouring is not black" |
| Stabilisation: all 119 tiles rebuilt (0 failed), fresh screenshots of every viewpoint + LOD transitions | Done | `renders/compare/vf2-final`, `docs/img/vf2/web-lod-transitions.jpg` |
| Defect found + fixed: flat ponds invisible in the viewer (back-face culled) | Verified | `web-pond-shore`, `web-river`; QA `surface_triangles_facing_down: 0`; unit test |
| Defect found + fixed: saw-tooth pond shore; streams vanishing into grass | Verified | same images; `test_pond_cut_leaves_no_terrain_above_water_inside_outline` |

## Iteration 3 — final audit (same day)

See docs/AUDIT_REPORT.md: GHSL storey rule removed (within product error), Microsoft licence corrected to
CDLA Permissive 2.0, counts reconciled, geometry QA (`make qa`) with road/tree/roof fixes, landmark tiers,
frame-time percentiles and stall fixes.


## Iteration 2 — autonomous realism (same day)

Goal set by the user: maximum realism without manual heights/photos; data quality first, then visuals.

| Item | Status | Evidence |
|---|---|---|
| Fixed viewpoints shared by Blender and the viewer; before/after renders | Done | `blender/presets/viewpoints.json`, `scripts/compare_renders.sh`, `docs/img/compare/*.jpg` (10 viewpoints × 2 renderers) |
| Data/geometry QA before visuals: ML footprints on roads (3 quarantined), buildings in water (0), DSM bias measured | Verified | `normalize_report.json`, `terrain_report.json` |
| DSM → DTM correction under buildings/woodland | Verified | `test_dtm_lowers_objects_only_inside_the_mask` |
| Height evidence from GHSL ANBH with method + confidence per building | Verified | `test_ghsl_*`, integration `test_height_provenance` |
| Height/roof provenance audit | Done | DATA_SOURCE_AUDIT §7, `building_attributes_report.json` |
| LiDAR / open DSM / legal roof imagery research | Done — none usable for Skvyra | DATA_SOURCE_AUDIT "Researched and not usable" |
| Roof engine: min-of-planes, 6 families (adds half-hipped), multi-part roofs for L/T/U plans and extensions | Verified | roof area = footprint area for 6 roofs × 6 shapes and 3 roofs × 5 complex plans; outward walls; gable faces |
| Architectural categories + roof materials (shader ids), style from licensed photos | Implemented, visually verified (web + Blender) | ARCHITECTURE_STYLE.md, `web-centre-street`, `web-houses-ne` |
| Landmarks from Wikidata, photo estimates with licences; ambiguous matches rejected | Verified | `landmarks_report.json`, `test_landmark_rejects_merged_ml_blob` |
| Sentinel-2 ground colour with roof-halo removal; context ground | Verified / visually verified | `test_sentinel_roof_halos_are_removed`, `web-overview` |
| Vegetation: multi-blob crowns, thuja, per-tree tint | Visually verified | `web-houses-ne` |
| Contact shadows (baked) | Visually verified; screen-space AO (N8AO) **removed** after it crashed with three r187 | — |
| Performance after changes | Measured | PERFORMANCE.md (1.4–3.0 ms/frame) |
| Tests: 102 unit + 10 integration (pytest), 34 vitest, 5 e2e | Passing | see final run in README |
| Strict typecheck (src + e2e) | Passing | `tsc --noEmit`, 0 errors |

Bugs found during this iteration: stale idle timer in e2e/compare helpers (screenshots taken too early);
GHSL tile index off by one row (grid origin 89.0996 N); landmark area gate wrongly rejecting a human-mapped
OSM footprint; photo estimate outranking OSM `building:levels`; non-atomic GLB writes; S2 roof halos;
burned-out bare soil in the colour grade.

## Iteration 1

Legend: **Verified** = automated test and/or inspected output (evidence named). **Implemented** = code
exists and runs, but no dedicated test. **Partial** = works with known gaps. **Blocked** / **Not started**.

## Milestone A — Geographic truth: **done**

| Item | Status | Evidence |
|---|---|---|
| Boundary (OSM rel 3811860, identity check, manual override hook) | Verified | `boundary.py`; tags checked each run; 17.22 km² |
| Data source inventory, licences, sizes | Done (some rows † unverified online) | DATA_SOURCE_AUDIT.md |
| Coverage statistics + data-poor areas | Verified | `audit.json`, `docs/img/coverage_grid.png` |
| OSM↔ML alignment cross-check | Verified | 1,428 pairs, median offset 0.82 m, bias (0.03, 0.02) m |
| 2D diagnostic map | Visually verified | `docs/img/diagnostic_map.png`, `diagnostic_center.png` |

## Milestone B — First real 3D neighbourhood: **done**

| Item | Status | Evidence |
|---|---|---|
| Acquisition with provenance, retries, offline mode, corrupted-cache detection | Verified | `test_unit.py` (429/503 retry, 400 fail-fast, remark/schema/non-JSON, checksum, offline) |
| Normalization: repair, quarantine, dedupe, sensitive suppression, stable ids | Verified | `test_unit.py`, `test_integration.py::test_normalization_counts_and_quarantine` |
| Terrain (fill, despike, smooth, lattice, skirts, seamless edges) | Verified | `test_terrain_tiles_share_edge_vertices`, `test_dem_cleanup_does_not_depend_on_logging` |
| Buildings: heights with provenance, 5 roof families, courtyards, concave, narrow | Verified | roof area = footprint area for 5 roofs × 6 shapes; outward walls; slopes (no floating) |
| Roads: width rules, unioned surfaces, draping, bridges | Verified (bridges: implemented only) | `test_drape_*`, `test_road_union_has_no_overlaps` |
| Blender scene + rendered preview | Visually verified | `docs/img/render-street-eevee.jpg`, `render-centre-sunset-cycles.jpg` |

## Milestone C — Interactive viewer: **done**

| Item | Status | Evidence |
|---|---|---|
| Orbit (map) controls, zoom-to-cursor, fly mode (WASD/QE, speed) | Verified (e2e: wheel zoom; fly mode moves the camera with W) | `viewer.spec.ts` |
| Building selection + metadata with provenance | Verified | e2e journey step 5–6, `viewer-3-selected.jpg` |
| Coordinates, elevation and street-name readout | Visually verified | `viewer-2b-zoomed.jpg` |
| Search with Ukrainian/Latin spellings | Verified | `search.test.ts`, e2e (Latin query "Shevchenka") |
| Data-quality modes + legend (geometry source, height source, issues) | Verified | e2e step 8, `viewer-4-data-quality.jpg` |
| Ukrainian default, English switch | Verified | e2e |
| Streaming: frustum + distance LOD + hysteresis, abort, LRU budget, retries | Verified | `select.test.ts`, e2e stress test, failing-tile test |
| Minimap, stats overlay, loading indicator, error states, attribution | Implemented; WebGL-missing and context-loss paths not exercised by tests | |

## Milestone D — Whole-city coverage: **done**

All 119 tiles, 8,063 buildings, 47,481 trees built; 0 failed tiles; missing coverage reported
(DATA_SOURCE_AUDIT §4). Verified: every building in exactly one tile, GLB validity/alignment,
deterministic byte-identical rebuilds, incremental rebuilds, partial-failure recovery
(`test_integration.py`).

## Milestone E — Visual fidelity: **partial**

| Item | Status |
|---|---|
| Roof families, chimneys, procedural windows, seeded palettes | Implemented, visually verified |
| Vegetation (mapped + land-cover procedural + labelled decorative) | Implemented, visually verified |
| Day / sunset / night presets, cinematic still, flyover | All three visually verified (`render-*.jpg`, `renders/flyover.mp4`). Night: every window is lit in Blender (no per-window randomisation there yet, the viewer shader does randomise) and there are no street lights |
| Seamless ground textures | Visually verified (seams fixed after first viewer run) |
| Landmark models from verified references | **Not started** (no verified references available) |
| Straight-skeleton roofs, facade details (doors/shops) | Not started |

## Milestone F — Polish and verification: **partial**

| Item | Status |
|---|---|
| Performance benchmark + budgets | Done on one machine (PERFORMANCE.md) |
| Regression tests: 76 pytest, 34 vitest, 5 Playwright e2e (+ benchmark) | Passing on 2026-11-12 |
| Strict TypeScript typecheck (src + e2e) | Passing (needed @types/node for the benchmark script) |
| Documentation set | Done |
| Pixel-diff visual regression | Not started |
| Production build (`vite build`) | Not run yet (heavy job; nothing requires it for local use) |

## Blocked

- Verified landmark geometry and measured heights: needs user photos/measurements (no open source exists).
- Imagery-based checks of the "empty streets": no legally usable high-resolution imagery.

## Bugs found and fixed during verification (for the record)

1. Tile-edge texture seams in the overview (per-tile noise) → world-space noise.
2. DEM no-data fill and spike removal were skipped for LOD1/2 after a logging refactor → caught by the
   integration test; regression test added.
3. Only the first Microsoft quadkey file was read → all files read, cross-file duplicates removed.
4. Linear `man_made=goods_conveyor` ways were quarantined as "unclosed areas" → treated as lines.
5. Viewer idle detection could fire before all tiles were requested → explicit `queued` counter.
6. Benchmark mislabelled a sample after a quality switch → route reordered.
