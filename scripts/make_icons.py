"""Render the raster icons from the same mark as the SVG favicon → site/assets/{favicon.ico,apple-touch-icon.png}.

    python3 -m scripts.make_icons

The SVG in build_site.py is the source of the shape; this draws the identical geometry with Pillow, because a
browser that wants an .ico or an apple-touch-icon will not read an SVG. Run it when the mark changes — the output
is committed beside the stylesheet, and the build never depends on Pillow.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "site" / "assets"
DARK, LIGHT = (5, 5, 5, 255), (250, 250, 250, 255)


def draw(size: int, bg=DARK, fg=LIGHT):
    from PIL import Image, ImageDraw
    ss = 8                                             # supersample, then downscale: clean edges at 16 px
    n = size * ss
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    u = n / 64.0                                        # the SVG's units
    d.rounded_rectangle([0, 0, n - 1, n - 1], radius=13 * u, fill=bg)
    # the tiers: a half-ring, outer radius 20, inner 11 — the SVG's path, drawn the same way
    d.arc([12 * u, 20 * u, 52 * u, 60 * u], start=180, end=360, fill=fg, width=round(9 * u))
    # the Speaker's platform below it
    d.rounded_rectangle([27 * u, 44 * u, 37 * u, 50 * u], radius=2 * u, fill=fg)
    return im.resize((size, size), 1)                   # LANCZOS


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args(argv)
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("Pillow is needed for the raster icons: pip3 install pillow", file=sys.stderr)
        return 1
    ASSETS.mkdir(parents=True, exist_ok=True)
    ico = ASSETS / "favicon.ico"
    draw(64).save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    touch = ASSETS / "apple-touch-icon.png"
    draw(180).save(touch)
    print(f"written {ico} ({ico.stat().st_size:,} B, 16/32/48/64) and {touch} ({touch.stat().st_size:,} B, 180×180)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
