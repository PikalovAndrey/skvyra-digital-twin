#!/bin/zsh
# Render every fixed viewpoint in Blender (Eevee) and the web viewer: renders/compare/<label>/
# usage: scripts/compare_renders.sh before|after
set -e
label=${1:?label}
cd "$(dirname "$0")/.."
B=${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}
mkdir -p renders/compare/$label
for vp in $(python3 -c "import json;print(' '.join(json.load(open('blender/presets/viewpoints.json'))['viewpoints']))"); do
  $B -b --factory-startup --python blender/scripts/build_scene.py -- --viewpoint $vp --preset day \
     --samples 32 --res 1600x900 --out renders/compare/$label/blender-$vp.png 2>&1 | grep -E "skvyra|Error" || true
done
(cd apps/web && COMPARE=$label npx playwright test compare)
