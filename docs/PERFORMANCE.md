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

**Paired re-run, 2026-11-14 18:51–18:56** (started only after the machine passed an idle check: load < 3, no
process > 40 % CPU, heavy-check queue empty; order 2.0 → 3.1 → 2.0 → 3.1; 2.0 = commit 6ba4c29 code + tiles,
3.1 = current code + tiles; files `16-52-32-code2.0`, `16-53-47`, `16-55-03-code2.0`, `16-56-19`):

| View | 2.0 p95 / p99 (run a, b) | 3.1 p95 / p99 (run a, b) | triangles 2.0 → 3.1 |
|---|---|---|---|
| overview (medium) | 12.8, 11.1 / 14.6, 13.4 | 12.3, 12.3 / 13.5, 14.1 | 229 k → 229 k |
| centre 350 m (medium) | 10.9, 13.0 / 13.3, 14.2 | 13.8, 13.2 / 15.1, 14.6 | 1,277 k → 2,019 k |
| houses 90 m (medium) | 8.3, 8.4 / 9.6, 9.6 | 8.1, 9.0 / 9.5, 10.7 | 723 k → 1,503 k |
| centre low angle (medium) | 12.8, 10.2 / 14.2, 13.6 | 13.6, 13.9 / 14.6, 14.8 | 1,019 k → 1,960 k |
| overview (high) | 13.2, 14.3 / 16.7, 15.8 | 14.6, 11.5 / 16.5, 16.2 | 229 k → 229 k |
| centre 350 m (high) | 11.4, 13.9 / 14.2, 15.3 | 11.7, 13.0 / 14.2, 14.1 | 1,277 k → 823 k |
| houses 90 m (high) | 9.4, 9.4 / 12.1, 12.7 | 11.9, 11.8 / 14.0, 14.3 | 723 k → 1,503 k |
| centre low angle (high) | 15.6, 15.6 / 16.7, 18.5 | 15.3, 13.7 / 16.7, 17.0 | 5,352 k → 3,617 k |
| flight p95 / p99 | 13.1, 13.1 / 14.8, 14.9 | 13.1, 13.0 / 15.0, 15.3 | |

* Differences between versions are mostly inside the run-to-run spread of one version; the largest: houses 90 m
  (high) p99 +1.6–2.2 ms. Teleport hitches (316–698 ms) occur in both versions.
* **Trees on the main thread** (new `treeBuildMs`): mean 0.35 ms per tile, p95 0.8–0.9 ms, max 9.6 ms — not a
  bottleneck. Tile load p95 was 475/721 ms (2.0) vs 384/252 ms (3.1): dominated by noise.
* **Still not a peak-performance measurement**: even the identical overview had p95 11–14 ms here vs 2.9 ms on the
  idle machine of 2026-11-12 (and p50 alternated between ~2.8 and ~7.5 ms between runs), so something else was
  still using the GPU/CPU. Re-run on a truly idle machine before quoting absolute numbers.

Reproduce: `make bench` (add `THROTTLE=4` for the CPU profile). Re-measure on an idle machine before quoting
absolute numbers.

## Camera jumps (2026-11-14, Authenticity 3.2)

The 90–700 ms "teleport stalls" reported above and in earlier runs come from the measurement mode: with vsync and
the frame-rate limit disabled, the CPU queues frames ahead of the GPU and the first texture upload after a jump
(`texSubImage2D`, one 1024² ground texture) waits for the whole backlog — 380–1,230 ms under background GPU load.
With normal browser frame pacing (`e2e/teleport.spec.ts`, 16 jumps, idle machine, paired with commit 9b6b330):
worst frame after a jump **p50 18–19 ms, p95 33–35 ms** for both versions (one 72 ms outlier in one run). GLB
parsing is asynchronous; a Web Worker texture decode was prototyped, measured and removed (no effect). Stage
timings are exposed as `__skvyra.stageMs` / `updateMs` for future profiling.

**100-jump run (Authenticity 3.3)**: 50 cold + 50 warm jumps, normal pacing: p50 17.8, p95 23.1, p99 = max
37.2 ms, none above 50 ms (cold p95 30.3 / p99 37.2 ms; warm p99 18.4 ms). One machine, one run.

## Demo readiness 3.5 (2026-11-15): first visit and network

