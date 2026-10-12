"""Render the setup wizard's images from annie.ico (run on Windows, then commit the BMPs).

    python installer/windows/make-wizard-images.py

* ``wizard.bmp`` / ``wizard-2x.bmp``: the panel on the Welcome and Finish pages,
  164x314 at 100% scaling (Inno's size for WizardStyle=modern) and its 200% version.
* ``wizard-small.bmp`` / ``wizard-small-2x.bmp``: the logo at the top right of the
  other pages, 55x55 and 110x110.

Inno picks the version that fits the screen's scaling. The text uses Segoe UI Semibold,
the Windows UI font, so this needs Windows (or the font next to it).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
PRIMARY = (0, 109, 119)  # theme.PRIMARY, #006d77
TOP, BOTTOM = (255, 255, 255), (226, 240, 241)  # white to a light tint of PRIMARY
FONT = Path("C:/Windows/Fonts/seguisb.ttf")


def _logo(size: int) -> Image.Image:
    ico = Image.open(HERE / "annie.ico")
    ico.size = max(ico.ico.sizes())
    return ico.convert("RGBA").resize((size, size), Image.LANCZOS)


def _gradient(width: int, height: int) -> Image.Image:
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / max(1, height - 1)
        draw.line(
            [(0, y), (width, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM))
        )
    return image


def panel(scale: int) -> Image.Image:
    """The Welcome/Finish panel: the logo and the name, on a soft vertical gradient."""
    width, height = 164 * scale, 314 * scale
    image = _gradient(width, height)
    logo = _logo(96 * scale)
    image.paste(logo, ((width - logo.width) // 2, 92 * scale), logo)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT), 26 * scale)
    text = "Annie"
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((width - (box[2] - box[0])) // 2, 200 * scale), text, font=font, fill=PRIMARY)
    # A thin brand line along the right edge, next to the page content.
    draw.rectangle([width - 2 * scale, 0, width, height], fill=PRIMARY)
    return image


def small(scale: int) -> Image.Image:
    """The logo for the top right of the inner pages, on white."""
    size = 55 * scale
    image = Image.new("RGB", (size, size), (255, 255, 255))
    logo = _logo(48 * scale)
    image.paste(logo, ((size - logo.width) // 2, (size - logo.height) // 2), logo)
    return image


def main() -> None:
    """Write the four BMPs next to this script."""
    panel(1).save(HERE / "wizard.bmp")
    panel(2).save(HERE / "wizard-2x.bmp")
    small(1).save(HERE / "wizard-small.bmp")
    small(2).save(HERE / "wizard-small-2x.bmp")
    print("wrote wizard.bmp, wizard-2x.bmp, wizard-small.bmp, wizard-small-2x.bmp")


if __name__ == "__main__":
    main()
