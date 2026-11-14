# Licences, attribution and what may be published

Verified 2026-11-12 against the providers' own pages (links below). Not legal advice.

| Source | Licence (verified at) | Required notice | Notes |
|---|---|---|---|
| OpenStreetMap | ODbL 1.0 — openstreetmap.org/copyright | "© OpenStreetMap contributors" | **share-alike** for derived databases |
| Microsoft Global ML Building Footprints | **CDLA Permissive 2.0** — LICENSE + README of github.com/microsoft/GlobalMLBuildingFootprints | none mandatory; we credit Microsoft | when the *data* is shared, the agreement text must be included (`LICENSES/CDLA-Permissive-2.0.txt`); "Results" are unrestricted. **Earlier docs said ODbL — that was wrong and has been corrected everywhere.** Imagery behind the footprints: Bing Maps 2014–2024 (repo README). |
| Copernicus DEM GLO-30 | free licence — dataspace.copernicus.eu COP-DEM page | modified data: "produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved" | we modify it (DTM correction), so the "produced using" form is used. The page references an ESA user licence PDF that was not read: re-check before commercial use |
| Copernicus Sentinel-2 | EU law, free full and open (Sentinel Data Legal Notice) | "Contains modified Copernicus Sentinel data 2026" | reproduction, distribution, adaptation explicitly allowed |
| GHSL GHS-BUILT-H R2023A | CC BY 4.0 — copyright.txt next to the data | "GHSL © European Union 1995-2026, JRC; changes made" | |
| Meta & WRI High Resolution Canopy Height Maps v2 | CC BY 4.0 — AWS Open Data registry entry `dataforgood-fb-forestsv2` | "Canopy height: Meta and World Resources Institute (WRI), CC BY 4.0; changes made" | tree tops, heights and crowns are *derived* (segmentation); window of one tile read with range requests |
| ESA WorldCover 2021 v200 | CC BY 4.0 — esa-worldcover.org | "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium" | used only to decide where the canopy model is outdated, never as geometry |
| KartaView street imagery | CC BY-SA 4.0 — KartaView terms (kartaview.org, checked 2026-11-14) | contributor + KartaView + licence | **not used**: the only Skvyra sequence is marked deleted and exposes no author name; viewed for research only |
| Wikidata | CC0 | — | coordinates and names of listed buildings |
| Wikimedia Commons photos | CC BY-SA 3.0/4.0 (per file) | — (not redistributed) | used only as visual reference to estimate colours/roof family/storeys of 2 buildings, the lyceum corner tower and the synagogue (blocks, heights, attic, stairs, colours); no pixels are copied into the model, and no photo is stored in the repository |

## Compatibility

- OSM (ODbL) + Microsoft (CDLA-P 2.0): compatible. CDLA-P 2.0 is permissive and lets the data be combined
  and re-licensed as part of a larger database; the combined building layer is then an ODbL *derivative
  database* because it contains OSM.
- GHSL (CC BY 4.0), Sentinel, Copernicus DEM: attribution licences, compatible with publishing the combined
  database under ODbL as long as their notices are kept.

## What may be published, and how

| Artefact | Status if published |
|---|---|
| Screenshots, renders, videos | ODbL *Produced Work*: allowed, must carry the attribution line (below) |
| The web viewer with its tiles (GLB contain OSM + ML geometry) | the tiles are a derivative database → offer them under ODbL (or provide the processed GeoJSON under ODbL on request), include `LICENSES/CDLA-Permissive-2.0.txt`, keep all notices (the viewer shows them in the attribution panel) |
| `data/processed/*.geojson` | same as tiles: ODbL + CDLA text + notices |
| Raw downloads (`data/raw`) | do not redistribute; re-download from the providers |
| Commons photos | not part of the project; only links |

Nothing has been published. Before publishing: (1) re-read the ESA COP-DEM user licence PDF,
(2) add an ODbL notice file next to any distributed tiles, (3) keep the attribution panel visible.

## Attribution line (use everywhere)

© OpenStreetMap contributors (ODbL) · Building footprints © Microsoft (CDLA-Permissive-2.0) · produced using
Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under
COPERNICUS by the European Union and ESA; all rights reserved · Contains modified Copernicus Sentinel data
2026 · GHSL © European Union, JRC (CC BY 4.0) · Canopy height © Meta and WRI (CC BY 4.0) · © ESA WorldCover
project 2021 (CC BY 4.0)
