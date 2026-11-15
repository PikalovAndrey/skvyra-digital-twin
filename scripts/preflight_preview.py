"""Read-only preflight before uploading the demo to a Cloudflare Pages PREVIEW. Run by scripts/deploy_preview.sh
(`preflight` and, again, right before `upload`); changes nothing, here or at Cloudflare.

Environment:
  PAGES_ACCOUNT_ID    Cloudflare account ID (dashboard → account home → Account ID)
  PAGES_PROJECT       existing Pages project name
  PREVIEW_EMAILS      comma-separated e-mail addresses approved to see the preview (kept out of the repository)
  CF_PREFLIGHT_TOKEN  API token, read-only, this account only: Account Settings Read, Cloudflare Pages Read,
                      Access: Apps and Policies Read, Access: Organizations, Identity Providers, and Groups Read
  PAGES_BRANCH        deployment branch (default: preview)

Checks (each prints ok / FAIL; exit 1 on any FAIL):
  local   git tree clean; dist-demo/version.json = HEAD, dirty false; pack check (no internal files, no leaks, limits);
          no source maps
  account the token reaches exactly this account (name shown for you to confirm)
  project exists; production branch is `production`; deployment branch is not it; no Git source (Direct Upload);
          no production deployment
  access  an Access application covers preview.<subdomain>; every policy is `allow` and includes only e-mail rules;
          those e-mails are a subset of PREVIEW_EMAILS; one-time PIN login is enabled (warning only)
  probes  https://<branch>.<subdomain>/ without login → redirect to Cloudflare Access; https://<subdomain>/ does not
          serve the demo
"""
import fnmatch
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "apps" / "web" / "dist-demo"
API = os.environ.get("CF_API", "https://api.cloudflare.com/client/v4")
SCHEME = os.environ.get("PROBE_SCHEME", "https")  # tests use http against a local stand-in
DEMO_MARK = '<div id="root">'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def http(url, token=None):
    """(status, headers, body) without following redirects."""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=20) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except OSError as e:
        return 0, {}, str(e).encode()


def api(path, token):
    status, _, body = http(f"{API}{path}", token)
    try:
        data = json.loads(body)
    except ValueError:
        data = {}
    return status, data


def mask(e):
    user, _, dom = e.partition("@")
    return f"{user[:2]}***@{dom}"


def local_checks(check):
    git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    head = git("rev-parse", "HEAD")
    check(git("status", "--porcelain") == "", "git tree clean (no uncommitted changes)")
    ver = json.loads((DIST / "version.json").read_text()) if (DIST / "version.json").exists() else {}
    check(ver.get("commit") == head and ver.get("dirty") is False, f"dist-demo/version.json = HEAD {head[:7]} (has {str(ver.get('commit'))[:7]}, dirty {ver.get('dirty')})")
    pack = subprocess.run([sys.executable, str(ROOT / "scripts" / "pack_demo.py")], cwd=ROOT, capture_output=True, text=True)
    check(pack.returncode == 0, "pack check: no internal files, no local paths or keys, within host limits")
    maps = [str(p.relative_to(DIST)) for p in DIST.rglob("*.map")]
    check(not maps, f"no source maps in the package {maps[:3]}")


def remote_checks(check, account, project, branch, emails, token):
    s, d = api(f"/accounts/{account}", token)
    check(s == 200 and d.get("success"), f"account {account}: {d.get('result', {}).get('name', '?')!r} (HTTP {s}) - is this the right account?")
    s, d = api(f"/accounts/{account}/pages/projects/{project}", token)
    p = d.get("result") or {}
    check(s == 200 and p.get("name") == project, f"Pages project {project!r} exists in this account (HTTP {s})")
    if not p:
        return
    sub = p.get("subdomain", "")
    check(p.get("production_branch") == "production", f"production branch is 'production' (is {p.get('production_branch')!r})")
    check(branch != p.get("production_branch") and branch not in ("main", "master", "production", "prod"), f"deployment branch {branch!r} is a preview branch")
    check(not p.get("source"), "Direct Upload project, no Git repository connected")
    s, d = api(f"/accounts/{account}/pages/projects/{project}/deployments?env=production", token)
    prod = [x for x in d.get("result") or [] if x.get("environment") == "production"]
    check(s == 200 and not prod, f"no production deployment (found {len(prod)}, HTTP {s})")

    host = f"{branch}.{sub}"
    s, d = api(f"/accounts/{account}/access/apps", token)
    apps = d.get("result") or []
    def hosts(a):
        out = [a.get("domain") or ""] + list(a.get("self_hosted_domains") or [])
        out += [x.get("uri", "") for x in a.get("destinations") or [] if isinstance(x, dict)]
        return [h.split("/")[0] for h in out if h]
    covering = [a for a in apps if any(fnmatch.fnmatch(host, h) for h in hosts(a))]
    check(s == 200 and bool(covering), f"Cloudflare Access application covers {host} (HTTP {s}, {len(covering)} found)")
    approved = {e.strip().lower() for e in emails if e.strip()}
    for a in covering:
        pols = a.get("policies") or []
        check(bool(pols), f"Access app {a.get('name')!r} has policies")
        for pol in pols:
            inc = pol.get("include") or []
            got = {(r.get("email") or {}).get("email", "").lower() for r in inc if set(r) == {"email"}}
            other = [list(r)[0] for r in inc if set(r) != {"email"}]
            check(pol.get("decision") == "allow", f"policy {pol.get('name')!r}: decision allow (is {pol.get('decision')!r})")
            check(not other and bool(got), f"policy {pol.get('name')!r}: includes only e-mail addresses (other rules: {other})")
            extra = got - approved
            check(not extra, f"policy {pol.get('name')!r}: {len(got)} e-mail(s), all approved (not approved: {sorted(mask(e) for e in extra)})")
    s, d = api(f"/accounts/{account}/access/identity_providers", token)
    if s == 200 and not any(x.get("type") == "onetimepin" for x in d.get("result") or []):
        print("warn one-time PIN login is not enabled: testers without a Cloudflare account cannot sign in")

    st, hd, _ = http(f"{SCHEME}://{host}/")
    loc = {k.lower(): v for k, v in hd.items()}.get("location", "")
    check(st in (301, 302, 303, 307) and "cloudflareaccess.com" in loc, f"{host} without login → Cloudflare Access ({st} {loc[:50]})")
    st, _, body = http(f"{SCHEME}://{sub}/")
    check(not (st == 200 and DEMO_MARK.encode() in body), f"{sub} (production) does not serve the demo ({st})")


def main(argv=None) -> int:
    env = os.environ
    missing = [k for k in ("PAGES_ACCOUNT_ID", "PAGES_PROJECT", "PREVIEW_EMAILS", "CF_PREFLIGHT_TOKEN") if not env.get(k)]
    if missing:
        print("set " + ", ".join(missing) + " (see the header of scripts/preflight_preview.py)")
        return 1
    fails = []
    def check(ok, what):
        print(("ok   " if ok else "FAIL ") + what)
        if not ok:
            fails.append(what)
    if "--remote-only" not in (argv or sys.argv[1:]):
        local_checks(check)
    remote_checks(check, env["PAGES_ACCOUNT_ID"], env["PAGES_PROJECT"], env.get("PAGES_BRANCH", "preview"),
                  env["PREVIEW_EMAILS"].split(","), env["CF_PREFLIGHT_TOKEN"])
    print(f"preflight: {'PASSED' if not fails else f'{len(fails)} problem(s) - nothing may be uploaded'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
