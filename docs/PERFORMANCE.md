# Performance

Measured on the development machine only (Apple M4 Pro, 14 cores, 24 GB, 20-core GPU), Chrome via
Playwright (`channel: chrome`, ANGLE Metal, **vsync and frame-rate limit off** so frame times show real
cost), 1440×900 CSS px, DPR 1, real Skvyra data. **Nothing here says anything about phones or weaker
laptops** — they were not measured. Reproduce: `make bench` (writes `benchmarks/results/<timestamp>.json`).

## Frame time — three consecutive runs (2026-11-12, after Visual Fidelity 2.0)

Each static sample: wait until streaming is idle, then record every frame for 3 s. Values per run.
Another Claude session was running unit tests on the same machine during these runs.

| View | p50 ms | p95 ms | p99 ms | max ms | draw calls | triangles | JS heap MB |
|---|---|---|---|---|---|---|---|
| overview (medium) | 1.7 / 1.6 / 1.8 | 3.0 / 2.9 / 2.9 | 3.3 / 3.1 / 3.2 | 4.4 / 4.2 / 4.6 | 380 | 230 k | 105 / 100 / 97 |
| centre 350 m (medium) | 2.2 / 2.2 / 2.2 | 2.9 / 2.9 / 2.9 | 3.1 / 3.2 / 3.2 | 3.4 / 3.5 / 3.5 | 154 | 1,278 k | 144 / 164 / 167 |
| houses 90 m (medium) | 1.5 / 1.2 / 1.4 | 2.6 / 2.7 / 2.6 | 2.7 / 2.9 / 2.8 | 3.1 / 3.2 / 3.2 | 76 | 723 k | 176 / 137 / 199 |
| centre low angle 10° (medium) | 2.0 / 1.9 / 1.9 | 2.8 / 2.8 / 2.8 | 3.1 / 3.1 / 3.0 | 3.8 / 3.3 / 3.4 | 197 | 1,019 k | 149 / 159 / 150 |
| overview (high) | 2.5 / 2.5 / 2.5 | 3.0 / 3.1 / 3.1 | 3.2 / 3.3 / 3.3 | 3.7 / 3.8 / 3.8 | 380 | 230 k | 176 / 183 / 166 |
| centre 350 m (high) | 2.3 / 2.3 / 2.3 | 2.9 / 2.9 / 2.9 | 3.1 / 3.2 / 3.2 | 3.6 / 3.7 / 5.5 | 154 | 1,278 k | 189 / 201 / 185 |
| houses 90 m (high) | 2.1 / 2.1 / 2.1 | 2.8 / 2.8 / 2.8 | 3.0 / 3.0 / 3.0 | 3.5 / 3.5 / 3.2 | 76 | 723 k | 175 / 164 / 173 |
| centre low angle 10° (high) | 2.8 / 2.8 / 2.8 | 3.6 / 3.6 / 3.6 | 4.0 / 3.9 / 4.0 | 5.6 / 4.4 / 4.3 | 321 | 5,353 k | 194 / 167 / 204 |

Reproducibility: p95 agrees within 0.1 ms across runs; heap varies by ±30 MB with GC timing.

