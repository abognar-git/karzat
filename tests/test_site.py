"""The committed page must be exactly what the builder produces from the committed inputs.

Same rule as trigger-discipline's index.html: never hand-edit site/index.html; change the
inputs or the builder and rebuild. `python3 -m scripts.build_site --check` is the CLI form.
"""

import re
import unittest
from pathlib import Path

from scripts.build_site import SITE, build, factions, hemicycle_layout

ROOT = Path(__file__).resolve().parent.parent


class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = build()

    def test_committed_page_matches_a_fresh_build(self):
        self.assertTrue(SITE.exists(), "site/index.html missing — run python3 -m scripts.build_site")
        self.assertEqual(SITE.read_text(encoding="utf-8"), self.page,
                         "site/index.html is stale — rebuild with python3 -m scripts.build_site")

    def test_page_is_self_contained(self):
        # every external reference is a parlament.hu link on a motion; no scripts, styles or fonts fetched
        for m in re.finditer(r'(?:src|href)="(https?://[^"]+)"', self.page):
            self.assertTrue(m.group(1).startswith("https://www.parlament.hu/"), m.group(1))
        self.assertNotIn("<link ", self.page)
        self.assertNotIn("@import", self.page)

    def test_hero_and_directory_are_present(self):
        self.assertEqual(self.page.count('<g class="seat"'), 199)
        self.assertIn("különbség +2", self.page)              # T/51: 135 igen, 133 needed
        self.assertIn("133 a 199-ból", self.page)
        self.assertIn("__KARZAT_VOTES__", self.page)
        self.assertIn('lang="hu"', self.page)
        for word in ("live", "élő", "real-time"):
            self.assertNotIn(word, self.page.split("<footer>")[0].casefold())   # freshness contract holds on the page

    def test_freshness_sentence_is_the_contract_sentence(self):
        m = re.search(r'<span class="hu">(.*?)</span>', self.page)
        self.assertTrue(m)
        self.assertRegex(m.group(1), r"^Szavazások \d{4}\. \w+ \d+-ig\.")


class Helpers(unittest.TestCase):
    def test_factions_reader_gets_colours_and_order(self):
        facs = factions()
        ids = [f["id"] for f in facs]
        self.assertEqual(ids[:4], ["TISZA", "Fidesz", "KDNP", "Mi Hazánk"])
        for f in facs:
            self.assertRegex(f["colour"], r"^#[0-9a-fA-F]{6}$")   # the '#' bug: never again

    def test_layout_places_exactly_n_seats_left_to_right(self):
        seats = hemicycle_layout(199)
        self.assertEqual(len(seats), 199)
        xs = [s["cx"] for s in seats]
        # first seats are on the left (negative x), last on the right
        self.assertLess(sum(xs[:20]) / 20, 0)
        self.assertGreater(sum(xs[-20:]) / 20, 0)
        self.assertTrue(all(s["cy"] <= 0.01 for s in seats))    # a semicircle above the axis


if __name__ == "__main__":
    unittest.main()
