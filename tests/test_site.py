"""The committed page must be exactly what the builder produces from the committed inputs.

Same rule as trigger-discipline's index.html: never hand-edit site/index.html; change the
inputs or the builder and rebuild. `python3 -m scripts.build_site --check` is the CLI form.
"""

import re
import unittest
from pathlib import Path

from scripts.build_site import HERO_TS, SITE, build, build_assets, build_mp_index, build_mp_page, build_vote_page, factions, hemicycle_layout, load_inputs, vote_view

ROOT = Path(__file__).resolve().parent.parent


def visible_text(page: str) -> str:
    """What a reader sees: markup and attributes stripped (aria-live is not the word 'live')."""
    return re.sub(r"<[^>]+>", " ", page).casefold()


class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = build()

    def test_committed_page_matches_a_fresh_build(self):
        self.assertTrue(SITE.exists(), "site/index.html missing — run python3 -m scripts.build_site")
        self.assertEqual(SITE.read_text(encoding="utf-8"), self.page,
                         "site/index.html is stale — rebuild with python3 -m scripts.build_site")

    def test_page_is_self_contained(self):
        # every external reference is a parlament.hu link on a motion; the only stylesheet and script are our
        # own generated files under site/assets/ (relative links, no CDN, no fonts fetched)
        for m in re.finditer(r'(?:src|href)="(https?://[^"]+)"', self.page):
            self.assertTrue(m.group(1).startswith("https://www.parlament.hu/"), m.group(1))
        self.assertEqual(re.findall(r'<link [^>]*href="([^"]+)"', self.page), ["assets/karzat.css"])
        self.assertEqual(re.findall(r'<script src="([^"]+)"', self.page), ["assets/karzat.js"])
        self.assertNotIn("@import", self.page)
        self.assertNotIn("url(http", build_assets()["karzat.css"])

    def test_committed_assets_match_a_fresh_build(self):
        for name, body in build_assets().items():
            f = ROOT / "site" / "assets" / name
            self.assertTrue(f.exists(), f"{f} missing — run python3 -m scripts.build_site")
            self.assertEqual(f.read_text(encoding="utf-8"), body, f"{f} is stale — rebuild")

    def test_boot_sequence_is_honest_and_optional(self):
        js = build_assets()["karzat.js"]
        self.assertIn("prefers-reduced-motion", js)                  # the whole boot choreography is skipped for reduced motion
        self.assertTrue(js.lstrip().startswith("(function(){"))
        # the terminal log is real values from the inputs (256 roll calls, not 259: three secret ballots), and no page claims to be live
        self.assertIn("kész — 259 szavazás-oldal", self.page)
        self.assertIn("259 részlet, 256 név szerinti lista", self.page)
        self.assertNotIn("SYSTEM_READY", self.page)
        self.assertIn("Frissítve: 2026. augusztus 18. 10:49 (budapesti idő).", self.page)   # absolute, not a frozen "15 perce"
        for word in ("live", "élő", "real-time"):
            self.assertNotIn(word, visible_text(self.page))
        # the footer is a landmark of its own, outside <main>
        self.assertLess(self.page.index("</main>"), self.page.index("<footer"))

    def test_hero_and_directory_are_present(self):
        self.assertEqual(self.page.count('<g class="seat"'), 199)
        self.assertIn("különbség +2", self.page)              # T/51: 135 igen, 133 needed
        self.assertIn("133 a 199-ból", self.page)
        self.assertEqual(self.page.count("<tr data-rule="), 259)      # the directory is rendered at build time: complete without JS
        self.assertIn('lang="hu"', self.page)
        for word in ("live", "élő", "real-time"):
            self.assertNotIn(word, visible_text(self.page))               # freshness contract holds on the page

    def test_freshness_sentence_is_the_contract_sentence(self):
        m = re.search(r'<span class="hu">(.*?)</span>', self.page)
        self.assertTrue(m)
        self.assertRegex(m.group(1), r"^Szavazások \d{4}\. \w+ \d+-ig\.")


