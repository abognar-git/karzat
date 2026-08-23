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


class EveryScrollingBoxCanBeReachedByAKeyboard(unittest.TestCase):
    """Statically, because the browser audit can only see this where the content happens to be wide.

    A container with `overflow-x:auto` scrolls, and without a `tabindex` a keyboard cannot put focus in it to
    scroll it. axe reports that only when the content actually overflows at the width being measured: taking
    the attribute off all three tables of a page was caught on one of them at 390px and on none at 1280px.
    So the rule the sample cannot enforce is enforced here instead — every class the stylesheet gives an
    `overflow` to has to carry the attribute at every point the builder emits it.
    """

    OVERFLOW = re.compile(r"\.([a-z][\w-]*)\s*\{[^}]*overflow(?:-x)?\s*:\s*(?:auto|scroll)", re.I)
    # A scrolling box whose own children are all focusable does not need a tab stop of its own: the cycle
    # switcher is a row of links, and a keyboard reaches every one of them by tabbing.
    EXEMPT = {"cyc"}

    def test_every_class_the_stylesheet_scrolls_carries_a_tabindex(self):
        """Read at the point of authorship rather than over the output.

        Walking all 160,395 built pages does work — it is how the one straggler here was found, the Riport
        page's chart box — but it takes fourteen minutes, and a check nobody will wait for is a check that
        gets skipped. Every scrolling container is emitted from this one file as a literal, so scanning the
        source is both exact and instant, and it fails where the mistake is made instead of one build later.
        """
        src = BUILDER.read_text(encoding="utf-8")
        css = __import__("scripts.build_site", fromlist=["build_assets"]).build_assets()["karzat.css"]
        classes = {m.group(1) for m in self.OVERFLOW.finditer(css)} - self.EXEMPT
        self.assertTrue(classes, "no scrolling class found — has the stylesheet or this pattern changed?")
        bad = []
        for c in sorted(classes):
            for m in re.finditer(rf'<\w+[^>]*\bclass="[^"]*\b{re.escape(c)}\b[^"]*"[^>]*>', src):
                if "tabindex" not in m.group(0):
                    bad.append(f"  .{c}: {m.group(0)[:120]}")
        self.assertEqual(bad, [], "scrolling boxes a keyboard cannot reach:\n" + "\n".join(bad))


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


class TheSitemapNamesPagesThatExist(unittest.TestCase):
    """A sitemap is a recommendation to a crawler, and a wrong one spends somebody else's bandwidth on 404s.

    It deliberately lists a fraction of the site: the doors, and one career page per human the record names.
    Naming all 160,395 pages at the two-second crawl delay this site asks for would be a request to spend four
    days fetching what one Parquet file hands over in a single request — which is the argument robots.txt
    already makes, so the sitemap makes it too."""

    def setUp(self):
        self.site = ROOT / "site"
        self.sm = self.site / "sitemap.xml"
        if not self.sm.exists():
            self.skipTest("site/ is not built")

    def test_it_is_well_formed_and_every_address_is_a_page_that_exists(self):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(self.sm.read_text(encoding="utf-8"))
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locs = [e.text or "" for e in root.findall(".//s:loc", ns)]
        self.assertGreater(len(locs), 100, "the sitemap is suspiciously short")
        self.assertLess(len(locs), 50_000, "a sitemap file may hold 50,000 URLs; split it before it grows past")
        self.assertEqual(len(locs), len(set(locs)), "the same address is listed twice")
        from scripts.build_site import SITE_URL
        missing = []
        for loc in locs:
            self.assertTrue(loc.startswith(SITE_URL + "/"), loc)
            rel = loc[len(SITE_URL) + 1:]
            if not (self.site / rel).exists():
                missing.append(rel)
        self.assertEqual(missing[:5], [], f"{len(missing)} addresses point at nothing")

    def test_robots_points_at_it(self):
        self.assertIn("Sitemap:", (self.site / "robots.txt").read_text(encoding="utf-8"))

    def test_it_leaves_out_what_a_crawler_should_not_be_asked_to_walk(self):
        """The scope is a decision, not an oversight, so it is written down here."""
        body = self.sm.read_text(encoding="utf-8")
        for leaf in ("/szavazas/", "/felszolalas/nap", "/iromany/1.html", "/kepviselo/"):
            if leaf == "/kepviselo/":
                self.assertNotIn("/kepviselo/0", body, "per-MP pages are covered by szemely/, which does not move")
            else:
                self.assertNotIn(leaf, body, f"{leaf} pages are reachable from an index and need not be listed")
        self.assertIn("/szemely/", body, "the career pages are the ones a name search should find")

    def test_it_names_every_career_page_and_not_a_subset(self):
        """The half the sitemap exists for, counted rather than sampled.

        It was written with robots.txt, before the cross-cycle pages and the careers were built — so a clean
        build produced a sitemap of the ten cycles and nothing else: no keresés, no módszer, and none of the
        people. Every build until now had been incremental and found the previous run's `szemely/` still on
        disk, which is the sort of bug that only appears the first time the output directory is emptied. So
        the count is asserted against what is actually there, not the presence of the string."""
        on_disk = {f.name for f in (self.site / "szemely").glob("*.html")} - {"index.html"}
        listed = set(re.findall(r"/szemely/([^<]+\.html)", self.sm.read_text(encoding="utf-8"))) - {"index.html"}
        self.assertEqual(sorted(on_disk - listed)[:5], [], f"{len(on_disk - listed)} career pages are not listed")
        self.assertEqual(sorted(listed - on_disk)[:5], [], "the sitemap lists a career page that is not built")


