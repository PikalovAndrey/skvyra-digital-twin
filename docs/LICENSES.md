# Licences, attribution and what may be published

Verified 2026-11-12 against the providers' own pages (links below); post-launch audit 2026-11-15 (end of file).
Not legal advice.

| Source | Licence (verified at) | Required notice | Notes |
|---|---|---|---|
| OpenStreetMap | ODbL 1.0 — openstreetmap.org/copyright | "© OpenStreetMap contributors" | **share-alike** for derived databases |
| Microsoft Global ML Building Footprints | **CDLA Permissive 2.0** — LICENSE + README of github.com/microsoft/GlobalMLBuildingFootprints | none mandatory; we credit Microsoft | when the *data* is shared, the agreement text must be included (`LICENSES/CDLA-Permissive-2.0.txt`); "Results" are unrestricted. **Earlier docs said ODbL — that was wrong and has been corrected everywhere.** Imagery behind the footprints: Bing Maps 2014–2024 (repo README). |
| Copernicus DEM GLO-30 | "Licence for the use of the Copernicus WorldDEM-30" (COP-DEM-GLO-30-F, Full, Free & Open; PDF linked from docs.sentinel-hub.com/api/latest/data/dem/, read 2026-11-15): worldwide, free, reproduction, distribution, communication to the public, adaptation (Art. 3–5); no commercial restriction | modified data (Art. 6b): "produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved" **and** (Art. 6c) in a licence or notice covering the distribution: "The organisations in charge of the Copernicus programme by law or by delegation do not incur any liability for any use of the Copernicus WorldDEM-30" | we modify it (DTM correction). Art. 6d: do not suggest endorsement; Art. 6e: pass the obligations to redistributors — all in `LICENSES/NOTICE.txt` |
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
- ODbL scope (checked 2026-11-15): the ODbL covers only the OSM-derived contents — OSM feature geometry, tags and
  names, and the building layer merged with Microsoft footprints. Copernicus DEM heights, Sentinel-2 colours, GHSL
  height values, Meta/WRI trees and WorldCover are separate sources placed in the same tiles; they are **not**
  relicensed under the ODbL (ODbL 4.5, Collective Database) and keep their own licences and notices. Where OSM was
  used to modify them (terrain lowered under OSM buildings, GHSL heights attached to OSM buildings) both sets of
  conditions apply to that part. Earlier wording here ("publish the combined database under ODbL") overreached and was
  corrected; `LICENSES/NOTICE.txt` states the same split.
- MIT (code licence, chosen by the owner 2026-11-15) covers only the project's own source code and documentation
  text. Third-party data, generated tiles, processed data, renders and Commons photos are not under MIT.

## What may be published, and how

| Artefact | Status if published |
|---|---|
| Screenshots, renders, videos | ODbL *Produced Work*: allowed, must carry the attribution line (below) |
| The web viewer with its tiles (GLB contain OSM + ML geometry) | the OSM-derived contents of the tiles are a derivative database → offer them under ODbL (the other sources in the tiles keep their own licences, see Compatibility) (or provide the processed GeoJSON under ODbL on request), include `LICENSES/CDLA-Permissive-2.0.txt`, keep all notices (the viewer shows them in the attribution panel) |
| `data/processed/*.geojson` | same as tiles: ODbL + CDLA text + notices |
| Raw downloads (`data/raw`) | do not redistribute; re-download from the providers |
| Commons photos | not part of the project; only links |

Published 2026-11-15 (https://skvyra-twin.vercel.app, source on GitHub). The three "before publishing" items were
checked only after the first release; results in the post-launch audit below.

## Attribution line (use everywhere)

© OpenStreetMap contributors (ODbL) · Building footprints © Microsoft (CDLA-Permissive-2.0) · produced using
Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under
COPERNICUS by the European Union and ESA; all rights reserved · Contains modified Copernicus Sentinel data
2026 · GHSL © European Union, JRC (CC BY 4.0) · Canopy height © Meta and WRI (CC BY 4.0) · © ESA WorldCover
project 2021 (CC BY 4.0)

## Post-launch audit (2026-11-15)

Checked against the live site and the first release (`257659a`), not assumed.

| Item | Finding | Fix (in the working tree, live after the next release) |
|---|---|---|
| (1) ESA COP-DEM user licence | Read in full (PDF text). Free, worldwide, redistribution and adaptation allowed, no commercial restriction. Art. 6b notice: present in the viewer's source list. **Art. 6c liability sentence: missing everywhere** (viewer, README, docs). | Sentence added to `LICENSES/NOTICE.txt` (shipped with the site, linked from the viewer) and to the README. |
| (2) ODbL notice next to distributed tiles | **Missing.** The public build exposes a derivative database (`tiles/*.glb`, `tiles/*.json`, `search.json`, `quality_gaps.json`) with only "© OpenStreetMap contributors (ODbL)" in the viewer — fine for the produced work (the rendered view), but the downloadable files carried no statement that they are offered under the ODbL (ODbL 4.4) and no pointer to the method of making them (4.6). The CDLA text required when Microsoft footprint data is shared was not in the build either. | `LICENSES/NOTICE.txt` + `LICENSES/CDLA-Permissive-2.0.txt` copied into every package by `scripts/pack_demo.py` (the build fails without them); the notice states the ODbL offer and points to the public pipeline (4.6). |
| (3) Attribution panel visible | Yes: full line on desktop, short "© OpenStreetMap contributors and other sources" on phones, full list with licences on click (smoke test + screenshots). CC BY sources (GHSL, Meta/WRI) did not say "changes made" in the viewer list. | "Changes made" for GHSL and Meta/WRI in `NOTICE.txt` (reachable from the panel). |
| Sentinel-2 | "Contains modified Copernicus Sentinel data 2026" shown. | — |
| Commons photos | Not in the build (no image files besides the app's own); only linked from building cards. | — |
| Root `LICENSE` for the code | **None** in the first release. Without it the code is all rights reserved by default, although the repository is public. | Owner chose **MIT** (2026-11-15): `LICENSE` at the root; scope (own code only, no data) in README, here and in `NOTICE.txt`. |

### Code licence

**MIT** (owner's decision, 2026-11-15), `LICENSE` at the repository root. It covers only our own source (pipeline,
viewer, scripts, tests, Blender scripts, docs text). It does not and cannot change the data licences: anything built
from OSM stays ODbL, the Microsoft data keeps CDLA-Permissive-2.0, Copernicus/Sentinel/CC BY notices stay, Commons
photos remain their authors' (only linked). Renders and screenshots in `docs/img/` are ODbL Produced Works carrying the
attribution line above, not MIT-licensed. Considered and not chosen: Apache-2.0 (patent grant, more text), GPL/AGPL
(copyleft).
