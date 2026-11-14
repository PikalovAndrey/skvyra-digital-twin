"""Terrain from the Copernicus GLO-30 DEM.

The DEM is resampled once onto a regular lattice in the *local* frame (spacing `step`). Each lattice
cell is split along the same diagonal everywhere, and `z()` evaluates exactly that triangulated
surface. Because every tile samples the same global lattice, neighbouring tiles at the same LOD share
edge vertices bit-for-bit (no cracks); skirts hide gaps between tiles at different LODs. Buildings,
roads and trees use `z()` of the same LOD, so nothing floats above or sinks below the rendered ground.

GLO-30 is a *surface* model (radar DSM): in built-up areas it includes some building/tree height.
A 3x3 median filter removes single-cell spikes, and a light smoothing reduces radar speckle;
vertical accuracy is documented in docs/GEOGRAPHIC_ACCURACY.md (~2-4 m typical, worse under trees).
"""
import numpy as np

from .log import log


class FlatTerrain:
    """Explicitly labelled fallback when no DEM is available: everything at z = 0."""
    source = "flat-fallback (no DEM)"

    def z(self, x, y):
        return np.zeros(np.broadcast(np.asarray(x), np.asarray(y)).shape)


class Terrain:
    source = "Copernicus GLO-30 DEM"

    def __init__(self, dem_path, frame, step: float = 10.0, spike_m: float = 8.0, verbose: bool = True):
        import rasterio
        with rasterio.open(dem_path) as src:
            a = src.read(1).astype(np.float64)
            nodata = src.nodata
            inv = ~src.transform
        bad = ~np.isfinite(a) | (a < -500) | (a > 9000)  # catches nodata and wrong vertical units
        if nodata is not None:
            bad |= a == nodata
        if bad.all():
            raise ValueError("DEM has no valid cells")
        if bad.any():
            if verbose:
                log.warning("DEM: filling %d no-data cells from nearest valid neighbours", int(bad.sum()))
            a = _fill_nearest(a, bad)
        med = _median3(a)
        spikes = np.abs(a - med) > spike_m
        if spikes.any():
            if verbose:
                log.warning("DEM: replacing %d outlier cells (> %.0f m from 3x3 median)", int(spikes.sum()), spike_m)
            a = np.where(spikes, med, a)
        self.dem = _smooth3(a)
        self.inv = inv
        self.frame = frame
        self.step = step
        self.stats = {"min": float(a.min()), "max": float(a.max()), "spikes_removed": int(spikes.sum()),
                      "nodata_filled": int(bad.sum())}

    def _bilinear_dem(self, x, y):
        lon, lat = self.frame.to_geo(x, y)
        t = self.inv  # affine: col = a*lon + b*lat + c, row = d*lon + e*lat + f
        lon, lat = np.asarray(lon), np.asarray(lat)
        col = t.a * lon + t.b * lat + t.c - 0.5  # pixel-centre convention
        row = t.d * lon + t.e * lat + t.f - 0.5
        h, w = self.dem.shape
        c0 = np.clip(np.floor(col).astype(int), 0, w - 2)
        r0 = np.clip(np.floor(row).astype(int), 0, h - 2)
        fc, fr = np.clip(col - c0, 0, 1), np.clip(row - r0, 0, 1)
        d = self.dem
        return ((d[r0, c0] * (1 - fc) + d[r0, c0 + 1] * fc) * (1 - fr)
                + (d[r0 + 1, c0] * (1 - fc) + d[r0 + 1, c0 + 1] * fc) * fr)

    def lattice(self, i, j):
        """Height at lattice node (i, j) -> local (i*step, j*step). Pure function of the node."""
        return self._bilinear_dem(np.asarray(i) * self.step, np.asarray(j) * self.step)

    def z(self, x, y):
        """Height of the triangulated lattice surface (diagonal from (i,j) to (i+1,j+1))."""
        x, y = np.asarray(x, float), np.asarray(y, float)
        s = self.step
        i, j = np.floor(x / s), np.floor(y / s)
        u, v = x / s - i, y / s - j
        z00, z11 = self.lattice(i, j), self.lattice(i + 1, j + 1)
        upper = v > u  # triangle (00, 11, 01) else (00, 10, 11)
        z10, z01 = self.lattice(i + 1, j), self.lattice(i, j + 1)
        return np.where(upper, z00 + u * (z11 - z01) + v * (z01 - z00),
                        z00 + u * (z10 - z00) + v * (z11 - z10))

    def grid_mesh(self, x0, y0, x1, y1, skirt: float = 6.0, clamp=()):
        """Triangle soup for the lattice inside [x0,x1]x[y0,y1] (tile bounds must be lattice-aligned)."""
        s = self.step
        i0, i1 = int(round(x0 / s)), int(round(x1 / s))
        j0, j1 = int(round(y0 / s)), int(round(y1 / s))
        I, J = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1), indexing="xy")
        Z = self.lattice(I, J)
        X, Y = I * s, J * s
        for poly, zmax in clamp:  # ponds: terrain nodes under still water sink below the water level
            import shapely
            inside = shapely.contains_xy(poly, X, Y)
            Z = np.where(inside, np.minimum(Z, zmax), Z)
        P = np.stack([X, Y, Z], -1)  # [rows=j, cols=i, 3]
        a, b = P[:-1, :-1], P[:-1, 1:]   # (i,j), (i+1,j)
        c, d = P[1:, :-1], P[1:, 1:]     # (i,j+1), (i+1,j+1)
        tris = np.concatenate([np.stack([a, b, d], -2).reshape(-1, 3, 3),
                               np.stack([a, d, c], -2).reshape(-1, 3, 3)])
        sk = []
        for edge in (P[0, :], P[-1, ::-1], P[::-1, 0], P[:, -1]):  # walk each border
            top = edge
            bot = edge - np.array([0, 0, skirt])
            for k in range(len(edge) - 1):
                sk.append([top[k], bot[k], bot[k + 1]])
                sk.append([top[k], bot[k + 1], top[k + 1]])
        return np.concatenate([tris, np.array(sk)]), (float(Z.min()), float(Z.max()))


