# Publishing the demo (static hosting)

**Live since 2026-11-15:** https://skvyra-twin.vercel.app — Vercel, personal Hobby scope, project `skvyra-twin`,
static files only, public. First release = commit `257659a`. Nothing in this repository deploys on its own: there is
no Git integration and no CI deploy; every release is the manual command below. Cloudflare Pages (preview behind
Access) was prepared first and is kept as an unused alternative (end of this file).

## Build

```
make build          # tiles (only changed ones are rebuilt)
make demo           # apps/web/dist-demo: viewer only, gzipped tiles, vercel.json + _headers, 404.html, version.json, LICENSES/, checks
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
- writes `vercel.json` with the same cache rules for Vercel (the `/tiles/index.json` rule comes last: in Vercel the
  last matching rule wins) and `.vercelignore` (`_headers`), so Vercel does not serve the Cloudflare file;
- copies `LICENSES/NOTICE.txt` (ODbL statement for the derived geodata, Copernicus DEM notice and liability
  sentence, CC BY notices) and `LICENSES/CDLA-Permissive-2.0.txt`; the viewer links the notice from
  *Джерела та ліцензії*;
- writes `404.html` — without a top-level 404 page Cloudflare Pages switches to single-page-app mode and answers
  every missing path (e.g. a missing tile) with `index.html` and status 200;
- writes `version.json` (`commit`, `dirty`, `tiles_generated_at`): `curl https://<site>/version.json` shows what is
  deployed;
- fails if a text file contains a local path or a key, a licence file is missing, a file is larger than 25 MiB, the
  folder has more than 15 000 files or 100 MB (Vercel Hobby CLI upload limits), or `review*` is still present.

Result (2026-11-15): 490 files, 46 MB, largest file 1.4 MB, no problems.

## Vercel (the actual host)

**Release** (only from a clean, committed tree; the package must be rebuilt from that commit):

```
git status --porcelain                       # must print nothing
make build && make demo                      # rebuilds tiles that changed + the package
cat apps/web/dist-demo/version.json          # commit = git rev-parse HEAD, "dirty": false
npx --yes vercel@65.16.0 whoami              # andriipikalov (personal account)
npx --yes vercel@65.16.0 deploy apps/web/dist-demo --prod --yes \
    --project skvyra-twin --scope andrtan10mailcom-7422s-projects
cd apps/web && SMOKE_URL=https://skvyra-twin.vercel.app/ SMOKE_ACCESS=0 SMOKE_PREVIEW=0 npx playwright test smoke
```

`--project` without `--create-if-missing` refuses to create a new project, and `--scope` pins the personal Hobby
team, so the upload cannot land in another project or team. The CLI is pinned (65.16.0). Vercel's own build step
is not used: the folder is static, there is no `package.json` in it, `vercel.json` only sets headers.

**Versioning.** `https://skvyra-twin.vercel.app/version.json` = commit, `dirty`, tile build time of what is live
(`max-age=0, must-revalidate`). Each deploy also has its own immutable URL
(`npx vercel@65.16.0 ls skvyra-twin --scope andrtan10mailcom-7422s-projects`); unique URLs are behind Vercel's
standard deployment protection, only the production domain is public. Tag a release in git when it goes live
(`git tag demo-YYYY-MM-DD`), so a rollback target can be rebuilt.

**Rollback.** Hobby can roll back only to the *immediately previous* production deployment:
`npx vercel@65.16.0 rollback <previous deployment URL or id> --scope andrtan10mailcom-7422s-projects`
(check with `vercel rollback status skvyra-twin`). After a rollback Vercel stops assigning new production
deployments to the domain until `vercel promote <deployment>` is run. Going back further on Hobby = check out the
older tag, `make build && make demo`, deploy again.

**Caching — real headers measured on https://skvyra-twin.vercel.app (2026-11-15):**

| Path | Cache-Control | Encoding | Notes |
|---|---|---|---|
| `/`, `index.html` | `public, max-age=0, must-revalidate` | — | Vercel default; ETag → 304 |
| `tiles/index.json` | `no-cache` | br | names the current tile versions |
| `tiles/*.glb?v=<hash>` (also `context.glb`) | `public, max-age=86400` | br on top of the gzip file | browser removes br, viewer inflates once; bytes identical to the package (smoke test) |
| `tiles/t*.json?v=<hash>` (lazy tile metadata) | `public, max-age=86400` | br | |
| `assets/*.js`, `assets/*.css` (hashed) | `public, max-age=31536000, immutable` | br | |
| `version.json`, `search.json`, `quality_gaps.json` | `public, max-age=0, must-revalidate` | br (JSON) | the viewer fetches them with `cache: 'no-cache'` anyway |
| missing file | status 404, `404.html` | — | a missing `tiles/…` path gets `max-age=86400` too (Vercel applies header rules regardless of status); harmless because every tile URL carries its content hash |
| `_headers`, `vercel.json`, `review.html` | 404 | — | |

