# Data source audit — Skvyra (Сквира / Skvyra / Skvira)

Audit date 2026-11-12. Numbers come from `data/processed/audit.json` and `normalize_report.json` (regenerate
with `make validate`). Percentages are always *of mapped features*: there is no authoritative count of
real buildings in Skvyra, so nothing here claims "x % of the real town is reconstructed".

## 1. Boundary

| | |
|---|---|
| Source | OpenStreetMap relation **3811860** — `boundary=administrative`, `admin_level=9`, `place=town`, `name=Сквира`, `wikidata=Q70598` |
| Not used | relation 12568090 *Сквирська міська громада* (admin_level 7, the hromada: town + villages); relation 1745152 *Сквирський район* (`boundary=historic`, pre-2020 raion) |
| Geometry | 1 polygon, valid, **17.22 km²**, EPSG:4326 (processed in EPSG:32635) |
| Retrieved | 2026-11-12 (OSM database timestamp in `data/raw/manifest.json`) |
| Confidence | Community-mapped administrative line, not an official cadastral boundary. Identity is verified on every run against the expected tags in `config/city.json`; a retagged or replaced relation stops the pipeline instead of silently using another area. |
| Manual correction | put a GeoJSON polygon in `data/overrides/boundary.geojson`; it wins and is labelled "manual override". |
| Official alternative | The national address/ATU registers (КАТОТТГ code `UA32020150010094707`) identify the town but publish no open boundary geometry we could verify; the State Geocadastre's public map is view-only. |

The OSM `place=town` node (337547206) carries `population=18000`; that tag is unverified and not used.

## 2. Source comparison matrix

| Source | Coverage of Skvyra | Resolution / accuracy | Freshness | Licence / reuse | Size for AOI | Used? |
|---|---|---|---|---|---|---|
| **OpenStreetMap** via Overpass API | Whole town; buildings concentrated in the south-west and centre (see §4) | Hand-traced, typically 1–3 m | Live (snapshot 2026-11-12T14:24Z) | ODbL 1.0, attribution, share-alike for derived databases | 2.1 MB JSON | **Yes – primary** for buildings, roads, water, land use, POIs, trees |
| Geofabrik `ukraine-latest.osm.pbf` † | Same data as above | Same | Daily | ODbL | ≈ 1–1.5 GB (not downloaded) | No – Overpass bbox is 700× smaller; keep for multi-town scale-up |
| **Microsoft Global ML Building Footprints** (release 2026-02-03, quadkey 120303230) | Whole town, 7,905 footprints inside boundary | ML-detected from imagery; median 0.82 m from matching OSM outlines, no systematic shift | Imagery date per footprint unknown | CDLA Permissive 2.0 (verified in the official repo README/LICENSE) | 12.5 MB gz | **Yes – gap fill only**, labelled *inferred* |
| Google Open Buildings v3 † | Not covered (Africa, S/SE Asia, Latin America) | — | — | CC BY 4.0 / ODbL | — | No |
| Overture Maps buildings † | Repackages OSM + Microsoft (+ Esri community) | Same as inputs | Monthly | ODbL | Parquet on S3 | Not needed: we take both inputs directly |
| **Copernicus DEM GLO-30** (AWS open data COG) | Full | 30 m posting (1″), *surface* model incl. some buildings/trees; ~2–4 m vertical RMSE in open terrain † | TanDEM-X 2011–2015 | Free licence, attribution required (see README) | 335 KB window via HTTP range reads (full 1°×1° tile not downloaded) | **Yes** – terrain |
| SRTM 1″ † | Full | 30 m, older (2000) and noisier | 2000 | Public domain | ~25 MB tile | No – GLO-30 is better |
| FABDEM (buildings/forests removed Copernicus) † | Full | 30 m, bare-earth | 2022 | **CC BY-NC-SA** – non-commercial only | — | No by default (licence); could be an optional local input |
| Sentinel-2 L2A † | Full | 10 m RGB | Every few days | Copernicus open licence | ~100s MB per scene | Not yet – too coarse for buildings; possible ground-colour source |
| Esri World Imagery / Bing / Google / Mapbox satellite | Full | Sub-metre | — | **Terms forbid** bulk download / reuse in our own 3D product | — | **No** (never scraped) |
| Google Earth / Maps 3D, Street View | — | — | — | Proprietary | — | **No** |
| Public 3D building datasets for Ukraine (CityGML / LoD2) | None found for Skvyra | — | — | — | — | — |
| Official Ukrainian open geodata (data.gov.ua, ДЗК public map) | No open vector footprints or roads for Skvyra found; cadastral map is view-only | — | — | — | — | No |
| ESA WorldCover 10 m land cover † | Full | 10 m, 11 classes | 2021 | CC BY 4.0 | small | Not yet – OSM land use is used; WorldCover could fill unmapped fields/forest |

**Verification status.** Rows and figures without a mark were verified during this audit by actually
querying/downloading the data (Overpass, Microsoft index + quadkey file, Copernicus COG headers and window).
Items marked † are from the providers' published documentation as known before this audit and were **not
re-checked online**; confirm them before relying on them (especially licences) for anything public.

