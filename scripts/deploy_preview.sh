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
# The preview hosts do not exist before the first upload, so their HTTP checks run right after it (--after-upload, the
# new deployment identified by ID): exit 0 private, 1 refused before upload (nothing uploaded), 2 CRITICAL (a URL is
# public or a production deployment exists; reported at once, nothing deleted), 3 upload not verifiable.
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
KNOWN=$(PAGES_BRANCH=$BRANCH $PY scripts/preflight_preview.py --list-preview-ids) || { echo "nothing uploaded"; exit 1; }
CLOUDFLARE_ACCOUNT_ID=$PAGES_ACCOUNT_ID WRANGLER_SEND_METRICS=false npx --yes "$WRANGLER" pages deploy "$DIR" \
  --project-name "$PAGES_PROJECT" --branch "$BRANCH" --commit-hash "$(git rev-parse HEAD)" \
  --commit-message "demo $(git log -1 --format=%s | cut -c1-60)" --commit-dirty=false < /dev/null ||
  echo "wrangler reported an error - checking what reached Cloudflare anyway"

# after: the new deployment (by ID) - unique URL and every alias behind Access, production empty. A public URL is
# reported at once (exit 2); "not visible yet" / API unreachable is retried, then reported as unverified (exit 3).
rc=3
for attempt in 1 2 3; do
  rc=0
  KNOWN_PREVIEW_IDS=$KNOWN PAGES_BRANCH=$BRANCH $PY scripts/preflight_preview.py --after-upload || rc=$?
  [ "$rc" = 3 ] || break
  [ "$attempt" = 3 ] || sleep "${AFTER_UPLOAD_RETRY_S:-15}"
done
case "$rc" in
  0) echo "ok: preview uploaded and private. Next: smoke test (docs/DEPLOY.md)." ;;
  2) echo "!!! CRITICAL: the upload is reachable without login (or a production deployment exists)."
     echo "!!! Nothing was deleted. To take the whole project offline, if you decide so:"
     echo "!!!   CLOUDFLARE_ACCOUNT_ID=$PAGES_ACCOUNT_ID npx $WRANGLER pages project delete $PAGES_PROJECT"
     exit 2 ;;
  *) echo "!!! UNVERIFIED: the upload happened but its privacy could not be confirmed - treat it as possibly public."
     echo "!!! Re-check: KNOWN_PREVIEW_IDS=$KNOWN $PY scripts/preflight_preview.py --after-upload"
     exit 3 ;;
esac
