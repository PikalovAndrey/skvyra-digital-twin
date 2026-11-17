# Limitations

Honest list of what this reconstruction is **not**, as of 2026-11-14.

## Data

- **Heights are estimates.** 1 building has a measured `height`, 8 have OSM levels, 1 a photo estimate;
  96 large non-residential buildings use GHSL (low confidence); 98.6 % use documented type defaults. The
  earlier GHSL 1-vs-2-storey rule for houses was removed by the audit (within the product's 3.55 m RMSE).
- **Roofs are all inferred** (no `roof:shape` in OSM, no legal imagery that shows them). Families are
  chosen per building by documented rules; rectilinear complex plans are split into parts with their own
  roofs (no true valleys/ridges intersection geometry: parts meet with vertical gable faces). Non-
  rectilinear complex plans keep a single roof over the bounding rectangle. Domes, towers, dormers,
  mansards and porches are not modelled.
- **77 % of buildings are ML-detected** (Microsoft). The imagery date is unknown: demolished buildings may
  remain, new ones may be missing, adjacent buildings may be merged.
- **23 % of residential street length has no building within 60 m** in either source (§4 of
  DATA_SOURCE_AUDIT.md). These areas look empty in the model; they may or may not be empty in reality.
- **Terrain is a 30 m radar surface model** with an approximate DTM correction under mapped buildings and
  woodland (morphological opening); ~2–4 m vertical error is typical, unmapped tree belts still read
  high. Rivers are not flattened; bridges are generic decks between the road levels at their ends
  (estimated rise, width, parapets; no piers).
- **Road widths are inferred** from class (no width tags); sidewalks are not modelled (none mapped).
  Road markings are not drawn at all rather than guessed.
- **Land use is incomplete**: unmapped areas take their colour from the Sentinel-2 scene of 18 July 2026
  (one season, one day).
- **Landmarks**: 4 of 7 listed buildings are matched to footprints; only colours, roof family and storeys
  are photo-estimated for two of them. No accurate 3D model of any landmark exists in open data.
- **Boundary** is OSM's administrative line, not an official cadastral boundary.
- Only legally reusable open data is used. Ground colour comes from Sentinel-2 at 10 m: real, but blurry
  close-up and with mixed pixels (unknown roofs may show as reddish smudges).

## Software

- Facades are procedural shader patterns by building type (windows, frames, shopfronts, arched windows,
  balconies); doors, signage, balconies in 3D and real window positions are not represented.
- Ambient occlusion is baked/approximated (ground contact shadow, wall darkening); a screen-space AO pass
  was tried (N8AO) and removed because it crashed with three r187.
- GLBs are not compressed (no meshopt/Draco/KTX2); rely on HTTP gzip.
- The override system has no editing UI yet: overrides are edited as JSON. Photo/model import is limited
  to referencing a model path in an override (`model` field is carried into tile metadata but the viewer
  does not load landmark models yet).
- No undo for local edits (there is no in-viewer editing yet).
- Mobile: touch orbit/pan/zoom works through three's MapControls and the layout collapses, but no real
  device was tested. Fly mode is keyboard-only.
- Visual regression: reference screenshots are produced by the e2e run, but there is no automated
  pixel-diff comparison against stored references yet.
- Performance measured on one high-end machine only.

## Process

- The OSM extract is a single snapshot (2026-11-12T14:24Z). Incremental updates are "re-run acquire with
  `--refresh`, rebuild": unchanged tiles are skipped by hash, but there is no diff-based (augmented diff)
  update.
- Data-source rows marked † in DATA_SOURCE_AUDIT.md were not re-verified online.

## Visual layer (Visual Fidelity 2.0)

- Doors, fences, shrubs, garden trees, window layouts, frame colours, roof materials and wall finishes are
  **invented** for appearance. Fences/shrubs/garden trees live in a separate switchable layer.
- Shoulders (1 m) and stream widths are inferred from road/waterway class.
- Pond water level is derived from shoreline terrain (20th percentile), not measured.
- Instant camera teleports cause one 90–310 ms frame on this machine (animated navigation does not).
- LOD0 tiles grew 36 % for overhangs, fascia, doors, fences and shoulders.
- Pond surfaces have one colour (no depth gradient); the bank wall at LOD0 is vertical, up to the height of
  the DEM above the derived water level.
- Trees are drawn only on the nearest LOD (≤ 650 m at medium quality, ≤ 2.8 km at high); further away the
  town looks sparser in trees than it is.
- Ground beyond ~200 m is the 10 m Sentinel-2 colour and looks blurry from low angles.

## Heritage status and landmark identity (Authenticity 3.2)

- No official State Register record was available for any Skvyra building: every official status is `unknown`;
  protection numbers are shown only as "reported by secondary sources" (WLM).
- The zemstvo building and the Rylskyi house are matched to outlines only as *probable*; the evidence against is
  recorded and shown. Both buildings may be missing from all footprint sources.
- The missing-outline layer is a suspicion built from 10 m land cover; it cannot locate or count missing houses.

## Local outlines and review (Authenticity 3.3)

- No real local outline exists yet: the workflow is verified on synthetic data; the city model is unchanged.
- The workbench's in-browser checks are approximate (vertex/edge tests); the authoritative validation is the GEOS
  check in `skvyra review apply`. The workbench draws in local metres on a 2D map without imagery (no legal
  high-resolution imagery is available), so it is for recording outlines obtained elsewhere, not for tracing.

## Demo / devices (3.5)

- Phones: the UI works on a 390 px touch viewport (places, search, building card, quality), measured only as an
  emulated viewport on a desktop GPU; real phone GPUs, memory limits and thermal throttling are untested. Low quality
  is the setting to try first on a phone.
- First seconds: until the context ground (0.42 MB, Sentinel-2 colour) arrives, the flat horizon plane is all there
  is (≈1 s fast, ≈2–3 s at 5 Mbit/s); after that a coarse satellite-coloured sheet fills the town area. It is shown
  exactly while some tile in view has nothing loaded (also later, e.g. reaching the far edge of town on low quality)
  and hidden otherwise, as it would poke through loaded terrain in valleys. The sheet is 6 m lower and 120 m coarse:
  a tile next to a missing one shows a small step at its edge while loading. Since 3.5.1 every fill vertex is at
  least 1.5 m below the lowest DEM within 120 m, so the fill never rises above the terrain of any LOD (measured: 0 of
  335 k terrain vertices; before, up to 9 m in narrow valleys).
- Older, separate: the outer satellite sheet (`context_ground`, 2.5 m below) continues under the edge of the outer
  tiles and rises above their terrain in a few valley spots (≤ 9.4 m; 2 187 of 276 k LOD0 vertices, e.g. tiles t-4-4,
  t+4+2). Visible only close up at the town's outer edge; not changed in 3.5.1.
- Places on a phone: the framings were chosen on a landscape screen; in a narrow portrait view the synagogue sits at
  the left edge of the picture.
- Needs WebGL 2 and `DecompressionStream`: Safari / any iOS browser 16.4+, Chrome Android 80+, Firefox Android
  113+ (MDN compat data; table in `docs/DEPLOY.md`). Checked from compatibility data and a desktop emulation only.
- Memory on phones is not measured on a device. In the emulated tour the tile cache holds ~60–73 MB of decoded tile
  data (budget: 220 / 480 / 900 MB for low / medium / high quality, evicting least-recently-used tiles beyond it). iOS
  Safari can drop the WebGL context or reload the tab under memory pressure (its limits are not documented); a lost
  context shows a message with a reload button, a tab reload cannot be intercepted.

## Historic centre from photos (Authenticity 3.4)

- Lyceum, annex, 1 Kyivska St and the Olshevskyi bust: dimensions, colours and details are photo estimates
  (`method: photo_estimate` in `config/landmarks.json`); facades not seen on any photo repeat the visible ones.
- The lyceum's street facade with a four-column portico is not modelled (its position along the facade is unknown).
- Not rebuilt: local history museum (no photo of the building), Afghan war memorial (coordinate is the photographer's
  position), WWII memorial (element positions unknown), Shevchenko monument (no photo). Kostel, zemstvo building and
  Rylskyi house remain as in 3.3 (no confirmed outline).

## Trees (Realism & Authenticity 3.0)

- Tree tops come from an **ML canopy height model** (Meta/WRI v2) of Maxar imagery from **2015-06-24** (84 % of
  the town) and **2011-04-28** (16 %, leaf-off → confidence `low`). Trees felled or planted since then are wrong;
  present-day buildings remove candidates on them, WorldCover 2021 flags larger grown patches, nothing else does.
- Tree candidates are local maxima of a smoothed surface: dense canopies give one tree per dominant crown (real
  counts are higher), small trees under a big crown are lost, trees < 3 m are not modelled.
- Species / crown shape (broadleaf, conifer, fruit) is **inferred appearance**, never data.
- Where the model is not trusted (model-fill plateaus 3.7 km² inside the town, outdated patches 1.19 km², 14 OSM
  orchards/forests without canopy in the model) trees are still rule-based, with invented positions.
- Landmarks rebuilt from photos: the lyceum's corner tower and the synagogue (3.1). All their dimensions are photo
  estimates (scale from door heights), only the street fronts were visible; the synagogue's basement windows,
  courtyard annexes and rear are not modelled. The kostel has no footprint and is not modelled.

## Multi-part roofs: systemic mesh defects (found 2026-11-17; defects 1-3 fixed on branch fix/roof-eave-junction)

Found while fixing house `ms:edc91edf970c` after owner feedback (see docs/CENTRE_REALISM.md §10). When `plan()` splits an
irregular footprint into several roof parts (`buildings.decompose()`), the multi-part branch of `building_mesh()` produces
four kinds of defect; single-part roofs have none:

1. **Overhang strips dropping down the walls.** Each triangle of the 0.45 m eave ring takes its height from the planes of
   the part nearest its centroid, extended without a lower clamp (`Part.z_ext()`); triangles spanning a whole wall can end
   0.2-2 m above the ground ("dark diagonal stripes").
2. **Missing step faces between parts.** The vertical face between two parts is built only when their outlines intersect
   as a line; parts made by `decompose()` share edges only up to rounding, so slots up to ~0.4 m tall stay open.
3. **Wall tops falling back to the main eave.** Outline corners a few millimetres outside every part fail the 1e-6 m test
   in `top_at()`; those walls stop at the main eave (cut gables, wall fins above lean-to roofs).
4. **Missing rake overhangs** on small gabled parts.

Scale (in-memory rebuild of every multi-part building, 2026-11-17): 689 of 731 multi-part buildings (94 %) have at least
one defect, 264 of 282 in the central 3x3 tiles; 0 of 400 sampled single-part houses. All of it predates the Centre
Realism work (identical in 06c2406).

Status: `ms:edc91edf970c` was corrected first with the override `roof_parts: 1` (released d205944). Owner feedback on
house `ms:b95e6bc42b42` (a band under the roof, a see-through gable) led to the global fix of 1-3 in `building_mesh()`:

- the multi-part overhang is built wall by wall (`_multipart_eaves()`): the inner edge is the wall top, the outer edge
  follows the owning part's roof plane clamped to [wall top - 1.2 x overhang, wall top]; corners and seams where the
  owning part changes share one height (no cracks);
- step faces between parts use the part boundary within 3 cm of the neighbour's (not an exact intersection);
- `top_at()` accepts parts within 2 cm of an outline point (multi-part only; single roofs keep 1e-6 m).

Result (same in-memory scan): overhang dropping more than 0.6 m below the nearest wall top 669 -> 41 buildings (the
remaining ones are corners where the nearest wall is a higher gable; by construction no strip is more than 0.54 m below
its own wall top), more than 1.5 m 372 -> 1, see-through gaps between parts 140 -> 0. Only multi-part buildings and the
composite lyceum (its eave band no longer sags) change; single-part buildings, the admin building, the House of Culture,
the Shevchenko monument, bridges and LOD2 are byte-identical. Not fixed: 4 (rake overhangs).
