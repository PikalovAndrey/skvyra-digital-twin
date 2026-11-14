"""Project configuration: one JSON file, validated on load. No geographic constants live in code."""
import json
import os
from pathlib import Path

# SKVYRA_ROOT relocates all data/ and assets/ paths (used by the offline integration test fixture).
ROOT = Path(os.environ.get("SKVYRA_ROOT") or Path(__file__).resolve().parents[2])
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
OVERRIDES = ROOT / "data" / "overrides" / "overrides.json"
GENERATED = ROOT / "assets" / "generated"

REQUIRED = ["name", "boundary", "crs_metric", "context_buffer_m", "tile_size_m", "floor_height_m",
            "seed", "overpass_url", "user_agent", "dem", "osm_license"]


class ConfigError(ValueError):
    pass


def load(path: Path = ROOT / "config" / "city.json") -> dict:
    try:
        cfg = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(f"cannot read config {path}: {e}") from e
    missing = [k for k in REQUIRED if k not in cfg]
    if missing:
        raise ConfigError(f"config {path} missing keys: {missing}")
    if not str(cfg["crs_metric"]).startswith("EPSG:"):
        raise ConfigError("crs_metric must be an EPSG code, e.g. EPSG:32635")
    if not (50 <= cfg["tile_size_m"] <= 5000):
        raise ConfigError("tile_size_m must be within 50..5000")
    if not (2.0 <= cfg["floor_height_m"] <= 6.0):
        raise ConfigError("floor_height_m must be within 2..6")
    o = cfg.get("origin_utm")
    if o is not None and (len(o) != 2 or not all(isinstance(v, (int, float)) for v in o)):
        raise ConfigError("origin_utm must be [easting, northing] or null")
    return cfg