### Added in the second iteration (2026-11-12, all verified by download)

| Source | Use | Coverage / resolution | Licence | Size |
|---|---|---|---|---|
| **GHSL GHS-BUILT-H ANBH R2023A** (JRC), epoch 2018 | building-height *evidence* (see §7) | global, 3″ (~90 × 60 m here) | CC BY 4.0, © European Union | 50 MB tile downloaded once, 12 KB window kept |
| **Copernicus Sentinel-2 L2A** `S2C_35UPR_20260718_0_L2A` (TCI) | ground colour (fields, gardens, woods) and context ground | 10 m, cloud cover ≈ 0 %, same UTM zone | Copernicus open licence, attribution | 1.6 MB window (HTTP range reads of the public COG) |
| **Wikidata** (items located in Q70598) | landmark list + coordinates | 36 items, 15 with coordinates | CC0 | query |
| **Wikimedia Commons** photos of listed buildings | style analysis, photo estimates for 2 landmarks | see ARCHITECTURE_STYLE.md | CC BY-SA 3.0/4.0 (referenced, not redistributed) | — |

Researched and **not usable**:

- **LiDAR / open DSM for Kyiv oblast**: none found (searches of open-data portals and literature; Ukrainian
  LiDAR studies exist but no open download). Microsoft footprints carry no heights for Ukraine (all −1).
  Overture adds heights from Esri Community Maps mostly in North America; no Ukrainian coverage found.
- **Aerial / satellite imagery for roof shapes**: Esri World Imagery and Bing permit tracing *into OSM*
  only; Google/Mapbox/Maxar terms forbid reuse in a 3D product; Maxar Open Data is CC BY-NC and event-based;
  the State Geocadastre orthophoto is view-only. Sentinel-2 (10 m) cannot resolve roof shapes of 10 m houses.
  → **No legal source for per-building roof shapes exists for Skvyra**; all roofs are inferred and labelled.

### Added in Realism & Authenticity 3.0 (2026-11-14, verified by download)

| Source | Access | Size for the AOI | Licence | Used for |
|---|---|---|---|---|
| Meta/WRI High Resolution Canopy Height Maps v2 | public S3 (`dataforgood-fb-data/forests/v2/global/dinov3_global_chm_v2_ml3`), z10 quadkey `1203032300`, HTTP range read of the AOI window | 4.3 MB window (of a 58.8 MB tile) + 0.5 MB imagery-date polygons (of 8.6 MB) | CC BY 4.0 | tree candidates (`canopy.py`) |
| ESA WorldCover 2021 v200 | public S3 COG, tile N48E027, window | 51 KB | CC BY 4.0 | trust check of the canopy model only |
| Sentinel-2 L2A, 7 extra scenes (2025-06-08 … 2026-08-29) | earth-search STAC, range reads | ~14 MB | free and open | tested as a median composite and **rejected** (see REALISM_3.md) |
| Panoramax / KartaView / Mapillary | public APIs | — | per item / not stated / CC BY-SA | 0 pictures / 134 photos without a licence field / unknown without a token — none used |
| GlobalBuildingAtlas LoD1 | Hugging Face `vitrobot/GBA.LoD1` | 1.02 GB tile (monolithic JSON) | CC BY-NC 4.0 | **not downloaded** (size; would be comparison-only, outside the pipeline) |

## 3. Measured coverage (inside the boundary)

| Feature | Count | Notes |
|---|---|---|
| OSM building footprints | **1,878** | 1,705 `building=yes`, 87 house, 58 industrial, 13 residential, 7 school, 3 apartments, 3 commercial |
| Microsoft ML footprints | 7,975 in AOI | 1,793 overlap an OSM building (dropped), 3 lie on a road centre line (quarantined); **6,111 added** inside the boundary as inferred gap fill (6,179 in AOI) |
| Buildings in the model | **7,989** in boundary (8,060 incl. 300 m context ring) | reconciled in `audit.json["building_reconciliation"]` |
| OSM buildings with `height` | **1** (0.05 %) | |
| … with `building:levels` | **9** (0.48 %) | |
| … with `roof:shape` | **0** | every roof in the model is inferred unless overridden |
| … with address / name | 20 / 17 | |
| Road segments | 399 (118.5 km) | residential 71.1 km, service 12.8, unclassified 11.7, primary 9.6, track 5.1, tertiary 4.6, footway 1.4, secondary 1.3 |
| Named streets (unique) | **108** | 63.5 % of road length named; 75.7 % of non-service vehicle roads |
| Roads with `width` / `surface` | 0 / 27 | widths are inferred from road class |
| Bridges | 13 segments | |
| Water polygons / waterway lines | 29 / 38 | river Сквирка and tributaries, ponds |
| Land-use / land-cover polygons | 282 (AOI) | industrial 71, farmland 49, forest 35, water 29, wetland 15, … |
| Vegetation features | 125 polygons + **45** individually mapped trees | |
| Named POIs | 30 | |
| Quarantined | 1 | `osm:node/8824532761` ПриватБанк is mapped as a point with `building=yes` – no footprint |
| Suppressed as sensitive | 0 | no military / security features present in the AOI |

