"""Regenerate every screenshot in the documentation, from the real UI.

Boots Annie against a bundled example config and drives it with Playwright, so the docs
show the **real UI** rather than images that drift every time a layout changes. Run it
after any change to the Home, Dataset, Browse, or Annotator screens:

.. code-block:: console

   $ make screenshots

Two sets of images are written:

* ``docs/ui/`` — the Home, Browse, and Annotator hero shots used by the README and the
  documentation front page.
* ``docs/playbooks/_screens/`` — the step-by-step playbook screens.

Two details matter for reproducibility, and both are handled here rather than left to the
person running it:

* The app is served from a **staged copy of the repo at a neutral path**, because the
  Dataset tab prints each source's absolute path — shooting from a personal checkout
  would bake a developer's directory layout into public documentation.
* It runs against a **throwaway** ``ANNIE_HOME``, so no real session's review state, and
  no previously queued videos, can leak into a capture.

Captures are taken at ``device_scale_factor=2`` for crisp text and then halved, which
keeps them sharp on normal displays at roughly a third of the file size.

Everything is captured against ``[Example] CMU-MOSEI Mini``. Segment review has no bundled
example source yet, so its playbook still uses hand-drawn SVG mockups.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from PIL import Image
    from playwright.sync_api import Page, sync_playwright
except ModuleNotFoundError:  # pragma: no cover - a tooling-only dependency
    sys.exit("Missing docs extras. Install with:\n  uv pip install -e '.[docs]'")

#: Repository root (this file lives in ``<repo>/scripts``).
REPO = Path(__file__).resolve().parent.parent
#: Where the playbooks look for their images.
OUT = REPO / "docs" / "playbooks" / "_screens"
#: The hero shots on the README and the documentation front page.
UI_OUT = REPO / "docs" / "ui"
#: Neutral checkout the app is served from, so rendered paths read like a generic clone.
STAGE = Path("/tmp/annie-docs/Annie")
#: Throwaway ANNIE_HOME, so a real session's review state never shows up in a capture.
HOME = Path("/tmp/annie-docs/home")
#: A port unlikely to collide with a developer's own running instance.
PORT = 8099
BASE = f"http://127.0.0.1:{PORT}"
#: Wide enough that the media strips lay out at full width, tall enough for a whole row.
VIEWPORT = {"width": 1600, "height": 1000}
#: Somewhere harmless to park the pointer so no hover tooltip is caught mid-shot.
IDLE_POINTER = (1580, 980)
#: The example config the playbook text refers to.
CONFIG_LABEL = "[Example] CMU-MOSEI Mini"
#: A Browse/Annotator sample row. Not every card is one — the Manipulate, Filter, and
#: View panels are cards too — so rows are identified by the selection corner they carry.
#: Note the space in ``z-index: 6``: the browser normalises inline CSS when it reflects it
#: back into the style attribute, so the unspaced form Annie writes never matches here.
ROW_CARD = "div.q-card:visible:has(div[style*='z-index: 6'])"


def stage_repo() -> None:
    """Copy the package and example data to a neutral path (paths appear in the UI)."""
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    for item in ("annie", "data"):
        shutil.copytree(REPO / item, STAGE / item, symlinks=True)


def boot() -> subprocess.Popen[bytes]:
    """Start Annie on a throwaway home and wait until it answers.

    Returns:
        The running process, for the caller to terminate.
    """
    if HOME.exists():
        shutil.rmtree(HOME)
    HOME.mkdir(parents=True)
    env = {**os.environ, "ANNIE_HOME": str(HOME), "ANNIE_PORT": str(PORT)}
    env.pop("ANNIE_DB_PATH", None)  # never shoot against a pinned personal database
    process = subprocess.Popen(
        [sys.executable, "-m", "annie.app"],
        cwd=STAGE,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    for _ in range(240):  # a cold boot loads torchcodec and runs the first scan
        if process.poll() is not None:
            details = (process.stderr.read() or b"").decode()[-2000:]
            sys.exit(f"annie exited during startup:\n{details}")
        try:
            urllib.request.urlopen(BASE, timeout=2)  # noqa: S310 - fixed localhost URL
            return process
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    process.kill()
    sys.exit(f"annie did not come up on {BASE} within two minutes")


def settle(page: Page, ms: int = 1200) -> None:
    """Park the pointer clear of the UI and let hover tooltips expire before a shot."""
    page.mouse.move(*IDLE_POINTER)
    page.wait_for_timeout(ms)


def shoot(page: Page, name: str, *, trim: bool = False, out: Path = OUT) -> None:
    """Write ``<name>.png``, optionally cropping the empty page below the content.

    Args:
        page: The driven page.
        name: Output stem, matching the reference in the playbook markdown.
        trim: Crop to the bottom of the last card. A short task leaves most of the
            viewport blank, which reads as a broken screenshot rather than an accurate one.
            This measures *every* card, not just :data:`ROW_CARD`: the Annotator's rows
            carry no selection corner, and its toolbar should be inside the crop anyway.
        out: Destination directory — :data:`OUT` for playbook screens, :data:`UI_OUT` for
            the README / front-page hero shots.
    """
    settle(page)
    clip = None
    if trim:
        bottom = page.evaluate(
            "() => { const els = [...document.querySelectorAll('.q-card')];"
            " return els.length"
            " ? Math.max(...els.map(e => e.getBoundingClientRect().bottom)) : 0; }"
        )
        if bottom:
            clip = {
                "x": 0,
                "y": 0,
                "width": VIEWPORT["width"],
                "height": min(bottom + 40, VIEWPORT["height"]),
            }
    out.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=out / f"{name}.png", clip=clip)
    print(f"  wrote {out.name}/{name}.png")


def select_row(page: Page, index: int) -> None:
    """Queue one Browse row for the Annotator by clicking its selection corner.

    The corner is absolutely positioned, which defeats Playwright's visibility heuristic,
    so it is clicked by coordinate. The row's position is re-read immediately before the
    click and the result is verified, because a collapsing panel above keeps reflowing the
    page for a moment: clicking a stale rectangle silently queues nothing, and the
    Annotator then stays disabled with no obvious cause.

    Args:
        page: The driven page.
        index: Zero-based row position.

    Raises:
        RuntimeError: If the row would not select, which would leave the Curation
            screenshot empty.
    """
    card = page.locator(ROW_CARD).nth(index)
    for _ in range(5):
        card.scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        box = card.bounding_box()
        if box is None:
            continue
        page.mouse.click(box["x"] + box["width"] - 26, box["y"] + 26)
        page.wait_for_timeout(1200)
        # An unselected row already has a 1px border, so the presence of "border" proves
        # nothing; selection thickens it to 2px (see browse._selection_control). The
        # browser normalises the value, hence matching "2px" rather than the exact string.
        if "2px" in (card.get_attribute("style") or ""):
            return
    raise RuntimeError(f"could not select Browse row {index} — the Annotator would be empty")


def capture_home(page: Page) -> None:
    """Home tab — the README and documentation front-page hero shot.

    Taken before any config is loaded, so it shows the landing state a new user meets.
    """
    print("home tab (README / docs front page)")
    page.get_by_role("tab", name="Home").click()
    page.wait_for_timeout(2500)
    shoot(page, "home", trim=True, out=UI_OUT)


def capture_browse_hero(page: Page) -> None:
    """Browse tab as the README shows it: rows, tags, and frame strips, panels collapsed.

    Distinct from :func:`capture_selection`, which deliberately selects a row to document
    the selection affordance; the hero shot wants the tab at rest.
    """
    print("browse tab (README / docs front page)")
    page.get_by_role("tab", name="Browse").click()
    page.wait_for_timeout(6000)  # rows + frame decodes
    shoot(page, "browse", out=UI_OUT)


def capture_dataset(page: Page) -> None:
    """Dataset tab with the example config loaded."""
    print("dataset tab")
    page.get_by_role("tab", name="Dataset").click()
    page.get_by_label("Config").click()
    page.get_by_role("option", name=CONFIG_LABEL).click()
    page.wait_for_timeout(7000)  # scan + metric cards
    shoot(page, "dataset-sources")


def capture_manipulate(page: Page) -> None:
    """Browse Manipulate panel, with a transform applied so the controls are populated."""
    print("browse — manipulate panel, sentiment thresholded")
    page.get_by_role("tab", name="Browse").click()
    page.wait_for_timeout(6000)  # rows + frame decodes
    page.get_by_text("Manipulate", exact=True).click()
    page.wait_for_timeout(2000)
    # Pick a transform so the threshold field and the "show original" checkbox — the
    # point of the panel — actually render.
    row = page.locator(
        "div.row:has(> span:text-is('sentiment')), div.row:has(> div:text-is('sentiment'))"
    ).first
    row.locator(".q-select").first.click()
    page.wait_for_timeout(800)
    page.get_by_role("option").filter(has_text="threshold").first.click()
    page.wait_for_timeout(1500)
    threshold = row.locator("input[type='number']").first
    threshold.fill("0")
    threshold.press("Enter")
    page.wait_for_timeout(1200)
    row.get_by_text("show original").click()
    page.wait_for_timeout(2500)
    shoot(page, "browse-manipulate")


def capture_selection(page: Page) -> None:
    """Browse rows with the first selected, contrasting with the faint corner tick."""
    print("browse — queue rows for the Annotator")
    page.get_by_text("Manipulate", exact=True).click()  # collapse; rows return to the top
    page.wait_for_timeout(1500)
    select_row(page, 0)
    shoot(page, "browse-selection")


def capture_curation(page: Page) -> None:
    """Annotator Curation task for the queued row.

    Doubles as the README / front-page Annotator hero: the same screen serves both, so it
    is written to each destination rather than captured twice.
    """
    print("annotator — curation task (also README / docs front page)")
    page.get_by_role("tab", name="Annotator").click()
    page.wait_for_timeout(8000)  # task switch + strip decodes
    shoot(page, "annotator-curation", trim=True)
    shoot(page, "annotator", trim=True, out=UI_OUT)


def downsize(*directories: Path) -> None:
    """Halve the 2x captures in place, keeping them sharp at a third of the size."""
    print("downsizing")
    for directory in directories:
        for path in sorted(directory.glob("*.png")):
            image = Image.open(path)
            image.resize((image.width // 2, image.height // 2), Image.LANCZOS).save(
                path, optimize=True
            )
            print(f"  {directory.name}/{path.name}: {path.stat().st_size // 1024}K")


def main() -> None:
    """Boot Annie, capture every playbook screen, and downsize the results."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--keep-scale",
        action="store_true",
        help="skip the halving step and keep the full 2x captures",
    )
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    stage_repo()
    process = boot()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2)
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_timeout(3000)

            # Order matters: Home is shot first, while no config is loaded, so it shows
            # the landing state; the Browse hero needs the config, but must precede the
            # panel and selection work that leaves the tab in a demonstrative state.
            capture_home(page)
            capture_dataset(page)
            capture_browse_hero(page)
            capture_manipulate(page)
            capture_selection(page)
            capture_curation(page)

            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)

    if not args.keep_scale:
        downsize(UI_OUT, OUT)
    print(f"\nDone — {UI_OUT.relative_to(REPO)} and {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
