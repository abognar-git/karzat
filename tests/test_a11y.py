"""The accessibility and weight gate: axe-core in a real browser, over one page of every kind the site builds.

This is the test that decides whether a change quietly excluded somebody. It found, on its first run over the
built site:

  * **34 scrollable regions no keyboard could reach.** Every wide table scrolls sideways on a narrow screen,
    and a container with `overflow-x:auto` and no `tabindex` can be dragged with a mouse and reached by
    nothing else. Twelve page kinds, and the data page alone had eleven.
  * **A day's entire vote navigation, invisible.** The strip along the top of a vote page draws every vote of
    that sitting day, and each bar is a link. The `<svg>` around them said `role="img"`, which declares an
    element a leaf: a screen reader announces one image and never reaches a single link inside it.
  * **A page that scrolled sideways because of a label written for screen readers.** The visually-hidden
    class used `position:absolute`, which inside a horizontally scrolling table is laid out against the page
    rather than the table — a 1px span at x=531 in a 390px viewport.

None of those is visible to someone reading the site the way I read it, and all three arrived while the pages
around them were being made carefully. That is the case for a gate rather than an audit.

**Skipping.** Without Chrome or the vendored axe this skips rather than passes: an accessibility check that
silently reports success when it did not run is worse than none, so the reason is printed. The nightly build
has both.
"""

from __future__ import annotations

import re
import shutil
import unittest
from pathlib import Path

from scripts import audit_a11y as A

ROOT = Path(__file__).resolve().parent.parent
BUILDER = ROOT / "scripts" / "build_site.py"


class EveryTemplateIsSampled(unittest.TestCase):
    """The registry has to keep up with the builder, or the gate silently stops covering the site."""

    # Page builders that emit no page of their own: helpers, fragments, or a redirect with nothing to audit.
    NOT_PAGES = {
        "build_assets",          # the stylesheet and script, not a document
        "build_moved_page",      # a two-line forwarding stub for a renamed address
        "build_cycle",           # the loop that writes a cycle, not a page
        "build_all",             # the loop that writes the site
    }

    def test_every_page_builder_has_a_page_kind(self):
        """Named, not matched. My first version compared substrings, and 'spokesperson' contains 'person', so
        it reported build_spokesperson_page as covered by the MP career page — while build_kormany_page,
        whose layout neither of them shares, went unsampled and unnoticed."""
        names = set(re.findall(r"^def (build_\w+)", BUILDER.read_text(encoding="utf-8"), re.M)) - self.NOT_PAGES
        self.assertEqual(sorted(names - set(A.COVERS.values())), [],
                         "these builders emit pages that no page kind samples — add them to "
                         "scripts/audit_a11y.PAGE_KINDS and COVERS, or to NOT_PAGES with a reason")
        self.assertEqual(sorted(set(A.COVERS.values()) - names), [], "COVERS names a builder that is gone")
        self.assertEqual(sorted(set(A.COVERS) ^ set(A.PAGE_KINDS)), [], "COVERS and PAGE_KINDS disagree")

    def test_the_registry_points_at_pages_that_exist(self):
        if not (ROOT / "site" / "index.html").exists():
            self.skipTest("site/ is not built")
        _, missing = A.resolve(None, "http://x")
        self.assertEqual(missing, [], "the registry names pages the build does not produce")


class TheReaderIsNotSentAnywhereElse(unittest.TestCase):
    """The site's stated position is that a visit is known to nobody but this project's own log. That is a
    promise about what the pages make the browser do, and until now nothing checked it.

    59 pages carried `<img src="https://www.parlament.hu/…">` for the 22 people whose portrait had no local
    copy — and 21 of those URLs answer 404, so the reader's browser called a third party in order to be told
    nothing, with `onerror` removing the empty image before anyone noticed. The claim and the markup have to
    agree, so this reads the built pages rather than the intention."""

    ALLOWED = (
        "wikimedia.org", "wikipedia.org",      # a Commons portrait, licensed and credited on the page
    )

    def test_no_page_makes_the_browser_fetch_from_a_third_party(self):
        site = ROOT / "site"
        if not (site / "index.html").exists():
            self.skipTest("site/ is not built")
        # Attributes that make a browser go and get something on its own. An <a href> is a link the reader
        # chooses to follow, which is a different act and stays.
        pat = re.compile(r'\b(?:src|srcset|data-src|poster)\s*=\s*"(https?://[^"]+)"')
        offenders: dict[str, int] = {}
        for f in site.rglob("*.html"):
            for url in pat.findall(f.read_text(encoding="utf-8", errors="replace")):
                if not any(a in url for a in self.ALLOWED):
                    offenders[url.split("?")[0][:110]] = offenders.get(url.split("?")[0][:110], 0) + 1
        self.assertEqual(offenders, {}, "these pages fetch from somewhere else on load:\n"
                                        + "\n".join(f"  {n}× {u}" for u, n in sorted(offenders.items())))


class NobodyIsExcluded(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("node is not available; the audit driver needs it")
        cls.chrome = A.chrome_path()
        if not cls.chrome:
            raise unittest.SkipTest("no Chrome or Chromium; an accessibility rule needs real layout to run, "
                                    "and a shim would pass a page nobody can read")
        if not A.AXE.exists():
            raise unittest.SkipTest(f"{A.AXE} is missing; run python3 -m scripts.fetch_axe")
        if not (ROOT / "site" / "index.html").exists():
            raise unittest.SkipTest("site/ is not built")
        cls.origin = A.Origin()
        cls.pages, _ = A.resolve(None, cls.origin.url)
        cls.reports = {vp: A.run(cls.pages, vp, cls.chrome) for vp in ("desktop", "mobile")}

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "origin", None):
            cls.origin.close()

    def test_no_critical_or_serious_violation_on_any_page_kind(self):
        hard = []
        for vp, report in self.reports.items():
            h, _ = A.judge(report, vp)
            hard += h
        self.assertEqual(hard, [], "\n" + "\n".join(hard))

    def test_the_engine_actually_ran(self):
        """A gate that reports zero because it audited nothing is the failure mode worth guarding."""
        for vp, report in self.reports.items():
            self.assertIsNone(report.get("error"), f"{vp}: {report.get('error')}")
            self.assertEqual(len(report["pages"]), len(self.pages), vp)
            for pg in report["pages"]:
                self.assertGreater(pg.get("nodes", 0), 20, f"{vp} {pg['kind']}: the page looks empty")
                self.assertTrue((pg.get("title") or "").strip(), f"{vp} {pg['kind']}: no title")

    def test_the_page_arrives_even_though_it_starts_invisible(self):
        """The boot script fades panels in from opacity 0. If it throws, the page stays blank and passes
        every rule there is — so one page is loaded with motion allowed and checked for having arrived."""
        m = self.reports["desktop"].get("motion")
        self.assertIsNotNone(m, "the motion check did not run")
        self.assertIsNone(m.get("error"), m.get("error"))
        self.assertEqual(m["motion"]["faded"], 0, f"panels stranded invisible: {m['motion'].get('worst')}")
        self.assertEqual(m.get("errors"), [], "the page threw while animating")


if __name__ == "__main__":
    unittest.main()
