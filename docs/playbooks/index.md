# Playbooks

Screen-by-screen walkthroughs of Annie's main flows, so the expected flow is clear
**without running the program**.

Screens live in `docs/playbooks/_screens/`. Most are **real screenshots**, captured
against the bundled `[Example] CMU-MOSEI Mini` config — so you can reproduce them by
picking that config on the Dataset tab. The Segment-review screens are still SVG mockups,
because no bundled example carries a segmentation source yet.

## Regenerating the screenshots

The captures are scripted, so they can be refreshed whenever a screen changes instead of
drifting out of date:

```console
$ make install-docs   # once — brings in Playwright and Pillow
$ make screenshots
```

That boots Annie against the example config, drives it with Playwright, and rewrites the
PNGs in place; commit whatever changes. The script serves the app from a copy of the repo
at a neutral path and uses a throwaway `ANNIE_HOME`, so no personal directory layout or
review state can end up in a published image. See `scripts/screenshot_docs.py`.

```{toctree}
:maxdepth: 1

browse-and-curate
segment-review
```

## The flows

- **[Dataset, Browse & Curation](browse-and-curate.md)** — define a dataset from data
  sources, reshape label columns into better filter facets, select videos in the
  read-only Browse viewer, then curate them (like/dislike/note) in the Annotator.
- **[Segment review](segment-review.md)** — declare a segmentation CSV, then accept or
  drop each per-clip segment of a long video, comparing competing start/end bands, and
  export the kept and discarded sets as two files.
