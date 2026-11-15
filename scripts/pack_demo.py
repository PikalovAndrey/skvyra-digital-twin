"""Pack the public demo build (apps/web/dist-demo) for a static host. Run by `make demo` after
`vite build --mode demo`; nothing is uploaded or deployed here.

- removes internal files: the review workbench data (review.json; the page itself is not built in demo mode) and
  the Blender export copied from assets/generated
- gzips every .glb in place (deterministic, mtime 0): the viewer inflates them in the browser (src/tiles/gz.ts), so
  any static host serves them byte for byte and no server-side compression of model/gltf-binary is needed
- writes `_headers` (Cloudflare Pages / Netlify syntax): hashed JS/CSS cached forever, tiles one day (every tile URL
  carries ?v=<content hash>; index.json / search.json are always revalidated by the client)
- writes vercel.json with the same rules for Vercel (the actual host) and .vercelignore so that Vercel does not
  serve `_headers` as a file
- copies LICENSES/ (NOTICE.txt: ODbL statement for the derived geodata, Copernicus DEM notice + liability sentence,
  CC BY notices; CDLA-Permissive-2.0 text required when Microsoft footprint data is shared)
- writes 404.html: without it Cloudflare Pages treats the folder as a single-page app and answers a missing tile with
  index.html and status 200; with it a missing file is a real 404 (the viewer reports and retries it)
- writes version.json (git commit, dirty flag, tile index date): `curl <site>/version.json` shows what is deployed
- refuses to finish if a file leaks a local path or a key, the licence notices are missing, or the folder breaks the
  host limits (Vercel Hobby CLI upload: 15 000 files, 100 MB; Cloudflare Pages free: 25 MiB per file)
"""
import gzip
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "apps" / "web" / "dist-demo"
INTERNAL = ["review.json", "review.html", "blender"]
MAX_FILES, MAX_FILE, MAX_TOTAL = 15_000, 25 * 2 ** 20, 100 * 10 ** 6
LICENCE_FILES = ["NOTICE.txt", "CDLA-Permissive-2.0.txt"]
HEADERS = """# Cache rules for the Skvyra demo (written by scripts/pack_demo.py; Cloudflare Pages syntax - `! Name` detaches a header)
/assets/*
  Cache-Control: public, max-age=31536000, immutable
/tiles/*
  Cache-Control: public, max-age=86400
/tiles/index.json
  ! Cache-Control
  Cache-Control: no-cache
/*
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
"""
_cc = lambda v: [{"key": "Cache-Control", "value": v}]
VERCEL = {"headers": [  # same rules as HEADERS; the later matching rule wins in Vercel, so index.json comes last
    {"source": "/(.*)", "headers": [{"key": "X-Content-Type-Options", "value": "nosniff"},
                                    {"key": "Referrer-Policy", "value": "strict-origin-when-cross-origin"}]},
    {"source": "/assets/(.*)", "headers": _cc("public, max-age=31536000, immutable")},
    {"source": "/tiles/(.*)", "headers": _cc("public, max-age=86400")},
    {"source": "/tiles/index.json", "headers": _cc("no-cache")}]}
NOT_FOUND = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>404</title><body style="font:16px system-ui;background:#0f172a;color:#e2e8f0;padding:2em">
<p>Сторінку не знайдено. Not found.</p><p><a style="color:#7dd3fc" href="./">Сквира 3D</a></p></body>
"""
LEAKS = [re.compile(rb"/Users/[A-Za-z]"), re.compile(rb"/home/[a-z]+/"), re.compile(rb"BEGIN [A-Z ]*PRIVATE KEY"),
         re.compile(rb"(?i)(api[_-]?key|secret|token)[\"']?\s*[:=]\s*[\"'][A-Za-z0-9_\-]{16,}")]


def main() -> int:
    if not (OUT / "index.html").exists():
        print("build first: cd apps/web && npx vite build --mode demo")
        return 1
    for n in INTERNAL:
        p = OUT / n
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    raw_total = gz_total = 0
    for p in sorted(OUT.rglob("*.glb")):
        b = p.read_bytes()
        if b[:2] == b"\x1f\x8b":  # already packed
            gz_total += len(b)
            continue
        z = gzip.compress(b, compresslevel=9, mtime=0)
        p.write_bytes(z)
        raw_total, gz_total = raw_total + len(b), gz_total + len(z)
    (OUT / "_headers").write_text(HEADERS)
    (OUT / "vercel.json").write_text(json.dumps(VERCEL, indent=1) + "\n")
    (OUT / ".vercelignore").write_text("_headers\n")
    (OUT / "LICENSES").mkdir(exist_ok=True)
    for n in LICENCE_FILES:
        if (ROOT / "LICENSES" / n).exists():
            shutil.copy(ROOT / "LICENSES" / n, OUT / "LICENSES" / n)
    (OUT / "404.html").write_text(NOT_FOUND)
    git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    idx = OUT / "tiles" / "index.json"
    index = json.loads(idx.read_text()) if idx.exists() else {}
    (OUT / "version.json").write_text(json.dumps({"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain")),
                                                  "tiles_generated_at": index.get("generated_at")}) + "\n")
    files = [p for p in OUT.rglob("*") if p.is_file()]
    problems = []
    for p in files:
        if p.suffix in (".html", ".js", ".css", ".json", ".txt", "") or p.name == "_headers":
            data = p.read_bytes()
            problems += [f"{p.relative_to(OUT)}: matches {rx.pattern!r}" for rx in LEAKS if rx.search(data)]
        if p.stat().st_size > MAX_FILE:
            problems.append(f"{p.relative_to(OUT)}: {p.stat().st_size / 2 ** 20:.1f} MiB > 25 MiB")
    problems += [f"licence file missing: LICENSES/{n}" for n in LICENCE_FILES if not (OUT / "LICENSES" / n).exists()]
    if sum(p.stat().st_size for p in files) > MAX_TOTAL:
        problems.append(f"folder larger than {MAX_TOTAL // 10 ** 6} MB")
    if len(files) > MAX_FILES:
        problems.append(f"{len(files)} files > {MAX_FILES}")
    left = [n for n in INTERNAL if (OUT / n).exists()] + [str(p.relative_to(OUT)) for p in OUT.rglob("review*")]
    problems += [f"internal file still present: {n}" for n in left]
    size = sum(p.stat().st_size for p in files)
    summary = {"dir": str(OUT.relative_to(ROOT)), "files": len(files), "size_mb": round(size / 1e6, 1),
               "largest_mb": round(max(p.stat().st_size for p in files) / 1e6, 2),
               "glb_raw_mb": round(raw_total / 1e6, 1), "glb_gzip_mb": round(gz_total / 1e6, 1), "problems": problems}
    print(json.dumps(summary, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