class VotePages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inp = load_inputs()

    def test_every_vote_builds_with_a_unique_slug(self):
        slugs = [self.inp["by_ts"][ts]["slug"] for ts in self.inp["order"]]
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertEqual(len(slugs), 259)
        for ts in self.inp["order"][:5] + self.inp["order"][-5:]:
            page = build_vote_page(self.inp, ts)
            self.assertIn('lang="hu"', page)
            self.assertIn('class="pager"', page)

    def test_hero_page_has_full_roll_call_and_neighbours(self):
        page = build_vote_page(self.inp, HERO_TS)
        self.assertEqual(page.count('<g class="seat"'), 199)
        self.assertEqual(page.count("<tr data-f="), 199)
        self.assertIn("különbség +2", page)
        self.assertIn('href="2026-06-15T17-19-04.html"', page)      # previous vote
        self.assertIn('href="2026-06-15T17-21-24.html"', page)      # next vote
        self.assertIn("3. szektor", page)                            # seats from the reconstructed plan
        self.assertIn('class="sortable"', page)
        self.assertIn('data-posrank=', page)                                 # sort key kept apart from the filter attribute

    def test_secret_ballot_page_has_no_roll_call(self):
        page = build_vote_page(self.inp, "2026.05.09.11:50:00")
        self.assertNotIn('id="roll"', page)
        self.assertIn("Titkos szavazás", page)
        self.assertEqual(page.count('<g class="seat"'), 0)

    def test_quorum_page_explains_itself(self):
        page = build_vote_page(self.inp, "2026.05.09.10:45:47")
        self.assertIn("Jelenlét megállapítása — nem döntés", page)
        self.assertEqual(page.count("<tr data-f="), 199)

    def test_view_model_expands_positions_from_the_store(self):
        v = vote_view(self.inp, HERO_TS)
        self.assertEqual(len(v["positions"]), 199)
        self.assertEqual(sum(1 for p in v["positions"] if p["position"] == "igen"), 135)
        self.assertTrue(all(p["mp_azon"] for p in v["positions"]))    # every name resolved (aliases included)


class MPPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inp = load_inputs()

    def test_alignment_definitions_hold(self):
        al = self.inp["alignment"]["per_mp"]
        self.assertEqual(len(al), 201)
        a = al["a011"]
        self.assertEqual(a["in_roll"], 256)                       # every non-secret roll call of the cycle
        self.assertEqual(a["cast"], a["counts"].get("igen", 0) + a["counts"].get("nem", 0) + a["counts"].get("tartozkodott", 0))
        self.assertLessEqual(a["with"] + a["against"], a["cast"])
        # a former MP only appears while seated
        self.assertLess(al["b076"]["in_roll"], 256)
        # per-vote pluralities exist for every roll call and every faction that cast a vote
        plur = self.inp["alignment"]["plurality_by_vote"]
        self.assertEqual(len(plur), 256)
        self.assertEqual(plur[HERO_TS]["Fidesz"][0], "nem")
        self.assertEqual(plur[HERO_TS]["TISZA"][0], "igen")

    def test_mp_page_content(self):
        page = build_mp_page(self.inp, "a011")
        self.assertIn("Ágh Péter", page)
        self.assertIn("2. szektor, 5. sor, 7. szék", page)
        self.assertIn("Vas 2. OEVK", page)
        self.assertIn("kepviselok_mobil", page)                     # parlament.hu profile link
        self.assertIn("wikidata.org/wiki/Q", page)
        self.assertIn("Szavazatok a frakció többségével szemben", page)
        self.assertIn('id="mine"', page)
        self.assertEqual(page.count('href="../szavazas/'), 256 + self.inp["alignment"]["per_mp"]["a011"]["against"])
        self.assertNotIn('<img', page)                                # portraits are linked, not embedded (licence unverified)

    def test_former_mp_page_says_so(self):
        page = build_mp_page(self.inp, "b076")
        self.assertIn("Mandátuma megszűnt", page)
        self.assertIn("nincs ülőhely-adat", page)

    def test_mp_index(self):
        page = build_mp_index(self.inp)
        self.assertEqual(page.count("<tr data-f="), 201)
        self.assertIn("volt képviselő", page)
        self.assertIn('href="a011.html"', page)

    def test_vote_page_links_names_to_mp_pages(self):
        page = build_vote_page(self.inp, HERO_TS)
        self.assertEqual(page.count('href="../kepviselo/'), 199)


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
