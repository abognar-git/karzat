"""The committed page must be exactly what the builder produces from the committed inputs.

Same rule as trigger-discipline's index.html: never hand-edit site/index.html; change the
inputs or the builder and rebuild. `python3 -m scripts.build_site --check` is the CLI form.
"""

import json
import re
import unittest
from pathlib import Path

from scripts.build_site import HERO_BY_CYCLE, HERO_TS, SITE, available_cycles, build, build_assets, build_index, build_mp_index, build_mp_page, build_person_index, build_person_page, build_vote_page, factions, hemicycle_layout, hu_date, load_inputs, mp_exports, sync_stamp, vote_exports, vote_view

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

    def test_only_the_index_and_assets_are_tracked(self):
        # everything else under site/ is generated and git-ignored; a tracked page would silently go stale
        import subprocess
        try:
            out = subprocess.run(["git", "ls-files", "site"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        self.assertEqual(sorted(out), ["site/assets/karzat.css", "site/assets/karzat.js", "site/index.html"])

    def test_links_stay_inside_the_tree(self):
        # the current cycle lives at the site root: nothing on its directory may climb out of it
        self.assertNotIn('href="../', self.page)
        self.assertIn('href="iromany/', self.page)                            # the hero's bill link (rendered at depth 0)

    def test_page_is_self_contained(self):
        # every external reference is a parlament.hu link on a motion; the only stylesheet and script are our
        # own generated files under site/assets/ (relative links, no CDN, no fonts fetched)
        for m in re.finditer(r'(?:src|href)="(https?://[^"]+)"', self.page):
            self.assertTrue(m.group(1).startswith("https://www.parlament.hu/"), m.group(1))
        self.assertEqual(re.findall(r'<link rel="stylesheet" [^>]*href="([^"]+)"', self.page), ["assets/karzat.css"])
        self.assertEqual(re.findall(r'<link rel="alternate" type="application/atom\+xml" href="([^"]+)"', self.page), ["feed/kulonvelemeny.xml", "feed/szavazasok.xml", "feed/heti.xml"])   # feed autodiscovery, own files
        self.assertEqual(re.findall(r'<script src="([^"]+)"', self.page), ["assets/karzat.js"])
        self.assertNotIn("@import", self.page)
        self.assertNotIn("url(http", build_assets()["karzat.css"])

    def test_committed_assets_match_a_fresh_build(self):
        for name, body in build_assets().items():
            f = ROOT / "site" / "assets" / name
            self.assertTrue(f.exists(), f"{f} missing — run python3 -m scripts.build_site")
            self.assertEqual(f.read_text(encoding="utf-8"), body, f"{f} is stale — rebuild")

    def test_long_tables_paginate_and_stay_complete_without_js(self):
        js = build_assets()["karzat.js"]
        self.assertIn("table[data-page-size]", js)                       # the shared pager
        self.assertIn('data-page-size="25" data-counter="n"', self.page)  # the directory: all 259 rows in the HTML, 25 shown at a time
        self.assertEqual(self.page.count("<tr data-rule="), 259)
        vote = build_vote_page(self.__class__.inp if hasattr(self.__class__, "inp") else load_inputs(), HERO_TS)
        self.assertIn('id="roll" data-page-size="25" data-counter="rn"', vote)
        mp = build_mp_page(load_inputs(), "a011")
        self.assertIn('id="mine" data-page-size="25" data-counter="rn"', mp)
        self.assertEqual(mp.count('data-page-size="25"'), 5)             # weeks, speeches, votes, dissents, motions

    def test_boot_sequence_is_honest_and_optional(self):
        js = build_assets()["karzat.js"]
        self.assertIn("prefers-reduced-motion", js)                  # the whole boot choreography is skipped for reduced motion
        self.assertTrue(js.lstrip().startswith("(function(){"))
        # the terminal log is real values: the corpus, then this page's cycle; no page claims to be live
        self.assertIn("ezen az oldalon: 43. ciklus · 259 szavazás · 23 ülésnap · 199 képviselő", self.page)
        self.assertRegex(self.page, r"&gt; \d+ ciklus · \d{4}\. \w+ \d+\. – 2026\. augusztus 11\. · [\d\u00a0]+ szavazás · [\d\u00a0]+ név szerinti lista · [\d\u00a0]+ képviselő")
        self.assertRegex(self.page, r"a számított eredmény (minden szavazásnál egyezik a jegyzőkönyvivel|[\d\u00a0]+ szavazásnál eltér a jegyzőkönyvitől — jelölve)")   # corpus-wide, either honest form
        stamp = sync_stamp(load_inputs())                                       # the last sync, whatever it was
        self.assertIn(f"frissítve {hu_date(stamp[:10])} {stamp[11:]}", self.page)
        self.assertNotIn("SYSTEM_READY", self.page)
        self.assertNotIn(".cgi", self.page.split("<footer")[1])          # the footer talks about the data, not the plumbing
        self.assertIn(f"Frissítve: {hu_date(stamp[:10])} {stamp[11:]} (budapesti idő).", self.page)   # absolute, not a frozen "15 perce"
        for word in ("live", "élő", "real-time"):
            self.assertNotIn(word, visible_text(self.page))
        # the footer is a landmark of its own, outside <main>
        self.assertLess(self.page.index("</main>"), self.page.index("<footer"))

    def test_hero_and_directory_are_present(self):
        self.assertEqual(self.page.count('<g class="seat"'), 199)
        self.assertIn("különbség +2", self.page)              # T/51: 135 igen, 133 needed
        self.assertIn("133 / 199", self.page)
        self.assertEqual(self.page.count("<tr data-rule="), 259)      # the directory is rendered at build time: complete without JS
        self.assertNotIn('data-year="', self.page)                      # one year only → no year filter
        self.assertIn('title="2022-05-02 – 2026-05-08">42</a>', self.page)      # the other cycles, one click away
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
        self.assertEqual(page.count('tabindex="-1"'), 198)                    # one tab stop for the chamber; the script walks the seats
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
        self.assertIn('class="portrait hero"', page)                  # the parlament.hu portrait, shown from there (the owner's decision, attributed)
        self.assertNotIn('src="data:', page)                            # never copied into the page

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
        self.assertIn("Mandátum vége:", page)
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
        self.assertEqual(available_cycles(), [43, 42, 41, 40, 39, 38, 37, 36, 35, 34])   # every cycle since 1990
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
        self.assertIn("Lezárt ciklus, 2022-05-02 – 2026-05-08: mind a 2\u00a0599 szavazás itt van.", page)
        self.assertNotIn("Frissítve 1", page)                                 # no relative age anywhere
        self.assertIn("214 képviselő", page)                                  # the cycle's own roster, not today's list
        self.assertNotIn("TISZA: 141", page)
        # links leave the cycle root only for the shared trees (assets, people, method, search, other cycles)
        for h in re.findall(r'href="\.\./([^"#]+)', page):
            self.assertRegex(h, r"^(assets/|szemely/|modszer/|kereses/|index\.html$|ckl\d+/)", h)
        self.assertIn('href="../index.html" class="near">43</a>', page)       # the cycle switch (43 is the neighbour of 42)
        self.assertIn('ciklusok: <a href="../index.html" title="2026-05-09 –">43</a> · <b>42</b>', page)   # …and in the section nav
        self.assertEqual(page.count('data-year="'), 5 + 1)                     # 2022…2026 + 'minden év'
        self.assertEqual(page.count('data-y="2023"'), 719)
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
        self.assertIn("Ülésrend csak a jelenlegi ciklusból ismert", page)
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
        self.assertIn("A 42. ciklus lezárult · mandátum vége:", page)
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
        self.assertIn("mandátum vége: 2026. május 8.", page)
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


class Exports(unittest.TestCase):
    """Every table on a page has a CSV/JSON twin, and the twins say the same thing as the page."""

    @classmethod
    def setUpClass(cls):
        cls.inp = load_inputs()

    def test_vote_exports_match_the_page(self):
        j, c = vote_exports(self.inp, HERO_TS)
        obj = json.loads(j)
        self.assertEqual(obj["vote"]["needed"], 133)
        self.assertEqual(obj["vote"]["igen"], 135)
        self.assertEqual(len(obj["positions"]), 199)
        self.assertEqual(sum(1 for p in obj["positions"] if p["position"] == "igen"), 135)
        lines = c.lstrip("\ufeff").splitlines()
        self.assertEqual(lines[0], "ts,p_azon,name,faction,position,position_label,against_faction,sector,row,seat")
        self.assertEqual(len(lines), 200)                                    # header + 199 rows
        self.assertTrue(all(l.startswith(HERO_TS + ",") for l in lines[1:]))
        page = build_vote_page(self.inp, HERO_TS)
        self.assertIn('href="2026-06-15T17-20-04.csv">CSV</a>', page)
        self.assertIn('href="2026-06-15T17-20-04.json">JSON</a>', page)
        self.assertIn("@misc{karzat-43-2026-06-15T17-20-04,", page)         # the citation box
        self.assertIn(f"frissítve {sync_stamp(self.inp)}", page)

    def test_mp_exports_match_the_page(self):
        j, c = mp_exports(self.inp, "a011")
        obj = json.loads(j)
        self.assertEqual(obj["summary"], {"in_roll": 256, "cast": 239, "with": 231, "against": 7})   # the quorum check is participation, not "with"
        self.assertEqual(len(obj["votes"]), 256)
        self.assertEqual(len(obj["motions"]), 5)
        lines = c.lstrip("\ufeff").splitlines()
        self.assertEqual(len(lines), 257)
        page = build_mp_page(self.inp, "a011")
        self.assertIn('href="a011.csv">CSV</a>', page)
        self.assertIn("@misc{karzat-43-a011,", page)
        self.assertIn('szemely/a011.html">pályakép ↗</a>', page)              # the career page is one click away

    def test_data_dictionary_names_every_exported_column(self):
        from karzat import export as ex
        text = " ".join(col for _, cols in ex.adatszotar() for col, _ in cols)
        for column in ex.VOTE_COLUMNS + ex.POSITION_COLUMNS + ex.MP_COLUMNS + ex.MOTION_COLUMNS:
            self.assertTrue(any(column == part.strip() for part in text.replace(",", " ").split()), f"{column} missing from the adatszótár")


class CareerPages(unittest.TestCase):
    def test_person_page_spans_the_loaded_cycles(self):
        inp = load_inputs()
        inp["facs_all"] = {f["id"]: f["colour"] for c in available_cycles() for f in factions(c)}
        stints = [{"cycle": 43, "name": "Ágh Péter", "faction": "Fidesz", "faction_first": "Fidesz", "mandate": "egyéni választókerület — Vas 2. OEVK",
                   "mandate_from": "2026-05-09", "mandate_to": None, "current": True, "wikidata_qid": "Q1469363", "parlament_url": "https://www.parlament.hu/x",
                   "factions": [{"ciklus": "2026-", "faction": "Fidesz", "from": "2026-05-09", "to": None}], "elections": [], "motion_stats": [],
                   "in_roll": 256, "cast": 239, "with": 231, "against": 7, "href": "kepviselo/a011.html"},
                  {"cycle": 42, "name": "Ágh Péter", "faction": "Fidesz", "faction_first": "Fidesz", "mandate": "egyéni választókerület — Vas 2. OEVK",
                   "mandate_from": "2022-05-02", "mandate_to": "2026-05-08", "current": True, "wikidata_qid": "Q1469363", "parlament_url": None,
                   "factions": [], "elections": [], "motion_stats": [], "in_roll": 2579, "cast": 2495, "with": 2487, "against": 8, "href": "ckl42/kepviselo/a011.html"}]
        page = build_person_page(inp, "a011", stints)
        self.assertIn("2 betöltött ciklus", page)
        self.assertIn('href="../kepviselo/a011.html">43. ciklus</a>', page)
        self.assertIn('href="../ckl42/kepviselo/a011.html">42. ciklus</a>', page)
        self.assertIn("2\u00a0835 névsorban · leadott 2\u00a0734 · frakciójával 2\u00a0718 · ellene 15", page)   # the two stints above, added up
        self.assertIn("@misc{karzat-szemely-a011,", page)
        idx = build_person_index(inp, {"a011": stints})
        self.assertIn('href="a011.html">Ágh Péter</a>', idx)
        self.assertIn("<td class=\"mono\">42 · 43</td>", idx)


class AllCycles(unittest.TestCase):
    """Whatever cycles have derived inputs: the same invariants hold for each of them."""

    def test_every_cycle_builds_and_reconciles(self):
        from karzat.normalise import CYCLE_LABELS
        from scripts.build_site import pick_hero
        cycles = available_cycles()
        self.assertEqual(cycles[0], 43)
        for c in cycles:
            inp = load_inputs(c)
            with self.subTest(cycle=c):
                self.assertEqual(inp["cycle"], c)
                self.assertGreater(len(inp["order"]), 0)
                # every group the roll calls print has a colour and an order for this cycle
                printed = set(inp["store"]["factions"]) - {""}
                configured = {f["id"] for f in inp["facs"]}
                self.assertEqual(printed - configured, set(), f"cycle {c}: groups without a factions.yml entry")
                # every person in the roster sits in that cycle per their own record, under the roster's faction
                label = CYCLE_LABELS[c]
                for azon, m in inp["mps"].items():
                    if not m.get("factions"):
                        continue                                                    # no record fetched (should not happen after the record sync)
                    rows = {(f["ciklus"], f["faction"]) for f in m["factions"]}
                    self.assertTrue(any(cl == label for cl, _ in rows), f"cycle {c}: {azon} {m['name']} has no {label} row")
                    if m.get("faction") and m["faction"] != "független":
                        self.assertIn((label, m["faction"]), rows, f"cycle {c}: {azon} {m['name']} roster faction {m['faction']} not in record")
                # the index and the hero page build, and the directory is complete
                page = build_index(inp, pick_hero(inp))
                self.assertEqual(page.count("<tr data-rule="), len(inp["order"]))
                self.assertIn(f"{c}. ciklus" if c != 43 else "karzat", page)
                hero = build_vote_page(inp, pick_hero(inp))
                if inp["archive"]:
                    self.assertIn("Archív ciklus", hero)                                    # light page: no chamber, no roll-call table
                    self.assertNotIn('id="roll"', hero)
                else:
                    self.assertIn('id="insp-data"', hero)
                # nobody in a roll call resolved to a person outside this cycle's roster
                self.assertEqual(set(inp["store"]["members"]) - set(inp["mps"]), set())


class ResearchPages(unittest.TestCase):
    """The newer builders: each renders, names itself, carries its data links and its definitions."""

    @classmethod
    def setUpClass(cls):
        from karzat import analytics as an
        cls.inp = load_inputs()
        cls.inp["facs_all"] = {f["id"]: f["colour"] for c in available_cycles() for f in factions(c)}
        cls.co = an.cohesion(cls.inp)
        cls.cl = an.close_votes(cls.inp, cls.co["plurality"])
        cls.bs = an.bills(cls.inp)
        cls.ms = an.monthly(cls.inp, cls.co)["months"]

    def test_cohesion_page(self):
        from scripts.build_site import build_cohesion_page
        page = build_cohesion_page(self.inp, self.co)
        self.assertIn("<title>Kohézió · 43. ciklus · karzat</title>", page)
        self.assertIn("egyöntetűség", page)                                             # Rice explained the right way round
        self.assertIn('href="szavazasonkent.csv">CSV</a>', page)                       # every export linked
        self.assertNotIn("„független” sor", page)                                       # no független row in cycle 43 → no note about it
        self.assertIn("döntetlennél nincs többség", page)
        self.assertRegex(page, r"\d,\d\d</td>")                                         # Hungarian decimals

    def test_close_votes_page(self):
        from scripts.build_site import build_close_page
        page = build_close_page(self.inp, self.cl)
        self.assertIn("<title>Szoros szavazások · 43. ciklus · karzat</title>", page)
        self.assertIn("feltevés, nem tény", page)                                       # the counterfactual is labelled
        self.assertIn('href="dontesek.csv"', page)

    def test_bill_pages(self):
        from scripts.build_site import build_bill_index, build_bill_page
        idx = build_bill_index(self.inp, self.bs)
        self.assertIn('data-key="num">Szám', idx)                                       # numeric sort, not lexicographic
        self.assertIn('href="iromanyok.csv">CSV</a>', idx)
        self.assertIn('data-filter-table="roll"', idx)
        page = build_bill_page(self.inp, self.bs[122])
        self.assertIn("a javaslatról magáról:", page)                                   # T/122's own passage counts as its own
        self.assertIn("<tr class=own>", page)

    def test_numbers_page(self):
        from scripts.build_site import build_numbers_page
        page = build_numbers_page(self.inp, self.ms)
        self.assertIn("<title>Számok · 43. ciklus · karzat</title>", page)
        self.assertEqual(page.count('role="img" aria-label="'), 5)                     # every chart is named
        self.assertIn('class="tswrap"', page)

    def test_data_page(self):
        from scripts.build_site import build_data_page
        page = build_data_page(self.inp)
        for f in ("iromanyok.csv", "szavazasonkent.csv", "dontesek.csv", "havonta.csv", "nevsorok.csv"):
            self.assertIn(f, page)
        self.assertIn("faction_tallies", page)                                          # documented

    def test_committee_file_names_fit_a_file_system(self):
        from scripts.build_site import cslug
        self.assertEqual(cslug("Költségvetési bizottság"), "koltsegvetesi-bizottsag")
        long = "A MALÉV Zrt. és a Budapest Airport Zrt. " * 12                             # cycle 39 has a 380-character inquiry committee
        self.assertLessEqual(len(cslug(long)), 110)
        self.assertEqual(cslug(long), cslug(long))                                       # stable
        self.assertNotEqual(cslug(long), cslug(long + " (2)"))                            # two long names → two files
        self.assertRegex(cslug(long), r"-[0-9a-f]{8}$")

    def test_search_and_method_pages(self):
        from scripts.build_site import build_method_page, build_search_page
        s = build_search_page(self.inp, 2088)
        self.assertIn("<noscript>", s)
        self.assertIn('id="sq"', s)
        m = build_method_page(self.inp)
        self.assertIn("<title>Módszer · karzat</title>", m)
        self.assertIn("jelöltek", m)                                                    # no English leaks in the rule table
        self.assertNotIn("n/a", m)
        self.assertIn("ülőhely-vizsgáló", m)

    def test_hostile_strings_are_escaped_everywhere(self):
        # a title with markup and a comment opener: never live markup, and the JSON island stays parseable
        from scripts.build_site import chart_block, esc, cut, ext_url, name_key
        bad = '</script><img src=x onerror=alert(1)><!--&"'
        self.assertNotIn("<img", esc(bad))
        self.assertEqual(ext_url("javascript:alert(1)"), "")
        self.assertEqual(ext_url("hendematepest13.hu"), "https://hendematepest13.hu")
        self.assertEqual(ext_url("https://www.facebook.com/x"), "https://www.facebook.com/x")
        self.assertEqual(cut("egy kettő három négy", 12), "egy kettő…")
        self.assertEqual(name_key("Dr. Bódis Péter"), "bodis peter")
        self.assertLess(name_key("Bódis"), name_key("Bujdosó"))                            # ó sorts with o
        self.assertGreater(name_key("Öri"), name_key("Ozsvát"))                            # ö after every o


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
