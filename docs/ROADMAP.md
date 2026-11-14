# Roadmap

Ordered by expected gain in *geographic truth* per hour of work.

## Next (highest value)

0. **After Realism & Authenticity 3.0** (see REALISM_3.md): map the kostel footprint in OSM (it unlocks a
   photo-based model); verify the zemstvo footprint on the ground; the synagogue has 25+ Commons photos and an
   OSM footprint — the next landmark candidate; a newer canopy model (or own imagery) would remove the 2011/2015
   vintage of the trees; build trees in a worker to cut tile-load time.

1. **Contribute to OSM** – the largest remaining accuracy gain is human mapping: `building:levels` and
   `roof:shape` for the centre and apartment blocks (anyone in the town can add them); the pipeline picks
   them up on the next `make acquire --refresh` and they outrank every estimate.
2. **Landmark models** – load the `model` field from overrides in the viewer and Blender (hide the generated
   mesh, place the user-supplied glTF at `anchor`). Start with the churches and the museum.
3. **Investigate the empty streets** (19.4 km): check on the ground or with permitted imagery whether
   those plots are built; map missing houses in OSM.
4. **Roof intersections**: true valleys where part roofs meet (currently vertical gable faces) and a
   straight-skeleton fallback for non-rectilinear complex plans.
5. **Sentinel-2 SWIR (B11/B12) built-up index** to flag likely unmapped buildings in the 19.4 km of empty
   streets as a data-quality layer (detection only, no invented geometry).
6. **Compression**: quantized/meshopt GLBs + KTX2 ground textures; serve with brotli. Target overview
   ≤ 5 MB transfer.

## Then

- Pixel-diff visual regression against stored reference views (Playwright `toHaveScreenshot`) for the
  four reference viewpoints used in the e2e journey.
- Facade variety: door + shopfront placement for `shop=*` / `amenity=*` POIs inside buildings; plinths and
  eaves overhangs; texture atlas for Blender renders.
- Land cover: fill unmapped areas from ESA WorldCover (CC BY 4.0) with a clear "satellite-classified"
  label.
- Hydrology: flatten river/pond surfaces to a level per water body; carve banks.
- Street labels in 3D for the streets near the camera; minimap street rendering.
- In-viewer override editor (writes overrides JSON, undo stack, conflict display from
  `overrides_report.json`).
- Seasonal presets in Blender and the viewer (labelled as appearance, not history).
- Long-session memory soak test; measurements on a low-end laptop and a phone.

## Later / only if justified

- Multi-town support (region `.osm.pbf` + osmium) if more settlements are added.
- Building heights from stereo / LiDAR **only** if a legally usable source appears for the area.
- Photogrammetry of individual landmarks from the user's own photographs (needs enough overlapping views).
