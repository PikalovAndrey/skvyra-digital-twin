# Architectural style of Skvyra — what the procedural rules are based on

Everything on this page is **decorative inference**: it decides how an *unknown* building is drawn
(roof family, materials, colours, window types). It never overrides observed data (OSM tags, overrides),
and the viewer labels every such attribute as "припущення / assumed".

## Sources looked at (licensed; referenced, not redistributed)

| What | File (Wikimedia Commons) | Licence |
|---|---|---|
| Former men's gymnasium, now lyceum (listed) | `Skvyra lyceum (1).jpg` | CC BY-SA 4.0 |
| Church of St John of Nepomuk (listed) | `Saint John of Nepomuk church, Skvyra (1).jpg`, `(4).jpg` | CC BY-SA 4.0 |
| Former zemstvo board building (listed) | `Будинок земської управи, Сквира 01.jpg` | CC BY-SA 4.0 |
| Babaiev mansion (listed) | `Особняк Бабаєва.JPG` | CC BY-SA 3.0 |
| House where M. Rylsky lived (now kindergarten) | `Будинок, у якому жив та працював Рильський М. Т.jpg` | CC BY-SA 4.0 |
| Bus station | `Skvyra bus station.jpg` | Commons licence on file page |
| Former zemstvo post office | `Будинок колишньої земської пошти в Сквирі Київської області.JPG` | Commons licence on file page |

Coordinates and the list of listed buildings come from Wikidata (items with *located in* = Skvyra,
Q70598), retrieved 2026-11-12. No Google/Yandex Street View or other proprietary imagery was used.

## Observations → rules

| Observation (photos, OSM, satellite) | Rule in `buildings.py` |
|---|---|
| Private houses are mostly single-storey, some two-storey; plastered or brick; hipped ("чотирисхилий") roofs dominate, gabled common, half-hipped and tent (шатровий) roofs on square plans | house roof mix: hipped 50 % / gabled 30 % / half-hipped 20 %; square plans < 160 m²: hipped 65 % / pyramidal 35 %; elongated (aspect > 2.2): gabled 70 % / half-hipped 30 %; pitch 24–34° |
| Houses grow with annexes (прибудови, верандa) under lean-to roofs | rectilinear footprints split into parts: annexes < 40 % of the main body get a shed roof rising to just under the main eave (60 %) or a cross gable |
| Roof coverings: brown/red/cherry/green metal tile, grey asbestos-cement slate ("шифер", very common on older houses and outbuildings), some standing-seam sheet | roof material mix for houses: metal tile 50 %, slate 35 %, seam 15 %; outbuildings: slate / corrugated / metal tile |
| Wall colours: white and cream plaster, pale yellow, peach, light grey, red and white (silicate) brick; historic centre: salmon / terracotta / ochre with white trim | weighted palettes, white/cream dominant so streets read as coherent, not random |
| Historic public buildings: two tall storeys, arched windows, white frames | facade type *historic* (tall arched windows, 4 m floors) for listed buildings matched to footprints |
| Front gardens with columnar thuja and fruit trees | decorative garden trees: fruit 50 %, deciduous 25 %, columnar thuja 25 % (switchable layer "Декоративні дерева (вигадані)") |
| Shops occupy ground floors of ordinary buildings | buildings containing an OSM `shop` / service point get the *commercial* facade (shopfronts on the ground floor) |

## Facade and roof types (shader ids)

`_STYLE` vertex attribute in the tile GLBs:

- walls: 0 none (outbuildings), 1 house, 2 apartment block, 3 commercial (ground-floor shopfronts),
  4 public, 5 industrial (no windows), 6 place of worship, 7 historic (arched windows), 8/9 tile-clad landmark
  styles (photos), 10 use unknown (large untagged building: plain plaster, plain regular windows), 11 tile-clad
  office block (light tile pilasters, wide windows, brick-red spandrels - the administrative building on the square, photos)
- roofs: 0 bitumen membrane (flat), 1 metal tile, 2 asbestos-cement slate, 3 corrugated sheet,
  4 standing seam

Variation is seeded by the building's stable id: the same building always looks the same, neighbours
differ, and nothing changes between rebuilds.

## Landmarks

`config/landmarks.json` lists 7 Wikidata items with coordinates. Matching to footprints
(`data/processed/landmarks_report.json`):

| Landmark | Result |
|---|---|
| Будівля чоловічої гімназії (ліцей) | matched to OSM footprint (1,766 m²); storeys from OSM; brick colour, hipped roof and arched facade from the photo. **Corner dome not modelled.** |
| Особняк Бабаєва | matched (ML footprint 386 m²); 1 storey (+ attic, dormers not modelled), grey plaster, brown roof from the photo |
| Синагога | matched (OSM, levels 2); named only — no licensed exterior photo found |
| Житловий будинок, вул. Київська, 1 | matched (ML footprint); named only |
| Костел Святого Яна Непомука | **not applied**: the only footprint at the coordinate is a 2,279 m² ML outline that looks like several merged buildings |
| Будинок земської управи | **not matched**: no footprint within 30 m of the Wikidata point |
| Будинок М. Т. Рильського | **not matched**: no footprint within 30 m |

None of these is an accurate model of the real building; the info panel says "оцінка за фото (не вимір)"
and links the photos and the Wikidata item.
