# Publishing the demo (static hosting) — prepared, not deployed

Nothing in this repository deploys anything on its own. `make demo` builds a self-contained static folder;
`scripts/deploy_preview.sh` uploads it only after an explicit typed confirmation, and only as a **preview**.

## Build

```
make build          # tiles (only changed ones are rebuilt)
make demo           # apps/web/dist-demo: viewer only, gzipped tiles, _headers, 404.html, version.json, checks
```

`make demo` = `vite build --mode demo` (viewer page only — the review workbench `review.html` is not built) +
`scripts/pack_demo.py`, which

- removes internal files copied from `assets/generated`: `review.json` (workbench data), `blender/` (Blender export);
- gzips every `.glb` in place (189 MB → 39 MB); the viewer inflates them with the browser's `DecompressionStream`
  (`src/tiles/gz.ts`), so no host-side compression of `model/gltf-binary` is needed and any static host works;
- writes `_headers` (Cloudflare Pages syntax): `/assets/*` immutable (hashed file names), `/tiles/*` one day (every
  tile URL carries `?v=<content hash>`, the context mesh `?v=<build time>`), `/tiles/index.json` `no-cache` (it names
  the current tile versions; the viewer also fetches it, `search.json` and `quality_gaps.json` with
  `cache: 'no-cache'`);
- writes `404.html` — without a top-level 404 page Cloudflare Pages switches to single-page-app mode and answers
  every missing path (e.g. a missing tile) with `index.html` and status 200;
- writes `version.json` (`commit`, `dirty`, `tiles_generated_at`): `curl https://<site>/version.json` shows what is
  deployed;
- fails if a text file contains a local path or a key, a file is larger than 25 MiB, the folder has more than
  20 000 files, or `review*` is still present.

Result (2026-11-15): 486 files, 46 MB, largest file 1.4 MB, no problems.

## How the tiles travel (checked)

