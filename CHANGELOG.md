# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] — 2026-07-01

### Architecture

- **Layered package structure** — `annie/core/` (config, models, logbook, state,
  theme), `annie/dataset/` (sources, scanning, storage, filtering, manipulate,
  corrections, datasets), `annie/media/` (decode, preview, color, compose, convert,
  probe, rendering), `annie/parsers/`, `annie/pages/`; each layer calls only the
  layer directly beneath it, enforced by import direction.
- **Env-driven, path-agnostic configuration** — every setting has an `ANNIE_*`
  environment variable; no paths are baked into the code.
- **Per-browser-connection UI** — built with NiceGUI `@ui.page("/")` so every
  browser tab gets its own live client and per-client state; multi-tab safe.
- **SQLite review store** — persistent verdict (`good` / `bad`), free-text note,
  and annotator-queue flag per video; CSV and JSON export.
- **Central event log** — in-memory ring buffer plus a dated `annie-YYYY-MM-DD.log`
  file written to `ANNIE_LOGS_DIR`; all background errors route here.
- **DevOps** — CI (ruff + ty + coverage + docs), CD (PyPI via OIDC, Docker), Sphinx + Furo
  + AutoAPI docs on GitHub Pages, release-please, dependabot, pre-commit, codecov,
  and a `Makefile` mirroring CI (`make dev`, `make check`, `make run`).

### Home tab

- Landing page (default on open) with the brand mark, a tagline, and one capability
  card per tab; clicking a card navigates directly to that tab.

### Convert tab

- Re-encode an audio/video dataset to a **torchcodec-validated** form: uniform audio
  (format / sample rate / channels) and constant-frame-rate H.264 video.
- Audio/video combination is explicit: mux matching-stem audio with `apad` +
  `-shortest` so lengths align; keep video frame-only when no audio matches;
  synthesise a black-frame video for audio-only sources.
- Every output is validated by decoding all frames and asserting torchcodec's
  `exact == approximate` frame count, eliminating broken-seeking files.
- A `min_frames` threshold (default **2**) drops degenerate single-frame files.
- Background batch with live `X/Y`, %, elapsed, and ETA progress; already-valid
  outputs are skipped on re-run (resume).
- **Export succeeded / failed ids** buttons write CSV files to a chosen folder.

### Dataset tab

- **Extensible source registry** — a dataset is an ordered list of data sources
  added via a `+` menu: a videos folder (mandatory spine), vdet/track folders,
  a main-character CSV, and any number of label CSVs. Folder and main-character
  CSV are singletons; label CSVs accumulate.
- Sources are **session-only** and seeded from `ANNIE_*` environment variables at
  startup; adding or removing a source rescans in place — no Scan button.
- **Generic CSV sources** — a CSV joins to videos by a configurable **key column**
  (auto-suggested by matching values to video stems); selected value columns become
  Browse tags and filter facets. Data type per column (str / int / float) is
  auto-detected and adjustable.
- **Live metric cards** — video count, vdet-file count, track-file count, and
  coverage breakdown (linked / video-only / annotation-only).
- **Dataset configs** — save the current source list as a named JSON config; configs
  are auto-discovered from `data/config/` and listed in a dropdown for one-click
  reload. Paths may be relative (resolved against the file) so bundled examples are
  portable.
- **Bundled example datasets** — `[Example] CMU-MOSEI Mini` and
  `[Example] First Impression V2 Mini` ship under `data/example/` with video,
  audio, vdet, track, main-character CSV, and a label CSV.
- **Server-side folder / file picker** — a dialog browses the server filesystem
  without requiring a path to be typed.

### Browse tab

- **One row per video** — each `VideoEntry` aggregates that video's vdet file and
  all its track files; the row shows the video id, annotation/label tags, a
  text-transcript line (when available), three media boxes, and review controls.
- **Original placeholder** — click to play the source video in-browser.
- **Five-frame strip** — evenly-sampled frames with vdet detections (blue) and the
  main-character track (green) overlaid.
- **Render box** — on-demand background job burns the full annotated clip (vdet
  blue, each track a unique colour, main character green) via libx264 + FFmpeg with
  audio muxed back; temp clips auto-purge on a configurable TTL.
- **Always-visible filter bar** — filter by name prefix, video / audio / vdet /
  track presence, frame-count threshold (`< X` / `> X`), review verdict, note
  presence, annotator-queue flag, and any label-column value. Facets combine with
  AND; label values combine with OR.
- **Manipulate block** — column-level value transforms applied before tags and
  filter facets: trim (text), round to int, threshold ≥ X, or sign (−/0/+).
- **Review controls** — liked by default (green thumb-up); dislike; free-text note;
  "Add to Annotator" checkbox; all persist immediately.
- **Show at location** — reveals the video file in the native file manager (Finder
  on macOS, Explorer on Windows, containing folder elsewhere).
- **`#frames` tag** — displays the decoded frame count per row.
- **Configurable row height** — shared preference with the Annotator (Settings tab).
- Floating **back-to-top** button pinned to the bottom-right.

### Annotator tab

- Greyed out until at least one video is queued via Browse; shows only queued
  videos in taller rows.
- **Main-character track selector** — pick the correct track; the selection
  auto-saves and the strip re-renders green immediately.
- The source-file value is marked as `(default)` in the selector.
- Click-to-play thumbnail and a per-row render box for end-to-end verification.
- **Export corrected datasource** — writes the resolved main-character mapping
  (manual ▸ source ▸ −1) to a standalone CSV at a chosen location.

### Log tab

- Filterable log list (by level and free text) with a per-entry **Copy** button
  and a **Details** dialog showing the full traceback.
- New log entries surface as auto-dismissing toasts while on other tabs.

### Settings tab

- **Layout** — Browse and Annotator row height (shared, configurable in px).
- **Render cache** — auto-delete TTL for rendered clips; per-category cleanup
  (old logs, old session databases, rendered clips) and a single **Clean up all**
  action with reclaimable-space summary.
- **Review status export / import** — export the full curation database as CSV
  or JSON.

[1.0.0]: https://github.com/fodorad/Annie/releases/tag/v1.0.0
