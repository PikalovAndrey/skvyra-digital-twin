"""scripts/deploy_preview.sh end to end, fully local: a throw-away git repository with the deploy scripts, a fake
`npx` (wrangler) and a fake Cloudflare API that also stands in, as an HTTP proxy, for the *.pages.dev hosts
(Access redirect or public page). Nothing reaches Cloudflare."""
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

REPO = Path(__file__).resolve().parents[1]
TOKEN = "TOKEN-SECRET-123"
HEAD_PLACEHOLDER = "HEAD"


def world(**over):
    w = {"project": {"name": "skv", "subdomain": "skv.pages.dev", "production_branch": "production", "source": None},
         "apps": [{"name": "skv previews", "domain": "*.skv.pages.dev", "policies": [
             {"name": "testers", "decision": "allow", "include": [{"email": {"email": "a@example.org"}}]}]}],
         "deployments": [], "public_hosts": [], "deploy_env": "preview", "visible_after": 0,
         "api_down": False, "api_down_after_deploy": False, "npx_calls": [], "probed": [], "preview_lists": 0}
    w.update(over)
    return w


class Fake(BaseHTTPRequestHandler):
    W: dict = {}

    def log_message(self, *a):
        pass

    def send(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # the fake npx reports each call here
        call = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        w = self.W
        w["npx_calls"].append(call)
        if call["args"][2:4] == ["pages", "deploy"]:
            a = call["args"]
            n = len(w["deployments"])
            w["deployments"].append({"id": f"dep-{n}", "environment": w["deploy_env"], "url": f"http://new{n:04d}.skv.pages.dev",
                                     "aliases": [f"http://{a[a.index('--branch') + 1]}.skv.pages.dev"],
                                     "deployment_trigger": {"metadata": {"branch": a[a.index("--branch") + 1],
                                                                         "commit_hash": a[a.index("--commit-hash") + 1]}},
                                     "_visible_at": w["preview_lists"] + w["visible_after"]})
            w["api_down"] = w["api_down_after_deploy"] or w["api_down"]
        self.send(200, b"{}")

    def do_GET(self):
        w = self.W
        if self.path.startswith("http://"):  # proxied request to a *.pages.dev host
            host = urlparse(self.path).netloc
            w["probed"].append(host)
            if host in w["public_hosts"]:
                return self.send(200, b'<html><div id="root"></div></html>')
            return self.send(302, b"", {"Location": f"https://team.cloudflareaccess.com/cdn-cgi/access/login/{host}?kid=SECRETMETA&meta=SECRETMETA"})
        if w["api_down"]:
            return self.send(503, b"down")
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self.send(403, b'{"success": false}')
        p = urlparse(self.path)
        path = p.path.removeprefix("/client/v4")
        ok = lambda r: self.send(200, json.dumps({"success": True, "result": r}).encode())
        if path == "/accounts/acc":
            return ok({"name": "Test account"})
        if path == "/accounts/acc/pages/projects/skv":
            return ok(w["project"])
        if path == "/accounts/acc/pages/projects/skv/deployments":
            env = p.query.split("env=")[-1]
            if env == "preview":
                w["preview_lists"] += 1
            return ok([{k: v for k, v in d.items() if k != "_visible_at"} for d in w["deployments"]
                       if d["environment"] == env and d.get("_visible_at", 0) < w["preview_lists"] + (env != "preview")])
        if path == "/accounts/acc/access/apps":
            return ok(w["apps"])
        if path == "/accounts/acc/access/identity_providers":
            return ok([{"type": "onetimepin"}])
        return self.send(404, b"{}")


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    root = tmp_path_factory.mktemp("deploy")
    repo, fakebin = root / "repo", root / "bin"
    (repo / "scripts").mkdir(parents=True)
    fakebin.mkdir()
    for f in ("deploy_preview.sh", "preflight_preview.py", "pack_demo.py"):
        shutil.copy(REPO / "scripts" / f, repo / "scripts" / f)
    dist = repo / "apps" / "web" / "dist-demo"
    (dist / "tiles").mkdir(parents=True)
    (dist / "index.html").write_text('<!doctype html><div id="root"></div>')
    (dist / "tiles" / "index.json").write_text('{"generated_at": "test"}')
    (repo / "Makefile").write_text("build:\n\t@true\ndemo:\n\t@.venv/bin/python scripts/pack_demo.py > /dev/null\n")
    (repo / ".gitignore").write_text("apps/web/dist-demo/\n.venv\n__pycache__/\n")
    (repo / ".venv").symlink_to(REPO / ".venv")
    git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", *a], cwd=repo, check=True, capture_output=True)
    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "test build")
    npx = fakebin / "npx"
    npx.write_text(f"#!{sys.executable}\nimport json, os, sys, urllib.request\n"
                   "body = json.dumps({'args': sys.argv[1:], 'account': os.environ.get('CLOUDFLARE_ACCOUNT_ID'), "
                   "'stdin': sys.stdin.read(), 'tty': os.isatty(0)}).encode()\n"
                   "urllib.request.urlopen(urllib.request.Request(os.environ['FAKE_CTL'], body, method='POST'))\n"
                   "print('Deployment complete!')\n")
    npx.chmod(0o755)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    yield repo, fakebin, base
    srv.shutdown()


