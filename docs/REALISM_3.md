# Realism & Authenticity 3.0 — real vegetation, first photo-based landmark part

Goal: recognisable Skvyra from *observed* data rather than more procedural effects. Rules kept: footprints,
coordinates, water outlines and the road network are unchanged; every new source carries provenance; generation
is deterministic; the decorative layer stays switchable. Before/after images: `docs/img/vf3/` (16 viewpoints,
same cameras as Visual Fidelity 2.0 plus `park`, `yards-sw`, `lyceum-tower`).

## Phase 0 — data discovery (2026-11-14)

| Source | Result for Skvyra | Decision |
|---|---|---|
| **Meta/WRI Canopy Height Map v2** (CC BY 4.0, AWS open data) | one z10 tile `1203032300` (58.8 MB COG); read as a 4.3 MB window with HTTP range requests. ~0.77 m ground pixels, uint8 metres. Source imagery: **2015-06-24 for 84 % of the town, 2011-04-28 (leaf-off) for 16 %** (per-area dates file). Defects found: constant 10 m *model-fill plateaus* (5.2 km² of the window, 3.7 km² inside the town, on the outskirts); vegetation planted/grown after 2015 is missing | **used** (Phase 1), with three trust masks |
| **ESA WorldCover 2021** (CC BY 4.0) | 51 KB window, 10 m classes | **used** as an independent, newer check of the CHM (tree class), not as geometry |
| Sentinel-2 multi-date median (7 cloud-free summer scenes 2025–2026, SCL cloud mask) | removes single-day noise, but colours turn grey-olive and fields blend different crop years; only 223 isolated bright artefact pixels exist in the single 2026-07-18 scene | **rejected** (code removed); the single pinned scene stays |
| Wikimedia Commons | lyceum 27 files (2010–2025), kostel 8, zemstvo 16, Babaev mansion 1 (identification marked doubtful on Commons), synagogue 25+ | lyceum tower modelled (Phase 2) |
| Panoramax | 0 pictures in the town bbox (nearest coverage ~90 km, Kyiv area) | nothing to use |
| KartaView | one 2019-11-22 sequence, 134 photos along ~600 m near the lyceum/zemstvo; the public API gives **no licence field** | not used (licence not verifiable) |
| Mapillary | coverage cannot be determined without a token (API returns "Invalid OAuth"); a free account would give image ids, dates, compass angles (CC BY-SA 4.0) | not used; ask only if needed |
| GlobalBuildingAtlas (CC BY-NC 4.0) | the LoD1 tile covering Skvyra (`e025_n50_e030_n45.json`) is a monolithic **1.02 GB** JSON (no windowed read) | comparison **not run**: needs approval for the download size. If approved it stays in a separate script and report, never in the pipeline |

## Phase 1 — real vegetation (`pipeline/skvyra/canopy.py`)

A CHM is a raster, not trees. Per pixel trees would be wrong, so:

1. Reproject the window to the local metric grid (0.75 m); smooth (σ ≈ 0.9 m).
2. **Tree tops** = local maxima with a height-dependent window (1.5 m for < 6 m trees … 3.75 m for > 18 m);
   minimum 3 m (lower = shrubs/hedges/noise, not modelled as trees).
3. **Crown radius** = mean of 8 rays down the crown flanks (stop at half the top height, at 2 m, or where the
   surface climbs into a neighbouring crown), capped at 0.35·h + 1 m.
4. **Merged crowns**: non-maximum suppression, tallest first — a candidate inside 0.7 × the crown radius of a
   taller accepted tree is dropped (one tree per dominant top, not one per pixel).
5. **Rejected**: tops on present-day buildings (+0.5 m; also catches change since 2015), water, the carriageway
   core, rail, bridges; 368 street-edge candidates are dropped at tile level for standing on inferred road surface.
6. **OSM trees** (45): one tree, OSM position, CHM height and crown.
7. **Trust masks** (outside them the VF2 land-cover rules stay as fallback):
   * *model fill*: 24 m blocks where > 35 % of pixels sit on a constant-height plateau;
   * *outdated*: coherent patches where WorldCover 2021 sees > 60 % tree cover but the CHM < 5 % (e.g. the
     centre of Парк культури, a plantation in the south-west) — 1.19 km²;
   * *newer OSM mapping*: OSM orchards/forests with < 10 % CHM canopy (14 polygons, 0.93 km² — mostly orchards
     mapped recently; trellis rows or planted after 2015). Real forests agree: CHM 65–100 %, WorldCover 70–99 %.
