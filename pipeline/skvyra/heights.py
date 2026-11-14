"""Per-building height evidence from GHSL GHS-BUILT-H ANBH (2018, ~90 x 60 m cells).

ANBH is the *average net height of the built-up surface in a cell*, not a per-building measurement.
It is used only where that meaning carries over to a building:

* ``dominant`` – the building covers >= 60 % of the mapped built area of its cell (and >= 150 m2):
  the cell average is then mostly this building's own height.
* ``small_cell`` – every building in the cell is small (< 250 m2). Reported for transparency only: an
  audit showed the 1-vs-2-storey decision it was used for is within the product's error (RMSE 3.55 m).
* ``nbr_max`` – the highest cell value in the 3x3 neighbourhood, used for tagged apartment blocks without
  levels (a mixed cell under-reports a block's height, the neighbourhood maximum less so).

ANBH values at the product's floor (~2.5 m) carry no information and are treated as missing.
"""
from collections import defaultdict

import numpy as np

ANBH_FLOOR = 2.6
DOMINANT_SHARE = 0.6


def evidence(buildings_wgs, raster_path) -> list[dict]:
    """buildings_wgs: [(lon, lat, area_m2)] (representative point) -> per building
    {anbh, share, small_cell, nbr_max} (or {} outside the raster)."""
    if raster_path is None or not raster_path.exists():
        return [{} for _ in buildings_wgs]
    import rasterio
    with rasterio.open(raster_path) as src:
        a = src.read(1).astype(float)
        t = ~src.transform
    h, w = a.shape
    cells, idx = defaultdict(list), []
    for i, (lon, lat, area) in enumerate(buildings_wgs):
        c = t.a * lon + t.b * lat + t.c
        r = t.d * lon + t.e * lat + t.f
        rc = (int(r), int(c)) if 0 <= r < h and 0 <= c < w else None
        idx.append(rc)
        if rc:
            cells[rc].append(i)
    pad = np.pad(a, 1, mode="edge")
    nbr = np.max(np.stack([pad[i:i + h, j:j + w] for i in range(3) for j in range(3)]), axis=0)
    out = []
    for i, (_, _, area) in enumerate(buildings_wgs):
        rc = idx[i]
        if rc is None:
            out.append({})
            continue
        ids = cells[rc]
        tot = sum(buildings_wgs[j][2] for j in ids)
        v = float(a[rc])
        out.append({
            "anbh": round(v, 2) if v > ANBH_FLOOR else None,
            "share": round(area / tot, 3) if tot else 0.0,
            "small_cell": max(buildings_wgs[j][2] for j in ids) < 250,
            "nbr_max": round(float(nbr[rc]), 2) if nbr[rc] > ANBH_FLOOR else None,
        })
    return out
