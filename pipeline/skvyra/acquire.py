"""Data acquisition with provenance.

Every downloaded file gets a manifest entry (url, query, retrieval time, source timestamp, sha256,
licence, size). Downloads go to a .part file and are renamed atomically, so an interrupted run never
leaves a truncated file that looks valid. A cached file whose checksum no longer matches the
manifest is treated as corrupted and re-fetched (or reported, in offline mode).
"""
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import config as C
from .log import log

RETRY_STATUS = {429, 500, 502, 503, 504}


class AcquisitionError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_path() -> Path:
    return C.RAW / "manifest.json"


def load_manifest() -> dict:
    p = manifest_path()
    return json.loads(p.read_text()) if p.exists() else {}


def save_manifest(m: dict) -> None:
    p = manifest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, indent=2, ensure_ascii=False, sort_keys=True))
    tmp.replace(p)


def cached(name: str) -> Path | None:
    """Return the cached file if present and its checksum matches the manifest."""
    entry = load_manifest().get(name)
    path = C.RAW / name
    if not entry or not path.exists():
        return None
    if sha256(path) != entry["sha256"]:
        log.warning("cache %s is corrupted (checksum mismatch) - ignoring it", name)
        return None
    return path


def http_post(url: str, data: dict, ua: str, timeout: int = 180, attempts: int = 6,
              sleep=time.sleep) -> bytes:
    """POST with exponential backoff on 429/5xx/timeouts. Honours Retry-After."""
    body = urllib.parse.urlencode(data).encode()
    delay = 5.0
    last = None
    for i in range(attempts):
        req = urllib.request.Request(url, data=body, headers={"User-Agent": ua})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code not in RETRY_STATUS:
                raise AcquisitionError(f"{url}: {last} {e.read()[:300]!r}") from e
            wait = float(e.headers.get("Retry-After") or delay)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = f"{type(e).__name__}: {e}"
            wait = delay
        log.warning("request failed (%s), attempt %d/%d, retrying in %.0fs", last, i + 1, attempts, wait)
        sleep(wait)
        delay = min(delay * 2, 120)
    raise AcquisitionError(f"{url}: gave up after {attempts} attempts ({last}). "
                           "Check connectivity or rerun later; cached data is untouched.")


