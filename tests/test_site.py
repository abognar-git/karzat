"""The committed page must be exactly what the builder produces from the committed inputs.

Same rule as trigger-discipline's index.html: never hand-edit site/index.html; change the
inputs or the builder and rebuild. `python3 -m scripts.build_site --check` is the CLI form.
"""

import json
import re
import unittest
from pathlib import Path

from scripts.build_site import HERO_BY_CYCLE, HERO_TS, SITE, available_cycles, build, build_assets, build_index, build_mp_index, build_mp_page, build_vote_page, factions, hemicycle_layout, load_inputs, vote_view

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
        self.assertIn('<span class="more" title="', self.page)          # long titles keep their tail for the search
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
        self.assertEqual(page.count("data-seat="), 199)                      # every row sortable by seat; the seatless carry the sentinel
        self.assertIn('data-seat="999999"', page)

    def test_seat_inspector_data_and_against_rings(self):
        # 2026-07-28 10:35 (387/7): Fidesz 0–23–16 → the 16 abstainers voted against their group's plurality
        ts = "2026.07.28.10:35:33"
        page = build_vote_page(self.inp, ts)
        self.assertEqual(page.count('data-al="against"'), 16)
        self.assertEqual(page.count('class="against"'), 16)
        self.assertIn("frakciója többsége ellen <span class=\"mono\">16</span>", page)
        self.assertIn("Fehér gyűrű: 16 képviselő", page)
        m = re.search(r'<script type="application/json" id="insp-data">(.*?)</script>', page, re.S)
        self.assertTrue(m)
        data = json.loads(m.group(1).replace("<\\/", "</"))
        self.assertEqual(len(data), 198)                                     # every resolved MP in the roll call
        a011 = data["a011"]
        self.assertEqual(a011[0], "Ágh Péter")
        self.assertEqual(a011[1], "Fidesz")
        self.assertEqual(a011[4], "tartozkodott")
        self.assertEqual((a011[5], a011[6]), (239, 256))                     # the cycle record, as on the MP page
        self.assertEqual(len(a011[9]), 20)                                    # the last 20 roll calls up to this vote
        self.assertEqual(a011[9][-1], "a")                                    # …ending in this vote: against
        self.assertEqual(page.count('class="seat"'), 198)
        self.assertEqual(page.count('tabindex="0"'), 198)                     # every seat is keyboard-reachable
        self.assertEqual(page.count("<tr data-f="), len(re.findall(r'<tr data-f="[^>]*data-az="', page)))   # every roll-call row is linked to a seat
        # the hero vote has no dissenters and says nothing about rings
        hero = build_vote_page(self.inp, HERO_TS)
        self.assertNotIn("Fehér gyűrű", hero)
        self.assertNotIn('data-al="against"', hero)
        self.assertIn('id="insp-data"', self.__class__.inp and hero)

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

    def test_this_cycles_motions_reconcile_with_the_record_for_every_mp(self):
        # iromanyok.cgi by submitter == the record's <inditvanyok> "önálló" for 2026-, for all 201 people
        for azon, mp in self.inp["mps"].items():
            stat = next((x["onallo"] for x in mp["motion_stats"] if x["ciklus"] == "2026-"), 0)
            self.assertEqual(len(mp["motions"]), stat, f"{azon} {mp['name']}: list {len(mp['motions'])} vs record {stat}")
        a = self.inp["mps"]["a011"]["motions"]
        self.assertEqual(len(a), 5)
        self.assertTrue(all(m["href"] and m["szam"] and m["title"] for m in a))
        self.assertEqual([m["kind"] for m in a].count("K"), 4)
        page = build_mp_page(self.inp, "a011")
        self.assertIn("a jelenlegi ciklusban</span> 5 iromány · K/ 4 · H/ 1", page)
        self.assertEqual(page.count("iromanyok_mobil"), 5)
        self.assertNotIn("még nincsenek betöltve", page)
        # a closed cycle only has the counts, and says why
        page42 = build_mp_page(load_inputs(42), "a011")
        self.assertIn("A tételek csak a jelenlegi ciklusból érhetők el.", page42)
        self.assertNotIn("iromanyok_mobil", page42)

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


