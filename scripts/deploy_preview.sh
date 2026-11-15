#!/bin/sh
# Upload the packed demo (apps/web/dist-demo) as a Cloudflare Pages PREVIEW deployment (Direct Upload, no Git link).
# Not run by any make target. It uploads only after you type the project name. See docs/DEPLOY.md.
#
#   PAGES_PROJECT=<name> sh scripts/deploy_preview.sh            # upload to branch "preview" (preview environment)
#   PAGES_PROJECT=<name> DRY_RUN=1 sh scripts/deploy_preview.sh  # checks only, prints the command, uploads nothing
#
# Refuses: a production-like branch name, a dirty git tree (the build must match a commit), a failed pack check.
set -eu
WRANGLER=wrangler@4.156.0 # pinned; released 2026-10-29
PROJECT=${PAGES_PROJECT:?set PAGES_PROJECT=<Cloudflare Pages project name>}
BRANCH=${PAGES_BRANCH:-preview}
DIR=apps/web/dist-demo
cd "$(dirname "$0")/.."

case "$BRANCH" in
  main | master | production | prod) echo "refusing: '$BRANCH' looks like a production branch (this script only makes previews)"; exit 1 ;;
esac
[ -z "$(git status --porcelain)" ] || { echo "refusing: uncommitted changes - commit first, then make demo"; exit 1; }
[ -f "$DIR/index.html" ] || { echo "no $DIR - run: make build && make demo"; exit 1; }
# re-check (idempotent): no internal files, no leaks, host limits; also stamps version.json with this commit
.venv/bin/python scripts/pack_demo.py > /dev/null || { echo "pack check failed: .venv/bin/python scripts/pack_demo.py"; exit 1; }
grep -q '"dirty": false' "$DIR/version.json" || { echo "refusing: $DIR/version.json is not from a clean commit"; exit 1; }

COMMIT=$(git rev-parse HEAD)
set -- npx --yes "$WRANGLER" pages deploy "$DIR" --project-name "$PROJECT" --branch "$BRANCH" \
  --commit-hash "$COMMIT" --commit-message "demo $(git log -1 --format=%s | cut -c1-60)" --commit-dirty=false
echo "build:   $(cat "$DIR/version.json")"
echo "files:   $(find "$DIR" -type f | wc -l | tr -d ' '), $(du -sh "$DIR" | cut -f1)"
echo "command: $*"
[ -z "${DRY_RUN:-}" ] || { echo "DRY_RUN: nothing uploaded"; exit 0; }
printf "Upload to Cloudflare Pages project '%s', preview branch '%s'? Type the project name to confirm: " "$PROJECT" "$BRANCH"
read -r answer
[ "$answer" = "$PROJECT" ] || { echo "cancelled, nothing uploaded"; exit 1; }
WRANGLER_SEND_METRICS=false "$@"