def record(name: str, path: Path, **meta) -> dict:
    m = load_manifest()
    m[name] = {"file": name, "sha256": sha256(path), "bytes": path.stat().st_size,
               "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "status": "ok", **meta}
    save_manifest(m)
    return m[name]


def overpass(name: str, query: str, cfg: dict, offline: bool = False, refresh: bool = False) -> Path:
    """Run an Overpass query once and cache the JSON response under data/raw/<name>."""
    hit = None if refresh else cached(name)
    if hit:
        log.info("using cached %s", name)
        return hit
    if offline:
        raise AcquisitionError(f"offline mode: {name} is not cached (or corrupted). Run `skvyra acquire` online first.")
    log.info("querying Overpass for %s ...", name)
    raw = http_post(cfg["overpass_url"], {"data": query}, cfg["user_agent"])
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AcquisitionError(f"Overpass returned non-JSON for {name}: {raw[:300]!r}") from e
    if "elements" not in doc:
        raise AcquisitionError(f"unexpected Overpass schema for {name}: keys {list(doc)}")
    if doc.get("remark"):  # Overpass reports runtime errors (timeouts, OOM) here with HTTP 200
        raise AcquisitionError(f"Overpass remark for {name}: {doc['remark']} - result would be incomplete")
    path = C.RAW / name
    part = path.with_suffix(".part")
    part.write_bytes(raw)
    part.replace(path)
    record(name, path, source="OpenStreetMap via Overpass API", url=cfg["overpass_url"], query=query,
           source_timestamp=doc.get("osm3s", {}).get("timestamp_osm_base"), license=cfg["osm_license"],
           elements=len(doc["elements"]))
    return path


def boundary_query(rel: int) -> str:
    return f"[out:json][timeout:90];rel({rel});out geom;"


def osm_query(bbox: tuple[float, float, float, float]) -> str:
    s, w, n, e = bbox
    return (f"[out:json][timeout:300][maxsize:536870912][bbox:{s},{w},{n},{e}];"
            "(way[building];rel[building];way[\"building:part\"];"
            "way[highway];way[railway];way[waterway];rel[waterway];"
            "way[natural];rel[natural];way[landuse];rel[landuse];way[leisure];rel[leisure];"
            "way[amenity];rel[amenity];way[place];way[man_made];way[barrier=wall];"
            "node[natural=tree];node[amenity];node[shop];node[tourism];node[historic];node[place];"
            "way[\"area:highway\"];);out geom qt;")


def dem_window(cfg: dict, bbox: tuple[float, float, float, float], offline: bool = False,
               refresh: bool = False) -> Path:
    """Read only the AOI window of the Copernicus COG via HTTP range requests (~100s of KB, not the
    whole 1x1 degree tile) and cache it as a local GeoTIFF."""
    name = "dem_copernicus_glo30.tif"
    hit = None if refresh else cached(name)
    if hit:
        log.info("using cached %s", name)
        return hit
    if offline:
        raise AcquisitionError(f"offline mode: {name} not cached. Terrain would fall back to flat.")
    import rasterio
    from rasterio.windows import from_bounds
    s, w, n, e = bbox
    url = cfg["dem"]["url"]
    log.info("reading DEM window from %s", url)
    last = None
    for attempt in range(4):
        try:
            with rasterio.Env(GDAL_HTTP_MAX_RETRY="3", GDAL_HTTP_RETRY_DELAY="5", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
                with rasterio.open("/vsicurl/" + url) as src:
                    win = from_bounds(w, s, e, n, src.transform).round_offsets().round_lengths()
                    arr = src.read(1, window=win)
                    prof = src.profile.copy()
                    prof.update(width=arr.shape[1], height=arr.shape[0], transform=src.window_transform(win),
                                driver="GTiff", compress="deflate", tiled=False)
                    for k in ("blockxsize", "blockysize", "interleave"):
                        prof.pop(k, None)
                    nodata = src.nodata
            break
        except Exception as ex:  # rasterio raises RasterioIOError for network failures
            last = ex
            log.warning("DEM read failed (%s), attempt %d/4", ex, attempt + 1)
            time.sleep(5 * 2 ** attempt)
    else:
        raise AcquisitionError(f"cannot read DEM: {last}")
    path = C.RAW / name
    part = path.with_suffix(".part.tif")
    with rasterio.open(part, "w", **prof) as dst:
        dst.write(arr, 1)
    part.replace(path)
    record(name, path, source=cfg["dem"]["name"], url=url, license=cfg["dem"]["license"],
           window_bbox_wgs84=[w, s, e, n], nodata=nodata, shape=list(arr.shape),
           vertical_datum="EGM2008 geoid (Copernicus DEM spec)", source_timestamp="GLO-30 release (2021/2022 public)")
    return path


def quadkey(lat: float, lon: float, z: int) -> str:
    import math
    s = math.sin(math.radians(lat))
    x, y = (lon + 180) / 360, 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)
    tx, ty = int(x * 2 ** z), int(y * 2 ** z)
    return "".join(str((1 if tx & (1 << (i - 1)) else 0) + (2 if ty & (1 << (i - 1)) else 0)) for i in range(z, 0, -1))


def http_get(url: str, dest: Path, ua: str, attempts: int = 5) -> None:
    part = dest.with_name(dest.name + ".part")
    delay = 5
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=300) as r, open(part, "wb") as f:
                expected = int(r.headers.get("Content-Length") or -1)
                n = 0
                for chunk in iter(lambda: r.read(1 << 20), b""):
                    f.write(chunk)
                    n += len(chunk)
            if expected >= 0 and n != expected:
                raise ConnectionError(f"truncated download: {n} of {expected} bytes")
            part.replace(dest)
            return
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS:
                raise AcquisitionError(f"{url}: HTTP {e.code}") from e
            err = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            err = str(e)
        log.warning("download failed (%s), attempt %d/%d", err, i + 1, attempts)
        time.sleep(delay)
        delay *= 2
    raise AcquisitionError(f"{url}: gave up after {attempts} attempts")


def ms_buildings(cfg: dict, bbox, offline: bool = False, refresh: bool = False) -> list[Path]:
    """Microsoft Global ML Building Footprints (CDLA Permissive 2.0) for the quadkeys (zoom 9) covering the AOI."""
    ms = cfg["ms_buildings"]
    s, w, n, e = bbox
    keys = sorted({quadkey(la, lo, 9) for la in (s, n, (s + n) / 2) for lo in (w, e, (w + e) / 2)})
    want = {f"ms_buildings_{k}.csv.gz": k for k in keys}
    if all(cached(nm) for nm in want) and not refresh:
        log.info("using cached Microsoft footprints %s", list(want))
        return [C.RAW / nm for nm in want]
    if offline:
        raise AcquisitionError(f"offline mode: Microsoft footprints {list(want)} not cached")
    import csv
    links = C.RAW / "ms_dataset_links.csv"
    http_get(ms["index_url"], links, cfg["user_agent"])
    rows = {r["QuadKey"]: r for r in csv.DictReader(open(links)) if r["Location"] == ms["region"]}
    out = []
    for nm, k in want.items():
        if k not in rows:
            log.warning("Microsoft dataset has no %s tile for quadkey %s (no footprints there)", ms["region"], k)
            continue
        log.info("downloading Microsoft footprints quadkey %s (%s)", k, rows[k]["Size"])
        http_get(rows[k]["Url"], C.RAW / nm, cfg["user_agent"])
        record(nm, C.RAW / nm, source="Microsoft Global ML Building Footprints", url=rows[k]["Url"],
               license=ms["license"], quadkey=k, source_timestamp=rows[k]["UploadDate"])
        out.append(C.RAW / nm)
    return out


