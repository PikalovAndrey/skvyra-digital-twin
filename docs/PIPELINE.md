# Pipeline

```
acquire ──► normalize ──► audit
   │            │
   │            └──► build (tiles × LODs) ──► viewer (apps/web)
   │                          └────────────► Blender scene / renders / .blend
data/raw      data/processed      assets/generated
```

Every stage reads the previous stage's files from disk, so any stage can be rerun alone
(`python -m skvyra.cli <stage>` or the `make` targets). `SKVYRA_DEBUG=1` shows full tracebacks; otherwise
a failure is one actionable line and exit code 1.

## 1. acquire (`pipeline/skvyra/acquire.py`, `boundary.py`)

- **Boundary**: `rel(3811860); out geom;` from Overpass; tags are checked against `config.boundary.expect`.
  The polygon is assembled from member ways (rings split across ways are handled).
- **OSM**: one Overpass query over the boundary bbox + `context_buffer_m` (300 m) for buildings,
  highways, railways, waterways, natural/landuse/leisure/amenity areas, trees and POIs (`out geom`).
- **Microsoft footprints**: the dataset index CSV gives the file for each zoom-9 quadkey covering the AOI.
- **DEM**: the AOI window (+~1 km) is read from the Copernicus GLO-30 COG with HTTP range requests and
  saved as a GeoTIFF; the 1°×1° tile is never downloaded.
- **GHSL building height (ANBH 2018)**: the 10° tile zip (~50 MB) is downloaded, the AOI window cut out,
  the zip deleted (the GHSL grid starts at 89.0996 N / −180.0079 E, not 90/−180 — verified from tile bounds).
- **Sentinel-2 true colour**: one fixed, cloud-free scene (config `sentinel2.item`) is read as a window from
  the public COG archive; the scene must be in the project's UTM zone (checked).
- Robustness: retries with exponential backoff on 429/5xx/timeouts (honours `Retry-After`), fail-fast on
  other HTTP errors, Overpass `remark` (silent timeouts) and schema changes are errors, downloads land in
  `.part` files and are renamed atomically, truncated downloads are detected by `Content-Length`.
- Provenance: `data/raw/manifest.json` records url, query, retrieval time, source timestamp, sha256,
  licence and size for every file. A cached file whose checksum differs is treated as corrupted.
- `--offline` never touches the network and fails if something is missing; `--refresh` re-downloads.

## 2. normalize (`normalize.py`, `osm.py`)

- OSM elements → shapely geometries in the local metric frame. Closed ways become areas only for area
  tags (`highway`, `barrier`, `man_made=goods_conveyor` … stay lines).
- Repair: `make_valid` + canonical orientation; polygonal parts kept; parts < 4 m² quarantined.
- Quarantine (`quarantine.geojson`, with reason): buildings mapped as nodes, unclosed rings, missing
  node references, unrepairable polygons, tiny parts. More than 5 % unrepairable stops the pipeline.
- Duplicates: identical geometries (1 cm precision) are dropped and counted.
- Sensitive features (`landuse=military`, `military=*`, substations, bunkers) are never reconstructed;
  the count is reported.
- Conflation: Microsoft footprints are added only where < 10 % of their area is covered by an OSM
  building; cross-quadkey duplicates are dropped. ML footprints crossed by a vehicle road centre line
  (> 2 m inside) are quarantined (`ml_footprint_on_road`; 3 in Skvyra): OSM roads win.
- Shops / services: OSM `shop`, `craft`, `office` and service `amenity` points are attached to the
  building that contains them (`poi`), which selects the commercial facade.
- Stable ids: OSM features keep `osm:<type>/<id>` (`#n` for split multipolygon parts); ML footprints get
  `ms:` + hash of quantized centroid and area. The internal id is `b` + sha1(source id). Overlapping
  footprints (> 50 %) are flagged, not deleted.
- Output: layered GeoJSON in WGS84 (`buildings`, `roads`, `railways`, `waterways`, `areas` with a
  normalized `cover` class, `trees`, `pois`, `quarantine`) + `normalize_report.json`. Output order is
  sorted by id → deterministic files.

## 3. audit (`audit.py`)

Coverage statistics (`audit.json`), OSM↔ML alignment, a 250 m coverage grid and diagnostic maps
(`docs/img/diagnostic_map.png`, `diagnostic_center.png`, `coverage_grid.png`).

## 4. build (`tiles.py`, `terrain.py`, `buildings.py`, `surfaces.py`, `mesh.py`, `overrides.py`)

Per tile (`tile_size_m` = 500 m, cells aligned to the local origin, id `t<ix><iy>` e.g. `t+0-1`) and per LOD:

