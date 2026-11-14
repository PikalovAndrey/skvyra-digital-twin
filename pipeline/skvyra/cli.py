"""Pipeline entry point: `python -m skvyra.cli <stage>`. Each stage reads the previous stage's outputs
from disk, so a failed stage can be rerun alone."""
import argparse
import json
import sys
import time

from . import config as C
from .log import log


def stage_acquire(cfg, args):
    from . import acquire, boundary
    from .geo import Frame
    poly, info = boundary.load(cfg, offline=args.offline)
    frame = Frame(cfg["crs_metric"], cfg["origin_utm"] or (0, 0))
    m = frame.geom_to_local(poly)  # origin may be 0 here: metric coords
    area_km2 = m.area / 1e6
    log.info("boundary %s: %.2f km2, valid=%s", info.get("source_id", info["source"]), area_km2, poly.is_valid)
    if cfg["origin_utm"] is None:
        c = m.centroid
        suggestion = [round(c.x / 100) * 100, round(c.y / 100) * 100]
        raise SystemExit(f"config.origin_utm is null. Set it once to a fixed value, e.g. {suggestion} "
                         "(boundary centroid rounded to 100 m), so local coordinates stay stable.")
    buf = frame.geom_to_geo(m.buffer(cfg["context_buffer_m"]))
    w, s, e, n = buf.bounds
    bbox = (round(s, 5), round(w, 5), round(n, 5), round(e, 5))
    acquire.overpass("osm.json", acquire.osm_query(bbox), cfg, offline=args.offline, refresh=args.refresh)
    pad = 0.01  # ~1 km of extra DEM so terrain never ends at the AOI edge
    acquire.ms_buildings(cfg, bbox, offline=args.offline, refresh=args.refresh)
    if cfg.get("sentinel2"):
        acquire.sentinel2_window(cfg, frame, m.buffer(cfg["context_buffer_m"]).bounds, offline=args.offline, refresh=args.refresh)
    if cfg.get("canopy_height"):
        acquire.chm_window(cfg, frame, m.buffer(cfg["context_buffer_m"]).bounds, offline=args.offline, refresh=args.refresh)
    if cfg.get("worldcover"):
        acquire.worldcover_window(cfg, bbox, offline=args.offline, refresh=args.refresh)
    if cfg.get("ghsl_height"):
        acquire.ghsl_height_window(cfg, bbox, offline=args.offline, refresh=args.refresh)
    acquire.dem_window(cfg, (s - pad, w - pad, n + pad, e + pad), offline=args.offline, refresh=args.refresh)
    C.PROCESSED.mkdir(parents=True, exist_ok=True)
    info["area_km2"] = round(area_km2, 3)
    (C.PROCESSED / "boundary.geojson").write_text(json.dumps(boundary.to_geojson(poly, info), ensure_ascii=False))
    (C.PROCESSED / "aoi.json").write_text(json.dumps({"bbox_wgs84_snwe": bbox, **frame.as_dict()}, indent=2))


def stage_normalize(cfg, args):
    from . import canopy, normalize
    normalize.run(cfg)
    if cfg.get("canopy_height"):
        canopy.run(cfg)


def stage_audit(cfg, args):
    from . import audit
    audit.run(cfg)


def stage_build(cfg, args):
    from . import tiles
    tiles.build(cfg, only=args.tiles, force=args.force, workers=args.workers)


STAGES = {"acquire": stage_acquire, "normalize": stage_normalize, "audit": stage_audit, "build": stage_build}


def review_main(argv) -> int:
    """skvyra review apply CHANGES.json [--yes] | revert"""
    from . import review
    from .geo import Frame
    p = argparse.ArgumentParser(prog="skvyra review")
    p.add_argument("action", choices=["apply", "revert"])
    p.add_argument("changes", nargs="?")
    p.add_argument("--yes", action="store_true", help="write the files (default: dry run with diff and validation)")
    p.add_argument("--config", default=str(C.ROOT / "config" / "city.json"))
    a = p.parse_args(argv)
    cfg = C.load(a.config)
    if a.action == "revert":
        return review.revert()
    if not a.changes:
        p.error("apply needs a change-set file (exported from review.html)")
    return review.apply(a.changes, Frame(cfg["crs_metric"], cfg["origin_utm"]), yes=a.yes)


def main(argv=None):
    import sys
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "review":
        return review_main(argv[1:])
    p = argparse.ArgumentParser(prog="skvyra")
    p.add_argument("stage", choices=[*STAGES, "all"])
    p.add_argument("--offline", action="store_true", help="never touch the network; fail if data is not cached")
    p.add_argument("--refresh", action="store_true", help="re-download source data even if cached")
    p.add_argument("--tiles", nargs="*", help="build only these tile ids (e.g. t_+0_+0)")
    p.add_argument("--force", action="store_true", help="rebuild tiles even if inputs are unchanged")
    p.add_argument("--workers", type=int, default=4, help="parallel tile workers (default 4)")
    p.add_argument("--config", default=str(C.ROOT / "config" / "city.json"))
    args = p.parse_args(argv)
    cfg = C.load(args.config)
    for d in (C.RAW, C.PROCESSED, C.GENERATED):  # gitignored, so absent in a fresh clone
        d.mkdir(parents=True, exist_ok=True)
    for name in (list(STAGES) if args.stage == "all" else [args.stage]):
        t = time.time()
        log.info("== stage %s", name)
        try:
            STAGES[name](cfg, args)
        except Exception as e:  # turn tracebacks into one actionable line; full trace with SKVYRA_DEBUG=1
            import os
            if os.environ.get("SKVYRA_DEBUG"):
                raise
            log.error("stage %s failed: %s: %s (rerun with SKVYRA_DEBUG=1 for a traceback)", name, type(e).__name__, e)
            return 1
        log.info("== stage %s done in %.1fs", name, time.time() - t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
