# Skvyra digital twin - common commands. Python stages are restartable: each reads the previous stage's
# outputs from disk, so a failed stage can be rerun alone.
PY      ?= .venv/bin/python
SK      := PYTHONPATH=pipeline $(PY) -m skvyra.cli
BLENDER ?= $(shell command -v blender || echo /Applications/Blender.app/Contents/MacOS/Blender)
WEB     := cd apps/web &&
TILES   ?= t-1-1 t+0-1 t+1-1 t-1+0 t+0+0 t+1+0 t-1+1 t+0+1 t+1+1

.PHONY: qa setup env acquire acquire-offline normalize audit validate preview build all render flyover \
        export web-dev web-build demo test test-unit test-integration test-web e2e bench clean-generated

setup:            ## create the Python venv and install web dependencies
	python3 -m venv .venv && $(PY) -m pip install -r requirements.txt
	$(WEB) npm install

env:              ## verify the environment (no installs)
	$(PY) scripts/check_env.py

acquire:          ## download/refresh source data (Overpass, Microsoft footprints, Copernicus DEM window)
	$(SK) acquire
acquire-offline:  ## verify cached data only (fails if missing or corrupted)
	$(SK) acquire --offline

normalize:        ## clean, repair, conflate -> data/processed/*.geojson
	$(SK) normalize
audit:            ## coverage statistics + diagnostic maps (docs/img)
	$(SK) audit
validate: normalize audit

qa:               ## geometry QA on the generated tiles (floating/buried buildings, roads in buildings, trees in buildings, outliers)
	$(PY) scripts/qa_geometry.py

preview:          ## build only the 3x3 city-centre tiles
	$(SK) build --tiles $(TILES)
build:            ## build/update all tiles (only changed tiles are rebuilt)
	$(SK) build --workers 6
all:              ## full pipeline from cached data
	$(SK) all --offline --workers 6

render:           ## Cycles still of the centre at sunset -> renders/
	$(BLENDER) -b --factory-startup --python blender/scripts/build_scene.py -- --tiles $(TILES) \
	  --camera oblique --preset sunset --engine cycles --samples 96 --out renders/centre_sunset.png \
	  --save assets/generated/blender/centre.blend
flyover:          ## Eevee flyover frames + mp4 (needs ffmpeg)
	$(BLENDER) -b --factory-startup --python blender/scripts/build_scene.py -- --tiles $(TILES) \
	  --camera flyover --preset day --frames 240 --res 1280x720 --samples 16 --out renders/flyover/frame.png
	ffmpeg -y -framerate 30 -i renders/flyover/frame_%04d.png -c:v libx264 -pix_fmt yuv420p -crf 20 renders/flyover.mp4
	rm -rf renders/flyover  # 240 PNG frames are ~360 MB; the mp4 is ~4 MB
export:           ## save an editable .blend of the whole city (LOD1)
	$(BLENDER) -b --factory-startup --python blender/scripts/build_scene.py -- --all --lod 1 --no-render \
	  --save assets/generated/blender/city_lod1.blend

web-dev:          ## interactive viewer on http://127.0.0.1:5199
	$(WEB) npx vite --host 127.0.0.1
web-build:        ## production build (heavy: run one at a time, see CLAUDE.md rules)
	$(WEB) npx vite build

demo:             ## public demo package -> apps/web/dist-demo (viewer only, gzipped tiles, _headers); no deploy
	$(WEB) npx vite build --mode demo
	$(PY) scripts/pack_demo.py

test: test-unit test-integration test-web
test-unit:
	$(PY) -m pytest tests/test_unit.py
test-integration: ## synthetic offline fixture through every stage, plus Blender export
	$(PY) -m pytest tests/test_integration.py
test-web:         ## TypeScript unit tests + strict typecheck
	$(WEB) npx vitest run && npx tsc --noEmit -p .
e2e:              ## browser end-to-end tests (uses installed Google Chrome)
	$(WEB) npx playwright test
bench:            ## frame-time route (uncapped) + camera jumps (normal pacing) -> benchmarks/results/
	$(WEB) BENCH=1 npx playwright test benchmark teleport

clean-generated:  ## remove generated tiles (source data and overrides are kept)
	rm -rf assets/generated/tiles assets/generated/search.json