def _median3(a):
    p = np.pad(a, 1, mode="edge")
    stack = [p[i:i + a.shape[0], j:j + a.shape[1]] for i in range(3) for j in range(3)]
    return np.median(np.stack(stack), axis=0)


def _smooth3(a):
    k = np.array([1, 2, 1], float) / 4
    p = np.pad(a, 1, mode="edge")
    r = p[:-2] * k[0] + p[1:-1] * k[1] + p[2:] * k[2]
    return r[:, :-2] * k[0] + r[:, 1:-1] * k[1] + r[:, 2:] * k[2]


def _fill_nearest(a, bad):
    out = a.copy()
    out[bad] = np.nan
    for _ in range(max(a.shape)):
        if not np.isnan(out).any():
            break
        p = np.pad(out, 1, mode="edge")
        neigh = np.nanmean(np.stack([p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]]), axis=0)
        out = np.where(np.isnan(out), neigh, out)
    return out


def prepare_dtm(dsm_path, out_path, object_geoms_wgs, max_lowering: float = 15.0) -> dict:
    """Approximate a bare-earth DTM from the GLO-30 *surface* model.

    Inside a mask of mapped buildings and woodland (dilated by one cell) the DSM is replaced by its 3x3
    grey-scale opening (erosion then dilation, ~90 m), which removes raised objects narrower than ~90 m
    (buildings, tree clumps, the grain elevator) while leaving open ground and broad relief untouched.
    Lowering is capped at `max_lowering`. Writes the result as a GeoTIFF and returns statistics.
    """
    import rasterio
    from rasterio.features import rasterize
    with rasterio.open(dsm_path) as src:
        a = src.read(1).astype(np.float64)
        prof = src.profile.copy()
        nodata = src.nodata
        transform = src.transform
    bad = ~np.isfinite(a) | (a < -500) | (a > 9000)
    if nodata is not None:
        bad |= a == nodata
    if bad.any():
        a = _fill_nearest(a, bad)
    mask = rasterize(((g, 1) for g in object_geoms_wgs), out_shape=a.shape, transform=transform, all_touched=True,
                     fill=0, dtype="uint8").astype(bool) if object_geoms_wgs else np.zeros(a.shape, bool)
    mask = _filter3(mask.astype(float), np.max) > 0  # dilate one cell
    opened = _filter3(_filter3(a, np.min), np.max)
    lowered = np.clip(a - opened, 0, max_lowering)
    dtm = np.where(mask, a - lowered, a)
    diff = a - dtm
    prof.update(dtype="float32", nodata=None, compress="deflate")
    with rasterio.open(out_path, "w", **prof) as dst:
        dst.write(dtm.astype(np.float32), 1)
    return {"cells": int(a.size), "masked_cells": int(mask.sum()), "lowered_cells_gt_0_5m": int((diff > 0.5).sum()),
            "mean_lowering_in_mask_m": round(float(diff[mask].mean()), 3) if mask.any() else 0.0,
            "p99_lowering_m": round(float(np.percentile(diff[mask], 99)), 2) if mask.any() else 0.0,
            "max_lowering_m": round(float(diff.max()), 2), "nodata_filled": int(bad.sum())}


def _filter3(a, fn):
    p = np.pad(a, 1, mode="edge")
    return fn(np.stack([p[i:i + a.shape[0], j:j + a.shape[1]] for i in range(3) for j in range(3)]), axis=0)