Final check after the water fixes (one run, `benchmarks/results/2026-11-12-20-11-09.json`, while another
session's unit tests loaded the CPU): same draw calls, triangles within ±0.1 %; p95 2.7–4.1 ms, p99 3.1–5.0 ms,
flight p99 3.9 ms, one 163 ms teleport frame. The +0.1–0.5 ms p95 is consistent with the background load; not
re-measured on an idle machine.

## Realism & Authenticity 3.0 (2026-11-14) — trees from the canopy height model

The machine was **not idle** during these runs (another app, an Android emulator, loaded CPU and GPU; load
average 6–14), so absolute values are 2–3× the 2026-11-12 ones; only runs made back-to-back are compared.

| Pair (same session, same viewer code) | Result |
|---|---|
| VF2 tiles → CHM trees, before tree LOD (`13-27-29` → `13-28-40`) | triangles ×2 in close views (723 k → 1,501 k at 90 m; 1,277 k → 2,758 k centre); p95 +0.3–2.8 ms, p99 +0.5–5.7 ms |
| + tree detail by distance: full crowns < 220 m, one-blob crowns beyond and in LOD1 (`13-39-31`) | centre low angle (high) 5.35 M → 2.69 M triangles; p95/p99 back to the VF2 run of the same session (p99 ≤ 15.6 ms in every view) |
| 4× CPU throttling (CDP `Emulation.setCPUThrottlingRate`; GPU not throttled — **not a weak-device test**) VF2 tiles vs 3.0 (`13-41-06` → `13-42-18`) | static views equal or better (centre low angle high p95 17.2 → 10.2 ms); flight p99 13.6 → 15.6 ms; tile load p95 229 → 382 ms (trees are built on the main thread) |
| Final (with OSM orchards back as rule-based trees, 50.6 k trees), VF2 tiles vs 3.0, heavy external load (`14-09-17` → `14-10-30`) | p99 +0–5 ms in tree-heavy views (worst: centre low angle high 18.0 → 22.9 ms), overview unchanged; flight p99 19.0 → 21.2 ms |

Reproduce: `make bench` (add `THROTTLE=4` for the CPU profile). Re-measure on an idle machine before quoting
absolute numbers.

## Fast camera movement

- Continuous 18 s flight across town at 120 m (≈ 9,300 frames, 14 tile loads while moving): p50 2.0, p95
  3.2, p99 3.5–3.6 ms; one 144–151 ms frame at the very start of each flight, when the camera *teleports* to
  the start point.
- Teleports in general: one 90–310 ms frame after each instant jump (GPU upload/driver sync of the newly
  visible LOD0 tiles; profiled with the Chrome CPU profiler, not shader compiles). Animated fly-to (what
  search and minimap use): ≤ 7.7 ms in 2 of 3 cases, 58 ms in one.
- Mitigations already in place: shader warm-up at start-up (the first close-up used to stall 450 ms), at
  most two LOD upgrades per streaming update, ground textures uploaded as tiles arrive, ground-clamp
  raycast every 4th frame, minimap at 10 Hz.

## Tile loading and memory

- Tile load (fetch + parse + prepare, local server): p50 16–18 ms, p95 79–167 ms, p99 183–189 ms (largest LOD0
  tiles); first streaming idle after start 1.8–1.9 s.
- JS heap during the flight 175–215 MB; stress test (4 cycles of rapid jumps, zooms, resizes, quality
  changes): 83–100 MB at the end of each cycle, no growth.

## Asset sizes

| | before Visual Fidelity 2.0 | after |
|---|---|---|
| LOD0 total / median / max tile | 92.8 MB / 615 KB / 2.13 MB | 127.0 MB / 666 KB / 3.98 MB (incl. pond shore cut) |
| LOD1 total | 36.2 MB | 47.3 MB |
| LOD2 total (what the city overview loads) | 13.4 MB | 13.4 MB |
| Context ground | 1.0 MB | 1.0 MB |

Vertex format: positions float32, colours 16-bit normalised RGBA, no normals (derived in the shader),
UVs float32, feature id + style id float32. gzip shrinks a dense LOD0 tile from 2.64 MB to 0.52 MB (−80 %; not enabled on the dev
server; enable on any static host).

## Pipeline timings

| Stage | Time |
|---|---|
| acquire (cached) | < 5 s |
| normalize / audit | ~4 s / ~5 s |
| build, 119 tiles × 3 LODs, 6 workers | ~12 s |
| `make qa` | ~5 s |
| Blender: Eevee viewpoint render 1600×900 | < 2 s each |
| Blender: Cycles still 1920×1080, 96 samples | 15–22 s |
