# Final audit — 2026-11-12

Scope requested: data credibility, licences, visual/geometric defects, landmarks, performance, full test
pass. Every number below is reproducible with the command named next to it.

## 1. Data credibility

### GHSL heights (`pipeline/skvyra/heights.py`)

- **Correct layer**: ANBH (*Average of the Net Building Height*), not AGBH (gross = height × built
  fraction). Product: 3 arc-second grid in EPSG:4326 (~92 × 60 m at Skvyra), epoch 2018, derived from
  AW3D30/SRTM30 DSMs + Sentinel-2. Stated error (GHSL Data Package 2023, §2.2.3): **MAE 1.97 m, RMSE 3.55 m**.
- **Defect found — 1 vs 2 storeys for small houses was within noise.** Sensitivity of the share of
  "two-storey" houses to the ANBH threshold: 6.0 m → 41.7 %, 6.5 → 31.3 %, **7.0 → 21.3 %** (previous rule),
  7.5 → 13.5 %, 8.0 → 7.9 %, 9.0 → 1.6 %. With RMSE 3.55 m a truly one-storey cell (4–5 m) reads ≥ 7 m in
  20–29 % of cases — the 21 % result is indistinguishable from error. The flagged houses also cluster near
  large buildings (67 % within 50 m of a ≥ 400 m² building vs 4 % beyond 200 m): spill-over of the 100 m
  model. **Fix**: the rule was removed; small houses use the documented type default; the GHSL value is
  still shown in the info panel as context with its error.
- **Kept, narrowed**: a GHSL height is used only for a *non-residential* building ≥ 400 m² that covers
  ≥ 60 % of its cell's built area (96 buildings, confidence "low").
- Result (inside the boundary, 7,989 buildings): measured/explicit 1 · OSM storeys 8 · photo estimate 1 ·
  GHSL 99 (1.2 %) · type default 7,880 (98.6 %, confidence "lowest").

### Building counts (`make validate` → `audit.json["building_reconciliation"]`)

| | count |
|---|---|
| OSM building parts accepted (AOI) | 1,881 (1 quarantined: bank mapped as a point) |
| Microsoft ML footprints in AOI | 7,975 |
| − overlapping an OSM building (dropped) | 1,793 |
| − on a road centre line (quarantined) | 3 |
| = ML footprints accepted | 6,179 |
| **Accepted in AOI** | **8,060** = 7,989 inside the boundary + 71 in the 300 m context ring |
| Meshed in tiles | 8,060 |
| Generated (invented) buildings | **0** |

7,992 (iteration 1) − 7,989 (now) = the 3 ML footprints quarantined on roads, all inside the boundary.

## 2. Licences — defect found and fixed

Microsoft Global ML Building Footprints is licensed **CDLA Permissive 2.0** (official repo LICENSE and
README), not ODbL as earlier docs, config, viewer attribution, Blender metadata and the provenance manifest
stated (an unverified assumption on my side). All corrected; licence text in
`LICENSES/CDLA-Permissive-2.0.txt`. The DEM notice now uses the required "produced using Copernicus
WorldDEM-30 …" form because the DEM is modified. Compatibility and publication rules: `docs/LICENSES.md`.

## 3. Geometry and visual defects (`make qa` → `data/processed/qa_report.json`)

| Check | Before | After | Fix |
|---|---|---|---|
| Floating buildings | 0 | 0 | — |
| Buried (uphill eave < 0.5 m above ground) | 0 | 0 | — |
| Low uphill wall 0.5–1.8 m (steep plots) | 13 | 13 | accepted, slopes are real |
| Buildings crossed by generated road surfaces > 1 m² | 59 | **0** | inferred road widths are clipped by observed footprints |
| Trees inside buildings | 11 | **0** | tree exclusion now includes buildings owned by neighbouring tiles |
| Houses whose roof is taller than their walls | 173 | **1** | roof rise capped per type (house 3.2 m, outbuilding 2 m …) |
| House height outliers > 11 m | 9 | **1** | same + no 2-storey GHSL guess |
| Probable landmark given 4 m storeys (13 m tall) | 1 | 0 | probable matches change the name only |

## 4. Landmarks (`data/processed/landmarks_report.json`)

| Landmark | Result | Why |
|---|---|---|
| Ліцей (чоловіча гімназія) | exact | OSM footprint contains the Wikidata point |
| Особняк Бабаєва, вул. Київська 1 | exact | ML footprint contains the point |
| Синагога | near (2.3 m) | OSM place of worship |
| Будинок земської управи | **probable** | Wikidata point 37 m off; the only non-shed footprint within 45 m that fronts the register address street (пров. Каштановий) — 637 m². Name only, appearance unchanged |
| Будинок М. Т. Рильського | **probable** | point 34 m off; only candidate fronting вул. Максима Рильського — 347 m². Name only |
| Костел Св. Яна Непомука | not applied | the only footprint is a merged 2,279 m² ML outline; OSM has no footprint; no open 3D source. Needs OSM mapping |

Nominatim resolves both register addresses only to the street (OSM has no house numbers there), so no
more precise open geocode exists.

## 5. Performance (`make bench`, 3 runs, M4 Pro, Chrome/ANGLE Metal, vsync off)

Numbers measured at audit time, before Visual Fidelity 2.0; current numbers: `docs/PERFORMANCE.md`.


- Static views, frame time p50 / p95 / p99: 1.1–2.9 / 2.5–3.6 / 2.6–4.2 ms; runs agree within ±0.2 ms at p95.
- Continuous 18 s flight across town at 120 m (streaming while moving, ~11,000 frames): p50 1.6, p95 2.5,
  p99 2.9 ms in all three runs.
- Tile load (fetch + parse + prepare): p50 16 ms, p95 46–145 ms, p99 ~170 ms (largest LOD0 tiles).
- JS heap during the flight 182–247 MB, no growth across runs.
- **Defects found**: (a) a one-frame stall after the first close-up (shader compile, 450 ms) → shader
  warm-up at start-up (now ≤ 90 ms); (b) per-frame ground-clamp raycast and minimap redraw → throttled;
  (c) tile vertex data −34 % (normals derived in the shader, colours as 16-bit).
- **Remaining**: an *instant* camera teleport (used only by test scripts) still causes one 90–310 ms
  GPU-upload stall; animated fly-to (what users get from search/minimap) peaks at 7.5 ms in 2 of 3 cases
  and 58 ms in one.

## 6. Tests

See README "Status" for the final run (pytest, vitest, Playwright e2e, typecheck, production build).