`FIRSTVISIT=1 NET=none|20mbit|5mbit VIEW=desktop|mobile npx playwright test firstvisit` against a served build
(results `benchmarks/results/*firstvisit*.json`). Bandwidth is throttled in Chrome (CDP); VIEW=mobile is a 390 × 844
touch viewport on this Mac's GPU — **not** a phone GPU test. Places: fly to the centre (350 m), lyceum (250 m),
private sector (150 m) and wait until streaming is idle.

| Build / network | first tile | first useful frame | MB in 10 / 60 s | centre | lyceum | private sector | tour total |
|---|---|---|---|---|---|---|---|
| unpacked (`vite preview`), none | 0.6 s | 2.6 s | 15.7 / 15.7 | 1.6 s, 31.7 MB | 0.4 s, 5.1 MB | 1.0 s, 3.7 MB | 56.3 MB |
| packed (gzip tiles), none | 0.6 s | 2.6 s | 4.35 / 4.35 | 1.6 s, 5.9 MB | 0.4 s, 1.2 MB | 1.2 s, 0.7 MB | 12.2 MB |
| packed + lazy tile metadata, none | 0.6 s | 2.6 s | 3.39 / 3.39 | 1.4 s, 5.2 MB | 1.0 s, 1.2 MB | 0.4 s, 0.8 MB | 10.6 MB |
| same, 20 Mbit/s | 0.9 s | 3.3 s | 3.39 / 3.39 | 2.4 s | 1.6 s | 1.4 s | 8.8 MB |
| same, 5 Mbit/s | 1.4 s | 6.5 s | 3.39 / 3.39 | 5.7 s | 2.8 s | 3.2 s | 8.8 MB |
| same, mobile viewport, 5 Mbit/s | 1.4 s | 6.6 s | 3.39 / 3.39 | 4.0 s | 2.6 s | 2.8 s | 7.3 MB |

Nothing more is transferred while the camera stands still (10 s = 60 s). No URL is requested twice without
throttling; under throttling 3–5 of ~300 tile URLs are requested twice (a tile whose LOD changes mid-flight is
cancelled and requested again later), no loops. Second visit: ~0.04 MB (revalidation). Overview loads LOD2 only
(119 tiles); LOD0/1 only around the camera.

Through Cloudflare's local Pages server (`wrangler pages dev`, real `_headers`, transport compression of text; final
3.5 build with the loading fill): 5 Mbit/s first tile 1.4 s, first useful frame 6.6 s, 3.45 MB, centre 5.5 s,
lyceum 2.8 s, private sector 3.3 s, 0 failed tiles; mobile viewport, unthrottled: 2.75 s, tile cache after the tour
60 MB. The loading fill costs +0.05 MB (context 0.38 → 0.42 MB gzip).

4× CPU throttling (CDP; GPU not throttled — not a weak-device test): static views p99 ≤ 14.5 ms, flight p99 12.9 ms
with one 319 ms hitch (`*-a35-cpu4x.json`).

## Historic Centre 3.4 (2026-11-15): paired A/B/A/B

3.3 code (3c353f7) vs 3.4 working tree, interleaved twice, best of two per view (another Claude session was rendering
in Blender the whole time, so absolute numbers are noisy; the flight and tile-load numbers vary 3x between runs of
the same code and are not compared). New `lyceum 120 m` still in the route.

| View | p95 before / after (ms) | p99 before / after (ms) | Triangles before / after |
|---|---|---|---|
| overview (medium) | 3.6 / 3.6 | 4.8 / 4.5 | 229 k / 229 k |
| centre 350 m (medium) | 3.2 / 3.4 | 4.7 / 5.0 | 823 k / 823 k |
| centre low angle 10° (high) | 4.8 / 4.7 | 6.2 / 6.1 | 3.62 M / 3.62 M |
| lyceum 120 m (medium) | 3.1 / 3.1 | 4.0 / 4.1 | 206 k / 210 k |
| lyceum 120 m (high) | 3.3 / 3.2 | 5.0 / 4.5 | 411 k / 415 k |

Tiles: +0.36 MB of 188 MB; the lyceum tile LOD0 802 → 959 KB, LOD1 217 → 372 KB (composite details, cornices at LOD1).

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
