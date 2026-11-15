#!/bin/sh
# Cloudflare Pages PREVIEW of the demo (Direct Upload, no Git link). Not run by any make target. See docs/DEPLOY.md.
#
#   sh scripts/deploy_preview.sh preflight   # rebuild the package from HEAD + read-only checks; uploads nothing
#   sh scripts/deploy_preview.sh upload      # the same preflight, then (only if it passed) typed confirmation, upload
#
# Environment: PAGES_ACCOUNT_ID, PAGES_PROJECT, PREVIEW_EMAILS, CF_PREFLIGHT_TOKEN (read-only API token), optional
# PAGES_BRANCH (default preview); upload also needs `npx wrangler@4.156.0 login` for that account.
# Prevention, not detection: the upload happens only after the preflight confirmed the account, the project, its
# production branch `production`, the preview branch, no production deployment, Access in front of the preview host
# with e-mail-only policies limited to PREVIEW_EMAILS, and a clean package built from HEAD. wrangler is pinned, gets
# the verified account ID and no terminal on stdin (it cannot offer to create a project or pick another account).
# After the upload it checks once more that no production deployment exists.
set -eu
MODE=${1:-}
WRANGLER=wrangler@4.156.0 # pinned; released 2026-10-29
BRANCH=${PAGES_BRANCH:-preview}
DIR=apps/web/dist-demo
PY=.venv/bin/python
cd "$(dirname "$0")/.."
case "$MODE" in preflight | upload) ;; *) echo "usage: sh scripts/deploy_preview.sh preflight|upload"; exit 1 ;; esac
: "${PAGES_ACCOUNT_ID:?}" "${PAGES_PROJECT:?}" "${PREVIEW_EMAILS:?}" "${CF_PREFLIGHT_TOKEN:?}"
case "$BRANCH" in
  main | master | production | prod) echo "refusing: '$BRANCH' looks like a production branch (this script only makes previews)"; exit 1 ;;
esac
[ -z "$(git status --porcelain)" ] || { echo "refusing: uncommitted changes - commit first"; exit 1; }

echo "== rebuilding the package from $(git rev-parse --short HEAD)"
make build > /dev/null && make demo > /dev/null || { echo "build failed"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "refusing: the build changed tracked files"; git status --short; exit 1; }
echo "== preflight"
PAGES_BRANCH=$BRANCH $PY scripts/preflight_preview.py || exit 1
[ "$MODE" = upload ] || { echo "preflight only: nothing uploaded"; exit 0; }

printf "Upload %s (%s files) to account %s, project '%s', PREVIEW branch '%s'? Type the project name to confirm: " \
  "$(cat $DIR/version.json)" "$(find $DIR -type f | wc -l | tr -d ' ')" "$PAGES_ACCOUNT_ID" "$PAGES_PROJECT" "$BRANCH"
read -r answer
[ "$answer" = "$PAGES_PROJECT" ] || { echo "cancelled, nothing uploaded"; exit 1; }
CLOUDFLARE_ACCOUNT_ID=$PAGES_ACCOUNT_ID WRANGLER_SEND_METRICS=false npx --yes "$WRANGLER" pages deploy "$DIR" \
  --project-name "$PAGES_PROJECT" --branch "$BRANCH" --commit-hash "$(git rev-parse HEAD)" \
  --commit-message "demo $(git log -1 --format=%s | cut -c1-60)" --commit-dirty=false < /dev/null

# after: still no production deployment (should be impossible after the preflight; checked anyway)
PROD=$($PY -c 'import os, sys; sys.path.insert(0, "scripts"); import preflight_preview as p
s, d = p.api(f"/accounts/{os.environ["PAGES_ACCOUNT_ID"]}/pages/projects/{os.environ["PAGES_PROJECT"]}/deployments?env=production", os.environ["CF_PREFLIGHT_TOKEN"])
print(len([x for x in d.get("result") or [] if x.get("environment") == "production"]) if s == 200 else "unknown")')
if [ "$PROD" != 0 ]; then
  echo "!!! production deployments: $PROD - take it offline now: npx $WRANGLER pages project delete $PAGES_PROJECT"
  exit 2
fi
echo "ok: preview uploaded, production deployments: 0. Next: smoke test (docs/DEPLOY.md)."
