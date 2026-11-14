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
  high. Rivers are not flattened; bridges are interpolated decks.
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

## Trees (Realism & Authenticity 3.0)

- Tree tops come from an **ML canopy height model** (Meta/WRI v2) of Maxar imagery from **2015-06-24** (84 % of
  the town) and **2011-04-28** (16 %, leaf-off → confidence `low`). Trees felled or planted since then are wrong;
  present-day buildings remove candidates on them, WorldCover 2021 flags larger grown patches, nothing else does.
- Tree candidates are local maxima of a smoothed surface: dense canopies give one tree per dominant crown (real
  counts are higher), small trees under a big crown are lost, trees < 3 m are not modelled.
- Species / crown shape (broadleaf, conifer, fruit) is **inferred appearance**, never data.
- Where the model is not trusted (model-fill plateaus 3.7 km² inside the town, outdated patches 1.19 km², 14 OSM
  orchards/forests without canopy in the model) trees are still rule-based, with invented positions.
- Only one landmark part is modelled (the lyceum's corner tower); its position, diameter and heights are photo
  estimates. The kostel has no footprint and is not modelled.
