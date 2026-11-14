# Visual Fidelity 2.0 — what changed, how it was checked

Rule kept throughout: **real footprints, coordinates and water outlines are never altered**; everything
added is appearance, labelled as inferred/decorative, and the decorative yard layer can be switched off.

Process: before screenshots from 13 fixed viewpoints (city, district, single house, street level, pond) →
prototype on one quarter (tiles t-2-2, t-2-1, t-1-2, t-1-1, private houses in the south-west) → fixes →
whole city → after screenshots from the same cameras (`docs/img/vf2/*.jpg`, viewer and Blender) →
geometry QA, tests, benchmark.

## Priority 1 — buildings

| Change | How | Cost |
|---|---|---|
| Eaves overhang (0.45 m houses, 0.25 m sheds, 0.12 m flat parapets) | roof planes continued outside the walls (exact per plane region on single-part roofs, nearest-part on multi-part); roofs rendered double-sided so the underside reads as a soffit | roof triangles +~100 % per building, LOD0/1 |
| Fascia board | vertical strip along the eaves edge, LOD0 only | 2 tris per eaves edge |
| Doors (decorative) | on the street-facing exterior edge nearest a road, placed on a 3 m bay boundary so it never covers a window | 2 tris per building, LOD0 |
| Window frames, mullions, sills, wood/PVC frames, curtains | facade shader (no geometry) | — |
| PBR relief: metal tile rows, asbestos slate waves and sheet overlaps, corrugated sheet, standing seams, bitumen patches; red brick bond with mortar; plaster grain | procedural height → normal perturbation (screen-space derivative bump) in the shader | — |
| Glass, metal roofs and water reflect the sky | environment map rendered once from the procedural sky (no download) | one 128 px cube map |
| Facade variation | seeded per building (window size, frame type, blank bays); palettes unchanged | — |

"Material atlas": all facade/roof variants are one shader program each, selected by the per-vertex `_STYLE`
id — no texture atlas download, no per-building materials.

## Priority 2 — ground and roads

| Change | How |
|---|---|
| Multi-scale ground | far: Sentinel-2 colour; district: + low-frequency macro variation; close (< ~220 m, fading from 25 m): procedural grass blades/clumps or soil/pebbles chosen by how green the satellite pixel is; bump; smooth distance blends (no LOD switch visible) |
| Asphalt | aggregate grain, repair patches, sparse hairline cracks, wear (world-space) |
| Shoulders (узбіччя) | 1 m gravel strip along paved roads, never under buildings/water; mesh at LOD0, texture beyond |
| Unpaved roads / paths / rail ballast | gravel shader |

## Priority 3 — vegetation and yards

- Trees: 6-blob deciduous crowns in two greens with smooth normals, layered conifers, fruit trees,
  columnar thuja; per-tree tint and rotation; leaf speckle in the shader.
- Decorative yard layer (**invented**, switch "Декоративний шар: сади, паркани, кущі (вигадані)"): street-side
  fences with a gate opposite each house within 25 m of a road (muted colours), garden shrubs. Stored as a
  separate mesh (`decor_fence`) and tree source 2, never mixed with data.

## Priority 4 — water

- Checked against the source: generated LOD0 water covers 969,916 m² vs 894,105 m² of OSM water polygons;
  the 73,823 m² beyond the polygons (+5 m) are OSM waterway *lines* buffered by class width (inferred).
  Outlines are unchanged (`make qa`).
- Ponds (still water ≥ 3,000 m², not elongated): flat surface at the 20th percentile of the shoreline DTM
  height. At LOD0 the terrain is cut exactly along the OSM outline and a low bank wall runs from the ground
  down to the water (no grid triangle can rise through the water); at LOD1/2 terrain nodes under the pond are
  lowered below the water. Rivers/streams stay draped.
- Colour: ponds one mostly-deep tone (their triangulation has only shore vertices, so a per-vertex distance
  fade cannot work); streams fade from the bank over 2 m and are never fully "shallow". Damp darker banks
  in the ground texture, animated ripples via normal perturbation, sky reflection.

## Priority 5 — light

Khronos PBR Neutral tone mapping (AgX tried: too desaturated with the environment light), sun 3.1, sky
environment 0.55, hemisphere 0.45, fog only beyond 7–22 km, shadow camera follows the view. Contact
shadows: baked darkening around footprints in the ground texture + shader darkening at the wall foot
(a screen-space AO pass was tried in iteration 2 and removed: incompatible with three r187).

## Priority 6 — UI

Side panel replaced by a compact toolbar (Шари · Розфарбування · Якість · Огляд міста) with one popover open
at a time, all closed by default; data-source colouring kept (vertical options + compact legend at the
bottom left); accuracy note collapses to a ⚠ chip; smaller minimap.

## Defects found during the visual QA and fixed

1. Doors drawn over windows → aligned to bay boundaries.
2. Metal-tile row highlights looked like snow lines → shadow-only rows, less metallic.
3. Asphalt "puzzle" cracks → sparse hairlines.
4. Too many walls rendered as yellow brick → brick only for clearly red walls.
5. Washed-out colours with AgX + environment light → Khronos Neutral tone mapping.
6. **Data-quality colouring rendered buildings black** after colours became 16-bit (recolouring wrote floats
   into a Uint16 array) → fixed, regression test added.
7. GEOS `clip_by_rect` / `intersection` topology errors on thin water/shoulder slivers → robust clipping.
8. Geometry growth: first whole-city build was LOD0 +45 % / LOD1 +67 % → soffit dropped (double-sided
   roofs), fascia LOD0 only, single-sided fences, cheap draping for shoulders → LOD0 +36 %, LOD1 +31 %.
9. Legend overlapping the popover; ML buildings showed the OSM licence line → fixed.

Found in the final stabilisation pass (fresh screenshots of the rebuilt city):

10. **Flat pond surfaces were never drawn** in the viewer: their triangles came out clockwise and were
    back-face culled; what looked like a pond was the satellite-coloured ground underneath. → triangles
    oriented up; `make qa` now fails on any ground/water/road triangle facing down
    (`surface_triangles_facing_down`); unit test added. Blender (no culling) was not affected.
11. Saw-tooth shoreline at close range: grid triangles straddling the shore rose above the flat water inside
    the outline → LOD0 terrain cut along the outline + bank wall (lowering the shore ring instead was rejected:
    1.4 m median, up to 14 m, would have lifted 12 buildings and 31 roads off the ground).
12. Narrow streams vanished into the grass: with an 8 m shore fade a < 16 m wide stream stayed olive
    "shallow" everywhere → 2 m fade with a deep floor.

## Cost (whole city)

| | before VF2 | after VF2 |
|---|---|---|
| LOD0 total / max tile | 92.8 MB / 2.13 MB | 127.0 MB / 3.98 MB |
| LOD1 total | 36.2 MB | 47.3 MB |
| LOD2 total (city overview) | 13.4 MB | 13.4 MB |
| Frame time p95 / p99, static views (M4 Pro) | 2.5–3.6 / 2.6–4.2 ms | 2.6–3.6 / 2.7–4.0 ms (final run, other session's tests running: 2.7–4.1 / 3.1–5.0 ms) |
| Continuous flight p99 | 2.9 ms | 3.5–3.6 ms |
| Triangles, centre 350 m | 926 k | 1,278 k |
