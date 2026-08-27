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

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SITE = ROOT / "site"

from karzat.linkcheck import check, file_set, report            # noqa: E402


class NoInternalLinkIsBroken(unittest.TestCase):
    """The whole site, every reference. The scoped variant of this same walk is what the intraday refresh
    runs — one implementation in karzat/linkcheck.py, so the two can never drift apart."""

    @classmethod
    def setUpClass(cls):
        if not (SITE / "index.html").exists():
            raise unittest.SkipTest("site/ is not built")
        cls.files = file_set(SITE)
        cls.checked, cls.broken, cls.missing = check(SITE, files=cls.files)

    def test_every_href_and_src_resolves(self):
        self.assertGreater(self.checked, 1_000_000,
                           "far fewer references than this site has — did the walk work?")
        self.assertEqual(self.broken, {}, "internal references that land on nothing:\n" + report(self.broken))

    def test_the_twins_a_page_advertises_are_there(self):
        """A page offering `CSV` beside a table is making a promise about a file, one link at a time."""
        self.assertEqual(self.missing[:8], [], f"{len(self.missing)} advertised data files do not exist")


if __name__ == "__main__":
    unittest.main()
