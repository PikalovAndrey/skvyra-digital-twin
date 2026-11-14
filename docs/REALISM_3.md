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

---

# Authenticity 3.1 (2026-11-14) — the synagogue, street photos re-checked

## Synagogue (Q9350086)

**Status**: a historic building, **not a protected monument** — it appears only in the Wiki Loves Monuments
"Jewish heritage" list (ID 99-322-4001) with "охоронний номер: (не охороняється)", and Wikidata has no heritage
designation (P1435). The model therefore does not mark it as heritage; the WLM id is kept as `wlm_id` with that note.
(The other landmarks' heritage flags rest on Wikidata P1435: local architectural/historic monument designations.)

**Photo credits** (all CC BY-SA 4.0, Wikimedia Commons, referenced not redistributed): dimensions from Michael
Byalsky (2019, 4 files), Oleksandr Mykhaylyk (2019) and Xsandriel (2025); identity confirmed additionally on photos
by Kiyanka (2015), Ростислав Маленков (2007) and Gabbai26 (2008). Per-file list: `config/landmarks.json →
photo_credits`. Limitation: the viewer links the files (author and licence are on each file page) and shows the
author line; any published image derived from these photos would itself have to be CC BY-SA.

**Identity checks**: the Commons category *Synagogue in Skvyra* is the item's P373; a 2025 photo geotag lies
inside the OSM footprint (`osm:way/953038401`, 672 m²) and the Wikidata point 2.3 m from it; 2007–2025 photos by
six authors (all CC BY-SA 4.0; 9 interior and 4 archival files ignored; three of them were used for dimensions, see credits above) show the same building — pink paint in
2008, red brick after the renovation of ~2018–19. The OSM outline (51 × 12 m with a 9 × 5.6 m projection at the
west end) matches the front seen on the photos.

| | Observed on the photos | Estimated (frontal photos, scale from door heights ≈ 2.2–2.4 m) |
|---|---|---|
| Layout | three blocks on a south front: west block with its gable to the south (= the outline's projection), central block, prayer hall | cut of the real outline at 9 m and 27 m from the west end |
| West block | 2 storeys over a rubble-stone semi-basement; Star-of-David window in the gable; door at the top of a stone stair along the facade | eave 5.9 m, ridge +2.8 m, plinth 2.1 m |
| Central block | 2 storeys over the same plinth; stepped brick attic with a niche over the entrance; stone stair descending east | eave 5.6 m, hipped roof +2.2 m; attic 5.8 → 3.4 → 1.4 m wide, up to 2.4 m above the eave |
| Prayer hall | one tall storey at street level, a door and 8 tall round-arched windows | eave 4.5 m, hipped roof +2.0 m, ~2.7 m window bays |
| Materials | red brick (#c96a43), grey rubble-stone plinth, brick corbel frieze under the eaves, dark brown metal-tile roofs (west/central), dark grey roof (hall) | colours sampled from sunlit photo areas |
| Not visible → not modelled | north facade details, courtyard annexes, roof plan behind the front, real basement window positions | |

Implementation: `config/landmarks.json → composite` (blocks + features) → `buildings.composite_parts / composite_details`,
used by the tile builder for any landmark with a `composite` spec. The outline is cut, never changed (unit test
checks the areas). Windows on the plain gables are switched off and window rows start above the semi-basement;
decorative (invented) fences are kept 12 m away from landmarks rebuilt from photos. Photos are references only:
nothing is copied into textures, and the photo/model comparison image stays outside the repository.

## Street-level photos

| Service | Finding |
|---|---|
| KartaView | Licence **verified**: the official terms (served by kartaview.org) license user imagery under **CC BY-SA 4.0**. The Skvyra material is one sequence (`3133530`): 134 GoPro-style fisheye frames, 2019-11-22 15:49–15:51, dusk, ~11 m/s along ~600 m from the lyceum to Каштановий провулок. **Caveats**: the sequence status is `deleted` (removed on KartaView, frames still served by the nearby-photo API); the API exposes only a numeric user id (no author name for attribution); positions/headings are track-derived (±10–20 m). Verifiable from the frames checked: the kindergarten «Світанок» (the Rylskyi house, Q122249375) is a one-storey terracotta building with white corner pilasters and a stone-pillar fence — consistent with the Commons-based attributes already used — but its footprint cannot be pinned: the camera stands ~20 m from the nearest mapped outline, so the building may be unmapped. **Not used in the model** (deleted sequence, no attributable author); kept as a lead for OSM mapping. |
| Panoramax | 0 pictures (unchanged) |
| Mapillary | not checked further (needs a token) |

## Results (3.1)

* Before/after: `docs/img/a31/web-synagogue-front.jpg` (street view, new viewpoint), `web-synagogue.jpg` (district view).
* Tests: 123 Python (new: composite landmark cut + outward-facing details), 34 vitest, tsc 0, e2e 6; `make qa` 0 defects.
* Benchmark tree build time per tile is now recorded (`treeBuildMs`); paired 2.0 vs 3.1 runs: see PERFORMANCE.md.

---

# Authenticity 3.2 (2026-11-14) — heritage audit, identity checks, camera jumps, missing-outline layer

## Phase 0 — heritage status audit

`config/landmarks.json → heritage` now has `status` ∈ {registered_monument, newly_identified, not_registered,
unknown}, the status *reported* by secondary sources, the official documents that mention the object, and the
check date. Official sources found and read: the Ministry of Culture list of national-significance monuments of
Kyiv Oblast (state 20.09.2024 — no Skvyra building), the Kyiv Oblast State Administration's 2017 inventory of
architectural monuments (lyceum, Babaev mansion, zemstvo building, kostel, Київська 1 — category not stated) and
its 2021 list of history monuments (Rylskyi house, lyceum). No State Register decision (number/date) for any
local object was available, so **every official status is `unknown`**; protection numbers (78/79/81-Ко, 1296) and
the kostel's "newly identified" (А-щв) come only from the Wiki Loves Monuments list and are shown as "reported by
secondary sources, not officially verified". The synagogue's WLM id is not a protection number. The viewer shows
an official badge only for confirmed statuses (none at the moment).

## Phase 1 — zemstvo building: identity NOT confirmed → not reconstructed

For the ML candidate: it fronts пров. Каштановий (address 8(10); the 2017 inventory gives "просп. Володарського,
8" = the same lane), and a geotagged 2019 Commons photo was taken on the lane 17 m away. Against: the candidate's
lane front is ~35 m with 5–8 m projections, while photos show a straight ~45 m front (14–16 window axes) with
shallow risalits; the WLM/Wikidata point lies across the lane in a block that ESA WorldCover classes as built-up,
without tree canopy and without any footprint in OSM or Microsoft — the real building may be unmapped there; the
photo geometry fits both sides (dusk, no shadows). Kept as `probable`; the viewer now labels such matches
"probable match, not confirmed" and shows the audit. Needed: a mapped outline on the NW side, or a photo with a
camera bearing / a landmark of the other side.

## Phase 4 — Rylskyi house: identity NOT confirmed → unchanged

The official 2021 list places it "on the corner of вул. Максима Рильського and Володарського" (= пров.
Каштановий). The ML candidate is the only outline within 60 m of that crossing (south corner), but the crossing's
other corners have no outline in any source although they are in the town centre; the WLM point is on the west
corner, and the KartaView frame with the «Світанок» plaque at arm's length was taken on the lane ~15 m from that
point. The photos show an L-shaped house, the candidate is a 15 × 23 m rectangle. Unchanged; the evidence that is
missing is recorded in `identity_audit`.

## Phase 2 — camera jumps: profiled, the 300–700 ms stall is a benchmark artefact

Per-stage instrumentation (`stageMs`: fetch, GLB decode, prepare, texture upload; `updateMs`: activations per
update) plus WebGL call timing during 8 jumps:

* GLB parsing is asynchronous and not the cause (prepare < 1 ms per tile).
* The entire stall was **one `texSubImage2D` of a 1024² ground texture** (380–1,230 ms), the first upload after a
  jump. Uploading raw pixels decoded in a Web Worker instead of an ImageBitmap changed nothing (530–990 ms) →
  removed again.
* It is a **GPU-queue wait**: `benchmark.spec.ts` runs Chrome with `--disable-gpu-vsync --disable-frame-rate-limit`,
  so the CPU queues frames far ahead of the GPU (and other processes were using the GPU); the first upload waits
  for that backlog. With normal browser frame pacing (what users get) the same jumps cost **18–35 ms**.
* New `e2e/teleport.spec.ts` (BENCH=1) measures jumps with normal pacing; paired on an idle machine
  (9b6b330 code vs current, ×2): worst frame after a jump p50 18.2–18.9 ms, p95/max 32.7–35.4 ms (one 72 ms
  outlier in one current run). The p99 < 50 ms target holds; no loader rewrite was justified.

## Phase 3 — data-quality layer: probably missing building outlines

`pipeline/skvyra/quality.py` samples the 87.3 km of residential streets in the boundary every 20 m: covered (an
outline within 60 m) 75.8 %; **probable gap 11.9 % (10.4 km)** — no outline but ESA WorldCover built-up ≥ 20 %
within 40 m (confidence medium ≥ 40 %), often under tree canopy; insufficient information 4.8 %; no signs of
buildings in 10 m land cover 7.5 % (not proof of absence). Viewer: layer "Якість даних: де, ймовірно, бракує
контурів будівель" (off by default) draws coloured lines along the streets — never buildings — and a panel with
the statistics and the method. Alternatives checked: Overture Maps 2026-10-21.0 buildings over Skvyra (read as 30
vector tiles, 1.4 MB) contain the same OSM + Microsoft outlines — 1 extra OSM building, closing 8 of 1,063 empty
sample points; GlobalBuildingAtlas not downloaded (1 GB, CC BY-NC).

Images: `docs/img/a32/gaps-district.jpg`, `gaps-street.jpg`, `info-zemska.jpg`.

---

# Authenticity 3.3 (2026-11-14) — geographic overrides, review workbench, central gaps

## Local building outlines (geometry overrides)

The existing override system (`data/overrides/`, `pipeline/skvyra/overrides.py`) was extended rather than duplicated:
attribute corrections stay in `overrides.json` (keyed by source id, re-matched by anchor); **outlines** go to
`footprints.geojson` because they have no source id to attach to.

| Rule | Implementation |
|---|---|
| Separate from sources | own file; OSM / Microsoft downloads and `buildings` from them are never edited |
| Format | GeoJSON FeatureCollection, RFC 7946 (WGS84 lon/lat is the CRS of record); Polygon, MultiPolygon, holes |
| Provenance | stable `local:<slug>` id, status, confidence, method, date, author, evidence (url, licence, author) |
| Confirmed vs approximate | only `confirmed` + evidence (url and licence each) + `approval {by, date, basis, checks}` enters the model (source `local`, colour "verified" when confidence is high); `approximate` goes to `processed/review_footprints.geojson` for the workbench only |
| Checks | schema; GEOS validity (self-intersection), plausible area; duplicates between local outlines; overlap with source outlines (> 1 m², unless listed in `replaces`); crossing a road centre line |
| Source updates | `replaces` entries re-match by anchor when ids change; if OSM later maps the building (> 50 % overlap), the OSM outline wins and the local one is reported as superseded — the file is kept |
| Approval | the workbench exports only `approximate`; `review apply` refuses a `confirmed` add; promotion is a separate `approve_outline` change naming who checked, the basis of the geometry (`site_survey` / `licensed_imagery` / `official_document` / `open_dataset` — a photo or a point is not a basis) and what was compared |
| Landmarks | `wikidata` on a confirmed outline binds the landmark (`confirmed_local`); `overrides.json → landmark_matches` rejects wrong candidates |
| Tiles, LOD, Blender | local outlines enter at the normalize stage like OSM / ML, so tiles (LOD0-2), QA, search and the Blender scene (built from tiles) need no special case |

Report: `data/processed/footprints_report.json` (applied, review-only, conflicts, superseded, replaced).

## Mapping review workbench

`review.html` (served with the viewer): priority list → per item a north-up 2D map with OSM / Microsoft / local
outlines, the candidate highlighted, streets, WLM/Wikidata points, photo camera geotags and missing-outline lines;
address, why the match is unconfirmed, what is needed, photo links. A reviewer can draw an outline (in-browser
checks: self-intersection, area, overlaps, required method; the outline is always exported as approximate), reject a candidate match,
review the pending change set, undo entries and download it. Nothing is written from the browser:
`skvyra review apply changes.json` validates with GEOS against the current data and prints the diff (dry run);
`--yes` writes with a timestamped backup in `data/overrides/history/`; `skvyra review revert` restores it.

Prototype verified on synthetic data (integration test): dry run (no write) → invalid bow-tie refused → `--yes` →
rebuild → the confirmed outline is in the tiles → revert restores the file. On the real city only a dry run was
made (rejecting the zemstvo candidate, not written): no unconfirmed building was added.

## Priority list: geographic gaps in central Skvyra

| # | Object | Likely location | Confirmed references | Photos | Existing candidates | Why not confirmed | Needed |
|---|---|---|---|---|---|---|---|
| 1 | Zemstvo building (1911-12) | пров. Каштановий 8(10): either the ML candidate SE of the lane or an unmapped building NW of it (WLM point) | the lane (OSM), address in WLM and the 2017 oblast inventory, 2019 camera geotag on the lane | 16 (5 authors, 2007-2025) | ms:aa972e1721d8 | front ~35 m with 5-8 m projections vs ~45 m straight front; WLM point in an unmapped built-up block | an outline of the real building (survey / OSM mapping from licensed imagery) or a photo with bearing |
| 2 | Rylskyi house | corner of вул. М. Рильського and пров. Каштановий (official 2021 list) | both streets (OSM), the crossing | 3 (WestBam 2019, camera geotags) | ms:c892328fbbee (south corner) | the other corners have no outline at all; WLM point and plaque frame point to the west corner; L shape vs rectangle | outlines of the west-corner buildings |
| 3 | Kostel (1885) | вул. Богачевського 9 (WLM, 6-decimal point) | the street, the point inside the blob | 8 (3 authors) | ms:1ca1695635ed (2,279 m² merged blob) | blob merges the nave with annexes / neighbours | an outline of the nave alone |
| 4 | Babaev mansion | вул. М. Рильського 32 | point inside the outline | 1 (identification doubtful) | ms:4801e63e6c77 | identity, not geometry | an independent second photo |
| 5-9 | Longest probable gaps within 1.2 km of the main square: вул. Ольшевського (292 m), Захисників України (215 m), Карла Болсуновського (200 m), Співоча (185 m), М. Рильського (180 m) | along these streets | streets (OSM) | — | none | no outline within 60 m, built-up land cover nearby | outlines from a legal source or survey |

No outline was created from a single Wikidata point or from 10 m land cover.

## Additional sources (bounded search, 2026-11-14)

| Source | Finding | Use |
|---|---|---|
| Historical town plans on Commons (ДАКО 1542-1-1972/1973: 1815, 1826; public domain) | street layout of the early 19th century | predate the zemstvo (1911) and the kostel (1885): cannot verify their outlines; possible later use for street history only |
| General plan of Skvyra (urban-planning documentation, public by law) | not found online (only a 2013 council decision referenced on e-construction.gov.ua) | — |
| data.gov.ua / Skvyra community open data | no building or address datasets found | — |
| Public cadastral map | land parcels, not buildings; access limited and licence unclear | not used |
No new usable outline source; the search was stopped there. (OSM, Microsoft, Overture, GlobalBuildingAtlas were not re-examined.)

## Camera jumps: 100-jump verification

`e2e/teleport.spec.ts` now jumps to 50 deterministic spots over the town and its edges (cold: nothing loaded or
cached) and then revisits them (warm), with normal browser frame pacing; started after an idle check (load 2.2).
Worst frame after a jump: **all 100: p50 17.8, p95 23.1, p99 37.2, max 37.2 ms, none > 50 ms**; cold (50): p95
30.3, p99 37.2 ms; warm (50): p99 18.4 ms (`benchmarks/results/2026-11-14-19-19-47-teleport.json`). Another
session started a video encode near the end of the run; the warm round, run during it, stayed at 18 ms. With 100
samples p99 equals the single worst jump, so "p99 < 50 ms" holds for this sample on this machine, not as a
guarantee. No optimisation was started. The uncapped `benchmark.spec.ts` stays the render-cost comparison only.

### 3.3 follow-up (2026-11-14): one real outline without a survey? — no, direction stopped

Re-checked only what could have changed since the 3.1–3.3 research: live OSM (Overpass, base 2026-11-14T21:11Z)
around the zemstvo building, the Rylskyi crossing and the kostel — the same 15 ways as the 2026-11-12 snapshot,
none contains a landmark point, the kostel has no OSM building or place_of_worship; Microsoft footprints — the same
release (dataset-links 2026-02-25, quadkey 120303230 from 2026-02-23). Every other source was already ruled out
(Overture = the same outlines; GlobalBuildingAtlas not downloaded by decision; no general plan, no open municipal
data, cadastre = parcels only; 1815/1826 plans predate the buildings). Without one of: a local OSM mapper, an
on-site GNSS walk-around, or an official plan with a licence, no outline can pass the `approval.basis` rule, so
none was drawn. Already-located objects with photos (lyceum, synagogue, 1 Kyivska St, the museum at 35
Bohachevskoho St, the central memorials) are the better target for visible improvement.

# Skvyra Historic Centre — Visual Authenticity 3.4 (2026-11-15)

Goal: a more recognisable historic centre from existing capabilities (landmark catalogue, composite landmarks,
facade shader), every new detail traced to a licensed photo, nothing invented as confirmed. Photos are referenced
(Wikimedia Commons, CC BY-SA), never copied into the model or the repository; screenshots in `docs/img/a34/` are
renders of the model only.

## What was done per object

| Object | Evidence | Result | Confidence / what stays approximate |
|---|---|---|---|
| Lyceum (boys' gymnasium), OSM way 952914329 | 27 Commons files (Xsandriel 2025 ×17, Kiyanka 2015/2025 ×6, WestBam 2019 ×2, Inishche 2010, Олекса-Київ 2014) | composite of four blocks on the unchanged OSM outline: two-storey pedimented west wing with oculus, door and blue/yellow plinth band; two-storey link; tall two-storey main block with the south risalit's pediment and the domed tower; two-storey pedimented east wing. Tile-clad facade style (pink/beige patchwork, rectangular ground-floor windows, tall upper windows with arched fanlights, mosaic panels, lighter pilasters), pink and white cornice bands, grey stone plinth. **Tower moved to the risalit's south-east corner** (3.0 had the south-west one; every south / south-west view shows the three-axis pediment face left of the tower and the six-axis face receding further left) | storeys = OSM `building:levels=2` (the upper row of arches is a fanlight of the same storey, not a third floor); eaves 12.3 / 10 / 8.8 m, pediment rises, colours: photo estimates. Not modelled: street (north) facade with a four-column portico (position along the facade not determinable), door canopies, mosaic motifs (generic speckle) |
| Lyceum annex, OSM way 952914328 | garden view Skvyra lyceum (1); 2025 photos of the Olshevskyi bust | was a windowless white box (classified as a warehouse); now one storey, beige tile cladding, windows, low blue metal roof | west side is yellow corrugated sheet on the 2025 photos — one colour per building, the lyceum-side beige is used |
| 1 Kyivska St (1903), Microsoft ML outline | 2 photos: Basil Tsymbaliuk 2017 (CC BY-SA 4.0), Олекса-Київ 2014 (CC BY-SA 3.0) | was a two-storey block with arched windows; now one tall storey of whitewashed brick, six windows on the 17.5 m street front (= the ML outline's south-east edge, 6 × 2.9 m bays), central ogee brick attic with an arched niche, dentil band under the eaves, low hipped slate roof, no invented street door | rear/side facades not visible (drawn with the same windows); attic size and eave from the bay scale |
| Bust of M. M. Olshevskyi (Q122579664) | 4 photos (Kiyanka 2015/2025, Xsandriel 2025); Wikidata/WLM point | new free-standing monument: two-step black stone base, black granite block and pillar, bronze bust; front faces west (the annex is behind it on the photos) | sizes from the head scale (~0.4 m); the bust is a blocky form, no likeness |
| Local history museum, OSM way 1573335660 | OSM (one mapper, 2026-10-29: name, вул. Богачевського 35); a tourism catalogue lists вул. К. Болсуновського 70 — the outline fronts Bolsunovskoho, so both can describe the same plot; **no photo of the building** on Commons; a 2019 panorama by WestBam shows a red two-storey pedimented building with a long one-storey brick wing in that direction | **not rebuilt** — identity plausible, appearance unconfirmed |
| Afghan war memorial (Q122330131) | 2 photos WestBam 2019 (CC BY-SA 4.0): three granite steles, gilded trident on the central one | **not rebuilt** — the Wikidata coordinate equals the photographer's geotag; the panorama (sun direction, three surrounding buildings matched) puts the steles ~20–30 m south-west of it, too uncertain to place |
| WWII memorial (obelisk, wall of names, cannon) | 1 photo (Олекса-Київ 2014); three unnamed OSM war_memorial nodes near the park | **not rebuilt** — which node is which element is unknown |
| Shevchenko monument | OSM node only, no photo | marker in the search index only (unchanged) |

## How (no new tools)

- `config/landmarks.json`: lyceum `composite` (4 parts, `decompose` for the risalit roof, per-part wall / frieze /
  plinth colours and bands, pediments and doors), annex and 1 Kyivska St attributes, new `monuments` list.
- `buildings.py`: facade styles `tiled_arched` (8) and `tiled` (9); `pediment_tris` (triangle or ogee attic,
  raking cornice, oculus) shared by composite and plain landmarks; `wall_frame` / `facade_features` (pediment, band on
  the wall nearest to a point); `monument_mesh`; landmark `kind`, `floor_h`, `bay_m`, `street_door: false`.
- `tiles.py`: rotunda on composite landmarks (takes the block's eave / storey height / facade), monuments in their
  tile (hash, LOD0 bust, LOD0–1 only), monuments in the search index.
- `materials.ts`: tile grid and patchwork, per-storey window shapes, mosaic and pilasters for style 8 only.

## Visual check (photo → model)

Fixed viewpoints matching the photos: `lyceum-garden` (Skvyra lyceum (1)), `lyceum-west` (Kiyanka 2025 03),
`kyivska-1` (Tsymbaliuk 2017), `olshevskyi` (Kiyanka 2025 02), plus `lyceum-tower`, `lyceum-ensemble`,
`historic-centre` (demo). Before/after: `docs/img/a34/*-before-after.jpg`; provenance colouring (`COLOR=geometry`
for the compare spec): `historic-centre-provenance.jpg`, `lyceum-ensemble-provenance.jpg`. Corrections made after the
comparison: main block colour less pink, annex beige instead of yellow on the photographed side, no street door on
1 Kyivska St.

## Performance and QA

Paired A/B/A/B frame benchmark (3.3 code vs 3.4, best of two, while another session rendered in Blender): every view
within ±0.4 ms p95/p99, lyceum view +4 k triangles (+1 %); `benchmarks/results/2026-11-15-a34-*.json`. Tiles +0.36 MB
of 188 MB (lyceum tile LOD0 802 → 959 KB, LOD1 217 → 372 KB). `make qa`: 0 floating / buried / road overlaps / trees
in buildings / faces down, 0 height outliers.