All headers from the Cloudflare `_headers` file are reproduced (cache rules, `nosniff`, `Referrer-Policy`); the
first release was deployed with the same rules written by hand, now they come from `pack_demo.py`. Vercel keeps
static files in its CDN for the lifetime of a deployment; a new deployment changes `index.json` and the hashed URLs.

**Hobby plan (checked in the Vercel docs and terms, 2026-11-15).** Hobby is for personal or non-commercial use only
(Terms of Service §4) — this project has no ads, sales or sponsors. Monthly included usage: 100 GB Fast Data Transfer,
1 000 000 CDN requests, 10 GB Fast Origin Transfer; 100 deployments per day; CLI upload up to 100 MB and 15 000
files. A Hobby account cannot buy extra usage: when a limit is reached the feature stops until the 30-day window
passes (no charge). Caching does not reduce Fast Data Transfer for static files. Measured first visit with the
tour: ~310 requests and ~9–10 MB (`e2e/firstvisit.spec.ts`), so the request quota binds first — roughly 3 000
full first visits a month (~1 000 000 / 310); 100 GB ≈ 10 000 such visits. Repeat visits within a day load tiles from
the browser cache. Vercel may disable a Hobby deployment at its discretion (terms §4): keep the package reproducible
(`make demo` from a tag) so it can be moved to another static host.

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

## Alternative, not used: Cloudflare Pages preview behind Access

Prepared before the decision to publish openly on Vercel (2026-11-15). Not used: Zero Trust was never activated (it
needs payment details even on the free plan). An empty Pages project `skvyra-twin` exists in the Cloudflare account
that wrangler is logged in to (no deployments); delete it with
`npx wrangler@4.156.0 pages project delete skvyra-twin` if it is not wanted. Note: wrangler 4.156 delegates
`pages project create` for a missing project to Workers and tries to deploy the current directory — create the
project from an empty directory with `--force`. The text below is kept for that alternative.

### Cloudflare Pages: preview via Direct Upload

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

In the Cloudflare dashboard (account owner):

1. Zero Trust → onboarding: team name, plan **Zero Trust Free** (payment details are required even for the free plan;
   nothing is charged). Every person who signs in to Access takes a seat.
2. Zero Trust → Integrations → Identity providers → Add new → **One-time PIN** (testers without a Cloudflare account
   get an e-mail code).
3. My Profile → API Tokens → Create Token → Custom token, **Account** permissions, all *Read*: Account Settings,
   Cloudflare Pages, Access: Apps and Policies, Access: Organizations, Identity Providers, and Groups; Account
   Resources: only this account. This is `CF_PREFLIGHT_TOKEN` (read-only; it cannot upload or change anything).

On this machine:

4. `npx wrangler@4.156.0 login`
5. `CLOUDFLARE_ACCOUNT_ID=<id> npx wrangler@4.156.0 pages project create <name> --production-branch production`

In the dashboard again:

6. Workers & Pages → `<name>` → Settings → General → **Enable access policy**; then Zero Trust → Access → Applications
   → the application for `*.<name>.pages.dev` → policy → Include → **Emails** = the approved addresses, nothing else
   (no "Everyone", no domains). Save.

Preflight and upload (environment: `PAGES_ACCOUNT_ID`, `PAGES_PROJECT`, `PREVIEW_EMAILS=a@x,b@y`,
`CF_PREFLIGHT_TOKEN`; the addresses and the token stay out of the repository):

7. `sh scripts/deploy_preview.sh preflight` — rebuilds the package from HEAD, runs every check, uploads nothing.
8. `sh scripts/deploy_preview.sh upload` — the same preflight again, then the typed confirmation, then the upload to
   branch `preview`, then a check that there is still no production deployment.
9. Smoke test (below).

### Preflight (`scripts/preflight_preview.py`, read-only) — prevents, not only detects

| Check | Fails when |
|---|---|
| git tree clean, `dist-demo/version.json` = HEAD, `dirty: false` (package rebuilt from HEAD just before) | uncommitted changes, stale package |
| pack check, no source maps | internal file, local path or key, host limits |
| account | the token cannot read this account ID (its name is printed to confirm) |
| project | missing, production branch not `production`, deployment branch is a production-like name, Git source connected, any production deployment |
| Access | no Access application covering both the branch alias `preview.<subdomain>` and unique deployment hosts `<hash>.<subdomain>`; a policy that is not `allow`, includes anything but e-mail addresses, or an address not in `PREVIEW_EMAILS` (shown masked); one-time PIN missing → warning |