8. Per tree: height, crown radius, `source=chm_v2`, imagery date, confidence (`medium` for leaf-on imagery and
   ≥ 5 m, else `low`; never `high`). Crown *shape* (broadleaf / conifer / fruit) is inferred appearance only —
   the CHM has no species.

Pilot first (residential quarter t-2-2/t-2-1 + Парк культури t-1-1/t-1+0), compared with the rule-based trees,
then city-wide.

| | VF2 | 3.0 |
|---|---|---|
| Trees in the model | 47,218 | 50,569 |
| … backed by observed data (CHM or OSM) | 45 (0.1 %) | **23,514 (46 %)** |
| … land-cover rule (forest/park/orchard polygons where the CHM is not trusted) | 41,276 | 25,684 |
| … decorative garden layer (switchable) | 5,897 | 1,371 (garden shrubs + untrusted areas) |

Visible effect: yards and streets now have trees where trees were (size varies with the real height), parks
and squares are open where the CHM is open, rule-based "lattice forests" remain only where data says the CHM is
not reliable. In the viewer, *Розфарбування → джерело геометрії* colours trees by provenance (teal = canopy
model, blue = OSM, orange = rule-based).

## Phase 2 — landmarks

| Landmark | What the evidence allows | Done |
|---|---|---|
| Lyceum (boys' gymnasium) | domed corner tower clearly visible on 2010–2025 photos; position inferred: the tower close-up shows both adjoining walls receding symmetrically (convex corner) and a geotagged 2019 photo taken 5 m from the south-west corner of the south wing shows the adjoining facades → three-quarter cylinder centred on that corner; Ø ≈ 7 m (5 window bays on the visible half-circumference), wall 0.6 m above the eaves, white drum with 8 round windows, ribbed grey metal dome | **modelled** (`config/landmarks.json` → `features`, `buildings.rotunda_mesh`), all dimensions labelled photo estimates |
| Kostel of St John of Nepomuk | no footprint: the only outline is a merged 2,279 m² ML blob; photos show a single-volume hall with a low gable roof and **no tower** | not modelled — inventing a footprint would break the geometry rule. Needs mapping in OSM |
| Zemstvo building | the nearest plausible footprint is 37 m from the Wikidata point, across a road, and its compact cross shape does not match the long risalit front on the photos | kept as *probable* (name only, appearance unchanged) |
| Babaev mansion | one photo whose identification Commons itself marks as doubtful | no change |

## Phase 3 — performance (targeted)

The CHM trees doubled triangles in close views (the broadleaf crown has ~500 triangles). Fix without
rewriting: **tree detail by distance** — full crowns within 220 m of the camera (per tile, with hysteresis),
one-blob crowns (~5× fewer triangles) beyond and in LOD1 tiles. Measured (M4 Pro, see PERFORMANCE.md):
`centre low angle (high)` 5.35 M → 2.69 M triangles with the CHM trees (before the orchard rule), p95/p99
back at the VF2 level in the same session. A 4× CPU-throttled profile (CDP; GPU *not* throttled, not a
weak-device test) shows no regression in static views; tile load p95 rises (trees are built on the main
thread). Final paired run under heavy external load (Android emulator): +0–5 ms p99 in tree-heavy views, every
view p99 < 23 ms.

## Phase 4 — ground

Sentinel-2 multi-date composite and WorldCover-based ground colour were tested and **not adopted** (see Phase 0):
the single real scene is more faithful than a blended one. The ground remains 10 m Sentinel-2 — the main
remaining visual limit, with no legal higher-resolution imagery available.

## Honest assessment: how much more recognisable?

* **From the air (district scale)**: clearly better. Tree positions and heights now follow reality for 46 % of
  the trees, gardens and streets look like the real town's green structure, and the fake uniform "forests" in
  parks are gone where the data contradicts them.
* **The lyceum**: recognisable for anyone who knows it — the domed corner tower is its signature.
* **Street level / private houses**: unchanged in substance. Roofs, facades, fences and yard layouts are still
  inferred; nothing in open data shows them.
* **Distance to Google Earth 3D**: still large; it comes from photogrammetry of real roofs and facades, which no
  open source provides for Skvyra.