class EveryColourTheStylesheetAsksForExists(unittest.TestCase):
    """A `var(--name)` nobody defined is not a syntax error — it is a rule that silently does nothing.

    Three rules for the Számok view switch referred to `--line` and `--muted`, which this project has never
    had; its tokens are `--border`, `--dim` and `--line2`. The pressed button therefore had no background at
    all, and `border:1px solid var(--line)` fell back to `currentColor`. None of that showed in a screenshot,
    because the pressed state also set a text colour and that part did work — so the control looked finished.

    Custom properties may also be set on an element, and six of them are: the seating chart and the bar widths
    pass geometry in through `style="--w:…"`. Those count as defined, so the builder source is read too.
    """

    def test_no_rule_refers_to_a_custom_property_that_is_never_set(self):
        css = __import__("scripts.build_site", fromlist=["build_assets"]).build_assets()["karzat.css"]
        src = BUILDER.read_text(encoding="utf-8")
        defined = set(re.findall(r"(--[\w-]+)\s*:", css)) | set(re.findall(r"(--[\w-]+)\s*:", src))
        used = set(re.findall(r"var\(\s*(--[\w-]+)", css))
        self.assertGreater(len(used), 20, "no custom properties found — has the stylesheet changed shape?")
        self.assertEqual(sorted(used - defined), [],
                         "the stylesheet asks for colours nobody defines; these rules do nothing")


class TwoPagesOfOneCycleAgreeOnHowManyDaysItSat(unittest.TestCase):
    """The cycle index prints "192 ülésnap" and the Számok page printed 106 of them, and nothing compared the
    two — so a five-fold overstatement of the House's pace sat in the accessible name of every month link on a
    chart captioned "mikor volt sűrű a Ház napirendje".

    `monthly()` had built its day count as the set of dates a vote fell on, which is a real and useful number
    and is not an ülésnap: the House sits and need not divide. Cycle 42 has 106 such dates against the
    record's own 192 numbered sittings; April 2023 has one against five, so the page announced 21.0 votes a
    day where the true pace was 4.2. Every check the feature had agreed with it, including the README's,
    because the README's gate recomputed the figure the same wrong way — two implementations of one mistake
    read as verification.

    What no check did was compare the number against the same cycle's other page. This does, over the built
    site, for every cycle that has both. It is deliberately a cross-page test rather than a unit one: the
    defect was not in either page's arithmetic but in the two of them meaning different things by one word.
    """

    MONTH = re.compile(r'data-m="(\d{4}-\d\d)"[^>]*data-sittings="(\d*)"')

    def setUp(self):
        self.site = ROOT / "site"
        if not (self.site / "index.html").exists():
            self.skipTest("site/ is not built")

    def test_the_month_strip_adds_up_to_the_cycle_card(self):
        checked = []
        for card in sorted(self.site.glob("ckl*/index.html")):
            cyc = card.parent.name
            strip = card.parent / "szamok" / "index.html"
            if not strip.exists():
                continue
            # On the card's own machine-readable attribute, not on the rendered digits: the figure is emitted
            # as <b data-kz-number="192">192</b><span data-kz-text>ülésnap</span>, and my first pattern tried
            # to walk the markup between the number and the word, matched nothing, and quietly compared zero
            # cycles. A test that passes by checking nothing is the exact failure this class exists to catch,
            # which is why the number of cycles actually compared is asserted at the end.
            m = re.search(r'data-kz-number="(\d+)"[^<]*</b><span data-kz-text>ülésnap</span>',
                          card.read_text(encoding="utf-8"))
            if not m:                       # cycles with no ulesnap list print no such figure, by design
                continue
            said = int(m.group(1))
            got = [int(n) for _mo, n in self.MONTH.findall(strip.read_text(encoding="utf-8")) if n]
            if not got:                     # the cycle's chart says it counts vote days instead, and is labelled so
                self.assertIn("szavazási naponként", strip.read_text(encoding="utf-8"),
                              f"{cyc}: no sittings on the strip and no statement that this is a different count")
                continue
            self.assertEqual(sum(got), said,
                             f"{cyc}: the cycle card says {said} ülésnap, the month strip adds up to {sum(got)}")
            checked.append(cyc)
        self.assertGreaterEqual(len(checked), 5, "too few cycles compared — has the markup changed?")