def run(sandbox, mode="upload", branch="preview", **over):
    repo, fakebin, base = sandbox
    Fake.W = world(**over)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    for d in Fake.W["deployments"]:
        d["deployment_trigger"]["metadata"]["commit_hash"] = d["deployment_trigger"]["metadata"]["commit_hash"].replace(HEAD_PLACEHOLDER, head)
    env = {**os.environ, "PATH": f"{fakebin}:{os.environ['PATH']}", "PAGES_ACCOUNT_ID": "acc", "PAGES_PROJECT": "skv",
           "PREVIEW_EMAILS": "a@example.org,b@example.org", "CF_PREFLIGHT_TOKEN": TOKEN, "PAGES_BRANCH": branch,
           "CF_API": f"{base}/client/v4", "PROBE_SCHEME": "http", "http_proxy": base, "HTTP_PROXY": base,
           "no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost", "FAKE_CTL": f"{base}/_ctl",
           "AFTER_UPLOAD_RETRY_S": "0.1"}
    r = subprocess.run(["sh", "scripts/deploy_preview.sh", mode], cwd=repo, env=env, input="skv\nLEFTOVER-STDIN\n",
                       capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    assert TOKEN not in out and "SECRETMETA" not in out  # no token, no Access redirect query in the log
    deploys = [c for c in Fake.W["npx_calls"] if c["args"][2:4] == ["pages", "deploy"]]
    return r.returncode, out, deploys, Fake.W


def old(n, env="preview", branch="preview", commit=HEAD_PLACEHOLDER):
    return {"id": f"old-{n}", "environment": env, "url": f"http://old{n:04d}.skv.pages.dev", "aliases": [],
            "deployment_trigger": {"metadata": {"branch": branch, "commit_hash": commit}}}


def test_preflight_passes_without_any_preview_deployment_and_uploads_nothing(sandbox):
    rc, out, deploys, w = run(sandbox, mode="preflight")
    assert rc == 0 and "preflight: PASSED" in out and not deploys and not w["probed"], out


def test_upload_private_preview(sandbox):
    rc, out, deploys, w = run(sandbox)
    assert rc == 0 and "uploaded and private" in out, out
    (call,) = deploys
    assert call["account"] == "acc" and call["stdin"] == "" and not call["tty"]  # verified account, no prompts possible
    assert call["args"][call["args"].index("--branch") + 1] == "preview"
    assert set(w["probed"]) >= {"new0000.skv.pages.dev", "preview.skv.pages.dev", "skv.pages.dev"}


def test_new_deployment_is_found_by_id_not_by_commit(sandbox):
    # an earlier upload of the same commit on the same branch exists: the check must look at the NEW one
    rc, out, deploys, w = run(sandbox, deployments=[old(0)])
    assert rc == 0 and "new deployment dep-1" in out, out
    assert "new0001.skv.pages.dev" in w["probed"] and "old0000.skv.pages.dev" not in w["probed"]


@pytest.mark.parametrize("change", [
    {"apps": []},                                                                     # no Access at all
    {"apps": [{**world()["apps"][0], "domain": "preview.skv.pages.dev"}]},              # alias only, not <hash> URLs
    {"apps": [{**world()["apps"][0], "policies": [{"name": "all", "decision": "allow", "include": [{"everyone": {}}]}]}]},
    {"apps": [{**world()["apps"][0], "policies": [{"name": "x", "decision": "allow", "include": [{"email": {"email": "stranger@example.com"}}]}]}]},
    {"apps": [{**world()["apps"][0], "policies": [{"name": "x", "decision": "bypass", "include": [{"email": {"email": "a@example.org"}}]}]}]},
    {"project": {**world()["project"], "production_branch": "preview"}},              # upload would become production
    {"project": {**world()["project"], "source": {"type": "github"}}},
    {"deployments": [old(0, env="production", branch="production")]},                 # production already exists
    {"api_down": True},                                                               # unreadable API: refuse
], ids=["no-access", "alias-only", "everyone", "unapproved-email", "bypass", "prod-branch", "git-source", "prod-exists", "api-down"])
def test_unsafe_setup_blocks_the_upload(sandbox, change):
    rc, out, deploys, _ = run(sandbox, **change)
    assert rc == 1 and not deploys, out


def test_production_like_branch_is_refused(sandbox):
    rc, out, deploys, _ = run(sandbox, branch="main")
    assert rc == 1 and not deploys, out


def test_upload_that_lands_in_production_is_critical_and_nothing_is_deleted(sandbox):
    rc, out, deploys, w = run(sandbox, deploy_env="production")
    assert rc == 2 and "CRITICAL" in out and len(deploys) == 1, out
    assert not any("delete" in c["args"] for c in w["npx_calls"])  # only the command is printed


@pytest.mark.parametrize("public", ["new0000.skv.pages.dev", "preview.skv.pages.dev", "skv.pages.dev"])
def test_public_url_after_upload_is_critical_at_once(sandbox, public):
    rc, out, _, w = run(sandbox, public_hosts=[public])
    assert rc == 2 and "CRITICAL: PUBLIC" in out and out.count("after upload:") == 1, out  # no retries
    assert not any("delete" in c["args"] for c in w["npx_calls"])


def test_deployment_that_appears_late_is_waited_for(sandbox):
    rc, out, _, _ = run(sandbox, visible_after=2)
    assert rc == 0 and out.count("after upload: NOT VERIFIED") >= 1, out


def test_deployment_that_never_appears_is_reported_unverified(sandbox):
    rc, out, _, _ = run(sandbox, visible_after=99)
    assert rc == 3 and "UNVERIFIED" in out and out.count("after upload:") == 3, out


def test_api_unreachable_after_upload_is_reported_unverified(sandbox):
    rc, out, deploys, _ = run(sandbox, api_down_after_deploy=True)
    assert rc == 3 and "UNVERIFIED" in out and len(deploys) == 1, out
