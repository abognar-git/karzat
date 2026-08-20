"""The committed page must be exactly what the builder produces from the committed inputs.

Same rule as trigger-discipline's index.html: never hand-edit site/index.html; change the
inputs or the builder and rebuild. `python3 -m scripts.build_site --check` is the CLI form.
"""

import json
import re
import unittest
from pathlib import Path

from scripts.build_site import CURRENT_CYCLE, HERO_BY_CYCLE, HERO_TS, SITE, available_cycles, build, build_assets, build_index, build_landing, composition_on, cycle_dir, site_totals, build_mp_index, build_mp_page, build_person_index, build_person_page, build_vote_page, factions, hemicycle_layout, hu_date, load_inputs, mp_exports, sync_stamp, vote_exports, vote_view

ROOT = Path(__file__).resolve().parent.parent


def visible_text(page: str) -> str:
    """What a reader sees: markup and attributes stripped (aria-live is not the word 'live')."""
    return re.sub(r"<[^>]+>", " ", page).casefold()


class Build(unittest.TestCase):
    """The site root is the landing page; the current cycle's page sits under ckl43/ like every other cycle's."""
    @classmethod
    def setUpClass(cls):
        cls.page = build()                                                  # the landing (site/index.html)
        cls.cyc = build_index(load_inputs(), HERO_TS)                       # the current cycle's page (site/ckl43/index.html)

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
        # the site root's own files are committed (they are generated, checked, and small); everything else is ignored
        expected = {"site/404.html", "site/assets/apple-touch-icon.png", "site/assets/favicon.ico",
                    "site/assets/favicon.svg", "site/assets/karzat.css", "site/assets/karzat.js",
                    "site/index.html", "site/robots.txt"}
        self.assertEqual(set(out) - {"site/tenyek.json"}, expected)

    def test_links_stay_inside_the_tree(self):
        # the landing is the root: nothing on it climbs out; every cycle is one level down, the current one included,
        # so a vote's address never changes when the next cycle starts
        self.assertNotIn('href="../', self.page)
        self.assertIn(f'href="{cycle_dir(CURRENT_CYCLE)}index.html"', self.page)
        self.assertEqual(cycle_dir(CURRENT_CYCLE), "ckl43/")
        self.assertIn('href="iromany/', self.cyc)                             # the hero's bill link on the cycle page (depth 0 in its tree)
        self.assertIn('href="../assets/karzat.css"', self.cyc)                # site/ckl43/index.html → ../assets
        self.assertIn('<a class="brand" href="../index.html">', self.cyc)     # the brand goes to the landing
        for h in re.findall(r'href="\.\./([^"#]+)', self.cyc):
            self.assertRegex(h, r"^(assets/|szemely/|modszer/|kereses/|index\.html$|ckl\d+/)", h)

    def test_landing_is_the_view_from_the_gallery(self):
        tot = site_totals()
        self.assertIn("<title>karzat — az Országgyűlés szavazásai 1990 óta</title>", self.page)
        self.assertIn("Tiszta adat, kommentár nélkül.", self.page)                # the lede's closing beat
        inp0 = load_inputs()
        n_sz = len((inp0["szoszolok"] or {}).get("people") or {})
        n_km = len((inp0["kormany"] or {}).get("people") or {})
        self.assertEqual(self.page.count('class="today"'), 199 + n_sz + n_km)  # every MP by faction, every spokesperson as a ring, every non-MP minister as a disc
        self.assertIn("Az ülésterem ma", self.page)
        self.assertIn(f'aria-label="Az ülésterem ma: 199 képviselő, {n_sz} nemzetiségi szószóló és {n_km} mandátum nélküli kormánytag a helyén', self.page)
        self.assertEqual(self.page.count('<a class="cyc'), len(tot["cycles"]))  # one row per cycle, the current one marked
        self.assertIn('<a class="cyc now" href="ckl43/index.html"', self.page)
        self.assertIn('title="TISZA 141"', self.page)                           # the composition at the constituent sitting
        self.assertIn('title="MDF 165"', self.page)                             # …back to 1990
        for c in tot["cycles"]:
            self.assertIn(f'href="{cycle_dir(c)}index.html"', self.page)
        self.assertIn(f'data-kz-number="{tot["votes"]}"', self.page)            # the corpus numbers are the builder's own
        self.assertIn(f'data-kz-number="{tot["roll_calls"]}"', self.page)
        self.assertIn(f'data-kz-number="{tot["people"]}"', self.page)
        self.assertIn("A legutóbbi ülésnap", self.page)
        self.assertIn('action="ckl43/kepviselom/index.html" method="get"', self.page)   # the doors are plain forms: no JS needed
        self.assertIn('action="kereses/index.html" method="get"', self.page)
        self.assertIn('action="ckl43/felszolalas/kereses.html" method="get"', self.page)
        self.assertIn('<link rel="alternate" type="application/atom+xml" href="ckl43/feed/kulonvelemeny.xml"', self.page)
        for word in ("live", "élő", "real-time"):
            self.assertNotIn(word, visible_text(self.page))
        self.assertLess(self.page.index("</main>"), self.page.index("<footer"))

    def test_the_chamber_on_the_landing_is_interactive_and_carries_its_own_data(self):
        from scripts.build_site import hall_data
        inp = load_inputs()
        data = hall_data(inp)
        n_sz = len((inp["szoszolok"] or {}).get("people") or {})
        n_km = len((inp["kormany"] or {}).get("people") or {})
        self.assertEqual(len(data), len(inp["plan"]["coords"]) + n_sz + n_km)   # one entry per person in the room, no more
        self.assertEqual(self.page.count('<g class="seat" role="button"'), len(inp["plan"]["coords"]))
        self.assertEqual(self.page.count('<g class="seat sz" role="button"'), n_sz)
        self.assertEqual(self.page.count('<g class="seat km" role="button"'), n_km)
        n_pres = self.page.count('<g class="seat pres"')                        # the presidium on the platform: the same members, a second handle
        self.assertEqual(self.page.count('class="hit"'), len(data) + n_pres)    # the whole seat cell answers to the pointer
        self.assertGreaterEqual(n_pres, 1)                                      # at least the Speaker
        for az in re.findall(r'<g class="seat pres"[^>]*data-az="([^"]+)"', self.page):
            self.assertIn(az, data)                                             # every platform dot is a member drawn on the floor too
        self.assertIn('id="hall-data" data-mp-base="ckl43/kepviselo/"', self.page)
        self.assertIn('<div class="inspector" id="hall"', self.page)
        for azon, row in data.items():
            self.assertIn(f'data-az="{azon}"', self.page)
            name, fac, mandate, seat, cast, in_roll, w, a, sp = row[:9]
            if fac in ("szószóló", "kormánytag"):
                continue                                                        # no roll call names them: checked in test_speeches
            rec = inp["alignment"]["per_mp"].get(azon) or {"cast": 0, "in_roll": 0, "with": 0, "against": 0}
            self.assertEqual([cast, in_roll, w, a], [rec["cast"], rec["in_roll"], rec["with"], rec["against"]])   # the MP page's own numbers
            self.assertLessEqual(w + a, in_roll)
            self.assertTrue(seat)                                               # a seated member always has a place
        js = build_assets()["karzat.js"]
        for hook in ("chamber-today", "hall-data", "data-hf", "ArrowRight", "Escape"):
            self.assertIn(hook, js)
        self.assertEqual(len(json.loads(re.search(r'id="hall-data"[^>]*>(.*?)</script>', self.page, re.S).group(1))), len(data))
        # a card names the member and links to their page; the legend doubles as a filter
        self.assertIn("Egy helyre mutatva:", self.page)
        for f, _, _ in [(f["id"], 0, 0) for f in factions()]:
            if f"data-f=\"{f}\"" in self.page:
                self.assertIn(f'data-hf="{f}"', self.page)

    def test_composition_is_read_from_the_records_dated_rows(self):
        mps = {"a": {"mandate_from": "2026-05-09", "mandate_to": None, "factions": [{"faction": "TISZA", "from": "2026-05-09", "to": None}]},
               "b": {"mandate_from": "2026-05-09", "mandate_to": "2026-06-01", "factions": []},                 # a mandate without a faction row: független
               "c": {"mandate_from": "2026-07-01", "mandate_to": None, "factions": [{"faction": "Fidesz", "from": "2026-07-01", "to": None}]},   # not yet
               "d": {"mandate_from": None}}
        comp = composition_on(mps, "2026-05-09", factions())
        self.assertEqual([(f, n) for f, n, _ in comp], [("független", 1), ("TISZA", 1)])   # config order: opposition left, government right

    def test_page_is_self_contained(self):
        # every external reference is a parlament.hu link on a motion; the only stylesheet and script are our
        # own generated files under site/assets/ (relative links, no CDN, no fonts fetched)
        for page in (self.page, self.cyc):
            for m in re.finditer(r'(?:src|href)="(https?://[^"]+)"', page):
                self.assertTrue(m.group(1).startswith("https://www.parlament.hu/"), m.group(1))
            self.assertNotIn("@import", page)
        self.assertEqual(re.findall(r'<link rel="stylesheet" [^>]*href="([^"]+)"', self.page), ["assets/karzat.css"])
        self.assertEqual(re.findall(r'<link rel="alternate" type="application/atom\+xml" href="([^"]+)"', self.page), ["ckl43/feed/kulonvelemeny.xml", "ckl43/feed/heti.xml", "ckl43/feed/felszolalasok.xml"])   # feed autodiscovery, own files
        self.assertEqual(re.findall(r'<script src="([^"]+)"', self.page), ["assets/karzat.js"])
        self.assertEqual(re.findall(r'<link rel="alternate" type="application/atom\+xml" href="([^"]+)"', self.cyc), ["feed/kulonvelemeny.xml", "feed/szavazasok.xml", "feed/heti.xml"])
        self.assertNotIn("url(http", build_assets()["karzat.css"])

    def test_the_favicon_is_generated_and_announced(self):
        from scripts.build_site import ROOT as _R, build_assets
        a = build_assets()
        svg = a["favicon.svg"]
        self.assertIn('viewBox="0 0 64 64"', svg)
        self.assertIn("prefers-color-scheme: light", svg)                        # the mark inverts under a light UI
        self.assertLess(len(svg), 1200)                                          # one shape, no gradients, no cruft
        for page in (self.page, self.cyc):
            self.assertIn('rel="icon"', page)
            self.assertIn('favicon.svg" type="image/svg+xml"', page)
            self.assertIn('rel="apple-touch-icon"', page)
        for name in ("favicon.ico", "apple-touch-icon.png"):                     # rendered by scripts/make_icons.py
            f = ROOT / "site" / "assets" / name
            self.assertTrue(f.exists(), f"{name} missing — run python3 -m scripts.make_icons")
            self.assertGreater(f.stat().st_size, 500)

    def test_the_footer_carries_a_fact_that_differs_by_page(self):
        from scripts.build_site import DERIVED, fact_for, facts
        pool = facts()
        if not pool:
            self.skipTest("no fact pool derived yet")
        for f in pool:
            self.assertTrue(f["hu"].strip() and f["scope"].strip())              # every fact says what it covers
            self.assertLessEqual(len(f["hu"]), 200)
        seen = {fact_for(f"ckl43/szavazas/x{i}", 1) for i in range(len(pool) * 3)}
        self.assertGreater(len(seen), 1)                                          # the page's address picks the fact
        self.assertEqual(fact_for("ckl43/szavazas/x1", 1), fact_for("ckl43/szavazas/x1", 1))   # …deterministically
        self.assertIn('class="factline"', self.page)
        self.assertIn("data-fact-next", self.page)
        js = build_assets()["karzat.js"]
        self.assertIn("tenyek.json", js)                                          # the rotate button reads the pool

    def test_the_inspector_card_contains_its_portrait(self):
        # the portrait floats beside the text; without containment the card's box ends above the photo and the
        # picture hangs outside the rectangle (it did — a reader saw it before the tests did)
        css = build_assets()["karzat.css"]
        self.assertRegex(css, r"\.inspector\{[^}]*display:flow-root")
        self.assertIn(".portrait.insp{width:48px;height:64px;float:left", css)

    def test_the_three_door_forms_line_up(self):
        # the doors' copy is one or two lines long; the search rows must still sit on one line across the three cards
        css = build_assets()["karzat.css"]
        self.assertRegex(css, r"\.door\{[^}]*display:flex[^}]*flex-direction:column")
        self.assertRegex(css, r"\.door \.row\{[^}]*margin-top:auto")
        self.assertRegex(css, r"\.door \.go\{[^}]*margin-top:auto")

    def test_committed_assets_match_a_fresh_build(self):
        for name, body in build_assets().items():
            f = ROOT / "site" / "assets" / name
            self.assertTrue(f.exists(), f"{f} missing — run python3 -m scripts.build_site")
            self.assertEqual(f.read_text(encoding="utf-8"), body, f"{f} is stale — rebuild")

    def test_long_tables_paginate_and_stay_complete_without_js(self):
        js = build_assets()["karzat.js"]
        self.assertIn("table[data-page-size]", js)                       # the shared pager
        self.assertIn('data-page-size="25" data-counter="n"', self.cyc)   # the directory: all 259 rows in the HTML, 25 shown at a time
        self.assertEqual(self.cyc.count("<tr data-rule="), 259)
        vote = build_vote_page(self.__class__.inp if hasattr(self.__class__, "inp") else load_inputs(), HERO_TS)
        self.assertIn('id="roll" data-page-size="25" data-counter="rn"', vote)
        mp = build_mp_page(load_inputs(), "a011")
        self.assertIn('id="mine" data-page-size="25" data-counter="rn"', mp)
        self.assertEqual(mp.count('data-page-size="25"'), 5)             # weeks, speeches, votes, dissents, motions

    def test_boot_sequence_is_honest_and_optional(self):
        """The terminal used to print four lines of true numbers on every page — the corpus, this page's cycle, the
        rule-versus-record check, the sync stamp — which every page repeated and nobody read twice. Each of them
        lives on the page that owns it now, and the terminal keeps the one line that differs page to page: a fact
        from the corpus. What must not come back is a claim of being live."""
        js = build_assets()["karzat.js"]
        self.assertIn("prefers-reduced-motion", js)                  # the whole boot choreography is skipped for reduced motion
        self.assertTrue(js.lstrip().startswith("(function(){"))
        stamp = sync_stamp(load_inputs())                                       # the last sync, whatever it was
        for page in (self.page, self.cyc):
            log = re.search(r'<div class="log">(.*?)</div>', page, re.S).group(1)
            self.assertEqual(log.count('class="factline"'), 1)                  # the fact…
            self.assertNotIn("<span>&gt;", log)                                 # …and none of the old boot lines
            self.assertNotIn("ezen az oldalon", page)
            self.assertNotIn("SYSTEM_READY", page)
            self.assertNotIn(".cgi", page.split("<footer")[1])          # the footer talks about the data, not the plumbing
            self.assertIn(f"Frissítve: {hu_date(stamp[:10])} {stamp[11:]} (budapesti idő).", page)   # absolute, not a frozen "15 perce"
            for word in ("live", "élő", "real-time"):
                self.assertNotIn(word, visible_text(page))
            # the footer is a landmark of its own, outside <main>
            self.assertLess(page.index("</main>"), page.index("<footer"))

    def test_hero_and_directory_are_present(self):
        self.assertEqual(self.cyc.count('<g class="seat"'), 199)
        self.assertIn("különbség +2", self.cyc)              # T/51: 135 igen, 133 needed
        self.assertIn("133 / 199", self.cyc)
        self.assertEqual(self.cyc.count("<tr data-rule="), 259)      # the directory is rendered at build time: complete without JS
        self.assertNotIn('data-year="', self.cyc)                      # one year only → no year filter
        self.assertIn('href="../ckl42/index.html" class="near">42</a>', self.cyc)   # the other cycles, one click away (the top bar)
        self.assertIn('<span class="kicker" data-kz-text>43. ciklus · folyamatban</span><h1>2026. május 9. óta</h1>', self.cyc)
        self.assertIn('<span class="more" title="', self.cyc)          # long titles keep their tail for the search
        self.assertIn('lang="hu"', self.cyc)
        for word in ("live", "élő", "real-time"):
            self.assertNotIn(word, visible_text(self.cyc))               # freshness contract holds on the page

    def test_freshness_sentence_is_the_contract_sentence(self):
        for page in (self.page, self.cyc):                                    # the landing and the cycle page carry the same sentence
            m = re.search(r'<span class="hu">(.*?)</span>', page)
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


