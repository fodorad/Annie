.PHONY: install dev upgrade install-docs fix lint type-check test docs docs-serve docs-deploy screenshots installer-icon run check clean docker-build docker-run help

# macOS: torchcodec requires FFmpeg dylibs which Homebrew installs to a non-standard
# prefix. make does not inherit DYLD_LIBRARY_PATH, so we set it explicitly here.
export DYLD_LIBRARY_PATH := /opt/homebrew/opt/ffmpeg/lib:$(DYLD_LIBRARY_PATH)

help:
	@echo "Dev (modify files):  fix"
	@echo "Checks (read-only):  lint | type-check | test | docs | check"
	@echo "Setup:               install | dev | upgrade | install-docs"
	@echo "Run:                 run  (override with 'make run PORT=8090 HOST=0.0.0.0')"
	@echo "Docker:              docker-build | docker-run"
	@echo "Docs:                docs-serve | docs-deploy | screenshots"
	@echo "Windows installer:   installer-icon  (installer itself is built in CI)"
	@echo "Cleanup:             clean"

# ── Setup ──────────────────────────────────────────────────────────────────────

install:
	uv pip install --python $(shell which python) "annie[all]"

dev:
	uv pip install --python $(shell which python) -e ".[all,dev]"

upgrade:
	uv pip install --python $(shell which python) --upgrade -e ".[all,dev,docs]"

install-docs:
	uv pip install --python $(shell which python) -e ".[docs]"

# ── Dev helpers (modify files) ─────────────────────────────────────────────────

fix:
	ruff format .
	ruff check --fix .

# ── Checks (read-only — mirrors GitHub CI) ─────────────────────────────────────

lint:
	ruff check .
	ruff format --check .

type-check:
	uv tool run ty check annie --python $(shell which python)

test:
	uv run coverage run -m unittest discover -s tests
	uv run coverage report
	uv run coverage html
	uv run coverage xml -o coverage.xml

docs:
	sphinx-build -b html docs/ site/

check: lint type-check test docs

# ── Run ────────────────────────────────────────────────────────────────────────

# Override the port when 8080 is taken by another project, e.g. `make run PORT=8090`.
# HOST is overridable the same way. Both feed the ANNIE_* env vars the app already reads
# (annie/core/config.py); an ANNIE_PORT already in the environment still wins.
PORT ?= 8080
HOST ?= 127.0.0.1

run:
	ANNIE_HOST=$(HOST) ANNIE_PORT=$(PORT) python -m annie.app

# ── Docker ─────────────────────────────────────────────────────────────────────

docker-build:
	docker build -t annie:latest .

docker-run:
	docker compose up

# ── Docs ───────────────────────────────────────────────────────────────────────

docs-serve:
	sphinx-autobuild docs/ site/

docs-deploy:
	@echo "Docs are deployed automatically via GitHub Actions on push to main."

# Regenerate every documentation screenshot from the real UI, driven against the bundled
# [Example] CMU-MOSEI Mini config: docs/ui/*.png (README + docs front page) and
# docs/playbooks/_screens/*.png. Run after changing the Home, Dataset, Browse, or
# Annotator screens, then commit the updated PNGs. Needs the docs extra (make install-docs).
screenshots:
	python -m playwright install chromium
	python scripts/screenshot_docs.py

# ── Windows installer ──────────────────────────────────────────────────────────

# Rebuild the installer/shortcut icon from the logo mark, then commit the .ico. Needs
# rsvg-convert (brew install librsvg) and Pillow. The installer itself is built and
# end-to-end tested on a Windows runner (.github/workflows/windows-installer.yml).
installer-icon:
	rsvg-convert -w 256 -h 256 docs/assets/mark.svg -o installer/windows/mark.png
	python -c "from PIL import Image; Image.open('installer/windows/mark.png').save('installer/windows/annie.ico', sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])"
	rm installer/windows/mark.png

# ── Misc ───────────────────────────────────────────────────────────────────────

clean:
	rm -rf .venv coverage_html dist/ .pytest_cache/ site/ tmp/
	rm -f .coverage coverage.xml
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
