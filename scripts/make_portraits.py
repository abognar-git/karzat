#!/usr/bin/env python3
"""Turn the cached portraits into what the pages actually serve → site/assets/portre/<p_azon>.webp
and the manifest the builder reads → data/derived/portre.json.

    python3 -m scripts.make_portraits

Three things make a picture quick, and all three happen here rather than in the browser:

  * **Size.** The largest a portrait is ever drawn is 72×96 CSS pixels (the hero); everything else is smaller. One
    file at 192×256 covers every use at twice the density and nothing more is sent than that.
  * **Format.** WebP at quality 72 — a tenth of the JPEG the API serves, for a picture this small no worse to look at.
  * **Grey.** The site draws every portrait in black and white. Baking the grey in means the browser is not asked to
    filter a colour image on every paint; the stylesheet's contrast and brightness still apply on top, so a local
    picture and a remote one look the same.

The manifest is committed and the images are not: a page's markup must not depend on what happens to be in the
output directory, or two builds of the same inputs would differ. Run this after `scripts.pull_portraits`, and again
after wiping site/.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RAW = ROOT / "data" / "raw" / "portre"
OUT = ROOT / "site" / "assets" / "portre"
MANIFEST = ROOT / "data" / "derived" / "portre.json"
W, H = 192, 256                      # 2× the largest drawn size (72×96), in the API's own 3:4
QUALITY = 72


def render(src: Path, dst: Path) -> int:
    from PIL import Image, ImageOps
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        im = ImageOps.grayscale(im)                     # the site's treatment, baked in
        im = ImageOps.fit(im, (W, H), method=Image.LANCZOS, centering=(0.5, 0.35))   # heads sit above centre
        im.save(dst, "WEBP", quality=QUALITY, method=6)
    return dst.stat().st_size


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="re-render pictures already made")
    args = ap.parse_args(argv)
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("Pillow is needed: pip3 install pillow", file=sys.stderr)
        return 1
    if not RAW.exists():
        print("no cached portraits — run: python3 -m scripts.pull_portraits", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    made = skipped = failed = 0
    total_in = total_out = 0
    entries: dict[str, int] = {}
    for src in sorted(RAW.iterdir()):
        if not src.is_file() or src.name.startswith("."):
            continue
        dst = OUT / f"{src.stem}.webp"
        total_in += src.stat().st_size
        try:
            if dst.exists() and not args.force and dst.stat().st_mtime >= src.stat().st_mtime:
                size = dst.stat().st_size
                skipped += 1
            else:
                size = render(src, dst)
                made += 1
            entries[src.stem] = size
            total_out += size
        except Exception as e:                          # noqa: BLE001 — one unreadable file must not stop the run
            failed += 1
            print(f"  {src.name}: {type(e).__name__}: {e}", file=sys.stderr)
    MANIFEST.write_text(json.dumps({"count": len(entries), "width": W, "height": H, "format": "webp",
                                    "quality": QUALITY, "bytes": total_out, "people": dict(sorted(entries.items()))},
                                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    avg = total_out / max(len(entries), 1)
    print(f"written {len(entries):,} portraits to {OUT} ({made} rendered, {skipped} unchanged, {failed} failed)")
    print(f"  {total_in / 1e6:.1f} MB in → {total_out / 1e6:.1f} MB out ({avg / 1024:.1f} kB each, {W}×{H} grey WebP)")
    print(f"  manifest {MANIFEST}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
