# Geographic accuracy

What is real, what is inferred, and how sure we are. The viewer's *Geometry source* / *Height source*
modes and the building info panel show this per building; this page explains the categories.

## Coordinate reference

| Frame | Definition |
|---|---|
| Geographic | WGS84 lon/lat (EPSG:4326), `always_xy` axis order (lon, lat) |
| Metric | UTM zone 35N, EPSG:32635 (Skvyra at 29.66° E is inside zone 35, 24–30° E) |
| Local | metric minus origin **E 691900, N 5512000** (= 49.729863° N, 29.662926° E, boundary centroid rounded to 100 m); x = east, y = north, z = up, metres |
| glTF / three.js | x = east, y = up, z = −north (a proper rotation of local: triangle winding preserved) |
| Vertical | heights above the EGM2008 geoid (the DEM's datum); not ellipsoidal heights |

Round trips are tested: Python local↔WGS84 < 1 µm; the browser's own UTM implementation (Krüger series)
matches pyproj to < 1 mm forward and < 1e-8° inverse at 8 reference points. Local coordinates stay within
±3.5 km, so float32 GPU vertices keep sub-millimetre precision.

## Categories

| Category | Meaning | Where |
|---|---|---|
| **Verified** | Value confirmed by a person (override with `verified: true`) | none yet |
| **Source-derived** | Taken from OSM (human-mapped): footprints of 1,878 buildings, all roads, water, land use, 45 trees | blue in *Geometry source* mode |
| **Inferred** | Machine-detected (Microsoft ML footprints: 6,111 buildings) or rule-based (heights, roofs, widths) | orange |
| **Satellite-estimated** | Heights from GHSL (2018, ~90 m cells): an *estimate*, never a measurement | purple in *Height source* mode |
| **Decorative** | Visual detail with no geographic claim: window grids, wall/roof colours, chimneys, ground-texture noise, "decorative" garden trees, procedural trees inside mapped forests/parks/orchards | not coloured; decorative trees can be switched off ("Декоративні дерева (вигадані)") |
| **Missing / invalid** | Quarantined features (red markers), e.g. a bank mapped as a point | red |

## Accuracy by layer

| Layer | Horizontal | Vertical / shape | Basis |
|---|---|---|---|
| OSM building footprints | ~1–2 m | — | OSM and ML outlines agree to 0.82 m median, no systematic offset (1,428 pairs) |
| ML footprints | ~1–2 m, some merged/split buildings | — | Microsoft; imagery date unknown; may include demolished or miss new buildings |
| Building heights | — | **Mostly estimates.** 1 from `height`, 8 from `building:levels`, 1 from a photo; 4,246 from GHSL satellite evidence (low confidence); 3,733 type defaults (lowest). Full table in DATA_SOURCE_AUDIT §7 | per building: `hM` (method) + `hC` (confidence) + the GHSL value used |
| Roofs | — | **All inferred** (0 OSM roof tags) except 2 photo estimates: rule-based families per building type and plan shape, multi-part roofs on 732 complex footprints | `roofM`, `roofMat`, `parts` |
| Roads | Centre lines ~1–3 m (OSM) | Widths inferred from class (0 width tags) | curves preserved: OSM geometry is used without simplification; buffered and unioned per surface class |
| Water | OSM polygons; streams buffered by class width | water surface follows the DEM (not flattened) | |
| Terrain | 30 m posting, resampled to 10/25/50 m lattices | GLO-30 is a radar **surface** model. A DTM is approximated: under mapped buildings and woodland (18,300 of 104,260 cells, dilated by one cell) the DSM is replaced by its 3×3 morphological opening, lowering those cells by 0.5 m on average (p99 4.9 m, max 13.4 m at the grain elevator); open ground is untouched. Then spike removal and 3×3 smoothing. Expect ~2–4 m vertical error | `data/processed/terrain_report.json`; relief 181–248 m |
| Ground colour | 10 m (Sentinel-2, 18 July 2026) | Real colour of fields, gardens and woods; roof pixels around known footprints are replaced from neighbouring ground (no roof "halos"); unknown buildings may still show as reddish smudges | context only, never geometry |
| Boundary | OSM relation 3811860 | — | not a cadastral line |

## Height method details (GHSL)

GHSL ANBH is the average *net* height of the built surface in a ~90 m cell, from 2018 satellite data. It is
used only where that meaning transfers to a single building (`pipeline/skvyra/heights.py`):

- the building covers ≥ 60 % of the cell's built area (and ≥ 150 m²): eave = ANBH − half the roof rise;
- every building in the cell is < 250 m²: 2 storeys if ANBH ≥ 7 m, else 1 (21 % of those houses get 2);
- tagged apartment blocks without levels: storeys = max ANBH in the 3×3 neighbourhood / 3 m (3–9);
- values at the product floor (≤ 2.6 m) are ignored. OSM tags and overrides always win.

There is no ground truth in Skvyra to calibrate these rules (no house in OSM has `building:levels`); they
are documented assumptions with "low" confidence.

## How elements are kept consistent

- Buildings sit on the *same* triangulated terrain surface that is rendered (per LOD). Foundations go
  0.5 m below the lowest ground point under the footprint, so no building floats on slopes (tested).
- Roads and water are split along the terrain lattice and its diagonals, so every piece lies exactly on a
  terrain triangle plus a small offset (tested: area preserved, z = terrain + offset).
- Neighbouring tiles sample one global lattice: shared edges are bit-identical (tested), skirts hide
  gaps between tiles shown at different LODs.

## Known inaccuracies (honest list)

- Buildings outside OSM's well-mapped south-west are ML outlines with type-default heights: the skyline
  is plausible, not measured.
- 23 % of residential street length has no building at all within 60 m in either source.
- Landmarks: the lyceum and Babaiev mansion use photo-estimated colours/roofs (domes, dormers not modelled);
  the Catholic church's only footprint is a merged ML outline, so it stays generic; two listed buildings
  have no footprint at their Wikidata coordinates.
- Roof shapes, materials, colours and window layouts are plausible for the region, not observed.
- Bridges are flat decks between the DEM heights of their ends.
- Water surfaces inherit DEM noise; rivers are not hydrologically flattened.