| Case | What the browser receives | Viewer |
|---|---|---|
| Cloudflare Pages (`model/gltf-binary` is not on Cloudflare's compression list) | the gzip file byte for byte, no `Content-Encoding` | inflates once |
| a CDN that compresses everything | transport encoding on top; the browser removes it | inflates once |
| a host that serves the gzip file with `Content-Encoding: gzip` | plain GLB (browser inflated it) | passes through (`glTF` magic) |
| dev server / unpacked build | plain GLB | passes through |
| corrupt or truncated file | `DecompressionStream` error | tile counted as failed, retried with back-off, shown in the status |
| missing file | HTTP 404 (thanks to `404.html`) | same |
| browser cache | same bytes as the first response (ETag → 304) | same as first response |

The viewer decides by the first two bytes (`1f 8b` = gzip), never by headers, so it cannot inflate twice.
Tests: `src/tiles/gz.test.ts` (real HTTP server with each variant, corrupt and truncated gzip, 404). Local check with
Cloudflare's own asset server (`npx wrangler@4.156.0 pages dev apps/web/dist-demo`, local only, nothing uploaded):
tile served as `model/gltf-binary` without `Content-Encoding`, bytes identical to the packed file; HTML/JS/JSON
transport-gzipped; `_headers` applied; revalidation `304`; missing tile and `review.html` `404`; first visit in Chrome
at 5 Mbit/s with 0 failed tiles.

## Cloudflare Pages: preview via Direct Upload (recommended first step)

Checked against the Cloudflare docs on 2026-11-15:

- **Free plan:** 20 000 files per site, 25 MiB per file, `_headers` up to 100 rules; requests to static assets are
  free and unlimited (no Pages Functions are used here); unlimited preview deployments; 100 projects per account
  (fewer may be created during the first 48 hours).
- **Direct Upload** = upload a prebuilt folder with Wrangler, no Git connection. A Direct Upload project cannot be
  switched to Git integration later (a new project would be needed) — fine here, nothing is linked automatically.
- **Preview vs production:** a deployment to a branch other than the project's production branch is a preview:
  unique URL `<hash>.<project>.pages.dev` (kept, always reachable) plus an alias `<branch>.<project>.pages.dev`
  that moves to the latest upload of that branch. Previews carry `X-Robots-Tag: noindex` automatically.
- **Previews are public by default.** Project → Settings → General → *Enable access policy* puts Cloudflare Access
  in front of the preview URLs (`*.<project>.pages.dev`); viewers then sign in (e.g. one-time e-mail code) and must
  be allowed by the policy. It does not protect `<project>.pages.dev` itself or a custom domain (docs:
  "Known issues → Enable Access on your pages.dev domain"). Cloudflare may ask to choose a Zero Trust plan (there is a
  free one) when Access is used for the first time — the account owner's decision.
- **Rollback:** production deployments can be rolled back instantly in the dashboard (Deployments → ⋯ → Rollback).
  Previews are not rollback targets, but each preview URL `<hash>.<project>.pages.dev` stays as uploaded, so going back
  = sharing the older hash URL (`wrangler pages deployment list`), or re-uploading an older commit's build. Remove a
  preview: `wrangler pages deployment delete <id> --project-name <project>`; remove everything:
  `wrangler pages project delete <project>`.
- **Caching:** Pages sends an `ETag` for every file and keeps uploaded assets on its CDN until the next deployment;
  without `_headers` the browser gets `max-age=0, must-revalidate`. Our `_headers` overrides that for hashed JS/CSS and
  tiles (see above). No cache rules on a custom domain are needed or wanted.
- **Relative URLs:** `base: './'`, one page, no client routes — works on `<hash>.<project>.pages.dev`, on the alias and
  on any sub-path.

### First test deployment — steps

1. (account owner) Create / sign in to a Cloudflare account. Pick a project name (becomes `<name>.pages.dev`).
2. `npx wrangler@4.156.0 login` (opens the browser; the token stays on this machine).
3. `npx wrangler@4.156.0 pages project create <name> --production-branch production` — creates an empty project;
   the production branch gets a name the script never uses.
4. Dashboard → Workers & Pages → `<name>` → Settings → General → **Enable access policy**; add the e-mails of the
   people who may look (Access → Applications → the project's policy).
5. `git status` clean (commit first), then `make build && make demo`.
6. `PAGES_PROJECT=<name> DRY_RUN=1 sh scripts/deploy_preview.sh` — checks only, prints the exact command.
7. `PAGES_PROJECT=<name> sh scripts/deploy_preview.sh` — type the project name to confirm; uploads to branch
   `preview` and prints `https://<hash>.<name>.pages.dev` and `https://preview.<name>.pages.dev`.
8. Check: `curl -I https://preview.<name>.pages.dev/` from a browser session that is not signed in → redirect to
   Cloudflare Access (not the page); `curl https://<name>.pages.dev/` → no demo (nothing deployed to production);
   after signing in: `/version.json` shows the commit, the first visit loads, tiles have no errors.

The script refuses `main`/`master`/`production`/`prod` as branch, a dirty git tree, and a build whose pack check fails.

## Other hosts

| Option | Notes |
|---|---|
| Netlify | `_headers` syntax is similar, but `! Header` (detach) is Cloudflare-only: drop the `/tiles/index.json` block; free plan has a bandwidth quota |
| GitHub Pages | no custom headers: default short caching; no `_headers`; 404.html is honoured |
| Object storage + CDN (e.g. Cloudflare R2) | headers set per object at upload |

## Network cost (measured, `e2e/firstvisit.spec.ts`, packed demo)

| | value |
|---|---|
| HTML + JS + CSS + index + search | 0.43 MB (JS 1.37 MB raw, 0.39 MB gzip) |
| whole town at overview (first useful frame) | 3.45 MB |
| tour: overview + centre + lyceum + private sector | 7–11 MB |
| second visit (cache) | ~0.04 MB (revalidation only) |

Rough traffic: 1 000 visitors × ~10 MB ≈ 10 GB (static traffic is not billed on Pages).

## Licences and attribution in the public build

Shown in the viewer (attribution line, short form on phones; full source list with licences and dates on click):
© OpenStreetMap contributors (ODbL — the tiles are a Produced Work, attribution required), Microsoft Building
Footprints (CDLA-Permissive-2.0), Copernicus WorldDEM-30 and Sentinel-2, GHSL (CC BY 4.0), Meta/WRI canopy height
(CC BY 4.0), ESA WorldCover (CC BY 4.0). Landmark photos (Wikimedia Commons, CC BY-SA) are only linked from the
building card with author and licence, never copied into the build. Full list: `docs/LICENSES.md`.

## Not in the public build

The review workbench (edits geographic overrides), its data, Blender files, pipeline code, raw data. There is no
server code and no secret in the build (checked by `pack_demo.py`).

## Browser requirements (MDN browser-compat-data 8.1.9)

| Needed | Safari iOS | Chrome Android | Firefox Android |
|---|---|---|---|
| `DecompressionStream` (tiles) — the binding minimum | 16.4 | 80 | 113 |
| WebGL 2 | 15 | 58 | 51 |
| Pointer events (touch pan / rotate / pinch) | 13 | 55 | 79 |
| `100dvh` (card height on phones; without it the card is just not height-limited) | 15.4 | 108 | 101 |

On iOS every browser uses WebKit, so "Chrome on iPhone" = Safari 16.4+. Older browsers get the WebGL error page or
failed tiles. Not tested on real phones — see `docs/LIMITATIONS.md`.
