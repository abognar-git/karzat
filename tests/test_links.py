"""Every internal reference the built site makes has to land on something that exists.

Nothing checked this, and the cost of not checking was 11,328 dead links shipped at once — all of them the
same mistake, made three times in three places: a path built from a prefix that walks to the site root and a
tail that assumes it is still inside a cycle directory.

  * **11,294** on the Beszédidő pages: `../../iromany/N.html` from `cklNN/beszedido/YYYY.html` lands on
    `site/iromany/`, which does not exist — `iromany/` is per cycle. That was the only link from a debate to
    the bill it was about, on the feature's every page, in all eight cycles that have one.
  * **30** on the data pages: the three whole-corpus Parquet downloads, the most prominent offer on the page.
  * **4** on the echo pages: the method link, one level short of the root.

None of it raised anything. A wrong `href` is not a syntax error, the pages render perfectly, and the reader
finds out by clicking. The lesson is the one this project keeps relearning — the defects that ship are the
ones no check was pointed at — so this walks the whole site rather than a sample: 160,395 documents and every
`href`, `src`, `srcset` and `poster` in them, resolved against the actual files on disk.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from urllib.parse import unquote, urldefrag

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"

REF = re.compile(r'\b(?:href|src|poster)\s*=\s*"([^"]+)"')


def path_of(raw: str) -> str:
    """The file part of a reference: the fragment and the query both dropped.

    `urldefrag` takes off `#dir` and leaves `index.html?m=2010-05`, which is a file nobody has. The month strip
    on the Számok pages links to the cycle's vote list narrowed to a month, and this test read 481 of those as
    dead links — a checker that cannot parse a query string reports the site broken and is itself the bug.
    A static host ignores the query, so what has to exist is the path before it.
    """
    return urldefrag(raw)[0].split("?", 1)[0]


class NoInternalLinkIsBroken(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (SITE / "index.html").exists():
            raise unittest.SkipTest("site/ is not built")
        # One pass over the tree, then set membership: resolving 13.8 million references with os.path.exists
        # would be 13.8 million stat calls. Case matters — the deploy target is case-sensitive even where
        # this filesystem is not, so a link that works locally and 404s in production is exactly the kind of
        # thing worth catching here.
        cls.files = {str(p.relative_to(SITE)) for p in SITE.rglob("*") if p.is_file()}

    def test_every_href_and_src_resolves(self):
        broken: dict[str, tuple[int, str]] = {}
        checked = 0
        for f in SITE.rglob("*.html"):
            here = f.parent.relative_to(SITE)
            html = f.read_text(encoding="utf-8", errors="replace")
            for raw in REF.findall(html):
                url = path_of(raw)
                if not url or url.startswith(("http://", "https://", "mailto:", "data:", "#", "//", "javascript:")):
                    continue
                checked += 1
                target = url.lstrip("/") if url.startswith("/") else os.path.normpath(str(here / url))
                target = unquote(target).replace(os.sep, "/")
                if target.startswith(".."):
                    key = "escapes the site root"
                elif target in self.files:
                    continue
                else:
                    key = target.split("/")[-1].rsplit(".", 1)[-1] or "no extension"
                n, ex = broken.get(key, (0, ""))
                broken[key] = (n + 1, ex or f"{f.relative_to(SITE)} → {raw}")
        self.assertGreater(checked, 1_000_000, "far fewer references than this site has — did the walk work?")
        self.assertEqual(broken, {}, "internal references that land on nothing:\n"
                                     + "\n".join(f"  {k}: {n:,}× e.g. {ex}" for k, (n, ex) in sorted(broken.items())))

    def test_the_twins_a_page_advertises_are_there(self):
        """A page offering `CSV` beside a table is making a promise about a file, one link at a time."""
        missing = []
        for f in SITE.rglob("*.html"):
            here = f.parent.relative_to(SITE)
            for raw in REF.findall(f.read_text(encoding="utf-8", errors="replace")):
                url = path_of(raw)
                if not url.endswith((".csv", ".json", ".xml", ".parquet", ".csv.gz")):
                    continue
                if url.startswith(("http", "//", "data:")):
                    continue
                t = url.lstrip("/") if url.startswith("/") else os.path.normpath(str(here / url))
                if unquote(t).replace(os.sep, "/") not in self.files:
                    missing.append(f"{f.relative_to(SITE)} → {raw}")
        self.assertEqual(missing[:8], [], f"{len(missing)} advertised data files do not exist")


if __name__ == "__main__":
    unittest.main()