def ghsl_height_window(cfg: dict, bbox, offline: bool = False, refresh: bool = False) -> Path:
    """GHSL GHS-BUILT-H ANBH (average *net* building height per ~90 m cell, epoch 2018, CC BY 4.0).
    The 10°x10° tile zip (~50 MB) is downloaded once, the AOI window is cut out, the zip is deleted."""
    name = "ghsl_anbh_2018.tif"
    hit = None if refresh else cached(name)
    if hit:
        log.info("using cached %s", name)
        return hit
    if offline:
        raise AcquisitionError(f"offline mode: {name} not cached")
    import math
    import rasterio
    from rasterio.windows import from_bounds
    g = cfg["ghsl_height"]
    s, w, n, e = bbox
    # GHSL 4326 tiles are 10 deg wide but the grid starts at 89.0996 N / -180.0079 E (verified from tile R5_C21 bounds)
    top0, left0 = 89.09958333863592, -180.00791676349128
    tile = lambda lat, lon: (int((top0 - lat) // 10) + 1, int((lon - left0) // 10) + 1)
    row, col = tile(n, w)
    if (row, col) != tile(s, e):
        raise AcquisitionError("AOI spans several GHSL tiles - extend ghsl_height_window to mosaic them")
    url = g["tile_url"].format(row=row, col=col)
    zpath = C.RAW / f"ghsl_tile_R{row}_C{col}.zip"
    log.info("downloading GHSL height tile R%d_C%d (~50 MB, deleted after cutting the AOI window)", row, col)
    http_get(url, zpath, cfg["user_agent"])
    inner = f"/vsizip/{zpath}/" + url.rsplit("/", 1)[1].replace(".zip", ".tif")
    with rasterio.open(inner) as src:
        win = from_bounds(w - 0.01, s - 0.01, e + 0.01, n + 0.01, src.transform).round_offsets().round_lengths()
        arr = src.read(1, window=win)
        prof = src.profile.copy()
        prof.update(width=arr.shape[1], height=arr.shape[0], transform=src.window_transform(win), driver="GTiff",
                    compress="deflate", tiled=False)
        for k in ("blockxsize", "blockysize", "interleave"):
            prof.pop(k, None)
    path = C.RAW / name
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr, 1)
    zpath.unlink()
    record(name, path, source="GHSL GHS-BUILT-H ANBH R2023A, epoch 2018 (JRC)", url=url, license=g["license"],
           source_timestamp="2018 epoch, released 2023", resolution="3 arcsec (~90 x 60 m here)",
           meaning="average net height of built-up surfaces in the cell, metres")
    return path


def sentinel2_window(cfg: dict, frame, aoi_local_bounds, offline: bool = False, refresh: bool = False) -> Path:
    """True-colour (TCI, 10 m) window of one fixed, cloud-free Sentinel-2 L2A scene, read with HTTP range
    requests from the public COG archive. Used only as ground *colour*, never as geometry."""
    name = "sentinel2_tci.tif"
    hit = None if refresh else cached(name)
    if hit:
        log.info("using cached %s", name)
        return hit
    if offline:
        raise AcquisitionError(f"offline mode: {name} not cached")
    import rasterio
    from rasterio.windows import from_bounds
    s2 = cfg["sentinel2"]
    req = urllib.request.Request(f"{s2['stac_url']}/collections/sentinel-2-l2a/items/{s2['item']}",
                                 headers={"User-Agent": cfg["user_agent"]})
    with urllib.request.urlopen(req, timeout=60) as r:
        item = json.loads(r.read())
    href = item["assets"]["visual"]["href"]
    x0, y0, x1, y1 = aoi_local_bounds
    pad = 1000
    bounds = (x0 - pad + frame.ox, y0 - pad + frame.oy, x1 + pad + frame.ox, y1 + pad + frame.oy)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        with rasterio.open("/vsicurl/" + href) as src:
            if src.crs.to_string() != cfg["crs_metric"]:
                raise AcquisitionError(f"Sentinel-2 scene CRS {src.crs} differs from {cfg['crs_metric']}; pick a scene in the same UTM zone")
            win = from_bounds(*bounds, src.transform).round_offsets().round_lengths()
            arr = src.read(window=win)
            prof = src.profile.copy()
            prof.update(width=arr.shape[2], height=arr.shape[1], transform=src.window_transform(win), driver="GTiff",
                        compress="deflate", tiled=False)
            for k in ("blockxsize", "blockysize", "interleave"):
                prof.pop(k, None)
    if (arr == 0).all(axis=0).mean() > 0.01:
        raise AcquisitionError("Sentinel-2 window has >1 % no-data pixels; choose a scene covering the whole AOI")
    path = C.RAW / name
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr)
    record(name, path, source=f"Copernicus Sentinel-2 L2A {s2['item']} (TCI true colour, 10 m)", url=href,
           license=s2["license"], source_timestamp=item["properties"]["datetime"],
           cloud_cover=item["properties"].get("eo:cloud_cover"))
    return path