class DayStrip(unittest.TestCase):
    """The sitting day as one strip on every vote page: a bar per vote, the margin above the axis when it passed and
    below when it did not, this vote marked. It is the cycle's own index read day by day — no new data."""

    @classmethod
    def setUpClass(cls):
        cls.inp = load_inputs()

    def test_the_strip_is_the_day_and_marks_the_vote(self):
        from scripts.build_site import build_vote_page, day_strip
        ts = HERO_TS
        day = self.inp["by_ts"][ts]["on_date"]
        same_day = [v for v in self.inp["idx"]["votes"] if v["on_date"] == day]
        html = day_strip(self.inp, ts)
        self.assertEqual(html.count('<a href='), len(same_day))                  # one bar per vote of the day
        self.assertEqual(html.count('class="b now'), 1)                           # exactly one is the page's own
        self.assertIn(hu_date(day), html)
        slugs = {v["slug"] for v in same_day}
        for href in re.findall(r'<a href="([^"]+)\.html"', html):
            self.assertIn(href, slugs)                                            # every bar goes to a vote of that day
        self.assertIn(day_strip(self.inp, ts), build_vote_page(self.inp, ts))     # …and the page carries it

    def test_a_single_vote_day_draws_nothing(self):
        from scripts.build_site import day_strip
        import collections
        per_day = collections.Counter(v["on_date"] for v in self.inp["idx"]["votes"])
        lonely = [d for d, n in per_day.items() if n == 1]
        if not lonely:
            self.skipTest("no single-vote day in this cycle")
        ts = next(v["ts"] for v in self.inp["idx"]["votes"] if v["on_date"] == lonely[0])
        self.assertEqual(day_strip(self.inp, ts), "")                             # a strip of one bar says nothing

    def test_a_long_day_is_windowed_and_says_so(self):
        from scripts.build_site import STRIP_MAX, day_strip
        import collections
        for c in available_cycles():
            inp = load_inputs(c)
            per_day = collections.Counter(v["on_date"] for v in inp["idx"]["votes"])
            long_days = [d for d, n in per_day.items() if n > STRIP_MAX]
            if not long_days:
                continue
            day = long_days[0]
            votes = sorted((v for v in inp["idx"]["votes"] if v["on_date"] == day), key=lambda v: v["ts"])
            html = day_strip(inp, votes[len(votes) // 2]["ts"])
            self.assertEqual(html.count('<a href='), STRIP_MAX)                   # windowed around the vote
            self.assertEqual(html.count('class="b now'), 1)
            self.assertIn("szavazásából", html)                                    # and the page says it is a window
            return
        self.skipTest("no day longer than the window in any cycle")

    def test_quorum_checks_get_a_tick_not_a_bar(self):
        from scripts.build_site import day_strip
        for v in self.inp["idx"]["votes"]:
            if v["kind"] != "jelenlet":
                continue
            html = day_strip(self.inp, v["ts"])
            if html:
                self.assertIn("b now q", html.replace('class="', ""))              # no margin, no threshold: a tick
                return
        self.skipTest("no quorum check in this cycle")


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
        n_k = [m["kind"] for m in a].count("K")
        self.assertTrue(all(m["href"] and m["szam"] and m["title"] for m in a))
        page = build_mp_page(self.inp, "a011")
        self.assertIn(f'a jelenlegi ciklusban</span> {len(a)} iromány · K/ {n_k} · H/ {len(a) - n_k}', page)   # the list grows between syncs; the record and the list agree above
        self.assertEqual(page.count("iromanyok_mobil"), len(a))
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
        # the cycle's own roster, not today's list — in the headline count, where the terminal no longer repeats it
        self.assertIn('data-kz-number="214">214</b><span data-kz-text>képviselő', page)
        self.assertNotIn("TISZA: 141", page)
        # links leave the cycle root only for the shared trees (assets, people, method, search, other cycles)
        for h in re.findall(r'href="\.\./([^"#]+)', page):
            self.assertRegex(h, r"^(assets/|szemely/|modszer/|kereses/|index\.html$|ckl\d+/)", h)
        self.assertIn('href="../ckl43/index.html" class="near">43</a>', page)  # the cycle switch (43 is the neighbour of 42)
        self.assertIn('<span class="kicker" data-kz-text>42. ciklus</span><h1>2022. május 2. — 2026. május 8.</h1>', page)   # the term is the title
        self.assertEqual(page.count('<div class="grp">'), 3)                    # the cycle's pages, grouped: szavazások · emberek · eszközök
        self.assertNotIn("ciklusok: ", page)                                      # the cycle list lives in the top bar, once
        self.assertEqual(page.count('data-year="'), 5 + 1)                     # 2022…2026 + 'minden év'
        self.assertEqual(page.count('data-y="2023"'), 719)
        self.assertIn("Egy szavazás, frakciónként", page)                     # not "az ülésteremben": no chamber for a closed cycle
        self.assertIn("2 599 szavazás", page.replace("\u00a0", " "))               # the lede counts the whole closed term
        self.assertIn("név szerint, frakciónként rendezve kirajzolva", page)  # the meta description
        self.assertIn("14 ülésszak, 2022. tavaszi … 2026. tavaszi", page)      # endpoints by date, not by string order
        self.assertIn('<a class="brand" href="../index.html">', page)         # the brand goes to the landing
        self.assertIn("<title>karzat — 42. ciklus, az Országgyűlés szavazásai</title>", page)

    def test_hero_vote_page_uses_the_fallback_layout_and_says_so(self):
        page = build_vote_page(self.inp, HERO_BY_CYCLE[42])
        self.assertIn("tizenötödik módosítása", page)
        self.assertIn("különbség +7", page)                                   # 140 igen, 133 needed
        self.assertEqual(page.count('<g class="seat"'), 199)
        self.assertIn("nem a valódi ülésrend", page)                                  # the drawing is the same, the placement is ours
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
        self.assertNotIn("../../ckl43/kepviselo/z012.html", page)             # no cycle-43 page for him
        page2 = build_mp_page(self.inp, "a011")                              # Ágh Péter sits in both cycles
        self.assertIn('href="../../ckl43/kepviselo/a011.html">43. ciklus ↗</a>', page2)
        page3 = build_mp_page(load_inputs(43), "a011")
        self.assertIn('href="../../ckl42/kepviselo/a011.html">42. ciklus ↗</a>', page3)   # every cycle is one level down now
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
        self.assertEqual(len(obj["motions"]), len(self.inp["mps"]["a011"]["motions"]))   # the list grows between syncs; MPPages pins it against the record
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
        self.assertEqual(ids, ["Fidesz", "KDNP", "Mi Hazánk", "független", "TISZA"])   # left→right as the chamber is drawn: opposition, then the government
        for f in facs:
            self.assertRegex(f["colour"], r"^#[0-9a-fA-F]{6}$")   # the '#' bug: never again

    def test_every_cycle_draws_the_same_chamber_language(self):
        # cycle 43 has the real floor plan; the others cannot (the API gives ülőhely for today only) — but the drawing
        # must read the same: seats as cells, the same glyphs, the platform, and a note that owns the difference
        from scripts.build_site import build_vote_page, seat_svg_fallback, vote_view
        for c in available_cycles():
            inp = load_inputs(c)
            if inp["archive"]:
                continue                                                        # archive cycles print no chamber at all
            ts = HERO_BY_CYCLE.get(c) or HERO_TS
            view = vote_view(inp, ts)
            svg = seat_svg_fallback(view, inp["facs"]) if not inp["plan"] else None
            page = build_vote_page(inp, ts)
            self.assertIn('class="seatshape', page)                             # the floor is drawn the same way
            self.assertIn("rostrum", page)                                      # …and so is the Speaker's platform
            self.assertIn("elnöki emelvény", page)
            self.assertIn('<g class="seat"', page)
            if svg is not None:
                n = len(view["positions"])
                self.assertEqual(svg.count('class="seatshape occ"'), n)         # one cell per member, none spare invented
                self.assertEqual(svg.count('<g class="seat"'), n)
                self.assertNotIn("rowline", svg)                                # the old guide arcs are gone
                self.assertIn("nem a valódi ülésrend", build_vote_page(inp, ts))

    def test_the_speakers_platform_names_who_presides(self):
        # the platform is not a seat in the plan: the Speaker and the deputies keep their floor seats and take the
        # chair in turn. The drawing marks the platform with the office-holders of the day, from the records' dated
        # <tisztseg> rows — never moving anyone, never inventing a seat
        from scripts.build_site import HOUSE_OFFICES, build_vote_page, podium_svg, presidium, vote_view
        inp = load_inputs()
        pres = presidium(inp["mps"])                                            # today
        self.assertEqual(len(pres["elnök"]), 1)                                 # one Speaker
        self.assertGreaterEqual(len(pres["alelnök"]), 3)
        for m in pres["elnök"] + pres["alelnök"]:
            self.assertIn(m["azon"], inp["mps"])                                # members, all of them
            self.assertIn(m["azon"], inp["plan"]["coords"])                     # …with a floor seat of their own
        svg = podium_svg(pres, inp["facs"])
        self.assertEqual(svg.count('<g class="seat pres"'), len(pres["elnök"]) + len(pres["alelnök"]))
        self.assertIn('data-role="elnök"', svg)
        self.assertEqual(podium_svg(None, inp["facs"]), podium_svg({"elnök": [], "alelnök": []}, inp["facs"]))   # no data → the bare platform
        # on a vote page the presidium is the one in force on the vote's day, and the card names the office
        page = build_vote_page(inp, HERO_TS)
        self.assertIn('class="seat pres"', page)
        self.assertIn("az Országgyűlés elnöke", page)
        # a closed cycle: the records reach back — cycle 42's hero day had a different Speaker
        inp42 = load_inputs(42)
        day = vote_view(inp42, HERO_BY_CYCLE[42])["on_date"]
        p42 = presidium(inp42["mps"], day)
        self.assertEqual([m["name"] for m in p42["elnök"]], ["Kövér László"])
        self.assertNotEqual({m["azon"] for m in p42["elnök"]}, {m["azon"] for m in pres["elnök"]})
        self.assertTrue(set(HOUSE_OFFICES) >= {"az Országgyűlés elnöke", "az Országgyűlés alelnöke", "jegyző"})

    def test_layout_places_exactly_n_seats_left_to_right(self):
        seats = hemicycle_layout(199)
        self.assertEqual(len(seats), 199)
        xs = [s["cx"] for s in seats]
        # first seats are on the left (negative x), last on the right
        self.assertLess(sum(xs[:20]) / 20, 0)
        self.assertGreater(sum(xs[-20:]) / 20, 0)
        self.assertTrue(all(s["cy"] <= 0.01 for s in seats))    # a semicircle above the axis


class CachesAreSeparate(unittest.TestCase):
    """Two caches shared one dictionary, and the first caller decided whether the second worked.

    `site_totals()` treats an empty `_TOTALS` as "not computed yet"; `facts()` used to store its list under a key
    in that same dict, so asking for a fact first left the totals permanently empty — every number on the landing
    page. It only ever worked because the terminal asked for the totals first, and it broke the day the terminal
    stopped. Whichever order they are called in now, both answer."""

    def test_asking_for_a_fact_first_does_not_empty_the_totals(self):
        import importlib
        import scripts.build_site as bs
        m = importlib.reload(bs)
        m.facts()
        tot = m.site_totals()
        for key in ("cycles", "votes", "roll_calls", "people", "from", "to"):
            self.assertIn(key, tot)
        self.assertGreater(len(m.build_landing()), 10_000)


class ProfileLine(unittest.TestCase):
    """The orientation paragraph: who this is, before the numbers.

    It is written from the record's fields and it covers everyone — mandates are recorded for all of them — where
    the record's own biography PDF exists for 184 and carries private contact details. Two properties matter and
    are checked here: it never inflects a value out of the record (a generated Hungarian suffix on
    "Vasvár Város Önkormányzata" is the one thing in it that could be wrong), and it says nothing it was not told."""

    @classmethod
    def setUpClass(cls):
        cls.mps = json.loads((ROOT / "data" / "derived" / "mps.json").read_text(encoding="utf-8"))["mps"]

    def test_the_two_pages_of_one_person_open_with_the_same_paragraph(self):
        """A sitting member has a cycle page and a career page, and both open with this paragraph, so it has to say
        the same thing on each. It did not: the career page's copy of the record was assembled without the
        committees, so the cycle page named a committee and the career page silently did not."""
        para = re.compile(r'<p class="profile">(.*?)</p>', re.S)
        checked = 0
        for azon, mp in self.mps.items():
            if not mp.get("current") or not [c for c in (mp.get("committees") or []) if not c.get("to")]:
                continue
            a = ROOT / "site" / cycle_dir(CURRENT_CYCLE) / "kepviselo" / f"{azon}.html"
            b = ROOT / "site" / "szemely" / f"{azon}.html"
            if not (a.exists() and b.exists()):
                self.skipTest("site not built")
            ma, mb = para.search(a.read_text(encoding="utf-8")), para.search(b.read_text(encoding="utf-8"))
            self.assertTrue(ma and mb, f"{azon}: a profile paragraph is missing from one of the two pages")
            self.assertEqual(ma.group(1), mb.group(1), f"{azon}: the two pages disagree")
            checked += 1
            if checked >= 25:
                break
        self.assertGreater(checked, 0, "no sitting member with a current committee to compare")

    def test_every_person_gets_one(self):
        from scripts.build_site import profile_html
        missing = [a for a, m in self.mps.items() if not profile_html(m, [])]
        self.assertEqual(missing, [])

    def test_values_appear_verbatim_never_inflected(self):
        from scripts.build_site import profile_html
        for azon, mp in self.mps.items():
            html = profile_html(mp, [])
            for c in (mp.get("committees") or []):
                if c.get("committee") and c["committee"] in html:
                    self.assertNotRegex(html, re.escape(c["committee"]) + r"[a-záéíóöőúüű]")
            for t in (mp.get("local_offices") or []):
                if t.get("body") and t["body"] in html:
                    self.assertNotRegex(html, re.escape(t["body"]) + r"[a-záéíóöőúüű]")

    def test_an_empty_field_shortens_the_line_rather_than_guessing(self):
        from scripts.build_site import profile_html
        bare = {"faction": "Fidesz", "elections": [{"ciklus": "2026-", "mandate_from": "2026-05-09"}], "current": True}
        html = profile_html(bare, [])
        self.assertIn("2026 óta képviselő.", html)
        for word in ("Tisztsége", "Bizottsága", "Végzettsége", "Önkormányzatban"):
            self.assertNotIn(word, html)

    def test_a_one_day_ceremonial_post_does_not_take_a_slot(self):
        """korjegyző opens a constituent sitting and is recorded from and to the same day; a line with room for two
        offices should not spend one of them on it."""
        from scripts.build_site import profile_html
        mp = {"faction": "Fidesz", "current": True,
              "elections": [{"ciklus": "2026-", "mandate_from": "2026-05-09"}],
              "offices": [{"office": "korjegyző", "from": "2006-05-16", "to": "2006-05-16"},
                          {"office": "Ipari Minisztérium államtitkára", "from": "2022-12-01", "to": "2026-05-12"}]}
        html = profile_html(mp, [])
        self.assertNotIn("korjegyző", html)
        self.assertIn("Ipari Minisztérium államtitkára", html)

    def test_a_local_post_held_with_a_gap_shows_the_latest_unbroken_run(self):
        from scripts.build_site import profile_html
        mp = {"faction": "Fidesz", "current": True,
              "elections": [{"ciklus": "2026-", "mandate_from": "2026-05-09"}],
              "local_offices": [{"body": "X Önkormányzata", "office": "polgármester", "from": "1990", "to": "1994"},
                                {"body": "X Önkormányzata", "office": "polgármester", "from": "1998", "to": "2002"},
                                {"body": "X Önkormányzata", "office": "polgármester", "from": "2002", "to": "2010"}]}
        self.assertIn("(1998–2010)", profile_html(mp, []))     # not 1990–2010, which would paper over the gap


class Portraits(unittest.TestCase):
    """The pictures are ours now: a grey WebP of the size they are drawn at, not a colour JPEG ten times larger
    filtered in the browser. What the markup says must follow the manifest and nothing else, or a build would
    depend on what happens to be sitting in the output directory."""

    def test_a_person_in_the_manifest_is_served_from_our_own_copy(self):
        from scripts.build_site import PORTRAITS, portrait_html, portrait_src
        if not PORTRAITS:
            self.skipTest("no portraits made — run: python3 -m scripts.make_portraits")
        azon = sorted(PORTRAITS)[0]
        self.assertEqual(portrait_src({"p_azon": azon, "photo_url": "https://example.invalid/x"}),
                         f"/assets/portre/{azon}.webp")
        html = portrait_html({"p_azon": azon, "photo_url": "https://example.invalid/x", "name": "X"}, "hero")
        self.assertIn(f'src="/assets/portre/{azon}.webp"', html)
        self.assertIn('loading="lazy"', html)
        self.assertIn('width="192" height="256"', html)          # the file's own size, so nothing is resampled

    def test_a_person_without_one_keeps_the_original_url(self):
        from scripts.build_site import portrait_src
        self.assertEqual(portrait_src({"p_azon": "nobody-here", "photo_url": "https://example.invalid/x"}),
                         "https://example.invalid/x")
        self.assertEqual(portrait_src({"p_azon": "nobody-here"}), "")

    def test_the_manifest_matches_the_files_it_claims(self):
        from scripts.build_site import PORTRAITS
        if not PORTRAITS:
            self.skipTest("no portraits made")
        out = ROOT / "site" / "assets" / "portre"
        missing = [a for a in PORTRAITS if not (out / f"{a}.webp").exists()]
        self.assertEqual(missing[:5], [], f"{len(missing)} portraits are in the manifest but not on disk")
        big = [a for a, n in PORTRAITS.items() if n > 40_000]
        self.assertEqual(big[:5], [], "a portrait over 40 kB means the render settings drifted")


class NoDuplicateVotes(unittest.TestCase):
    """A vote appears once per cycle, whatever the cache holds.

    The listing cache carries the same vote twice on purpose: once from the month it was called for, once from the
    day-level re-listing that recovered what the 400-row cap had cut. The derivation merges them on the timestamp —
    the API's own key for a vote. When it did not, the six archive cycles counted 22,254 votes twice and every
    headline built on them was inflated, so this is checked for every cycle rather than the one being worked on."""

    def test_each_cycle_lists_a_vote_once(self):
        import collections
        for c in available_cycles():
            votes = load_inputs(c)["idx"]["votes"]
            dupes = [ts for ts, n in collections.Counter(v["ts"] for v in votes).items() if n > 1]
            self.assertEqual(dupes[:3], [], f"cycle {c}: {len(dupes)} timestamps listed more than once")

    def test_the_index_and_the_roll_call_store_agree_on_what_exists(self):
        """Every vote the store names must be a vote the index lists — a store key with no index row would mean the
        two derivations disagree about which votes the cycle had."""
        for c in available_cycles():
            inp = load_inputs(c)
            listed = {v["ts"] for v in inp["idx"]["votes"]}
            named = {ts for ts, p in ((inp["store"] or {}).get("positions") or {}).items() if p}
            self.assertEqual(named - listed, set(), f"cycle {c}: roll calls for votes the index does not list")


class LifePath(unittest.TestCase):
    """The person on one time axis: mandates, offices, local-government seats, the years the degrees carry. It is
    drawn from the record's own dated rows, and a span the record leaves open must stop at the newest vote on the
    site rather than at whatever day the build happens to run."""

    @classmethod
    def setUpClass(cls):
        cls.inp = load_inputs()
        cls.inp["facs_all"] = {f["id"]: f["colour"] for c in available_cycles() for f in factions(c)}
        cls.mps = json.loads((ROOT / "data" / "derived" / "mps.json").read_text(encoding="utf-8"))["mps"]

    def stint(self, azon):
        mp = self.mps[azon]
        st = {"cycle": CURRENT_CYCLE, "name": mp["name"], "faction": mp.get("faction"), "in_roll": 0, "cast": 0,
              "with": 0, "against": 0, "mandate": "", "href": ""}
        st.update({k: mp.get(k) or [] for k in ("factions", "elections", "motion_stats", "offices", "schools",
                                                "languages", "declarations", "remuneration", "local_offices")})
        return st

    def test_a_lane_per_kind_the_record_dates(self):
        from scripts.build_site import life_path_html
        azon = max(self.mps, key=lambda a: len(self.mps[a].get("local_offices") or []))
        st = self.stint(azon)
        html = life_path_html(self.inp, st, [st])
        self.assertIn("Országgyűlés", html)                                  # the mandates are always a lane
        self.assertIn("Önkormányzat", html)                                  # this person has local seats
        n_local = len(st["local_offices"])
        n_mand = len([e for e in st["elections"] if e.get("mandate_from")])
        drawn = html.count('class="sp')
        self.assertLessEqual(drawn, n_local + n_mand + len(st["offices"]))    # consecutive terms of one post merge
        self.assertGreaterEqual(drawn, n_mand)                                 # …but a mandate is never merged away
        years = {s["year"] for s in st["schools"] if s.get("year")}
        self.assertEqual(html.count('class="sch"'), len(years))   # one dot per year, however many degrees it carries
        for o in st["offices"]:                                   # every span names itself, not only on hover
            if o.get("office"):
                self.assertIn(f'<b>{o["office"]}</b>', html)

    def test_nothing_dated_draws_nothing(self):
        from scripts.build_site import life_path_html
        bare = {"name": "X", "elections": [], "offices": [], "local_offices": [], "schools": [], "factions": []}
        self.assertEqual(life_path_html(self.inp, bare, []), "")

    def test_an_open_span_ends_at_the_newest_vote_not_today(self):
        from scripts.build_site import life_path_html, site_today
        day = site_today(self.inp)
        self.assertEqual(day, max(v["ts"] for v in self.inp["idx"]["votes"])[:10].replace(".", "-"))
        azon = next(a for a, m in self.mps.items()
                    if any(e.get("mandate_from") and not e.get("mandate_to") for e in m.get("elections") or []))
        st = self.stint(azon)
        self.assertIn(hu_date(day), life_path_html(self.inp, st, [st]))        # the page names the day it runs to

    def test_a_cycle_the_site_never_loaded_still_gets_its_colour(self):
        """The colour comes from the stint when the site has one, and from the record's own faction rows when it
        does not — otherwise every pre-1998 bar would be grey."""
        from scripts.build_site import life_path_html
        azon = max(self.mps, key=lambda a: len(self.mps[a].get("elections") or []))
        st = self.stint(azon)
        if len(st["elections"]) < 2:
            self.skipTest("nobody in this cycle has served in another")
        html = life_path_html(self.inp, st, [st])                              # one stint only: earlier cycles are unloaded
        colours = set(re.findall(r'--c:(#[0-9a-fA-F]{6})', html))
        self.assertTrue(colours - {"#8a8a8a", "#d4d4d8", "#8a8a93"})           # at least one real faction colour


class RecordPanels(unittest.TestCase):
    """The three sections of the MP record the site had never read: declarations, remuneration, schooling."""

    @classmethod
    def setUpClass(cls):
        cls.mps = json.loads((ROOT / "data" / "derived" / "mps.json").read_text(encoding="utf-8"))["mps"]

    def test_every_declaration_gets_a_square_and_the_filed_ones_a_link(self):
        from scripts.build_site import declarations_html
        azon = max(self.mps, key=lambda a: len(self.mps[a].get("declarations") or []))
        mp = self.mps[azon]
        html = declarations_html(mp)
        ds = mp["declarations"]
        self.assertEqual(html.count("<a href=") + html.count('<span class="due"'), len(ds))
        self.assertEqual(html.count("<a href="), sum(1 for d in ds if d.get("url")))
        for d in ds:
            if d.get("url"):
                self.assertIn(d["url"], html)

    def test_a_declaration_recorded_as_due_but_not_published_is_hollow(self):
        from scripts.build_site import declarations_html
        azon = next((a for a, m in self.mps.items()
                     if any(not d.get("url") for d in m.get("declarations") or [])), None)
        if not azon:
            self.skipTest("every declaration in this cycle carries a link")
        html = declarations_html(self.mps[azon])
        self.assertIn('class="due"', html)
        self.assertIn("nincs közzétett dokumentum", html)

    def test_remuneration_names_its_one_month_and_never_implies_a_series(self):
        from scripts.build_site import remuneration_html
        azon = next(a for a, m in self.mps.items() if m.get("remuneration"))
        mp = self.mps[azon]
        html = remuneration_html(mp)
        self.assertIn(mp["remuneration"][0]["period"], html)
        self.assertIn("Egyetlen hónap", html)
        parts = [mp["remuneration"][0].get(k) or 0 for k in ("gross", "constituency_allowance", "housing_allowance")]
        self.assertEqual("összesen" in html, sum(1 for v in parts if v) > 1)   # no total when there is one line

    def test_nothing_recorded_renders_nothing(self):
        from scripts.build_site import declarations_html, remuneration_html, schooling_html
        for fn in (declarations_html, remuneration_html, schooling_html):
            self.assertEqual(fn({"declarations": [], "remuneration": [], "schools": [], "languages": []}), "")


class Coverage(unittest.TestCase):
    """The page that draws the data's own edge. Its numbers must be the same ones every cycle page is built from."""

    @classmethod
    def setUpClass(cls):
        cls.inp = load_inputs()
        cls.inp["facs_all"] = {f["id"]: f["colour"] for c in available_cycles() for f in factions(c)}

    def test_the_months_account_for_every_vote_of_the_cycle(self):
        from scripts.build_site import coverage_rows
        rows = coverage_rows(self.inp)
        self.assertEqual(sum(r["votes"] for r in rows), len(self.inp["idx"]["votes"]))
        named = sum(r["named"] for r in rows)
        with_names = {ts for ts, p in ((self.inp["store"] or {}).get("positions") or {}).items() if p}
        self.assertEqual(named, len(with_names & {v["ts"] for v in self.inp["idx"]["votes"]}))
        self.assertTrue(all(r["named"] <= r["votes"] for r in rows))

    def test_before_1998_the_page_says_there_is_no_roll_call(self):
        from scripts.build_site import build_coverage_page, coverage_rows
        by_cycle = {}
        for c in available_cycles():
            by_cycle[c] = coverage_rows(load_inputs(c))
        html = build_coverage_page(self.inp, by_cycle)
        # 1990–1998 is tallies only: cycle 34 names nobody at all and cycle 35 carries a single roll call in 7,677
        # votes. If either count starts climbing, the payloads changed and the page's claim needs re-reading.
        for c, allowed in ((34, 0), (35, 1)):
            if c in by_cycle:
                self.assertLessEqual(sum(r["named"] for r in by_cycle[c]), allowed,
                                     f"cycle {c} should carry no more than {allowed} roll call")
        for c in by_cycle:
            if c >= 36:
                votes = sum(r["votes"] for r in by_cycle[c])
                self.assertGreater(sum(r["named"] for r in by_cycle[c]) / max(votes, 1), 0.9)
        self.assertIn("1998", html)
        self.assertIn("nem a lekérdezés hiányos", visible_text(html).replace(" ", " "))

    def test_a_month_two_cycles_share_is_added_up_not_overwritten(self):
        """Nothing forbids a cycle's last vote and the next cycle's first from falling in the same month — the
        House has simply never done it since 1990 (the old cycle stops voting weeks before the constituent
        sitting). So the merge is checked on rows built for the purpose rather than on a month that happens to
        exist: two cycles handing in the same month must add up, not overwrite."""
        from scripts.build_site import build_coverage_page, hu_num
        by_cycle = {41: [{"month": "2099-05", "votes": 7, "named": 3, "secret": 1}],
                    42: [{"month": "2099-05", "votes": 5, "named": 2, "secret": 0}]}
        html = build_coverage_page(self.inp, by_cycle)
        self.assertIn(hu_num(12), html)                     # 7 + 5, not 5
        self.assertIn("2099-05 · 12 szavazás · név szerint 5", html)

    def test_the_truncated_days_are_the_recorded_ones(self):
        from scripts.build_site import CAP_FILE, build_coverage_page, coverage_rows
        cap = json.loads(CAP_FILE.read_text(encoding="utf-8"))
        html = build_coverage_page(self.inp, {c: coverage_rows(load_inputs(c)) for c in available_cycles()})
        self.assertIn(str(len(cap["days_still_truncated"])), html)


class Profile(unittest.TestCase):
    """A Ház arcéle: one measure is a time series and the rest deliberately are not."""

    @classmethod
    def setUpClass(cls):
        from scripts.build_site import landing_inputs
        cls.inp = load_inputs()
        cls.inp["facs_all"] = {f["id"]: f["colour"] for c in available_cycles() for f in factions(c)}
        cls.rows = landing_inputs()["rows"]

    def test_the_first_parliament_is_all_first_term(self):
        from scripts.build_site import build_profile_page
        html = build_profile_page(self.inp, self.rows)
        if any(r["cycle"] == 34 for r in self.rows):
            self.assertRegex(html, r'34\. ciklus · [\d  ]+ első ciklusos a [\d  ]+ mandátumot viselőből')
            self.assertIn(">100%<", html)

    def test_it_prints_how_completely_each_cycle_is_recorded(self):
        from scripts.build_site import build_profile_page
        html = build_profile_page(self.inp, self.rows)
        self.assertIn("Mit rögzít a nyilvántartás", html)
        text = visible_text(html)
        self.assertIn("nem bontja az oldal ciklusra", text)      # and says why the other three are not a series


class BillRoad(unittest.TestCase):
    """A motion's road: submitted, the votes the record dates, promulgated — current cycle only, by the service's
    own limit."""

    @classmethod
    def setUpClass(cls):
        cls.inp = load_inputs()

    def test_every_dated_vote_points_at_its_own_page(self):
        from scripts.build_site import bill_path_html
        recs = self.inp.get("bill_recs") or {}
        if not recs:
            self.skipTest("no motion records cached — run: python3 -m karzat sync-bills")
        rec = max(recs.values(), key=lambda r: len(r["votes"]))
        html = bill_path_html(self.inp, rec)
        hrefs = re.findall(r'href="\.\./szavazas/([^"]+)\.html"', html)
        self.assertEqual(len(hrefs), len([v for v in rec["votes"] if v.get("ts")]))
        for v in rec["votes"]:
            if v.get("ts"):
                self.assertIn(f'{v["ts"][:10].replace(".", "-")}T{v["ts"][11:].replace(":", "-")}', hrefs)

    def test_a_promulgated_law_shows_its_reference(self):
        from scripts.build_site import bill_path_html
        recs = self.inp.get("bill_recs") or {}
        rec = next((r for r in recs.values() if (r.get("promulgation") or {}).get("law_ref")), None)
        if not rec:
            self.skipTest("nothing promulgated in this cycle yet")
        html = bill_path_html(self.inp, rec)
        self.assertIn(rec["promulgation"]["law_ref"], html)
        self.assertIn("kihirdetve", visible_text(html))

    def test_the_whole_event_log_is_printed_not_a_selection(self):
        from scripts.build_site import bill_path_html
        recs = self.inp.get("bill_recs") or {}
        if not recs:
            self.skipTest("no motion records cached")
        rec = max(recs.values(), key=lambda r: len(r["events"]))
        html = bill_path_html(self.inp, rec)
        self.assertEqual(html.count('<td class="ts mono">'), len(rec["events"]))   # every row of the log, none dropped
        from scripts.build_site import hu_num
        self.assertIn(f'({hu_num(len(rec["events"]))} sor)', html)


if __name__ == "__main__":
    unittest.main()