Before the first upload the preview hosts do not exist yet, so the preflight checks Access through the API only (an
HTTP probe there would block the first deployment for ever). Right after the upload, `--after-upload` (no login):

| Check | Fails when |
|---|---|
| deployment | no preview deployment of this commit on the branch (API) |
| unique URL `https://<hash>.<subdomain>/` and every alias | not redirected to Cloudflare Access, or the demo page is served |
| production | `https://<subdomain>/` serves the demo, or a production deployment exists |

The new deployment is identified by its ID (IDs listed right before the upload), environment `preview`, branch and
commit — an earlier upload of the same commit is not mistaken for it. Exit codes of `deploy_preview.sh upload`:
0 private; 1 refused before the upload (nothing uploaded); **2 CRITICAL** — a URL is readable without login or a
production deployment exists, reported at once without retries; 3 uploaded but not verifiable (deployment not visible
in the API after 3 attempts 15 s apart, or API unreachable) — treat as possibly public. Nothing is deleted
automatically; the script prints the command to take the project offline for you to decide. No token, cookie or
Access redirect query is printed; the smoke test runs without Playwright traces (they would store the session).

Tested end to end without Cloudflare (`tests/test_deploy_preview.py`: fake wrangler + fake Cloudflare API/hosts):
preflight passes with no preview deployment yet; missing Access, alias-only Access, "everyone", unapproved address,
bypass, production branch `preview`, Git source, existing production, unreachable API and branch `main` all block the
upload; an upload landing in production and a public unique URL / alias / production URL give exit 2 at once; a late
deployment is waited for; a never-visible one and an API outage after the upload give exit 3; the token and the
Access query never appear in the output. The upload command gets the verified account ID, the pinned
wrangler and no terminal on stdin (it cannot offer to create a project or switch account). Tested:
`tests/test_unit.py::test_preview_preflight_blocks_every_unsafe_cloudflare_setup` (fake Cloudflare API: the good
setup passes, each unsafe variant fails before the upload; after it a public unique URL, alias or production URL,
a production deployment or a missing deployment fails; a not yet existing preview host does not block the first
upload).

## Smoke test (`e2e/smoke.spec.ts`, read-only)

Vercel (public): `SMOKE_URL=https://skvyra-twin.vercel.app/ SMOKE_ACCESS=0 SMOKE_PREVIEW=0 npx playwright test smoke`
— all checks passed on 2026-11-15 against the first release. The Cloudflare preview variant:

```
cd apps/web
SMOKE_URL=https://preview.<name>.pages.dev/ SMOKE_PROD_URL=https://<name>.pages.dev/ npx playwright test smoke --headed
```

A Chrome window opens on the Access login; sign in there (e-mail code) — the test continues by itself and reuses that
session for the HTTP checks (or set `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` of an Access service token).
It checks:

- without login the preview is stopped by Access; the production URL does not serve the demo;
- `version.json` = the commit of the local `dist-demo` (or `SMOKE_COMMIT`), `dirty: false`;
- index page, `X-Robots-Tag: noindex`; `tiles/index.json` `no-cache`;
- a tile and the context mesh: gzip file inside, `glTF` after exactly one inflate, bytes identical to the local
  package, `max-age=86400`, revalidation `304`; hashed JS `immutable`;
- not served: missing tile, `review.html`, `review`, `review.json`, `blender/`, `_headers`, scripts, `.git`, overrides;
- desktop: welcome card, overview (119 tiles, 0 failed), lyceum and synagogue via Places (waits until the camera has
  arrived), no page errors; phone viewport 390 px: welcome, synagogue, no horizontal scroll. Screenshots in
  `renders/smoke/`. The phone part is Chrome on the Mac, not a phone.

Verified locally against Cloudflare's own asset server (`wrangler pages dev`, no Access → `SMOKE_ACCESS=0
SMOKE_PREVIEW=0`): all checks pass.

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

Rough traffic: 1 000 visitors × ~10 MB ≈ 10 GB; on Vercel Hobby see the request quota above.

## Licences and attribution in the public build

Shown in the viewer (attribution line, short form on phones; full source list with licences and dates on click,
plus a link to `LICENSES/NOTICE.txt` with the full notices, the ODbL statement and the Copernicus liability sentence):
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