### OSM vs ML alignment (independent cross-check of geographic position)

1,428 one-to-one OSM ↔ ML matches (IoU > 0.5): median centroid offset **(+0.03 m E, +0.02 m N)**,
median absolute offset **0.82 m**, 90th percentile 2.03 m. Two independent sources agree, with no
systematic shift, so building positions are trustworthy at the ~1–2 m level.

## 4. Data-poor areas

- `docs/img/coverage_grid.png`: share of buildings that are human-mapped (250 m cells). **100 of 169**
  built-up cells have < 20 % OSM buildings – essentially the whole north, east and west of the town.
  The south-west/centre is well mapped.
- **19.4 of 82.8 km** of residential streets (23 %) have *no building within 60 m in either source* –
  e.g. вулиця Комарова, Лівобережна вулиця, Польова вулиця, вулиця Михайла Гордовенка (north-west),
  вулиця Ольги Косач-Кривинюк and вулиця Василя Кучера (south). These may be undeveloped plots or ML
  misses; without legally usable imagery we cannot tell, so nothing is invented there.
- Heights and roofs: effectively no source data (§3). Every rendered height except 10 buildings is a
  documented type default.

## 4b. Where are buildings missing? (Sentinel-2 check)

Along the 19.4 km of streets with no footprint in either source, 6.8 % of 10 m pixels have roof-like
colour (red/brown or bright grey) and 40 % are vegetation; along built streets the figures are 8.0 % and
32 %. Farmland scores 22 % "roof-like" because bare soil looks the same in true colour, so this test cannot
prove anything; it is *consistent with* unmapped houses in those streets. Nothing was added there.

## 5. Download sizes (AOI)

| Item | Bytes |
|---|---|
| Overpass response | 2.07 MB |
| Microsoft footprint quadkey | 12.5 MB (+ 7.2 MB dataset index CSV) |
| DEM window (HTTP range read of the COG) | 0.33 MB |
| GHSL height window (after cutting the 50 MB tile) | 0.01 MB |
| Sentinel-2 TCI window | 1.6 MB |
| **Total kept** | **≈ 24 MB** (peak 74 MB while the GHSL tile is processed) |

## 6. Feasibility and strategy

Feasible on this laptop end-to-end. Strategy adopted:
1. OSM is the primary, human-verified layer; ML footprints fill gaps and are always labelled *inferred*.
2. Heights/roofs are inferred by explicit, documented rules and stored with their method per building.
3. Terrain from Copernicus GLO-30 with spike removal; documented as a surface model.
4. The biggest fidelity wins now come from **mapping**, not code: adding `building:levels`/`roof:shape`
   in OSM or local overrides for the centre's landmark buildings (see ROADMAP).

## Attribution (required)

- © OpenStreetMap contributors, ODbL 1.0 — https://www.openstreetmap.org/copyright
- Microsoft Global ML Building Footprints, **CDLA Permissive 2.0** — https://github.com/microsoft/GlobalMLBuildingFootprints (corrected 2026-11-12; earlier versions of this file wrongly said ODbL)
- Copernicus DEM GLO-30 (modified: DTM correction) — produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under
  COPERNICUS by the European Union and ESA; all rights reserved.

## 7. Building attribute provenance (inside the town boundary, 7,989 buildings)

From `data/processed/building_attributes_report.json` (rebuilt with every `make build`).

| Height source | Buildings | Share | Confidence |
|---|---|---|---|
| Measured / explicit (`height` tag or verified override) | 1 | 0.01 % | high |
| From storeys in OSM (`building:levels`) | 8 | 0.1 % | medium |
| Estimated from a licensed photo of a listed building | 1 | 0.01 % | medium |
| Satellite-derived estimate (GHSL), building dominates its cell | 165 | 2.1 % | low |
| Satellite-derived storeys (GHSL), cell of small houses (1 vs 2 storeys; 21 % got 2) | 4,078 | 51.0 % | low |
| Satellite-derived storeys for tagged apartment blocks (GHSL neighbourhood) | 3 | 0.04 % | low |
| Procedural type default only | 3,733 | 46.7 % | lowest |

| Roof shape | Buildings | Share |
|---|---|---|
| Known from OSM `roof:shape` | 0 | 0 % |
| Estimated from a photo (landmarks) | 2 | 0.03 % |
| Approximate (rule-based, deterministic per building) | 7,987 | 99.97 % |
| of which multi-part roofs on complex footprints | 732 | 9.2 % |

Roof families in the model: hipped 35.6 %, gabled 32.9 %, shed 10.9 %, half-hipped 10.3 %, pyramidal 7.3 %,
flat 3.0 %. Roof materials (all assumed): metal tile 38.7 %, slate 36.0 %, corrugated 11.2 %, standing seam
11.0 %, bitumen 3.0 %.