class Cycle42(unittest.TestCase):
    """The earlier cycle: same builder, its own tree (site/ckl42/), no seating (the API has none for it)."""

    @classmethod
    def setUpClass(cls):
        cls.inp = load_inputs(42)

    def test_inputs_are_the_whole_cycle(self):
        self.assertEqual(available_cycles(), [43, 42])
        self.assertEqual(len(self.inp["order"]), 2599)
        self.assertEqual(len({self.inp["by_ts"][ts]["slug"] for ts in self.inp["order"]}), 2599)
        self.assertEqual(len(self.inp["mps"]), 214)
        self.assertIsNone(self.inp["plan"])                                   # seats are present-tense in the API
        self.assertTrue(self.inp["closed"])
        self.assertEqual([f["id"] for f in self.inp["facs"]][:3], ["DK", "MSZP", "Párbeszéd"])
        self.assertNotIn("z012", self.inp["also_in"][43])                    # a cycle-42-only person is not in the 43 roster
        self.assertIn("a011", self.inp["also_in"][43])                        # a person in both cycles is

    def test_index_is_complete_closed_and_one_level_down(self):
        page = build_index(self.inp, HERO_BY_CYCLE[42])
        self.assertEqual(page.count("<tr data-rule="), 2599)
        self.assertIn('href="../assets/karzat.css"', page)                   # site/ckl42/index.html → ../assets
        self.assertIn("A 42. ciklus lezárult (2022-05-02 – 2026-05-08)", page)
        self.assertNotIn("Frissítve 1", page)                                 # no relative age anywhere
        self.assertIn("214 képviselő", page)                                  # the cycle's own roster, not today's list
        self.assertNotIn("TISZA: 141", page)
        self.assertIn('href="../index.html">43</a>', page)                    # the cycle switch
        self.assertIn("Egy szavazás, frakciónként", page)                     # not "ülőhelyenként": no chamber for a closed cycle
        self.assertIn("az Országgyűlés szavazásai, név szerint —", page)
        self.assertIn("név szerint, frakciónként rendezve kirajzolva", page)  # the meta description
        self.assertIn("14 ülésszak, 2022. tavaszi … 2026. tavaszi", page)      # endpoints by date, not by string order
        self.assertIn('<a class="brand" href="../ckl42/index.html">', page)   # the brand stays in its own tree
        self.assertIn("<title>karzat — 42. ciklus, az Országgyűlés szavazásai</title>", page)

    def test_hero_vote_page_uses_the_fallback_layout_and_says_so(self):
        page = build_vote_page(self.inp, HERO_BY_CYCLE[42])
        self.assertIn("tizenötödik módosítása", page)
        self.assertIn("különbség +7", page)                                   # 140 igen, 133 needed
        self.assertEqual(page.count('<g class="seat"'), 199)
        self.assertIn("nem az ülésrend", page)
        self.assertNotIn(">Ülőhely</th>", page)                               # no seat column without a plan
        self.assertIn('href="../../assets/karzat.css"', page)
        self.assertEqual(page.count('href="../kepviselo/'), 199)

    def test_seventh_state_is_drawn_where_it_occurs(self):
        page = build_vote_page(self.inp, "2025.05.20.10:20:49")               # the fixture vote: 6 Ig.távol
        self.assertIn("igazoltan távol", page)
        self.assertEqual(page.count('class="g g-igazoltan_tavol"'), 6)

    def test_mp_page_for_a_cycle_42_only_person(self):
        page = build_mp_page(self.inp, "z012")                                # Z. Kárpát Dániel, Jobbik, list mandate
        self.assertIn("Z. Kárpát Dániel", page)
        self.assertIn("Jobbik", page)
        self.assertIn("A 42. ciklus lezárult; mandátuma megszűnt", page)
        self.assertNotIn("Ülőhely az ülésteremben", page)                # no seat panel on a closed cycle
        self.assertNotIn("../../kepviselo/z012.html", page)                   # no cycle-43 page for him
        page2 = build_mp_page(self.inp, "a011")                              # Ágh Péter sits in both cycles
        self.assertIn('href="../../kepviselo/a011.html">43. ciklus ↗</a>', page2)
        page3 = build_mp_page(load_inputs(43), "a011")
        self.assertIn('href="../ckl42/kepviselo/a011.html">42. ciklus ↗</a>', page3)
        self.assertIn("<title>Ágh Péter (Fidesz) · 42. ciklus · karzat</title>", page2)
        self.assertIn("<title>Ágh Péter (Fidesz) · karzat</title>", page3)

    def test_every_attribution_agrees_with_the_persons_own_record(self):
        # the Kiss János lesson: a roll-call name resolved to a person whose record has no 2022-2026 row is a wrong person
        for azon, m in self.inp["mps"].items():
            rows = {(f["ciklus"], f["faction"]) for f in m["factions"]}
            self.assertTrue(any(c == "2022-2026" for c, _ in rows), f"{azon} {m['name']} has no cycle-42 row in the record")
            self.assertIn(("2022-2026", m["faction"]), rows, f"{azon} {m['name']}: roster faction {m['faction']} not in record {rows}")
        self.assertNotIn("003N", self.inp["mps"])                             # the 2026 TISZA Kiss János
        self.assertEqual(self.inp["mps"]["k166"]["name"], "Dr. Kiss János")     # the 2022–2026 Fidesz one
        for azon, m in load_inputs(43)["mps"].items():
            if m["current"]:
                self.assertIn(("2026-", m["faction"]), {(f["ciklus"], f["faction"]) for f in m["factions"]}, azon)

    def test_mandate_end_comes_from_the_record_not_wikidatas_first_statement(self):
        page = build_mp_page(self.inp, "j020")                                # Jakab Péter: Jobbik → független in 2022
        self.assertIn("mandátuma megszűnt 2026. május 8", page)
        self.assertNotIn("2022. augusztus 12", page)
        self.assertEqual(self.inp["mps"]["j020"]["mandate_to"], "2026-05-08")

    def test_switchers_carry_their_last_faction(self):
        m = self.inp["mps"]
        jakab = next(x for x in m.values() if x["name"] == "Jakab Péter")
        self.assertEqual((jakab["faction_first"], jakab["faction"]), ("Jobbik", "független"))
        idx = build_mp_index(self.inp)
        self.assertEqual(idx.count("<tr data-f="), 214)
        self.assertNotIn(">Ülőhely</th>", idx)
        self.assertIn("ma is képviselő", idx)


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