| LOD | Terrain lattice | Ground texture | Buildings | Roads / water / rail | Trees (viewer) |
|---|---|---|---|---|---|
| 2 – city overview | 50 m | 128 px JPEG | simplified prisms, flat tops at mid-roof | painted into texture only | – |
| 1 – neighbourhood | 25 m | 512 px | full roofs, window UVs | draped meshes | high quality only |
| 0 – close-up | 10 m | 1024 px | full roofs + chimneys | draped meshes | yes |

- Terrain: first `prepare_dtm` (once per build) approximates a bare-earth DTM from the DSM by a 3×3
  morphological opening under mapped buildings and woodland only (`data/processed/dtm.tif`,
  `terrain_report.json`); then no-data fill, 3×3-median spike removal, 3×3 smoothing, a global lattice with
  a fixed diagonal, skirts (6 m) on tile edges.
- Buildings: attributes resolved once (height/roof/kind/facade/roof material with provenance and confidence,
  using GHSL evidence (`heights.py`) and matched landmarks (`landmarks.py`, `config/landmarks.json`);
  deterministic colours seeded by id + `config.seed`). Roofs are planned once (`buildings.plan`: rectilinear
  decomposition into parts, per-part roof planes) and meshed per LOD; owned by the tile containing the
  footprint's representative point. Vertex attributes: `_FEATURE_ID`, `_STYLE` (facade / roof material),
  wall UVs (bays, floors), roof UVs (metres along eave / up slope).
- Roads: buffered by width (tag > lanes × 3.25 m > class default), unioned per surface class (paved /
  unpaved / foot) so junctions are one coherent surface; bridges become separate decks
  (`surfaces.bridge_mesh`: deck in the road material, fascia/parapets in `bridge`; built once, in the tile holding the
  bridge midpoint; neighbouring road caps are trimmed at the deck ends).
- Draping: polygons are split along terrain cells and diagonals, triangulated (constrained Delaunay) and
  lifted to terrain + offset.
- Ground textures: Sentinel-2 colour (graded, roof pixels around known footprints in-painted from nearby
  ground) as the base, land cover only tints it, water/rail/roads painted crisply, a soft baked contact
  shadow around buildings, world-space value noise (continuous across tiles).
- Context ground: `context.glb`, a 120 m terrain sheet with the satellite colour over AOI + 1 km, with a hole
  under the built tiles (helper, not reconstruction).
- Reports: `building_attributes_report.json` (height/roof provenance counts), `landmarks_report.json`.
- Trees: mapped trees + procedural trees only inside mapped forest/park/orchard/cemetery/scrub/wetland,
  on a global jittered lattice (seamless, deterministic), never inside buildings/roads/rail/water;
  optional decorative garden trees near houses (labelled, switchable).
- Output: `tiles/<id>.lod{0,1,2}.glb` (custom attribute `_FEATURE_ID` = index into the tile's building
  list), `tiles/<id>.json` (building metadata, trees, data issues), `tiles/index.json` (tiles, bounds,
  elevation range, file sizes, sources/licences, boundary line with terrain heights), `search.json`.
- Incremental: each tile has a hash of its inputs (features, overrides, DEM checksum, config, pipeline
  code). Unchanged tiles are skipped; `--force` rebuilds; `--tiles` builds a subset. The tile's JSON is
  written last, so a crash never leaves a tile that looks complete. Failed tiles are listed with
  tracebacks, recorded in `index.json["failed"]`, and the stage exits non-zero; rerunning retries only them.

## 5. Blender (`blender/scripts/build_scene.py`)

Headless: imports tiles into collections (Terrain, Buildings, Roads, Vegetation, Water, Landmarks,
Lighting, Cameras, Helpers), replaces glTF materials with shared procedural ones (window mask from the
facade UVs, emissive windows at night), instances trees with geometry nodes, adds a labelled horizon
ground, sky + sun presets (day / sunset / night) and cameras (overview / oblique / street / flyover with
smooth Bézier keys). Eevee previews, Cycles (Metal GPU) stills, `.blend` saved for editing.
`make render`, `make flyover`, `make export`.

## Configuration (`config/city.json`)

Boundary relation + expected tags, CRS, fixed origin, context buffer, tile size, floor height, seed,
endpoints, licences. Rules that are style rather than geography (roof choice, palettes, road widths) are
module-level constants documented in the code and in GEOGRAPHIC_ACCURACY.md.

## Overrides (`data/overrides/overrides.json`)

See the module docstring in `overrides.py`. Overrides are keyed by stable source id, validated
(allowed fields, roof shapes, plausible heights), re-matched by `anchor` if the source id disappears, and
any that cannot be matched are reported in `data/processed/overrides_report.json` (never dropped).
Changing an override rebuilds only the affected tile.
