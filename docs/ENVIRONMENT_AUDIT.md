# Environment audit

Audited 2026-11-12 on the development laptop. Re-run with `make env` (prints the same table, installs nothing).

## Verified capabilities

| Item | Found | Notes |
|---|---|---|
| OS / arch | macOS 27.2.1 (Darwin 27.2.0), arm64 | |
| CPU | Apple M4 Pro, 14 cores | |
| RAM | 24 GB | |
| GPU | Apple M4 Pro, 20 cores, Metal 4 | Browser WebGL2 via ANGLE Metal ("ANGLE Metal Renderer: Apple M4 Pro") |
| Display | 3456×2234 Retina | DPR 2: viewer caps DPR per quality level |
| Free disk | 67–69 GB of 460 GB (84 % used) | The binding constraint: see budget below |
| Blender | 5.2.3 LTS, bundled Python 3.13.13 | Eevee + Cycles (Metal GPU), glTF importer/exporter, geometry nodes all verified by script |
| Python | 3.14.2 (Homebrew) in `.venv` | shapely 2.2.0 (GEOS 3.14.1), pyproj 3.8.0 (PROJ 9.8.1), rasterio 1.5.2 (GDAL 3.12.2), numpy 2.5.4, matplotlib 3.11.2, pillow 13.0.0, pytest 9.2.0 |
| Node / npm | Node 25.2.1, npm 11.19.1 | pnpm not installed (npm used); bun 1.4.3 present but unused |
| TypeScript | 7.0.2 (native `tsc`) | strict mode |
| Browsers | Google Chrome (used by Playwright via `channel: 'chrome'`, no browser download) | |
| ffmpeg | 8.1.1 | only for turning flyover frames into mp4 |

## Missing (and why it does not matter)

- **System GDAL / PROJ / osmium CLIs**: not installed. Not needed: the Python wheels bundle GEOS, PROJ
  and GDAL, and the town is small enough that Overpass extracts (2 MB) replace a regional `.osm.pbf`
  (Geofabrik Ukraine ≈ 1.5 GB). Install `osmium-tool` only if the project grows to many towns.
- **Remote build box**: a remote machine is configured for the user's *work* backends; this project's full
  pipeline runs in ~15 s locally, so nothing was offloaded.

## Measured resource use

| What | Size / time |
|---|---|
| Raw data (`data/raw`) | ~24 MB (OSM 2.1 MB, Microsoft footprints 12.5 MB, footprint index 7.2 MB, DEM window 0.33 MB, Sentinel-2 window 1.6 MB, GHSL window 12 KB; the 50 MB GHSL tile is deleted after cutting) |
| Processed GeoJSON | 3.5 MB |
| Generated tiles (119 tiles × 3 LODs + metadata + context) | 214 MB (iteration 2; 185 MB in iteration 1) |
| Python venv / web `node_modules` | 237 MB / 293 MB |
| Full pipeline (normalize + audit + build, 6 workers) | ~15 s; tile build alone 4–5 s |
| Blender: import 9 tiles + trees | < 1 s; Eevee 1600×900 preview < 2 s |

## Expected bottlenecks

1. **Disk**: 67 GB free. A whole-city LOD0 `.blend` with packed textures, Cycles animation frames and
   many benchmark runs are the only things that could grow large. Animation frames at 1080p PNG are
   ~3 MB each (240 frames ≈ 0.7 GB): delete frames after encoding.
2. **Machine-wide load**: user rule — at most one heavy check (`tsc`, `vite build`, browser vitest, Go
   builds) at a time across all sessions. Typecheck/build commands here call
   `~/.claude/bin/heavy-busy.sh` first and queue if another job is running (this happened once during
   development: another session's vitest run; the typecheck waited).
3. **Cycles renders** use the GPU heavily; run them one at a time and not during browser benchmarks.

## Configurations

- **Development**: `make web-dev` (Vite dev server serves `assets/generated` directly), quality *medium*.
- **Production**: `make web-build` → static `apps/web/dist` (includes the tiles; ~190 MB). Any static
  host works; enable gzip/brotli for `.glb`/`.json` (GLBs are uncompressed float32 today).
