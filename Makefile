.PHONY: install dev upgrade install-docs fix lint type-check test docs docs-serve docs-deploy screenshots installer installer-try installer-icon run check clean docker-build docker-run help

# macOS: torchcodec requires FFmpeg dylibs which Homebrew installs to a non-standard
# prefix. make does not inherit DYLD_LIBRARY_PATH, so we set it explicitly here.
export DYLD_LIBRARY_PATH := /opt/homebrew/opt/ffmpeg/lib:$(DYLD_LIBRARY_PATH)

# The active environment's interpreter, as a path native tools understand. `which python`
# would give Git Bash's /c/... form on Windows, which uv and ty cannot read.
PYTHON := $(shell python -c "import sys; print(sys.executable)")

help:
	@echo "Dev (modify files):  fix"
	@echo "Checks (read-only):  lint | type-check | test | docs | check"
	@echo "Setup:               install | dev | upgrade | install-docs"
	@echo "Run:                 run  (override with 'make run PORT=8090 HOST=0.0.0.0')"
	@echo "Docker:              docker-build | docker-run"
	@echo "Docs:                docs-serve | docs-deploy | screenshots"
	@echo "Windows installer:   installer | installer-try  (Windows + Inno Setup) | installer-icon"
	@echo "Cleanup:             clean"

# ── Setup ──────────────────────────────────────────────────────────────────────

install:
	uv pip install --python "$(PYTHON)" "annie[all]"

dev:
	uv pip install --python "$(PYTHON)" -e ".[all,dev]"

upgrade:
	uv pip install --python "$(PYTHON)" --upgrade -e ".[all,dev,docs]"

install-docs:
	uv pip install --python "$(PYTHON)" -e ".[docs]"

# ── Dev helpers (modify files) ─────────────────────────────────────────────────

fix:
	ruff format .
	ruff check --fix .

# ── Checks (read-only — mirrors GitHub CI) ─────────────────────────────────────

lint:
	ruff check .
	ruff format --check .

type-check:
	uv tool run ty check annie --python "$(PYTHON)"

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

# Build Annie-Setup.exe on Windows exactly as CI does (needs Inno Setup 6:
# `winget install JRSoftware.InnoSetup`). installer-try also installs it with a wheel of
# this checkout, to try your changes before pushing. Without make, run the script:
#   powershell -ExecutionPolicy Bypass -File installer\windows\build.ps1 [-Try]
# CI builds and end-to-end tests it too (.github/workflows/windows-installer.yml).
installer:
	powershell -NoProfile -ExecutionPolicy Bypass -File installer/windows/build.ps1

installer-try:
	powershell -NoProfile -ExecutionPolicy Bypass -File installer/windows/build.ps1 -Try

# Rebuild the installer/shortcut icon from the logo mark, then commit the .ico. Needs
# rsvg-convert (brew install librsvg) and Pillow.
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
