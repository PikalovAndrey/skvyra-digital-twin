"""Coordinate frames.

Three frames are used, always explicitly:

* geographic  - WGS84 lon/lat in degrees (EPSG:4326, always_xy axis order = lon, lat)
* metric      - UTM zone 35N (EPSG:32635), metres; all spatial processing happens here
* local       - metric minus a fixed origin (config `origin_utm`), metres; x = east, y = north, z = up.
                Values stay within a few km, so float32 GPU vertices keep ~mm precision.

glTF / three.js are Y-up: local (x, y, z) -> glTF (x, z, -y). Heights are metres above the
EGM2008 geoid (the DEM's vertical datum); that is what "z" means everywhere in this project.
"""
from functools import cached_property

import numpy as np
from pyproj import Transformer


class Frame:
    def __init__(self, crs_metric: str, origin: tuple[float, float]):
        self.crs = crs_metric
        self.ox, self.oy = float(origin[0]), float(origin[1])

    @cached_property
    def _fwd(self):
        return Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)

    @cached_property
    def _inv(self):
        return Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)

    def to_metric(self, lon, lat):
        return self._fwd.transform(lon, lat)

    def to_local(self, lon, lat):
        x, y = self._fwd.transform(np.asarray(lon, float), np.asarray(lat, float))
        return x - self.ox, y - self.oy

    def to_geo(self, x, y):
        return self._inv.transform(np.asarray(x, float) + self.ox, np.asarray(y, float) + self.oy)

    def geom_to_local(self, g):
        import shapely
        return shapely.transform(g, lambda c: np.column_stack(self.to_local(c[:, 0], c[:, 1])))

    def geom_to_geo(self, g):
        import shapely
        return shapely.transform(g, lambda c: np.column_stack(self.to_geo(c[:, 0], c[:, 1])))

    def as_dict(self):
        lon, lat = self._inv.transform(self.ox, self.oy)
        return {"crs": self.crs, "origin_utm": [self.ox, self.oy], "origin_wgs84": [lon, lat],
                "axes": "local x=east, y=north, z=up (m); glTF x=east, y=up, z=-north",
                "vertical_datum": "EGM2008 geoid heights (Copernicus DEM)"}


def to_gltf(xyz: np.ndarray) -> np.ndarray:
    """local (east, north, up) -> glTF (x, y-up, z = -north)."""
    return np.column_stack([xyz[:, 0], xyz[:, 2], -xyz[:, 1]])
