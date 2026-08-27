"""The committed page must be exactly what the builder produces from the committed inputs.

Same rule as trigger-discipline's index.html: never hand-edit site/index.html; change the
inputs or the builder and rebuild. `python3 -m scripts.build_site --check` is the CLI form.
"""

import json
import math
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
        cls.inp = load_inputs()
        cls.n_votes = len(cls.inp["idx"]["votes"])          # the cycle grows every sitting day; the directory must keep up
        cls.page = build()                                                  # the landing (site/index.html)
        cls.cyc = build_index(cls.inp, HERO_TS)                       # the current cycle's page (site/ckl43/index.html)

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
        self.assertIn("a karzatról nézve.", self.page)                            # the lede's closing beat
        inp0 = load_inputs()
        n_sz = len((inp0["szoszolok"] or {}).get("people") or {})
        n_km = len((inp0["kormany"] or {}).get("people") or {})
        n_mp = sum(1 for m in inp0["mps"].values() if m.get("current"))   # the roster, not the full house: it moves
        self.assertEqual(self.page.count('class="today"'), n_mp + n_sz + n_km)  # every MP by faction, every spokesperson as a ring, every non-MP minister as a disc
        self.assertIn("Az ülésterem ma", self.page)
        self.assertIn(f'aria-label="Az ülésterem ma: {n_mp} képviselő, {n_sz} nemzetiségi szószóló és {n_km} mandátum nélküli kormánytag a helyén', self.page)
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
        self.assertEqual(self.page.count('<a class="seat" href='), len(inp["plan"]["coords"]))
        self.assertEqual(self.page.count('<a class="seat sz" href='), n_sz)
        self.assertEqual(self.page.count('<a class="seat km" href='), n_km)
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
        self.assertIn('data-page-size="25" data-counter="n"', self.cyc)   # the directory: every row in the HTML, 25 shown at a time
        self.assertEqual(self.cyc.count("<tr data-rule="), self.n_votes)
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
        # The property is that the bundle's first statement is a wrapper, so nothing leaks to the global scope
        # and nothing runs before it. Matching the raw first characters also forbade a comment above it, which
        # is how this failed: the explanation of why that block has to come first is worth more than the
        # convenience of a one-line assertion, so the leading comments are skipped and the statement checked.
        first = "\n".join(l for l in js.split("\n") if l.strip() and not l.lstrip().startswith("//"))
        self.assertTrue(first.startswith("(function(){"), f"the bundle does not open with a wrapper: {first[:60]!r}")
        stamp = sync_stamp(load_inputs())                                       # the last sync, whatever it was
        for page in (self.page, self.cyc):
            log = re.search(r'<div class="log">(.*?)</div>', page, re.S).group(1)
            self.assertEqual(log.count('class="factline"'), 1)                  # the fact…
            self.assertNotIn("<span>&gt;", log)                                 # …and none of the old boot lines
            self.assertNotIn("ezen az oldalon", page)
            self.assertNotIn("SYSTEM_READY", page)
            self.assertNotIn(".cgi", page.split("<footer")[1])          # the footer talks about the data, not the plumbing
            # absolute, never a frozen "15 perce". A build from a cache that carries no sync record — which is what
            # the scheduled run does on its first pass — has no stamp to print, and must say so rather than invent one.
            if stamp == "—":
                self.assertIn("A szinkronizálás még sosem futott le.", page)
                self.assertNotIn("Frissítve:", page)          # no stamp is a sentence, not a made-up timestamp
            else:
                self.assertIn(f"Frissítve: {hu_date(stamp[:10])} {stamp[11:]} (budapesti idő).", page)
            for word in ("live", "élő", "real-time"):
                self.assertNotIn(word, visible_text(page))
            # the footer is a landmark of its own, outside <main>
            self.assertLess(page.index("</main>"), page.index("<footer"))

    def test_hero_and_directory_are_present(self):
        self.assertEqual(self.cyc.count('<g class="seat"'), 199)
        self.assertIn("különbség +2", self.cyc)              # T/51: 135 igen, 133 needed
        self.assertIn("133 / 199", self.cyc)
        self.assertEqual(self.cyc.count("<tr data-rule="), self.n_votes)   # rendered at build time: complete without JS
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
        self.assertEqual(len(slugs), len(self.inp["idx"]["votes"]))   # every listed vote has a page
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
        al = self.inp["alignment"]["per_mp"]["a011"]
        self.assertEqual((a011[5], a011[6]), (al["cast"], al["in_roll"]))    # the cycle record, as on the MP page
        self.assertEqual(len(a011[9]), 20)                                    # the last 20 roll calls up to this vote
        self.assertEqual(a011[9][-1], "a")                                    # …ending in this vote: against
        self.assertEqual(page.count('class="seat"'), 198)
        # one tab stop for the chamber; the script walks the seats — the presidium's six dots included,
        # since the QA round gave them the same idiom instead of the button role they falsely announced
        self.assertEqual(ts_page_count := page.count('tabindex="-1"'), 198 + page.count('class="seat pres"'), ts_page_count)
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
        self.assertIn("csak azt rögzítette, ki van jelen a teremben", page)
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
        pos = (self.inp["store"] or {}).get("positions") or {}
        rolls = sum(1 for v in self.inp["idx"]["votes"] if pos.get(v["ts"]))
        if not self.inp["mps"]["a011"].get("mandate_to"):     # seated the whole cycle → in every roll call
            self.assertEqual(a["in_roll"], rolls)             # every non-secret roll call of the cycle
        self.assertEqual(a["cast"], a["counts"].get("igen", 0) + a["counts"].get("nem", 0) + a["counts"].get("tartozkodott", 0))
        self.assertLessEqual(a["with"] + a["against"], a["cast"])
        # a former MP only appears while seated
        self.assertLess(al["b076"]["in_roll"], rolls)
        # per-vote pluralities exist for every roll call and every faction that cast a vote
        plur = self.inp["alignment"]["plurality_by_vote"]
        self.assertEqual(len(plur), rolls)
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
        _al = self.inp["alignment"]["per_mp"]["a011"]       # one link per roll call, and again for each dissent
        self.assertEqual(page.count('href="../szavazas/'), _al["in_roll"] + _al["against"])
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
        # Four groups, and each entry under the question it answers. This used to be three, and the odd one
        # out was not the count but the filing: pályaképek (people's careers) sat under "eszközök", visszhang
        # (repeated passages in speeches) under "szavazások", and beszédidő under "emberek" although it is
        # about what was debated. A parliament debates and then votes, so "viták" is a group, not a leftover.
        self.assertEqual(page.count('<div class="grp">'), 4)
        nav = re.search(r'<nav class="cyc-nav".*?</nav>', page, re.S).group(0)   # .lbl is used elsewhere too
        groups = dict(re.findall(r'<span class="lbl">([^<]+)</span>(.*?)</div>', nav, re.S))
        self.assertEqual(list(groups), ["szavazások", "viták", "emberek", "eszközök"])
        for label, entry in (("szavazások", "kohézió"), ("szavazások", "szoros szavazások"),
                             ("viták", "irományok"), ("viták", "beszédidő"), ("viták", "felszólalások"),
                             ("viták", "visszhang"), ("emberek", "képviselők"), ("emberek", "pályaképek"),
                             ("emberek", "bizottságok"), ("eszközök", "adatok"), ("eszközök", "módszer")):
            self.assertIn(f">{entry}</a>", groups[label], f"{entry} is not under {label}")
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
        _al = self.inp["alignment"]["per_mp"]["a011"]
        self.assertEqual(obj["summary"], {k: _al[k] for k in ("in_roll", "cast", "with", "against")})   # the quorum check is participation, not "with"
        self.assertEqual(len(obj["votes"]), _al["in_roll"])
        self.assertEqual(len(obj["motions"]), len(self.inp["mps"]["a011"]["motions"]))   # the list grows between syncs; MPPages pins it against the record
        lines = c.lstrip("\ufeff").splitlines()
        self.assertEqual(len(lines), _al["in_roll"] + 1)          # the header, then one row per roll call
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
        # Every chart is named — checked by looking at every chart, not by counting them. The assertion was
        # `count(...) == 5`, so adding the per-sitting-day view failed a test about accessible names with a
        # page on which every name was present. A test that fixes the number forbids the sixth chart.
        svgs = re.findall(r"<svg\b[^>]*>", page)
        self.assertGreaterEqual(len(svgs), 6, "the page lost a chart")
        self.assertEqual([s for s in svgs if not re.search(r'aria-label="[^"]+"', s)], [],
                         "a chart with no accessible name")
        self.assertIn('class="tswrap"', page)
        # Both readings of the vote count are in the markup: the switch is an upgrade, not a requirement.
        self.assertEqual(page.count('class="view" data-label='), 2)
        self.assertIn('data-label="ülésnaponként"', page)
        # Every chart panel explains its own arithmetic, closed by default and usable without a script:
        # a native <details> per panel — the votes panel shares one across its two views.
        self.assertEqual(page.count('<details class="how">'), 5)
        self.assertEqual(page.count("Miből számoltuk"), 5)
        # The cycle's peaks are real links into the same ?m= drill-down the strip uses, and every month the
        # line names is a month the strip actually has — the contract, not the wording.
        self.assertIn('class="mopeaks', page)
        for m in re.findall(r'mopeaks[^>]*>.*?</div>', page, re.S)[0].split('href="../index.html?m=')[1:]:
            self.assertIn(f'data-m="{m[:7]}"', page, f"the peaks line names {m[:7]}, which is not in the strip")
        # "a számok a táblázatban" is a promise in six accessible names, so the table must carry what the
        # charts draw: the day column and the agreement index were the two it lacked.
        self.assertIn('<th scope="col" class="num">Ülésnap<span class="sub">szavazás/nap</span></th>', page)
        # the header's three metrics each carry their definition — the same sentence the how-block opens
        # with, from METRIC_DEFS, so header and explanation cannot drift apart
        from scripts.build_site import METRIC_DEFS
        for t in ("részvétel", "ellene", "index"):
            self.assertIn(f'data-def="{METRIC_DEFS[t]}">{t}</span>', page)

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


class SourceIsFutureProof(unittest.TestCase):
    """The builder holds JavaScript and CSS in Python strings, and a JS regex is full of sequences Python does not
    recognise as escapes: /index\\.html$/, /\\d{4}/, /\\s+/. Python has warned about these for years and will
    make them an error; on the day it does, an unattended nightly build stops with a syntax error and nobody is
    watching. Worse is the pair that already means something: \\b is a word boundary in JavaScript and a backspace
    in Python, so it would corrupt a regex rather than fail loudly. There are none today and this keeps it so."""

    def test_the_builder_compiles_with_syntax_warnings_as_errors(self):
        import py_compile
        import tempfile
        import warnings
        for mod in ("scripts/build_site.py", "scripts/nightly.py", "karzat/normalise.py"):
            with warnings.catch_warnings():
                warnings.simplefilter("error", SyntaxWarning)
                with tempfile.NamedTemporaryFile(suffix=".pyc") as tmp:
                    try:
                        py_compile.compile(str(ROOT / mod), doraise=True, cfile=tmp.name)
                    except (SyntaxWarning, py_compile.PyCompileError) as e:
                        self.fail(f"{mod}: {e}")


class SpeakingTime(unittest.TestCase):
    """Seconds at the microphone — the first aggregate the site draws from the speech lists, and the one with a
    trap in it.

    A speech held in a joint debate is listed again under every motion the debate covered, with the same recorded
    length. Nothing had summed these seconds before, so nothing had met the trap: adding the rows as they come
    overstates a cycle by 1.29× to 6.68× and shifts the factions' shares of the floor against each other. These
    tests exist so the rule cannot be quietly dropped by whoever writes the next page that wants a total."""

    @classmethod
    def setUpClass(cls):
        cls.inp = load_inputs()

    def test_a_reprinted_speech_is_counted_once(self):
        from scripts.build_site import speaking_time
        rows = (self.inp["speeches"] or {}).get("speeches") or []
        reprints = [r for r in rows if r.get("reprint")]
        self.assertTrue(reprints, "no reprinted rows in this cycle — the rule would be untested")
        sp = speaking_time(self.inp)
        naive = sum(int(r.get("duration_s") or 0) for r in rows)
        self.assertEqual(sp["total"], sum(int(r.get("duration_s") or 0) for r in rows if not r.get("reprint")))
        self.assertGreater(naive, sp["total"], "the copies carry time, so ignoring them would overstate")
        self.assertEqual(sp["reprints_skipped"], len(reprints))

    def test_the_parts_add_up_to_the_whole(self):
        from scripts.build_site import speaking_time
        sp = speaking_time(self.inp)
        by_mp = sum(e["seconds"] for e in sp["per_mp"].values())
        by_fac = sum(e["seconds"] for e in sp["per_faction"].values())
        self.assertLessEqual(by_mp, sp["total"])            # some speakers are not members
        self.assertLessEqual(by_fac, sp["total"])           # …and some members of the House have no faction
        self.assertGreaterEqual(sp["total"], sp["substantive"])

    def test_the_speakers_table_inherits_the_reprint_rule_and_ranks_by_the_stated_number(self):
        """Szónokok says its order is the measured substantive time; the first row must actually hold the
        maximum, the kind breakdown must add up to the speaker's own total, and the whole thing must sit on
        speaking_time() — a second implementation of "count each speech once" is how the 6.68× overstatement
        would come back wearing a new panel."""
        from scripts.build_site import speakers_panel, speaking_time
        import re as _re
        sp = speaking_time(self.inp)
        html = speakers_panel(self.inp)
        top_azon = max((a for a, e in sp["per_mp"].items() if e.get("substantive")),
                       key=lambda a: sp["per_mp"][a]["substantive"])
        first = _re.search(r"<tbody><tr[^>]*>(.*?)</tr>", html, _re.S).group(1)   # the row carries data-f for the filter
        top = sp["per_mp"][top_azon]
        self.assertIn(top["name"], first, "the first row is not the speaker with the most measured time")
        for azon, e in sp["per_mp"].items():
            if e.get("kinds"):
                self.assertEqual(sum(e["kinds"].values()), e["substantive"],
                                 f"{azon}: the kind breakdown does not add up to the speaker's total")
        self.assertIn('href="szonokok.csv"', html, "the table advertises its CSV twin")
        self.assertIn("a vezérszónoki idő a frakcióé", html, "the role caveat is the panel's licence to rank")

    def test_the_floor_page_order_and_filters_are_what_the_reader_asked_for(self):
        """Szónokok before Milyen jogcímen before A vita szakaszai; both filterable; every per-faction
        variant hidden in the markup so a scriptless reader meets one table and no dead control."""
        from scripts.build_site import load_inputs, build_floor_page
        import karzat.analytics as an
        inp = load_inputs()
        html = build_floor_page(inp, an.floor_time(inp))
        i1, i2, i3 = html.index(">Szónokok<"), html.index(">Milyen jogcímen<"), html.index(">A vita szakaszai<")
        self.assertLess(i1, i2, "Szónokok is not before Milyen jogcímen")
        self.assertLess(i2, i3, "Milyen jogcímen is not before A vita szakaszai")
        self.assertIn('data-filter-table="szonokok"', html)
        self.assertIn('data-rowf="all" data-table="szonokok"', html)
        import re as _re
        variants = _re.findall(r'data-ffv="([^"]*)"( hidden)?', html)
        self.assertGreaterEqual(len(variants), 2, "no per-faction variant was built")
        for name, hid in variants:
            self.assertEqual(bool(hid), name != "", f"variant {name or 'mind'}: wrong initial visibility")

    def test_the_kind_by_faction_split_never_exceeds_the_unfiltered_table(self):
        """The filter's licence: a per-faction figure is a subset of the figure beside it, or it is wrong."""
        from scripts.build_site import load_inputs
        import karzat.analytics as an
        ft = an.floor_time(load_inputs())
        fbf = ft["forms_by_faction"]
        self.assertTrue(fbf)
        for kind, secs in ft["forms"].items():
            self.assertLessEqual(sum(kv.get(kind, 0) for kv in fbf.values()), secs, kind)
        # and the totals stay honest against the raw rows under the same rules the page states
        rows = (load_inputs()["speeches"] or {}).get("speeches") or []
        dedup = sum(int(r["duration_s"]) for r in rows
                    if not r.get("technical") and r.get("duration_s") and r.get("date") and not r.get("reprint"))
        self.assertEqual(ft["seconds"], dedup, "floor_time no longer equals the deduplicated substantive sum")

    def test_every_label_a_definition_family_covers_is_actually_covered(self):
        """The glossary families track the vocabularies they explain, mechanically: a rule majority.py can
        classify, a position normalise.py can print, or a voting mode the committed indexes carry, with no
        definition behind it, is a hover that shows nothing — the hole ships silently unless this fails."""
        from karzat.normalise import POSITION_LABEL
        from karzat.majority import RULES
        from scripts.build_site import POSITION_DEFS, RULE_DEFS, MODE_DEFS
        self.assertEqual([l for l in POSITION_LABEL.values() if l not in POSITION_DEFS], [])
        self.assertEqual([r.label_hu for r in RULES.values() if r.label_hu not in RULE_DEFS], [])
        import gzip as _gz, json as _json
        from pathlib import Path as _P
        root = _P(__file__).resolve().parent.parent / "data" / "derived"
        modes = set()
        for f in (root / "votes_index.json", root / "votes_index_ckl42.json.gz"):
            if not f.exists():
                continue
            raw = _gz.decompress(f.read_bytes()) if f.suffix == ".gz" else f.read_bytes()
            modes |= {v.get("mode") for v in _json.loads(raw)["votes"] if v.get("mode")}
        self.assertTrue(modes)
        self.assertEqual(sorted(m for m in modes if m not in MODE_DEFS), [],
                         "voting modes with no definition — add them to MODE_DEFS")

    def test_the_remuneration_page_counts_everyone_and_marks_part_months(self):
        """One row per member whose datasheet states an amount; the lede's median is the rows' own; a mandate
        that ended inside the stated month is marked on its row — the outlier explains itself or not at all."""
        from scripts.build_site import build_remuneration_page, HU_MONTH_NUM
        html = build_remuneration_page(self.inp)
        import re as _re
        n = sum(1 for m in self.inp["mps"].values() if any(x.get("gross") for x in m.get("remuneration") or []))
        self.assertEqual(len(_re.findall(r"<tr data-f=", html)), n)
        gs = sorted((x["gross"] for m in self.inp["mps"].values() for x in m.get("remuneration") or [] if x.get("gross")), reverse=True)
        med = gs[len(gs) // 2] if len(gs) % 2 else (gs[len(gs) // 2 - 1] + gs[len(gs) // 2]) // 2
        from scripts.build_site import hu_num
        self.assertIn(hu_num(med), html, "the lede's median is not the rows' median")
        period = next(x.get("period") for m in self.inp["mps"].values() for x in m.get("remuneration") or [] if x.get("gross"))
        y, mo = period.replace(".", "").split()[0], HU_MONTH_NUM[period.replace(".", "").split()[1]]
        enders = [m for m in self.inp["mps"].values()
                  if (m.get("mandate_to") or "").startswith(f"{y}-{mo}") and any(x.get("gross") for x in m.get("remuneration") or [])]
        if enders:
            self.assertIn("tört hónap — a mandátum vége:", html)

    def test_the_404_stands_at_any_depth(self):
        """CloudFront serves the error document at whatever path missed, so its assets must be root-absolute:
        a reader two directories deep met an unstyled page, which is the one page that should never look
        broken — it is where the lost arrive."""
        from scripts.build_site import build_404
        import re as _re
        h = build_404()
        rel = [u for u in _re.findall(r'(?:href|src)="([^"]*assets/[^"]*)"', h) if not u.startswith("/")]
        self.assertEqual(rel, [], "the 404 references assets relatively; it breaks below the root")
        # …and not only the assets: EVERY link on the page, the footer's fact line included — the QA round
        # found two relative footer hrefs that broke everywhere but a root-level miss
        loose = [u for u in _re.findall(r'(?:href|src)="([^"]+)"', h)
                 if not u.startswith(("/", "http://", "https://", "#", "mailto:"))]
        self.assertEqual(loose, [], "a relative link on the 404 resolves against whatever path missed")

    def test_the_landing_offers_no_dead_controls_and_the_pay_door(self):
        """Without a script the landing must offer nothing it cannot honour: the chamber legend ships as inert
        spans (information, not controls) and the presidium seats carry no button role. And the pay table's
        door stands where the 43rd cycle tile used to — the reader asked for the swap."""
        from scripts.build_site import build_landing
        h = build_landing()
        legend = h.split('<div class="legend">', 1)[1].split("</div>", 1)[0]
        self.assertNotIn("<button", legend)
        self.assertIn('<span class="f" data-hf=', legend)
        self.assertNotIn('class="seat pres" role="button"', h)
        self.assertIn("javadalmazas/index.html", h)
        self.assertNotIn(">A 43. ciklus</span>", h)
        # the door order tells the story: people first, the floor after, the tools last
        i_j, i_a, i_m = h.find("Javadalmazás</span>"), h.find("A Ház arcéle</span>"), h.find("Módszer és adatok</span>")
        self.assertTrue(0 < i_j < i_a < i_m, (i_j, i_a, i_m))

    def test_the_widget_and_the_house_page_share_one_anchor(self):
        """The MP widget's band sentence and the House page's x-column come from remuneration_anchor(),
        cached on inp — this asserts they actually use it: the widget names a band only on a forint-exact
        multiple of the same anchor the page found, and the base member's widget says base, cited."""
        from scripts.build_site import remuneration_html, remuneration_anchor, OGYTV_SCHEDULE
        base, covered, mults = remuneration_anchor(self.inp)
        self.assertTrue(base, "no anchor found — the whole layer would be silent")
        base_azon = next(a for a, m in self.inp["mps"].items()
                         if any(x.get("gross") == base for x in m.get("remuneration") or []))
        h = remuneration_html(self.inp, self.inp["mps"][base_azon])
        self.assertIn("Ez maga az alapösszeg (Ogytv. 104. § (1))", h)
        import re as _re
        for azon, m in list(self.inp["mps"].items())[:400]:
            g = next((x["gross"] for x in m.get("remuneration") or [] if x.get("gross")), None)
            if not g:
                continue
            h = remuneration_html(self.inp, m)
            named = _re.search(r"Az összeg az alap (\d,\d\d)-szerese", h)
            exact = next((mm for _r, mm, _c in OGYTV_SCHEDULE
                          if abs(g - base * float(mm.replace(",", "."))) <= 1), None)
            if named:
                self.assertEqual(named.group(1), exact, f"{azon}: a widget más sávot mond, mint az aritmetika")

    def test_the_ratio_column_prints_only_exact_statutory_multiples(self):
        """The x-column is arithmetic or absent: every printed multiplier is in the statute's own table, and
        the anchor the page found explains most of the House to the forint — if either stops being true, the
        column has started guessing and must fail here rather than ship."""
        from scripts.build_site import build_remuneration_page, OGYTV_SCHEDULE
        import re as _re
        html = build_remuneration_page(self.inp)
        body = _re.search(r'<tbody>(.*?)</tbody>', html[html.find('id="jav"'):], _re.S).group(1)
        printed = _re.findall(r">(\d,\d\d)×</td>", body)
        law = {m for _r, m, _c in OGYTV_SCHEDULE}
        self.assertEqual(sorted(set(printed) - law), [], "a multiplier outside the statute's table")
        n_rows = len(_re.findall(r"<tr data-f=", body))
        self.assertGreaterEqual(len(printed) / n_rows, 0.8,
                                "the anchor no longer explains the House — the column must not guess")

    def test_the_speakers_csv_is_the_table_in_long_form(self):
        from scripts.build_site import speaking_time
        from karzat.export import floor_speakers_csv
        sp = speaking_time(self.inp)
        body = floor_speakers_csv(sp, 43)
        lines = body.strip().split("\n")
        want = sum(len(e.get("kinds") or {}) for e in sp["per_mp"].values())
        self.assertEqual(len(lines) - 1, want, "one row per speaker per kind")

    def test_the_panel_accounts_for_every_second(self):
        """The faction column would otherwise stop short of 100% with no explanation; the remainder is a row."""
        from scripts.build_site import speaking_panel, speaking_time
        html = speaking_panel(self.inp)
        sp = speaking_time(self.inp)
        if sum(e["seconds"] for e in sp["per_faction"].values()) < sp["total"]:
            self.assertIn("frakció nélkül", html)
        shares = [int(x) for x in re.findall(r'<td class="num mono">(\d+)%</td>', html)]
        self.assertTrue(shares)

    def test_every_cycle_with_speech_lists_has_reprints_to_skip(self):
        """If a later capture stopped marking the copies, the totals would silently double. Fail loudly instead."""
        for c in available_cycles():
            inp = load_inputs(c)
            rows = (inp["speeches"] or {}).get("speeches") or []
            if len(rows) < 1000:
                continue
            self.assertTrue(any(r.get("reprint") for r in rows), f"cycle {c}: no row marked reprint")


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

    def test_a_person_without_one_gets_no_picture_rather_than_somebody_elses_server(self):
        """The old rule was that a person the manifest does not carry keeps the record's own URL, so that
        anyone added since the last render still had a face. It put `<img src="https://www.parlament.hu/…">`
        on 59 pages for 22 people — and 21 of those URLs answer 404, so the browser called a third party in
        order to be told nothing while `onerror` removed the empty image. Meanwhile the site's standing claim
        is that a visit is known to nobody but its own log. Our copy, or nothing."""
        from scripts.build_site import PHOTO_BASE, portrait_src
        self.assertEqual(portrait_src({"p_azon": "nobody-here", "photo_url": PHOTO_BASE + "nobody-here"}), "")
        self.assertEqual(portrait_src({"p_azon": "nobody-here", "photo_url": "https://example.invalid/x"}), "")
        self.assertEqual(portrait_src({"p_azon": "nobody-here"}), "")
        # the exception, and the only one: a Commons file, licensed and credited beside the picture
        commons = "https://upload.wikimedia.org/wikipedia/commons/1/2/x.jpg"
        self.assertEqual(portrait_src({"p_azon": "nobody-here", "photo_url": commons}), commons)

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
        cls.inp = load_inputs()

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
        html = remuneration_html(self.inp, mp)
        self.assertIn(mp["remuneration"][0]["period"], html)
        self.assertIn("Egyetlen hónap", html)
        parts = [mp["remuneration"][0].get(k) or 0 for k in ("gross", "constituency_allowance", "housing_allowance")]
        self.assertEqual("összesen" in html, sum(1 for v in parts if v) > 1)   # no total when there is one line

    def test_nothing_recorded_renders_nothing(self):
        """Declarations and pay vanish on an empty record; schooling instead SAYS the record is empty — a reader
        who wonders why there is no degree deserves the source's own answer rather than a silent gap."""
        from scripts.build_site import declarations_html, remuneration_html, schooling_html
        empty = {"declarations": [], "remuneration": [], "schools": [], "languages": []}
        self.assertEqual(declarations_html(empty), "")
        self.assertEqual(remuneration_html(self.inp, empty), "")
        absence = schooling_html(empty)
        self.assertIn("nem közöl iskolai", absence)
        self.assertNotIn("<table", absence)


class MonthlyCsv(unittest.TestCase):
    """The download the month table advertises must cover every month the table shows — the QA round found
    ckl34's file empty and ckl35's covering 1 month of 39, because rows existed only per (month, faction)."""

    def test_every_month_gets_a_row_even_without_factions(self):
        from karzat.export import monthly_csv
        months = [{"month": "1990-05", "votes": 3, "decisions": 2, "qualified": 1, "roll_calls": 0,
                   "sittings": 2, "vote_days": 1, "factions": {}},
                  {"month": "1990-06", "votes": 5, "decisions": 4, "qualified": 0, "roll_calls": 5,
                   "sittings": 3, "vote_days": 2,
                   "factions": {"MDF": {"in_roll": 10, "cast": 9, "with": 8, "against": 1,
                                        "participation": 0.9, "dissent": 0.1, "ai": 0.8}}}]
        out = monthly_csv(months, 34)
        lines = out.strip().split("\n")
        self.assertEqual(len(lines), 3)                       # header + one bare month row + one faction row
        self.assertIn("1990-05", out)
        bare = next(l for l in lines if "1990-05" in l)
        self.assertIn(",3,2,1,0,", bare)                      # the month's own numbers survive without a faction


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
        self.assertIn("nem a letöltés hibája", visible_text(html).replace(" ", " "))

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

    def test_every_dated_vote_points_at_its_own_page_where_there_is_an_axis(self):
        """Where the chart is drawn, every vote on it is a link to that vote's page.

        Restated rather than deleted. It used to take the record with the most votes, which is S/5 — submitted,
        voted fourteen times and promulgated on one day, 2026-05-09. A road across a single day is a chart with
        every marker at the same x, so the chart is now suppressed there and the fourteen votes reach the reader
        through the roll-call table above instead. The claim worth guarding is the one about the chart."""
        from scripts.build_site import bill_path_html
        recs = self.inp.get("bill_recs") or {}
        if not recs:
            self.skipTest("no motion records cached — run: python3 -m karzat sync-bills")
        def spread(r):
            days = {e["date"] for e in r.get("events") or [] if e.get("date")}
            days |= {v["ts"][:10].replace(".", "-") for v in r.get("votes") or [] if v.get("ts")}   # the record dates votes with dots
            return len(days)
        voted = [r for r in recs.values() if len([v for v in r["votes"] if v.get("ts")]) >= 2 and spread(r) >= 2]
        self.assertTrue(voted, "no motion is both voted and spread over two days — the axis case is untested")
        rec = max(voted, key=lambda r: len(r["votes"]))
        html = bill_path_html(self.inp, rec)
        self.assertIn("<svg", html)
        hrefs = re.findall(r'href="\.\./szavazas/([^"]+)\.html"', html)
        self.assertEqual(len(hrefs), len([v for v in rec["votes"] if v.get("ts")]))
        for v in rec["votes"]:
            if v.get("ts"):
                self.assertIn(f'{v["ts"][:10].replace(".", "-")}T{v["ts"][11:].replace(":", "-")}', hrefs)

    def test_a_one_day_motion_keeps_its_event_log_when_the_chart_is_dropped(self):
        """The guard wraps the chart, not the panel. It used to return the empty string for the whole thing, which
        on 100 records took 675 events and five committee tables with it."""
        from scripts.build_site import bill_path_html
        recs = self.inp.get("bill_recs") or {}
        if not recs:
            self.skipTest("no motion records cached")
        one_day = [r for r in recs.values()
                   if r.get("events") and len({e["date"] for e in r["events"] if e.get("date")}
                                              | ({r["submitted_on"]} if r.get("submitted_on") else set())) < 2]
        if not one_day:
            self.skipTest("every motion spans two dated days")
        rec = max(one_day, key=lambda r: len(r["events"]))
        html = bill_path_html(self.inp, rec)
        self.assertNotIn("<svg", html)
        self.assertIn("eseménynaplója", html)
        self.assertEqual(html.count('<td class="ts mono">'), len(rec["events"]))

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


class MotionNumbering(unittest.TestCase):
    """Why every motion can have a page at iromany/<n>.html without colliding, and why the 129 pages that
    existed before keep their addresses.

    The number after the prefix is the record's own `izon`, and izon is one sequence per cycle shared by every
    motion kind — so T/10 and K/10 cannot both exist. That is a property of the source, not of our data, which
    is exactly the kind of thing that can change under us without any error: if a future cycle numbered each
    prefix separately, two motions would quietly render to the same file and one would overwrite the other."""

    @classmethod
    def setUpClass(cls):
        cls.recs = (load_inputs().get("bill_recs") or {})

    def test_the_number_is_the_izon(self):
        if not self.recs:
            self.skipTest("no motion records cached — run: python3 -m karzat sync-bills")
        for szam, r in self.recs.items():
            m = re.match(r"^([A-ZÁÉÍÓÖŐÚÜŰ]+)/(\d+)", szam)
            self.assertIsNotNone(m, f"{szam}: a motion number that is not <prefix>/<number>")
            self.assertEqual(m.group(2), str(r.get("izon")),
                             f"{szam}: the number and izon disagree, so iromany/<n>.html is no longer the record's address")

    def test_no_two_motions_share_a_page(self):
        if not self.recs:
            self.skipTest("no motion records cached")
        seen: dict[str, str] = {}
        for szam, r in self.recs.items():
            n = str(r.get("izon"))
            self.assertNotIn(n, seen, f"{szam} and {seen.get(n)} would both write iromany/{n}.html")
            seen[n] = szam
        self.assertEqual(len(seen), len(self.recs))


class MotionSpeeches(unittest.TestCase):
    """The join that makes a page worth having for a motion nobody voted on: the record's events carry a
    felszólalás reference, and the reference is <ülésnap>/<sorszám> — the same pair the speech list is keyed by.

    Both assertions are invariants against drift rather than checks of today's numbers. A reference that stops
    resolving means the two syncs have fallen out of step; a substantive speech whose text is missing means the
    text fetch is behind the list. Either would show up on the page as an absence, and an absence on this site
    is supposed to mean "the record does not hold it", not "we did not fetch it"."""

    @classmethod
    def setUpClass(cls):
        cls.inp = load_inputs()
        cls.recs = cls.inp.get("bill_recs") or {}
        cls.speeches = {f'{s["ulnap"]}-{s["seq"]}': s
                        for s in ((cls.inp.get("speeches") or {}).get("speeches") or [])
                        if s.get("ulnap") is not None and s.get("seq") is not None}
        cls.texts = ((cls.inp.get("texts") or {}).get("texts") or {})
        cls.refs = [(szam, e) for szam, r in cls.recs.items()
                    for e in (r.get("events") or []) if e.get("speech")]

    def test_every_reference_resolves_to_a_speech_of_this_cycle(self):
        if not self.refs or not self.speeches:
            self.skipTest("no motion records or no speech list cached")
        missing = [(szam, e["speech"]) for szam, e in self.refs
                   if e["speech"].replace("/", "-") not in self.speeches]
        self.assertEqual(missing[:5], [], f"{len(missing)} of {len(self.refs)} references point at no speech we hold")

    def test_every_substantive_referenced_speech_has_its_text(self):
        if not self.refs or not self.speeches or not self.texts:
            self.skipTest("no speech texts cached — run: python3 -m karzat sync-speech-texts")
        gaps = []
        for szam, e in self.refs:
            key = e["speech"].replace("/", "-")
            s = self.speeches.get(key)
            if s and not s.get("technical") and key not in self.texts:
                gaps.append((szam, e["speech"], s.get("kind")))
        self.assertEqual(gaps[:5], [],
                         f"{len(gaps)} substantive speeches a motion points at have no text — the text sync is behind the list")


class PrefixRenameDidNotEatWords(unittest.TestCase):
    """A guard against the way the `kz-` prefix arrived.

    The prefix was applied to the whole file as a plain text substitution, and it bit into three English words
    that happen to contain the same letters: `flow-root` became `flkz-root` and `overflow-x` became `overflkz-x`
    in five rules. Both are invalid CSS, so the browser dropped the declarations silently — the inspector stopped
    containing its floated portrait, and every wide table pushed the page sideways instead of scrolling in its
    own box (318 px of it at 375 px wide). Nothing failed: the suite stayed green because the assertion that
    named `flow-root` had been rewritten by the same substitution.

    So this asserts the shape rather than any one value. In real markup `kz-` is only ever preceded by `.`, `-`,
    a quote or whitespace — never by a letter or a digit, because that would mean it landed inside a word."""

    def test_no_generated_asset_has_the_prefix_inside_a_word(self):
        bad = []
        for name, text in build_assets().items():
            for m in re.finditer(r"[A-Za-z0-9]kz-[a-z-]+", text):
                bad.append(f'{name}: …{text[max(0, m.start() - 20):m.end() + 6]}…')
        self.assertEqual(bad[:5], [], f"{len(bad)} places where kz- landed inside a word")

    def test_the_two_layout_rules_the_substitution_broke_are_intact(self):
        css = build_assets()["karzat.css"]
        # named explicitly, because these two are the ones that fail invisibly rather than loudly
        self.assertIn("display:flow-root", css)          # the inspector contains its floated portrait
        self.assertGreaterEqual(css.count("overflow-x:auto"), 5)   # wide content scrolls in its own box


class MotionEventLinks(unittest.TestCase):
    """Every felszólalás a motion record points at must land on a page this build writes.

    It did not. The log linked each reference to `felszolalas/<ülésnap>-<sorszám>.html` whenever the cycle held a
    row with that id — 4,361 of them — but pages are written only for the substantive speeches, 1,957 here. The
    other 990 links were 404s on the live site, and at 537 motion pages it would have been 1,962 of 3,606.

    The trap worth remembering is that the site's own `speech_href` helper would not have fixed it: its gate is
    `speech_has_page`, which returns True unconditionally for a live cycle, so it emits the identical dead link.
    The only gate that cannot drift is the set the writer actually iterates."""

    @classmethod
    def setUpClass(cls):
        cls.inp = load_inputs()
        cls.recs = cls.inp.get("bill_recs") or {}

    def _hrefs(self):
        from scripts.build_site import bill_path_html
        out = []
        for szam, rec in self.recs.items():
            for h in re.findall(r'href="\.\./felszolalas/([^"]+)"', bill_path_html(self.inp, rec)):
                out.append((szam, h))
        return out

    def test_no_event_reference_links_to_a_page_that_is_not_written(self):
        from scripts.build_site import paged_speech_ids
        if not self.recs:
            self.skipTest("no motion records cached")
        paged = paged_speech_ids(self.inp)
        days = {str(d["ulnap"]) for d in ((self.inp.get("speeches") or {}).get("days") or [])}
        bad = []
        for szam, h in self._hrefs():
            if h.startswith("nap"):
                if h.split(".html")[0][3:] not in days:
                    bad.append(f"{szam}: {h} — no such sitting day")
            elif h[:-5] not in paged:
                bad.append(f"{szam}: {h} — no page is written for that speech")
        self.assertEqual(bad[:5], [], f"{len(bad)} references point at a file the build never writes")

    def test_a_reference_without_its_own_page_falls_back_to_the_day_anchor(self):
        from scripts.build_site import paged_speech_ids, build_day_page
        if not self.recs:
            self.skipTest("no motion records cached")
        paged = paged_speech_ids(self.inp)
        anchors = [(s, h) for s, h in self._hrefs() if h.startswith("nap")]
        self.assertTrue(anchors, "every reference has its own page — this fallback is untested")
        for szam, h in anchors[:200]:
            self.assertRegex(h, r"^nap\d+\.html#s\d+-\d+$", f"{szam}: malformed day-anchor href")
            self.assertNotIn(h.split("#s")[1], paged, f"{szam}: {h} sends a paged speech to the day list")
        # and the day page really does carry the anchor the href names
        uln, sid = int(anchors[0][1][3:].split(".html")[0]), anchors[0][1].split("#s")[1]
        rows = [r for r in ((self.inp.get("speeches") or {}).get("speeches") or []) if str(r["ulnap"]) == str(uln)]
        html = build_day_page(self.inp, uln, rows, (self.inp["texts"] or {}).get("texts") or {})
        self.assertIn(f'id="s{sid}"', html)


class MotionPages(unittest.TestCase):
    """A page for every motion the registry answers for, not only the ones the House voted on by name.

    The rule the old behaviour produced, stated as a set: the site published an oversight instrument if and only
    if the member rejected the minister's answer, because only then was there a recorded vote. 129 of 537."""

    @classmethod
    def setUpClass(cls):
        from scripts.build_site import merge_motion_records
        import karzat.analytics as an
        cls.inp = load_inputs()
        cls.voted = an.bills(cls.inp)
        cls.bs = merge_motion_records(cls.voted, cls.inp.get("bill_recs_by_num") or {})
        cls.byn = cls.inp.get("bill_recs_by_num") or {}

    def test_every_record_gets_a_page_and_the_old_ones_keep_their_address(self):
        if not self.byn:
            self.skipTest("no motion records cached")
        self.assertEqual(set(self.bs), set(self.byn) | set(self.voted))
        for n in self.voted:                                  # no address moves, no citation key changes
            self.assertIn(n, self.bs)
            self.assertEqual(self.bs[n]["votes"], self.voted[n]["votes"])

    def test_the_merge_is_the_identity_on_a_closed_cycle(self):
        from scripts.build_site import merge_motion_records
        import karzat.analytics as an
        inp42 = load_inputs(42)
        bs42 = an.bills(inp42)
        self.assertEqual(merge_motion_records(bs42, inp42.get("bill_recs_by_num") or {}), dict(sorted(bs42.items())))

    def test_a_field_the_record_does_not_carry_renders_no_row(self):
        from scripts.build_site import motion_card_html
        if not self.byn:
            self.skipTest("no motion records cached")
        pairs = (("Kihirdetés", "promulgation"), ("Címzett", "addressee"), ("Megjegyzés", "note"),
                 ("Tárgyalási mód", "procedure_mode"))
        for rec in self.byn.values():
            card = motion_card_html(self.inp, rec)
            self.assertNotIn("<td></td>", card)
            self.assertNotIn("<td>—</td>", card)
            for label, field in pairs:
                self.assertEqual(f'<th scope="row">{label}</th>' in card, bool(rec.get(field)),
                                 f'{rec["szam"]}: the {label} row and the {field} field disagree')

    def test_the_missing_field_line_never_names_something_this_kind_never_has(self):
        """Ungated, the line fires on all 537 and tells every question in the cycle that it lacks a committee and
        a promulgation — which questions do not have. Gated on the kind's own fill it fires on far fewer, and on
        no oversight page at all."""
        from scripts.build_site import motion_card_html, _kind_fill, _MISSING_NAME, OVERSIGHT
        if not self.byn:
            self.skipTest("no motion records cached")
        inverse = {v: k for k, v in _MISSING_NAME.items()}
        fired = oversight_fired = 0
        for rec in self.byn.values():
            m = re.search(r"nem közöl: ([^<.]+)\.", motion_card_html(self.inp, rec))
            if not m:
                continue
            fired += 1
            if rec.get("main_type") in OVERSIGHT:
                oversight_fired += 1
            fill = _kind_fill(self.inp).get(rec.get("main_type") or "", {})
            for word in (x.strip() for x in m.group(1).split(",")):
                self.assertGreaterEqual(fill.get(inverse[word], 0), 0.5,
                                        f'{rec["szam"]}: names {word!r}, which most motions of its kind also lack')
        self.assertEqual(oversight_fired, 0, "an oversight page regrets a field its kind never has")
        self.assertLess(fired, len(self.byn) / 2, "the line still fires on most pages")

    def test_every_oversight_page_reaches_exactly_one_empty_state_or_an_exchange(self):
        from scripts.build_site import exchange_html, OVERSIGHT
        if not self.byn:
            self.skipTest("no motion records cached")
        states = ("A felszólalás-szövegek nincsenek betöltve.", "Nem hangzott el: az irományt visszavonták.",
                  "Írásbeli kérdés: a válasz írásban érkezik", "felszólalásra hivatkozik; a szövegük nincs betöltve.",
                  "Elhangzott felszólalást nem jelez.", "nem jelez elhangzott felszólalást ehhez")
        with_turns = 0
        for rec in self.byn.values():
            if rec.get("main_type") not in OVERSIGHT:
                self.assertEqual(exchange_html(self.inp, rec), "", f'{rec["szam"]}: a bill has no exchange panel')
                continue
            html = exchange_html(self.inp, rec)
            self.assertIn("Ahogy elhangzott", html)
            hits = sum(1 for x in states if x in html)
            if 'class="turn"' in html:
                with_turns += 1
                self.assertEqual(hits, 0, f'{rec["szam"]}: an exchange that also apologises for having none')
            else:
                self.assertEqual(hits, 1, f'{rec["szam"]}: {hits} empty states fired, want exactly one')
        self.assertGreater(with_turns, 100)

    def test_the_exchange_prints_the_question_before_the_answer(self):
        """Sorted by the transcript's own (ülésnap, sorszám), never by the record's (date, event text): 235 of the
        238 multi-turn exchanges happen on one sitting day, so the date is not an order, and alphabetising the
        label puts the follow-up ahead of the question it follows."""
        from scripts.build_site import exchange_html, OVERSIGHT
        if not self.byn:
            self.skipTest("no motion records cached")
        asked = "elhangzik az interpelláció/kérdés/azonnali kérdés"
        checked = 0
        for rec in self.byn.values():
            if rec.get("main_type") not in OVERSIGHT:
                continue
            html = exchange_html(self.inp, rec)
            labels = re.findall(r'· ([^<·]+)</div>\s*<div class="turn-t', html)
            labels = [x.strip() for x in labels]
            answer = next((x for x in labels if "megválaszolva" in x or "miniszteri viszonválasz" in x), None)
            if asked not in labels or not answer:
                continue
            checked += 1
            self.assertLess(labels.index(asked), labels.index(answer),
                            f'{rec["szam"]}: the answer is printed before the question')
        self.assertGreater(checked, 100, "too few exchanges carried both a question and a reply to prove this")

    def test_a_turn_from_an_earlier_sitting_day_keeps_its_place_in_front(self):
        """Not every exchange opens with the question, and the three that do not are the interesting ones.

        On A/155 the member announced on sitting day 6 that he would not accept a substitute answerer; the question
        itself was put on day 8. Ordering by (ülésnap, sorszám) puts the announcement first, which is what happened.
        A rule that forced the question to the top would hide the refusal — and the refusal is the news."""
        from scripts.build_site import exchange_html, OVERSIGHT
        if not self.byn:
            self.skipTest("no motion records cached")
        asked = "elhangzik az interpelláció/kérdés/azonnali kérdés"
        early = []
        for rec in self.byn.values():
            if rec.get("main_type") not in OVERSIGHT:
                continue
            labels = [x.strip() for x in re.findall(r'· ([^<·]+)</div>\s*<div class="turn-t', exchange_html(self.inp, rec))]
            if asked in labels and len(labels) > 1 and labels.index(asked) != 0:
                early.append(rec["szam"])
        self.assertLessEqual(len(early), 5, f"more exchanges than expected open before the question: {early}")
        for szam in early:                     # and each one is genuinely a two-day exchange, not a sorting bug
            rec = next(r for r in self.byn.values() if r["szam"] == szam)
            days = {e["speech"].split("/")[0] for e in rec["events"] if e.get("speech")}
            self.assertGreater(len(days), 1, f"{szam}: opens before the question but sits on one sitting day")

    def test_the_index_marks_an_unvoted_motion_as_zero_and_never_blank(self):
        from scripts.build_site import build_bill_index
        if not self.byn:
            self.skipTest("no motion records cached")
        html = build_bill_index(self.inp, self.bs)
        self.assertEqual(html.count("<td></td>"), 0)
        self.assertNotIn('data-nv=""', html)
        self.assertEqual(html.count("<tr data-"), len(self.bs))
        # the three records that carry only a title get no date attribute rather than a zero
        dateless = [r for r in self.byn.values() if not r.get("submitted_on")]
        self.assertEqual(html.count("<tr data-") - html.count("data-d="), len(dateless))
        for n in self.voted:                                   # every voted motion still shows its count
            self.assertIn(f'data-num="{n}" data-nv="{len(self.voted[n]["votes"])}"', html)


class Traffic(unittest.TestCase):
    """The access log's aggregation. Two claims are worth guarding, and neither is about a number.

    The first is that a robot is counted as one. A static site of 240,000 pages is mostly crawled, not read, and
    the day the distribution first served 37,842 requests it had been announced to nobody — so a figure that does
    not separate the two is not a visitor count, it is a crawler count wearing one.

    The second is that no address survives the aggregation. The raw log carries the client IP and expires by
    itself after thirty days; what this writes is counts, and the test asserts the absence rather than trusting
    the code to have remembered."""

    LOG = (b"#Version: 1.0\n"
           b"#Fields: date time x-edge-location sc-bytes c-ip cs-method cs(Host) cs-uri-stem sc-status "
           b"cs(Referer) cs(User-Agent) cs-uri-query\n"
           b"2026-08-21\t10:00:01\tVIE50\t5120\t66.249.66.1\tGET\togykarzat.hu\t/ckl43/iromany/109.html\t200\t-\t"
           b"Mozilla/5.0%20(compatible;%20Googlebot/2.1;%20+http://www.google.com/bot.html)\t-\n"
           b"2026-08-21\t10:00:02\tVIE50\t4096\t203.0.113.7\tGET\togykarzat.hu\t/ckl43/iromany/109.html\t200\t"
           b"https://example.org/cikk\tMozilla/5.0%20(iPhone;%20CPU%20iPhone%20OS%2017_5)%20Safari/604.1\t-\n"
           b"2026-08-21\t10:00:03\tVIE50\t1024\t203.0.113.7\tGET\togykarzat.hu\t/assets/karzat.css\t200\t-\t"
           b"Mozilla/5.0%20(iPhone;%20CPU%20iPhone%20OS%2017_5)%20Safari/604.1\t-\n")

    def test_a_robot_is_counted_as_a_robot(self):
        from scripts.traffic import parse_log, classify
        import gzip as _gz
        rows = parse_log(_gz.compress(self.LOG))
        self.assertEqual(len(rows), 3)
        kinds = [classify(r["cs(User-Agent)"].replace("%20", " ")) for r in rows]
        self.assertEqual(kinds, ["Googlebot", None, None])

    def test_the_aggregate_keeps_no_address(self):
        from scripts.traffic import parse_log, classify, unquote
        import gzip as _gz
        rows = parse_log(_gz.compress(self.LOG))
        # the shape scripts.traffic builds, reproduced here so the assertion is about the output, not the code path
        sources, paths = set(), []
        for r in rows:
            if classify(unquote(r["cs(User-Agent)"])) is None:
                sources.add(r["c-ip"])
                if not r["cs-uri-stem"].startswith("/assets/"):
                    paths.append(r["cs-uri-stem"])
        out = {"requests": len(rows), "distinct_sources_not_robot": len(sources), "top_pages_not_robot": paths}
        blob = json.dumps(out, ensure_ascii=False)
        self.assertEqual(out["distinct_sources_not_robot"], 1)
        self.assertEqual(paths, ["/ckl43/iromany/109.html"])       # assets are not pages
        for ip in ("66.249.66.1", "203.0.113.7"):
            self.assertNotIn(ip, blob, "an address survived into the aggregate")


class Echo(unittest.TestCase):
    """The page that shows passages repeated word for word in two members' speeches.

    Its most important property is not a number: it is that the site never characterises what a repetition means.
    A matcher cannot separate a committee template from a quotation from a talking point, so the page prints what
    is measurable and names the three possibilities in the reader's hands. If a future edit ever slips a word like
    "coordination" into the site's own prose, that is the moment this feature stops being honest."""

    @classmethod
    def setUpClass(cls):
        cls.inp = load_inputs()

    def test_the_page_never_says_what_a_repetition_means(self):
        from scripts.build_site import build_echo_page
        html = build_echo_page(self.inp)
        if not html:
            self.skipTest("no echo data derived — run: python3 -m scripts.derive_echo")
        # the record's own words are quoted and may say anything; the site's words are what is under test
        ours = re.sub(r'<td class="prose">„.*?”</td>', "", html).lower()
        for word in ("koordin", "összehangolt", "utasításra", "kampány", "propaganda", "sugalmaz"):
            self.assertNotIn(word, ours, f"the site's own prose characterises the finding: {word!r}")
        for kind in ("Sablon.", "Idézés.", "Átvett mondat."):      # …and it offers all three readings
            self.assertIn(kind, html)

    def test_every_row_links_both_speeches_and_no_cell_is_blank(self):
        from scripts.build_site import build_echo_page, paged_speech_ids
        html = build_echo_page(self.inp)
        if not html:
            self.skipTest("no echo data derived")
        self.assertEqual(html.count("<td></td>"), 0)
        paged = paged_speech_ids(self.inp)
        hrefs = re.findall(r'href="\.\./felszolalas/([^"]+)\.html"', html)
        self.assertTrue(hrefs)
        for h in hrefs:
            self.assertIn(h, paged, f"{h}: the echo links a speech page that is not written")
        rows = re.findall(r"<tr data-p=.*?</tr>", html, re.S)
        for r in rows:
            self.assertGreaterEqual(len(re.findall(r"felszolalas/", r)), 2, "a row with fewer than two speakers")

    def test_a_passage_really_is_in_both_speeches(self):
        """The claim is verbatim repetition, so it is checked against the transcript rather than trusted."""
        from scripts.build_site import normalise_echo_words
        e = self.inp.get("echo") or {}
        texts = (self.inp.get("texts") or {}).get("texts") or {}
        if not e.get("items") or not texts:
            self.skipTest("no echo data derived")
        for f in e["items"][:20]:
            for sp in f["speeches"]:
                body = " ".join(normalise_echo_words(texts[sp["id"]].get("paragraphs")))
                self.assertIn(f["passage"], body,
                              f'{sp["id"]}: the passage is not in that speech after all')


class NightlyRunsEveryDerivation(unittest.TestCase):
    """The unattended run must regenerate everything the site reads, not a subset of it.

    For a while it ran seven of the twelve derive scripts. A nightly therefore refreshed the votes and the speeches
    while the footer's facts, the ministerial bench, the seating plan and the repeated-passage index kept the
    previous answer — and every page still carried a fresh "frissítve" stamp above them. A stale number under a
    current timestamp is precisely the failure the freshness contract exists to prevent, and the pipeline was
    quietly manufacturing it.

    So this compares the scripts that exist against the scripts the nightly names, rather than checking any one of
    them: a derivation added next month is wired in or this fails."""

    EXEMPT = {
        # a helper that runs the other two with one cycle's date window — the nightly addresses the cycle directly
        "derive_cycle",
        # the current cycle's roster and vote index, rebuilt by the sync step itself before anything else runs
        "derive_first_light",
    }

    def test_every_derive_script_is_named_in_the_nightly(self):
        nightly = (ROOT / "scripts" / "nightly.py").read_text(encoding="utf-8")
        on_disk = {p.stem for p in (ROOT / "scripts").glob("derive_*.py")}
        named = set(re.findall(r"scripts\.(derive_\w+)", nightly))
        missing = sorted(on_disk - named - self.EXEMPT)
        self.assertEqual(missing, [], f"the nightly never runs: {', '.join(missing)}")
        self.assertTrue(named <= on_disk, f"the nightly names a script that does not exist: {named - on_disk}")

    def test_the_last_two_steps_are_the_ones_that_read_the_others(self):
        """Two derivations consume what the rest produce, and their order is the claim.

        tenyek.json counts the derived corpus, so it must come after everything that writes to it. receipt.json
        hashes the derived files, so it must come after *that* — a receipt taken before the last write would
        certify a state the build then left behind, which is worse than no receipt at all."""
        nightly = (ROOT / "scripts" / "nightly.py").read_text(encoding="utf-8")
        order = re.findall(r"scripts\.(derive_\w+)", nightly)
        self.assertIn("derive_facts", order)
        self.assertEqual(order[-1], "derive_receipt", f"the receipt is not last: {order[-3:]}")
        # facts counts the derived corpus, so everything that writes to it has to have run; the receipt then
        # hashes the result, facts and parquet included. Only the two relative positions are the claim — what
        # sits between them is free to grow, and it has.
        self.assertLess(order.index("derive_facts"), order.index("derive_receipt"))
        for earlier in ("derive_mps", "derive_speeches", "derive_bills", "derive_echo"):
            if earlier in order:
                self.assertLess(order.index(earlier), order.index("derive_facts"),
                                f"{earlier} runs after the facts that count it")

    def test_deriving_one_cycle_does_not_drop_the_others(self):
        """The nightly passes --cycle, and a run that rewrote echo.json with only the running cycle would silently
        empty the three archive ones. The merge is asserted here because nothing on the page would show the loss."""
        import json as _json
        p = ROOT / "data" / "derived" / "echo.json"
        if not p.exists():
            self.skipTest("no echo data derived")
        before = set(_json.loads(p.read_text(encoding="utf-8"))["cycles"])
        self.assertGreater(len(before), 1, "only one cycle derived — the merge is untested")
        src = (ROOT / "scripts" / "derive_echo.py").read_text(encoding="utf-8")
        self.assertIn("out[\"cycles\"] = {**prev, **out[\"cycles\"]}", src)


class FactionSwitches(unittest.TestCase):
    """Two records of the same fact, and the page that makes them answer to each other.

    The claim the inventory carried into this work was "261 switches, 255 people, 135 checkable, 135/135 agree".
    Two of those four numbers were wrong: 261 counts person-cycles rather than switches, and the sources do not
    all agree. The test guards the shape of the answer rather than the answer, so a resync can move the counts
    without going red — but it cannot quietly turn a disagreement into agreement."""

    @classmethod
    def setUpClass(cls):
        cls.inp = load_inputs()
        cls.sw = cls.inp.get("switches") or {}

    def test_the_three_counts_answer_three_different_questions(self):
        if not self.sw:
            self.skipTest("no switch data derived — run: python3 -m scripts.derive_faction_switches")
        items = self.sw["items"]
        self.assertEqual(self.sw["switches"], len(items))
        self.assertEqual(self.sw["people"], len({s["azon"] for s in items}))
        self.assertEqual(self.sw["person_cycles"], len({(s["azon"], s["ciklus"]) for s in items}))
        # switches ≥ person-cycles ≥ people, always: one mandate can hold several changes, one person several mandates
        self.assertGreaterEqual(self.sw["switches"], self.sw["person_cycles"])
        self.assertGreaterEqual(self.sw["person_cycles"], self.sw["people"])

    def test_a_switch_is_inside_one_mandate_never_across_an_election(self):
        if not self.sw:
            self.skipTest("no switch data derived")
        for s in self.sw["items"]:
            self.assertNotEqual(s["from"], s["to"], f'{s["name"]}: a "switch" to the same faction')
            self.assertTrue(s["since"] <= s["on"], f'{s["name"]}: the change predates the row it comes from')

    def test_every_switch_carries_a_verdict_and_the_disagreements_are_not_hidden(self):
        from scripts.build_site import build_switch_page, VERDICT_ORDER
        if not self.sw:
            self.skipTest("no switch data derived")
        for s in self.sw["items"]:
            self.assertIn(s["verdict"], VERDICT_ORDER, f'{s["name"]}: unknown verdict {s["verdict"]!r}')
        html = build_switch_page(self.inp)
        self.assertEqual(html.count("<tr data-p="), len(self.sw["items"]), "a switch is missing from the page")
        # every disagreeing member is named on the page, by name, not folded into a count
        for s in self.sw["items"]:
            if s["verdict"] in ("tovább tartja a régit", "más frakciót nevez meg"):
                self.assertIn(s["name"], html, f'{s["name"]}: a disagreement that the page does not name')

    def test_the_verified_window_stops_at_the_next_change(self):
        """Open-ended, a member who went A to B to C reads as a disagreement at A to B, because the list correctly
        says C later. The bound is what makes the verdicts mean anything, so it is asserted in the data."""
        if not self.sw:
            self.skipTest("no switch data derived")
        by_person = {}
        for s in self.sw["items"]:
            by_person.setdefault((s["azon"], s["ciklus"]), []).append(s)
        chained = [v for v in by_person.values() if len(v) > 1]
        if not chained:
            self.skipTest("nobody changed faction twice inside one mandate")
        for seq in chained:
            seq.sort(key=lambda s: s["on"])
            for a, b in zip(seq, seq[1:]):
                self.assertEqual(a["until"], b["on"], f'{a["name"]}: the window does not end at the next change')


class PagesLoadTheirAssets(unittest.TestCase):
    """A page one level below the root has to say so, or it loads nothing.

    frakciovaltas/index.html shipped once with depth 0, so its stylesheet href was `assets/karzat.css` — resolved
    against its own directory, which has no assets — and it rendered as unstyled HTML on the live site. Nothing in
    the suite noticed, because every test asked about content and none about the page's own furniture.

    This checks the whole set: for each page the build writes below the root, the stylesheet, the script and the
    topbar's home link must climb out of its directory exactly as far as the directory is deep."""

    def test_every_site_wide_page_reaches_the_stylesheet(self):
        from scripts.build_site import (build_switch_page, build_profile_page, build_coverage_page,
                                        landing_inputs, load_inputs, available_cycles, coverage_rows)
        inp = load_inputs()
        pages = {"frakciovaltas/index.html": build_switch_page(inp),
                 "arcel/index.html": build_profile_page(inp, landing_inputs()["rows"])}
        for name, html in pages.items():
            if not html:
                continue
            # The failure that matters is a path that resolves *inside* the page's own directory, because there is
            # no assets/ there and the browser has nowhere to fall back to. Climbing too far is harmless — a
            # browser clamps ../ at the root, so ../../assets/ from /arcel/ still lands on /assets/ — and both
            # pages do it today. It is wrong and worth tidying, but it is not what took the stylesheet away.
            for bad in re.findall(r'(?:href|src)="((?!\.\./|https?:|//|#|mailto:|data:)[^"]*assets/[^"]*)"', html):
                self.fail(f"{name}: {bad} resolves inside the page's own directory, where no assets/ exists")
            self.assertRegex(html, r'href="(\.\./)+assets/karzat\.css"', f"{name}: no stylesheet climbing out")
            self.assertRegex(html, r'src="(\.\./)+assets/karzat\.js"', f"{name}: no script climbing out")


class Receipt(unittest.TestCase):
    """The receipt pins a published number to a corpus state and a code version.

    Its one load-bearing property is sensitivity: if any of the 146,709 cached payloads changes by a byte, the
    corpus root must move. A digest that misses a small edit would certify a corpus that is not the one that was
    read, which is worse than publishing no receipt — it would make an unverifiable claim look verified."""

    def test_a_single_changed_byte_moves_the_corpus_root(self):
        import hashlib as _h
        from scripts.derive_receipt import service_root
        d = ROOT / "data" / "raw" / "ulesnap"
        if not d.is_dir() or not any(d.glob("*.xml")):
            self.skipTest("no cached payloads to fingerprint")
        # a COPY of the payloads, not the payloads: the claim here is about service_root's behaviour, and
        # editing the real cache makes this test unsafe to run beside anything that reads data/raw — which is
        # exactly what parallel workers do. The property is the same on a copy; the shared-state hazard is not.
        import tempfile, shutil as _sh
        tmp = Path(tempfile.mkdtemp())
        for f in sorted(d.glob("*.xml"))[:6]:
            _sh.copy2(f, tmp / f.name)
        d = tmp
        before, n, _ = service_root(d)
        p = sorted(d.glob("*.xml"))[0]
        keep = p.read_bytes()
        try:
            p.write_bytes(keep + b"<!-- -->")
            after, n2, _ = service_root(d)
        finally:
            p.write_bytes(keep)
        self.assertEqual(n, n2)
        self.assertNotEqual(before, after, "an edited payload left the root unchanged")
        self.assertEqual(service_root(d)[0], before, "restoring the payload did not restore the root")

    def test_the_root_moves_for_an_added_or_removed_payload_too(self):
        from scripts.derive_receipt import service_root
        d = ROOT / "data" / "raw" / "ulesnap"
        if not d.is_dir():
            self.skipTest("no cached payloads")
        import tempfile, shutil as _sh                      # see above: never write into the real cache
        tmp = Path(tempfile.mkdtemp())
        for f in sorted(d.glob("*.xml"))[:6]:
            _sh.copy2(f, tmp / f.name)
        d = tmp
        before, _, _ = service_root(d)
        extra = d / "zzz-receipt-test.xml"
        try:
            extra.write_bytes(b"<x/>")
            self.assertNotEqual(service_root(d)[0], before, "an added payload left the root unchanged")
        finally:
            extra.unlink(missing_ok=True)
        self.assertEqual(service_root(d)[0], before)

    def test_the_receipt_says_when_the_tree_was_dirty(self):
        """A build from uncommitted code must not be attributed to the commit it sits on: that is exactly the
        case where somebody trying to reproduce the number fails and cannot see why."""
        from scripts.derive_receipt import code_state
        c = code_state()
        self.assertIn("commit", c)
        self.assertIsInstance(c["clean"], bool)
        self.assertEqual(c["clean"], c["uncommitted_files"] == 0)

    def test_the_method_page_prints_the_receipt(self):
        from scripts.build_site import build_method_page
        inp = load_inputs()
        r = inp.get("receipt") or {}
        if not r:
            self.skipTest("no receipt derived — run: python3 -m scripts.derive_receipt")
        html = build_method_page(inp)
        self.assertIn(r["corpus_root"][:16], html)
        self.assertIn(r["code"]["described"], html)


class ParquetCorpus(unittest.TestCase):
    """The whole roll-call corpus in one seekable file, and the two ways it could quietly lie.

    The first is arithmetic: if the positions do not sum to the tallies the vote's own header carries, the file
    disagrees with the site built from the same source and one of them is wrong. The second is subtler and is the
    commonest way this data gets misread — collapsing the seven positions the House distinguishes into
    yes/no/abstain. A file that did that silently would spread the mistake to everyone who downloaded it."""

    @classmethod
    def setUpClass(cls):
        try:
            import pyarrow.parquet as pq
        except ImportError:
            raise unittest.SkipTest("pyarrow not installed")
        cls.pq = pq
        cls.dir = ROOT / "site" / "adatok"
        if not (cls.dir / "szavazatok.parquet").exists():
            raise unittest.SkipTest("no parquet built — run: python3 -m scripts.derive_parquet")
        cls.votes = pq.read_table(cls.dir / "szavazasok.parquet")
        cls.pos = pq.read_table(cls.dir / "szavazatok.parquet")

    def test_it_holds_the_same_number_of_votes_the_site_publishes(self):
        from scripts.build_site import site_totals
        self.assertEqual(self.votes.num_rows, site_totals()["votes"])

    def test_a_vote_s_rows_add_up_to_its_own_header(self):
        """Stopping after three thousand votes sounded like a sample and was a prefix.

        The table is written cycle by cycle, newest first, so the cap fell inside cycle 41 and the six older
        parliaments were never reconciled at all — including the ones whose payloads needed repairs to parse.
        A budget spent evenly across the cycles costs the same and covers all of them."""
        import collections
        by_ts = collections.defaultdict(collections.Counter)
        for ts, p in zip(self.pos.column("szavazas_id").to_pylist(), self.pos.column("szavazat").to_pylist()):
            by_ts[ts][p] += 1
        cyc = self.votes.column("ciklus").to_pylist()
        rows_by_cycle = collections.defaultdict(list)
        for i, c in enumerate(cyc):
            rows_by_cycle[c].append(i)
        per = max(60, 3000 // max(1, len(rows_by_cycle)))
        checked = collections.Counter()
        for c, idx in sorted(rows_by_cycle.items()):
            step = max(1, len(idx) // per)                  # spread across the cycle, not its first rows
            for i in idx[::step]:
                ts = self.votes.column("szavazas_id")[i].as_py()
                counts = by_ts.get(ts)
                if not counts:
                    continue                               # a vote with no name-level list: tallies only
                for col in ("igen", "nem", "tartozkodott"):
                    want = self.votes.column(col)[i].as_py()
                    if want is None:
                        continue
                    self.assertEqual(counts.get(col, 0), want,
                                     f"cycle {c} {ts}: {col} header {want}, rows {counts.get(col, 0)}")
                checked[c] += 1
        named = {c for c in rows_by_cycle if checked[c]}
        self.assertGreaterEqual(len(named), 6, f"only {sorted(named)} were reconciled at all")
        self.assertGreater(sum(checked.values()), 1000, "too few named votes checked to prove anything")

    def test_all_seven_positions_survive_the_export(self):
        """The first three million rows are cycles 43 and 42. A cycle that collapsed its seven positions into
        three would have passed this, which is precisely the mistake the column exists to prevent."""
        import collections
        cyc = self.pos.column("ciklus").to_pylist()
        pos = self.pos.column("szavazat").to_pylist()
        seen = collections.defaultdict(set)
        for c, p in zip(cyc, pos):
            seen[c].add(p)
        every = set().union(*seen.values()) if seen else set()
        for p in ("igen", "nem", "tartozkodott", "jelen_nem_szavazott", "nem_szavazott",
                      "bejelentett_hianyzo", "igazoltan_tavol"):
            self.assertIn(p, every, f"{p} is missing — the export collapsed the House's own distinctions")
        # and no cycle with a real roll call may have been flattened to the three headline positions
        for c, vals in sorted(seen.items()):
            if len(vals) <= 1:
                continue                                   # a cycle with tallies only
            self.assertGreater(len(vals), 3, f"cycle {c} carries only {sorted(vals)} — collapsed")

    def test_it_is_seekable_rather_than_one_block(self):
        """A single row group would mean any question downloads the whole file; the browser page depends on not."""
        md = self.pq.ParquetFile(self.dir / "szavazatok.parquet").metadata
        self.assertGreater(md.num_row_groups, 10, "the positions are one block, so no query can seek")
        self.assertLess(self.pos.nbytes and (self.dir / "szavazatok.parquet").stat().st_size, 60_000_000)


class ExportedRowsBelongToTheirCycle(unittest.TestCase):
    """A row carries a cycle number, and that number is a claim about when the thing happened.

    The speech exporter fell back to the unsuffixed `speeches.json.gz` — the CURRENT cycle's file — whenever a
    per-cycle one was missing. Cycles 34 and 35 have none, so the 1990-94 and 1994-98 parliaments were each
    given 4,651 speeches dated 2026, by members who would not sit for thirty years. 9,302 rows of fiction that
    parsed cleanly, counted plausibly, and were wrong only if you compared a date against its cycle.

    I had the correct figure — 495,191 — measured before the exporter was written, and did not compare it with
    the 504,493 the exporter produced. So the check is arithmetic the machine does every run, not a number I
    remember to look at."""

    @classmethod
    def setUpClass(cls):
        cls.d = ROOT / "site" / "adatok"
        if not (cls.d / "felszolalasok.parquet").exists():
            raise unittest.SkipTest("parquet not built")

    def _spans(self):
        """The date each cycle's own votes span, from the votes table — an independent witness."""
        import pyarrow.parquet as pq
        t = pq.read_table(self.d / "szavazasok.parquet", columns=["ciklus", "datum"])
        span = {}
        for c, dt in zip(t.column("ciklus").to_pylist(), t.column("datum").to_pylist()):
            if not dt:
                continue
            lo, hi = span.get(c, (dt, dt))
            span[c] = (min(lo, dt), max(hi, dt))
        return span

    def test_no_speech_is_dated_outside_the_cycle_it_is_filed_under(self):
        import pyarrow.parquet as pq
        span = self._spans()
        t = pq.read_table(self.d / "felszolalasok.parquet", columns=["ciklus", "datum"])
        bad = {}
        for c, dt in zip(t.column("ciklus").to_pylist(), t.column("datum").to_pylist()):
            if not dt or c not in span:
                continue
            lo, hi = span[c]
            # a sitting day may sit a little outside the votes' own span; a whole parliament may not
            if dt < lo[:4] + "-01-01" or dt > str(int(hi[:4]) + 1) + "-12-31":
                bad[c] = bad.get(c, 0) + 1
        self.assertEqual(bad, {}, f"speeches filed under a cycle they cannot belong to: {bad}")

    def test_two_cycles_never_share_an_identical_block_of_speeches(self):
        """The symptom the date check would miss if the fallback ever pointed at a same-era file: two cycles
        holding the very same rows."""
        import pyarrow.parquet as pq
        t = pq.read_table(self.d / "felszolalasok.parquet", columns=["ciklus", "datum", "ulesnap", "sorszam"])
        seen = {}
        for c, dt, u, sq in zip(*[t.column(k).to_pylist() for k in ("ciklus", "datum", "ulesnap", "sorszam")]):
            seen.setdefault(c, set()).add((dt, u, sq))
        cycles = sorted(seen)
        for i, a in enumerate(cycles):
            for b in cycles[i + 1:]:
                if not seen[a] or not seen[b]:
                    continue
                shared = len(seen[a] & seen[b])
                self.assertLess(shared, min(len(seen[a]), len(seen[b])) * 0.5,
                                f"cycles {a} and {b} share {shared} identical speech rows")

    def test_every_exported_table_s_cycle_numbers_are_ones_the_site_builds(self):
        import pyarrow.parquet as pq
        from scripts.build_site import available_cycles
        known = set(available_cycles())
        for name in ("szavazasok", "szavazatok", "kepviselok", "szavazas_iromany", "felszolalasok"):
            f = self.d / f"{name}.parquet"
            if not f.exists():
                continue
            got = set(pq.read_table(f, columns=["ciklus"]).column("ciklus").to_pylist())
            self.assertTrue(got <= known, f"{name} carries cycles the site does not build: {sorted(got - known)}")


class TheSpeechSearchRanks(unittest.TestCase):
    """The search over the record's texts required every typed word and then ordered by date.

    Measured against 492 questions the record answers for itself — an iromány's subject as the question, the
    speeches filed against that bill as the answer — that put the right speech in the first ten 7.7% of the
    time in cycle 42, and 5.3% in cycle 40. Not because the ranking was poor: because there was none. A long
    question matched almost nothing, and whatever it matched came back by accident of when it was said.

    The same index scored by BM25 finds 40.6% and 31.5%. `scripts/bench_search.py` is the measurement; these
    are the invariants that keep the index and the page able to do it at all."""

    @classmethod
    def setUpClass(cls):
        from scripts.build_site import build_assets, load_inputs, speech_search_index
        cls.inp = load_inputs()
        if not (cls.inp.get("texts") or {}).get("texts"):
            raise unittest.SkipTest("no speech texts derived")
        cls.meta, cls.idx = speech_search_index(cls.inp)
        cls.js = build_assets()["karzat.js"]

    def test_a_posting_carries_how_often_not_merely_whether(self):
        """A set of document ids is all a boolean AND needs, and all this index used to hold. BM25 is made of
        the counts, so their absence is the whole defect, in one line."""
        pairs = 0
        for key, toks in self.idx.items():
            if "__split" in toks:
                continue
            for tok, arr in toks.items():
                self.assertEqual(len(arr) % 2, 0, f"{tok}: a posting list of odd length is not (doc, tf) pairs")
                for j in range(1, len(arr), 2):
                    self.assertGreaterEqual(arr[j], 1, f"{tok}: a frequency below one")
                    self.assertLessEqual(arr[j], 255, f"{tok}: a frequency that will not fit a byte")
                pairs += len(arr) // 2
        self.assertGreater(pairs, 1000, "the index is too small to be the real one")

    def test_every_document_declares_its_length(self):
        """BM25 divides by it. A missing length silently becomes the average, which flatters long speeches —
        exactly the bias the length normalisation exists to remove."""
        for row in self.meta:
            self.assertEqual(len(row), 8, "a meta row has no length field")
            self.assertIsInstance(row[7], int)
            self.assertGreaterEqual(row[7], 0)
        self.assertGreater(sum(r[7] for r in self.meta), 0, "every document is empty")

    def test_a_posting_never_names_a_document_that_is_not_in_the_meta(self):
        n = len(self.meta)
        for key, toks in self.idx.items():
            if "__split" in toks:
                continue
            for tok, arr in toks.items():
                for j in range(0, len(arr), 2):
                    self.assertLess(arr[j], n, f"{tok}: posting for document {arr[j]} of {n}")

    def test_a_long_question_does_not_build_a_table_nobody_can_read(self):
        """Dropping the requirement that every word appear turns a sentence into most of the corpus. Measured
        on cycle 40: "a magyar gazdaság helyzete és a jövő évi költségvetés" scores 19,602 of 24,365 speeches,
        and a row for each is about five megabytes of HTML into innerHTML — the tab stops responding.

        Ranked results have a long tail by construction and nobody reads it. The cut is fine; a silent cut is
        not, because a search that shows two hundred of nineteen thousand and says "200 találat" has told the
        reader something false about the record."""
        js = self.js[self.js.index("  var q = document.getElementById('spq')"):]
        js = js[:js.index("})();")]
        self.assertIn("SHOW_MAX", js, "every scored document is rendered")
        self.assertIn("hits.slice(0, SHOW_MAX)", js)
        self.assertIn("a legjobb ", js, "the count does not say the list was cut")
        self.assertIn("' a ' + scored", js, "the count does not say how many were scored")

    def test_each_cycle_quotes_its_own_measurement(self):
        """The sentence names a cycle because the figures differ by a factor of four between them — cycle 43
        goes 14.9% to 59.1%, cycle 40 5.3% to 31.5%. Printing one cycle's result on another's page, unlabelled,
        would be a true number about the wrong parliament."""
        import json as _json
        from scripts.build_site import build_speech_search_page, load_inputs, speech_search_index
        bench = _json.loads((ROOT / "data" / "derived" / "search_bench.json").read_text(encoding="utf-8"))
        for cyc in sorted(int(k) for k in bench["cycles"]):
            inp = load_inputs(cyc)
            if not (inp.get("texts") or {}).get("texts"):
                continue
            meta, _ = speech_search_index(inp)
            html = build_speech_search_page(inp, len(meta), len(meta))
            b = bench["cycles"][str(cyc)]
            self.assertIn(f"a {cyc}. ciklus", html, f"cycle {cyc} quotes another cycle's measurement")
            for v in (b["was"]["recall10"], b["now"]["recall10"]):
                self.assertIn(f"{100 * v:.1f}".replace(".", ","), html)

    def test_the_index_moves_when_its_shape_changes(self):
        """A shard held [doc, doc, …] and now holds [doc, tf, doc, tf, …]. The JSON is cached for a day and
        the script for five minutes, so after a deploy a returning reader runs the new code against the old
        index for up to twenty-four hours — and the new code reads a document id as a frequency. Two postings
        in five disappear and the rest are scored on numbers that mean nothing, with no error anywhere.

        Yesterday the same class of bug went live with a Parquet column rename, and it was found by a reader's
        error message rather than by anything here. A cache cannot answer a path it has never seen, so the
        version is in the path; this asserts the script and the builder agree on which one."""
        from scripts.build_site import SEARCH_INDEX_DIR, SEARCH_META
        js = self.js[self.js.index("  var q = document.getElementById('spq')"):]
        js = js[:js.index("})();")]
        self.assertIn(f"'{SEARCH_INDEX_DIR}/' + key", js, "the script reads a different directory from the one built")
        self.assertIn(f"'{SEARCH_META}'", js, "the script reads a different meta file from the one built")
        self.assertNotEqual(SEARCH_INDEX_DIR, "idx", "the shape changed but the address did not")
        self.assertNotEqual(SEARCH_META, "meta.json")

    def test_an_old_index_could_not_be_read_as_a_new_one(self):
        """The specific silent failure, written down so nobody restores the shared path thinking it harmless:
        an old posting list parsed as pairs loses its last entry when the count is odd and turns document ids
        into frequencies."""
        old = [3, 7, 11, 42, 58]                       # five documents, the shape before this change
        tf = {}
        for j in range(0, len(old) - 1, 2):
            tf[old[j]] = tf.get(old[j], 0) + old[j + 1]
        self.assertEqual(tf, {3: 7, 11: 42}, "the failure mode is not what the comment says it is")
        self.assertNotIn(58, tf, "the last document survives, so the example is wrong")

    def test_the_page_scores_rather_than_filters(self):
        js = self.js[self.js.index("felszolalas/kereses.html: terms are AND-ed"):] \
            if "felszolalas/kereses.html: terms are AND-ed" in self.js else self.js
        block = self.js[self.js.index("  var q = document.getElementById('spq')"):]
        block = block[:block.index("})();")]
        self.assertIn("k1 = 1.5", block, "the page no longer computes BM25")
        self.assertIn("Math.log(1 + (N - df + 0.5)", block, "the inverse document frequency is gone")
        self.assertNotIn("hits.reverse()", block, "the results are ordered by date again")
        self.assertIn("meta[d][7]", block, "the length normalisation is not reading the document length")

    def test_the_reader_can_still_ask_for_chronological_order(self):
        """Ranking is the right default and not the only thing anyone wants; a reader following a debate wants
        the order it happened in. Taking that away would be trading one imposed order for another."""
        from scripts.build_site import build_speech_search_page
        html = build_speech_search_page(self.inp, len(self.meta), len(self.meta))
        self.assertIn('id="spdate"', html)
        self.assertIn("időrendben", html)
        block = self.js[self.js.index("  var q = document.getElementById('spq')"):]
        self.assertIn("byDate && byDate.checked", block)

    def test_no_label_still_describes_the_search_that_was_replaced(self):
        """The tag directly above the search box read "több szó: mind szerepel" — every word must appear — on
        all ten pages, which is the opposite of what the box now does. My own grep missed it because I searched
        for the words I had written in the prose, not the ones somebody else had written in the label."""
        from scripts.build_site import build_speech_search_page, speech_search_index
        meta, _ = speech_search_index(self.inp)
        html = build_speech_search_page(self.inp, len(meta), len(meta))
        for stale in ("mind szerepel", "mindegyiknek szerepelnie kell", "AND-elve"):
            self.assertNotIn(stale, html, f"a label still promises the old behaviour: {stale}")
        self.assertIn("relevancia szerint rendezve", html)

    def test_the_page_states_what_it_measured(self):
        """The change is defensible because it was measured, and a reader has no way to know that unless the
        page says so. Both figures come from scripts/bench_search.py."""
        from scripts.build_site import build_speech_search_page
        html = build_speech_search_page(self.inp, len(self.meta), len(self.meta))
        self.assertIn("BM25", html)
        # This asserted the literal 43,8 and so agreed with a figure that had drifted three points from what
        # the benchmark measures — a test pinning a typed number is two mistakes holding each other up. The
        # page and the assertion now read the same file the measurement writes.
        import json as _json
        bench = _json.loads((ROOT / "data" / "derived" / "search_bench.json").read_text(encoding="utf-8"))
        b = bench["cycles"][str(self.inp["cycle"])]
        for value in (b["was"]["recall10"], b["now"]["recall10"]):
            printed = f"{100 * value:.1f}".replace(".", ",")
            self.assertIn(printed, html, f"the page does not quote the measured {printed}%")
        self.assertNotIn("43,8", html, "a figure from a run that never shipped is back on the page")


class TheAxisSaysWhatItKnows(unittest.TestCase):
    """The one axis the roll calls describe, and the three ways it could lie about itself.

    The first fit put two Fidesz members of cycle 42 at +9.5 on a scale whose standard deviation is one. They
    were the two most active members in the House — 2,063 votes each, and not one against their own side. A
    member who is never on the losing side is perfectly separable: the likelihood has no maximum for them and
    the estimate runs until the iteration count stops it, at a number that looks like an extraordinary finding
    and is an artefact of arithmetic. Nothing would have flagged it; the page would have named two people as
    outliers for being the opposite.

    The prior is what bounds it, and these are what keep the prior there."""

    @classmethod
    def setUpClass(cls):
        import json as _json
        f = ROOT / "data" / "derived" / "idealpoints.json"
        if not f.exists():
            raise unittest.SkipTest("run: python3 -m scripts.derive_idealpoints")
        cls.ip = _json.loads(f.read_text(encoding="utf-8"))["cycles"]

    def test_no_position_runs_away(self):
        """The signature of a runaway estimate is a gap, not a large number.

        My first version of this test bounded |x| at four and failed on cycle 41, where a DK member sits at
        −5.63. That member voted against the majority 95.9% of the time and their group's mean is −5.58: they
        are typical of their bloc, and the axis is simply wider in a parliament whose 2,008 divided votes
        identify the blocs sharply. A prior fixes a scale convention weakly; strong data moves the posterior
        away from it, which is the arithmetic working rather than failing.

        What separation actually produced was two members at +9.5 with the next highest at +1.2 — a chasm
        between them and the whole House. So the test is on the chasm."""
        for cyc, r in self.ip.items():
            xs = sorted(v["x"] for v in r["positions"].values())
            span = xs[-1] - xs[0]
            if len(xs) < 10 or span <= 0:
                continue
            # Not the gap below the single most extreme member: the two runaways sat together, so there was
            # no gap between them and my first version of this check sailed past the very bug it was written
            # for. The gap that matters is between a HANDFUL of members and the rest, at either end — real
            # polarisation separates two large blocs, an artefact separates one or two people from everybody.
            for k in (1, 2, 3):
                for lo, hi, where in ((xs[k - 1], xs[k], "bottom"), (xs[-k - 1], xs[-k], "top")):
                    self.assertLess((hi - lo) / span, 0.25,
                                    f"cycle {cyc}: {k} member(s) at the {where} stand {hi - lo:.2f} clear of "
                                    f"the rest on a {span:.2f} axis — the mark of an estimate that ran away")

    def test_a_faction_is_not_wider_than_the_house(self):
        """The symptom the separation produced: one group's internal spread larger than the gap between the
        blocs, which contradicts every cohesion figure on the same page."""
        for cyc, r in self.ip.items():
            means = [v["mean"] for v in r["factions"].values() if v["n"] >= 5]
            if len(means) < 2:
                continue
            span = max(means) - min(means)
            for f, v in r["factions"].items():
                if v["n"] >= 5:
                    self.assertLess(v["sd"], span,
                                    f"cycle {cyc}: {f} is spread wider ({v['sd']:.2f}) than the whole House ({span:.2f})")

    def test_the_axis_is_oriented_on_something_that_does_not_move(self):
        """Orienting each cycle so the largest group sits on the right flipped the axis between the 42nd and
        the 43rd, because the largest group changed party. The same picture would have read as a realignment
        that never happened."""
        anchors = {r.get("anchor") for r in self.ip.values()}
        self.assertEqual(len(anchors), 1, f"the axis is anchored on different groups in different cycles: {anchors}")
        for cyc, r in self.ip.items():
            a = r.get("anchor")
            if a and a in r["factions"]:
                others = [v["mean"] for f, v in r["factions"].items() if f != a]
                if others:
                    self.assertGreater(r["factions"][a]["mean"], min(others),
                                       f"cycle {cyc}: the anchor is not on the right-hand side")

    def test_a_fit_worth_nothing_is_not_presented_as_a_fit(self):
        """Cycle 39 classifies 92.1% of votes where guessing the majority already gets 82.0% — an APRE of
        56% against 90-plus everywhere else, and 5.2% of structure left over. One line cannot hold two
        cleavages, and the page has to say so above the picture rather than under it."""
        from scripts.build_site import load_inputs, build_cohesion_page
        import karzat.analytics as analytics
        weak = [c for c, r in self.ip.items() if r["apre"] < 0.75 or r["second_dimension"] > 0.03]
        self.assertIn("39", weak, "cycle 39 no longer measures as the poor fit this test is about")
        for cyc in weak:
            inp = load_inputs(int(cyc))
            html = build_cohesion_page(inp, analytics.cohesion(inp))
            # On the marker, not the wording: this assertion broke the moment the prose was rewritten
            # from "tengely" to "vonal", which is a test measuring the copy rather than the contract.
            self.assertIn('data-axis="weak"', html, f"cycle {cyc} draws a poor fit without saying so")

    def test_the_lede_never_claims_what_the_warning_denies(self):
        """The panel must not assert above the picture what it retracts below it.

        The opening used to name what the axis turned out to be — the government/opposition split — which is
        false for one cycle in eight, and this asserted the sentence appeared exactly where the fit was good.
        Then the section was rewritten and the sentence was deleted from the builder entirely: the reading is
        no longer claimed anywhere, only *denied* on the cycle where a single line is not enough. So the test
        was searching for a string that exists nowhere, which meant it could only pass on a poor fit — a test
        that has quietly inverted into a check that the feature is missing. It is on the contract now: the
        percentages that describe the fit are stated once, in the warning where the fit is poor and in the
        lede where it is not, and the government/opposition reading is asserted on no page at all. The
        neighbouring test learned this same lesson from this same rewrite; this one had not."""
        from scripts.build_site import load_inputs, build_cohesion_page
        import karzat.analytics as analytics
        for cyc, r in self.ip.items():
            inp = load_inputs(int(cyc))
            html = build_cohesion_page(inp, analytics.cohesion(inp))
            weak = r["apre"] < 0.75 or r["second_dimension"] > 0.02
            warned = 'data-axis="weak"' in html
            fit_line = "ennyit tesz hozzá ez az egy vonal" in html
            self.assertEqual(warned, weak, f"cycle {cyc}: the warning is {'missing' if weak else 'shown'} "
                                           f"on a {'poor' if weak else 'good'} fit")
            self.assertNotEqual(warned, fit_line, f"cycle {cyc}: the fit's numbers are stated twice or not "
                                                  f"at all — they belong in exactly one of the two blocks")
            denial = "nem is a kormány–ellenzék határ" in html
            self.assertEqual(denial, weak, f"cycle {cyc}: the denial does not match the fit")
            self.assertEqual(html.count("kormány–ellenzék"), 1 if weak else 0,
                             f"cycle {cyc}: the government/opposition reading is claimed rather than only denied")

    def test_the_figures_beside_each_other_add_up(self):
        """A reader checked the arithmetic and it did not work: the page said the model gets 98.5% right and
        that a second axis would add 0.2%, leaving 1.5% unaccounted for. The 0.2% was a ratio of singular
        values from the residual matrix — a fine diagnostic, and not the thing the sentence claimed. It is
        measured in votes now, so the three numbers on the page are in one currency and can be added."""
        for cyc, r in self.ip.items():
            self.assertLessEqual(r["correct"] + r["second_dimension"], 1.0 + 1e-9,
                                 f"cycle {cyc}: the model and a second axis together call more than every vote")
            self.assertGreaterEqual(r["correct"], r["null"] - 1e-9,
                                    f"cycle {cyc}: the model is worse than guessing the majority")
            self.assertGreaterEqual(r["second_dimension"], 0.0)

    def test_a_cycle_with_no_named_record_gets_no_axis(self):
        """Cycle 34 has no roll call naming members and cycle 35 has one. An empty chart would imply the
        estimate was made and came out flat."""
        from scripts.build_site import load_inputs, build_cohesion_page
        import karzat.analytics as analytics
        for cyc in (34, 35):
            self.assertNotIn(str(cyc), self.ip, f"cycle {cyc} has positions it cannot have")
            inp = load_inputs(cyc)
            html = build_cohesion_page(inp, analytics.cohesion(inp))
            self.assertIn("nem becsülhető", html)
            self.assertIn("lefedettseg/index.html", html, "the empty state does not point at the coverage page")

    def test_the_page_never_invites_a_comparison_between_cycles(self):
        """The positions are estimated per cycle from votes the other cycles never saw, so +1.2 here and +1.2
        there are different places. The claim is easy to make by accident and impossible to see once made."""
        from scripts.build_site import load_inputs, build_cohesion_page
        import karzat.analytics as analytics
        inp = load_inputs(42)
        html = build_cohesion_page(inp, analytics.cohesion(inp))
        self.assertIn('data-axis="scale"', html, "the chart has no note about what its numbers mean")
        cap = html[html.index('data-axis="scale"'):]
        cap = cap[:cap.index("</figcaption>")]
        self.assertIn("csak ezen az egy rajzon belül", cap,
                      "the caption does not say the numbers are confined to this one chart")

    def test_every_position_carries_its_uncertainty(self):
        """A member with forty votes and one with four thousand are not known to the same accuracy, and a
        chart drawing them as identical dots says they are."""
        for cyc, r in self.ip.items():
            for azon, v in r["positions"].items():
                self.assertIn("sd", v, f"cycle {cyc} {azon}: no interval")
                self.assertGreaterEqual(v["sd"], 0.0)
                self.assertGreaterEqual(v["votes"], 20, "a position estimated from too few votes was kept")


class UmbrellaPages(unittest.TestCase):
    """Six pages answer over all ten cycles, and were built with the cycle pages' machinery.

    They inherited its identity with it: the breadcrumb read "43. ciklus / riport" and the cycle strip marked 43
    as where the reader was. Both were false — the Riport queries 1990 to 2026, the coverage page draws the edge
    of the whole record, the ledger counts switches across ten cycles. A reader who saw the trail would fairly
    conclude only the current cycle was in there.

    The same inheritance also put them one directory too deep: `../../assets/karzat.js` from `site/riport/`.
    Browsers clamp `..` at the root so nothing broke, which is exactly why it survived — it would have broken
    the day the site were served from a subpath."""

    ROOTED = ("riport", "arcel", "lefedettseg", "frakciovaltas", "modszer", "kereses", "szemely")

    def page(self, name):
        p = ROOT / "site" / name / "index.html"
        if not p.exists():
            self.skipTest(f"{name}/ not built")
        return p

    def test_none_of_them_claims_to_be_inside_a_cycle(self):
        for name in self.ROOTED:
            h = self.page(name).read_text(encoding="utf-8")
            m = re.search(r'<nav aria-label="Útvonal">(.*?)</nav>', h, re.S)
            crumb = re.sub("<[^>]+>", " ", m.group(1)) if m else ""
            self.assertNotIn("ciklus", crumb, f"{name}/ says it is under a cycle: {crumb.strip()!r}")
            self.assertIsNone(re.search(r'aria-current="true">\d+</b>', h),
                              f"{name}/ marks a cycle as the one the reader is in")

    def test_a_career_page_does_not_date_itself_to_the_current_cycle(self):
        """A pályakép spans whatever cycles the person served. Gyimóthy Géza left the House in 1998 and his page
        said "43. ciklus" at the head of its trail, because it was built with the current cycle's machinery."""
        pages = sorted((ROOT / "site" / "szemely").glob("*.html"))
        if len(pages) < 20:
            self.skipTest("career pages not built")
        for p in pages[:40] + pages[-40:]:
            h = p.read_text(encoding="utf-8")
            m = re.search(r'<nav aria-label="Útvonal">(.*?)</nav>', h, re.S)
            self.assertNotIn("ciklus", re.sub("<[^>]+>", " ", m.group(1)) if m else "",
                             f"{p.name} dates itself to a cycle")

    def test_every_link_stays_inside_the_site(self):
        """`../..` from a page one level down leaves the tree. A browser clamps it and a subpath deploy does
        not, so the check is on the path rather than on whether a request happens to succeed."""
        site = (ROOT / "site").resolve()
        for name in self.ROOTED:
            p = self.page(name)
            for href in sorted(set(re.findall(r'(?:href|src)="([^"#?]+)"', p.read_text(encoding="utf-8")))):
                if href.startswith(("http", "mailto:", "data:", "/")):
                    continue
                t = (p.parent / href).resolve()
                self.assertIn(site, [t, *t.parents], f"{name}/index.html → {href} escapes the site root")
                self.assertTrue(t.exists(), f"{name}/index.html → {href} does not exist")


class ReportPage(unittest.TestCase):
    """The corpus queried in the reader's own browser. Two properties are the whole point of the design.

    Nothing may reach off-origin. This site sets no cookie, runs no analytics and calls no third party, so one
    `<script src="https://cdn…">` would quietly undo that for every visitor. The runtime is vendored and pinned;
    a test that only checked the page renders would not notice the day somebody reinstates the CDN one-liner.

    And every path must be absolute. The wasm is fetched by the worker rather than the page, so a relative path
    resolves against the wrong base — '../assets/…' became '/assets/assets/…' and 404ed, and the only symptom was
    a WebAssembly compile error with no mention of a URL."""

    @classmethod
    def setUpClass(cls):
        from scripts.build_site import build_report_page, build_assets
        cls.inp = load_inputs()
        if not (cls.inp.get("parquet") or {}).get("tables"):
            raise unittest.SkipTest("no parquet built — run: python3 -m scripts.derive_parquet")
        cls.html = build_report_page(cls.inp)
        cls.js = build_assets()["karzat.js"]

    def test_the_page_and_its_runtime_call_no_third_party(self):
        for m in re.findall(r'(?:src|href)="(https?://[^"]+)"', self.html):
            self.fail(f"the SQL page loads {m} from off-origin")
        self.assertNotIn("cdn.jsdelivr", self.js, "the loader reaches for a CDN")
        d = ROOT / "site" / "assets" / "duckdb"
        if not d.is_dir():
            self.skipTest("runtime not vendored — run: python3 -m scripts.fetch_duckdb")
        for f in d.glob("*.mjs"):
            self.assertIsNone(re.search(rb'from\s*["\']https://', f.read_bytes()),
                              f"{f.name} imports from off-origin at run time")

    def test_the_vendored_runtime_matches_its_lock(self):
        import hashlib
        lock = ROOT / "reference" / "duckdb-wasm.lock"
        d = ROOT / "site" / "assets" / "duckdb"
        if not lock.exists() or not d.is_dir():
            self.skipTest("runtime not vendored")
        want = {}
        for ln in lock.read_text(encoding="utf-8").splitlines():
            if ln.strip() and not ln.startswith("#"):
                h, name = ln.split(None, 1)          # the file is "<sha256>  <name>"
                want[name.strip()] = h
        for name, h in want.items():
            p = d / name.strip()
            self.assertTrue(p.exists(), f"{name} is missing from the vendored runtime")
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(), h,
                             f"{name} does not match the lock — executable code served to readers has changed")

    def test_the_loader_resolves_every_path_absolutely(self):
        block = self.js[self.js.index("var base = new URL"):]
        block = block[:block.index("say('kész")]
        for rel in re.findall(r"""(?:import|Worker|instantiate)\(\s*['"](\.\.?/[^'"]+)['"]""", block):
            self.fail(f"a relative path survives in the loader: {rel}")
        self.assertIn("new URL('../assets/duckdb/', location.href)", self.js)
        self.assertIn("new URL('../adatok/'", self.js)

    def test_nothing_from_a_query_reaches_innerHTML_unescaped(self):
        """A red-team pass found this after the page was built and before it shipped.

        Values were escaped and column names were not, so `SELECT 1 AS "<img src=x onerror=…>"` put a live
        element in the header and ran the handler. The reach is the reader's own — they typed the query, the
        origin holds no cookie and no session, and nothing here reads a query from the URL — but a known
        injection is not something to serve, and the fix is one call.

        The guard is on the shape rather than the symptom: every interpolation into innerHTML in the SQL loader
        must go through esc()."""
        # the guard used to stop at render()'s end, one statement short of the chart's own innerHTML calls:
        # those are static strings today, and a guard that cannot see them would not notice the day they are not
        block = self._sql_block()
        block = block[block.index("function render(tbl)"):]
        for raw in re.findall(r"innerHTML\s*=\s*([^;]+);", block):
            for interp in re.findall(r"\+\s*([A-Za-z_$][\w$.()\[\]]*)", raw):
                if interp.startswith(("esc(", "cols.map", "rows.join")):
                    continue
                self.fail(f"unescaped interpolation into innerHTML: {interp} in {raw[:70]}")
        self.assertIn("esc(c)", block, "column names are not escaped")
        self.assertIn("esc(v)", block, "values are not escaped")

    def test_a_runaway_query_can_be_stopped(self):
        """`SELECT count(*) FROM szavazatok a, szavazatok b` is a trillion-row cross join; the worker will sit on
        it until the tab is closed. Terminating the worker is the only reliable brake."""
        self.assertIn('id="stop"', self.html)
        self.assertIn("db.terminate()", self.js)

    def test_a_cancelled_boot_stays_cancelled(self):
        """`db` is assigned after `await import(...)`, so a stop clicked during the module download found
        `if (db)` false, terminated nothing, and let the boot run on: the page said "megszakítva — a következő
        futtatás újraindítja az adatbázist" and then rendered the answer to the query it had just disowned.
        Both halves of that sentence were false, which is the worst thing a status line can be.

        Terminating mid-instantiate leaves the library's own promise pending for ever, so the abandonment has
        to be ours: a generation counter the stop handler bumps and every await checks."""
        js = self._sql_block()
        self.assertIn("var gen = 0, booting = null;", js)
        self.assertIn("function stale(mine)", js)
        stop = js[js.index("if (stopBtn) stopBtn.addEventListener"):]
        stop = stop[:stop.index("});")]
        self.assertIn("gen++", stop, "the stop handler does not invalidate what is in flight")
        boot = js[js.index("  async function bootOnce("):js.index("  var TABLES =")]
        self.assertGreaterEqual(boot.count("stale(mine)"), 4,
                                "the boot does not check for abandonment after each await")
        self.assertIn("if (stale(mine)) { worker.terminate();", boot,
                      "a worker created after the stop is left running")

    def test_two_clicks_cannot_start_two_databases(self):
        """The old stop handler re-enabled the run button and nulled `conn`, so a second click sailed past the
        `if (conn)` guard and began a whole second boot — another Worker, another 35 MB instantiate — with the
        second assignment to `db` orphaning the first. One in-flight promise, shared."""
        js = self._sql_block()
        b = js[js.index("  async function boot(){"):js.index("  async function bootOnce(")]
        self.assertIn("if (booting) return booting;", b, "concurrent callers each start their own boot")
        self.assertIn("finally { booting = null; }", b, "a failed boot is never retried")

    def test_the_status_line_follows_the_run_that_just_finished(self):
        """say() was only ever called from inside boot(), and boot() short-circuits once `conn` exists. So one
        failed query pinned "hiba — a lekérdezés nem futott le" to the tag for the rest of the session, beside
        every correct answer that followed. On a page whose claim is that the figures are the record's own, a
        status line contradicting the table is the wrong thing to leave standing."""
        js = self._sql_block()
        h = js[js.index("  runBtn.addEventListener('click'"):]
        ok = h[:h.index("} catch (e) {")]
        self.assertIn("say('kész", ok, "a successful run does not clear a previous error")
        self.assertIn("__stopped", h, "an abandoned boot is reported to the reader as a failure")

    def test_the_chart_is_drawn_in_the_site_s_own_language(self):
        """A charting library would bring its own palette, type and grid and look like a guest on the page. The
        chart is hand-drawn SVG like the chamber and the day strip, so matching the design is structural."""
        for lib in ("chart.js", "d3.", "vega", "plotly", "echarts"):
            self.assertNotIn(lib, self.js.lower(), f"a charting library crept in: {lib}")
        self.assertIn("createElementNS('http://www.w3.org/2000/svg'", self.js)
        self.assertIn("chartbox", self.html)

    def _chart_fns(self):
        """The chart's decisions run under node rather than being grepped for.

        Every earlier defect on this page was one a grep would have passed: the substitution that ate three
        words left the source looking right, and the export test passed on a file that carried var(--c). These
        two functions decide what the reader is shown, so they are executed against fabricated rows."""
        import shutil
        if not shutil.which("node"):
            self.skipTest("node not available")
        js = self._sql_block()
        return (js[js.index("  function num(v){"):js.index("  function svgEl(")]
                + js[js.index("  function step(range){"):js.index("  function draw(){")])

    def _sql_block(self):
        """Three functions in the bundle are called step() and two are somebody else's. Every lookup here is
        scoped to the SQL loader first, because a test that reads the pager's step() would pass on anything."""
        start = self.js.index("// @sql-block")
        return self.js[start:self.js.index("})();", self.js.index("  function draw(){", start))]

    def test_every_exported_table_is_registered_by_the_loader(self):
        """The loader had the three table names written into it. Adding a fourth to the export would have left
        it unregistered and invisible from the page, with nothing failing anywhere — so the list now comes from
        the manifest the page is built from, and this checks that it still does."""
        import re as _re
        names = list((self.inp.get("parquet") or {}).get("tables") or {})
        m = _re.search(r'data-tables="([^"]*)"', self.html)
        self.assertIsNotNone(m, "the page does not carry its table list")
        self.assertEqual(m.group(1).split(","), names)
        js = self._sql_block()
        self.assertNotIn("['szavazasok','szavazatok','kepviselok']", js, "the loader hard-codes its tables again")
        for n in names:
            self.assertIn(n, self.html, f"{n} is exported but never named on the page")

    def test_no_vote_loses_a_motion_to_the_export(self):
        """`szavazasok.iromany` is the first motion of however many a vote names, and 10,960 of 79,829 name more
        than one — so for one vote in seven the single column was a quiet truncation. The link table carries all
        of them, and this asserts the two agree rather than trusting that they do."""
        import pyarrow.parquet as _pq
        d = ROOT / "site" / "adatok"
        if not (d / "szavazas_iromany.parquet").exists():
            self.skipTest("parquet not built")
        v = _pq.read_table(d / "szavazasok.parquet", columns=["ciklus", "szavazas_id", "iromany", "iromany_db"])
        li = _pq.read_table(d / "szavazas_iromany.parquet", columns=["ciklus", "szavazas_id", "sorszam", "iromany"])
        self.assertEqual(li.num_rows, sum(x.as_py() or 0 for x in v.column("iromany_db")),
                         "the link table and the per-vote count disagree")
        firsts = {(c.as_py(), t.as_py()): i.as_py()
                  for c, t, n, i in zip(li.column("ciklus"), li.column("szavazas_id"),
                                        li.column("sorszam"), li.column("iromany")) if n.as_py() == 1}
        bad = [(c.as_py(), t.as_py()) for c, t, i, n in
               zip(v.column("ciklus"), v.column("szavazas_id"), v.column("iromany"), v.column("iromany_db"))
               if n.as_py() and firsts.get((c.as_py(), t.as_py())) != i.as_py()]
        self.assertEqual(bad[:3], [], f"{len(bad)} votes disagree with their own first link row")
        self.assertGreater(sum(1 for n in v.column("iromany_db") if (n.as_py() or 0) > 1), 1000,
                           "no vote names more than one motion — the fixture is not the real corpus")

    def test_a_line_is_refused_when_the_x_axis_is_not_a_sequence(self):
        """A line between Fidesz and MSZP asserts a path from one to the other, and there is none — the order is
        whatever ORDER BY produced. The subtler case is the one that caught me: dates *sorted by margin* look
        like a timeline and are not one, so the check is on the sequence, not on the shape of the labels.

        `ORDER BY datum DESC` is an axis with a direction too, and calling it "names, not order" was its own
        small lie — the first version tested only for ascending."""
        import json, subprocess
        cases = {
            "faction names": [{"faction": "Fidesz", "n": 3}, {"faction": "MSZP", "n": 2}],
            "years ascending": [{"ev": "1990", "n": 3}, {"ev": "1991", "n": 2}, {"ev": "1992", "n": 9}],
            # two rows are monotonic in one direction whatever they hold, so an unsorted axis needs three:
            # this is the closest-votes query's real shape, dates ordered by margin rather than by time
            "dates out of order": [{"date": "2019-05-02", "n": 1}, {"date": "1998-11-03", "n": 2},
                                   {"date": "2007-03-19", "n": 3}],
            "dates descending": [{"date": "2026-08-11", "n": 1}, {"date": "2026-05-09", "n": 2},
                                 {"date": "1990-05-02", "n": 3}],
            "numbers ascending": [{"cycle": 41, "n": 5}, {"cycle": 42, "n": 7}, {"cycle": 43, "n": 6}],
            "a string column wins the label from a numeric one":
                [{"cycle": 41, "ev": "b", "n": 5}, {"cycle": 42, "ev": "a", "n": 7}],
        }
        prog = self._chart_fns() + "\nconst C = " + json.dumps(cases, ensure_ascii=False) + ";\n" + \
            "const o = {}; for (const k in C) o[k] = shape(C[k], Object.keys(C[k][0])).ordered;\n" + \
            "console.log(JSON.stringify(o));"
        got = json.loads(subprocess.run(["node", "-e", prog], capture_output=True, text=True,
                                        check=True).stdout)
        self.assertEqual(got, {"faction names": False, "years ascending": True,
                               "dates out of order": False, "dates descending": True,
                               "numbers ascending": True,
                               "a string column wins the label from a numeric one": False})

    def test_the_y_axis_lands_on_numbers_a_reader_can_hold(self):
        """The first version divided the range by four and printed the result: 0 / 1528 / 3057 / 4585 / 6113.
        Every value on it is correct and none of them is a number anybody reads off a chart."""
        import json, subprocess
        prog = self._chart_fns() + "\nconsole.log(JSON.stringify([1, 37, 6113, 7882237, 0.42].map(step)));"
        got = json.loads(subprocess.run(["node", "-e", prog], capture_output=True, text=True,
                                        check=True).stdout)
        for r, st in zip([1, 37, 6113, 7882237, 0.42], got):
            mant = st / 10 ** math.floor(math.log10(st))
            self.assertIn(round(mant, 6), (1.0, 2.0, 5.0), f"step {st} for range {r} is not 1/2/5×10ⁿ")
            self.assertLessEqual(st * 4, r * 2.5, f"step {st} makes the axis far taller than the data")

    def test_every_point_is_named_and_carries_its_faction_colour(self):
        """The shipped version labelled only the first and last point, so a scatter of sixteen parties named
        two of them — which the reader saw before I did. And where the rows are people, the colour comes from
        the query's own faction column rather than from a name that is in no palette."""
        js = self._sql_block()
        block = js[js.index("    } else {"):js.index("    fig.appendChild(svg);")]
        self.assertNotIn("[0, rows.length - 1]", block, "only the end points are labelled")
        self.assertIn("rows.forEach", block, "the labels are not drawn per row")
        fn = js[js.index("  function colourOf("):js.index("  function step(range){")]
        self.assertIn("sh.facCol", fn, "a faction column in the query does not colour the marks")

    def test_a_decimal_is_descaled_before_it_reaches_the_reader(self):
        """Arrow returns a DECIMAL as its unscaled integer, so 1.5 arrives as 15 and 0.0001 as 1, and the page
        printed them raw. DuckDB types a bare `1.5` as DECIMAL(2,1), so a reader met this without going looking.

        The first fix matched on the type's constructor name and did nothing at all: the bundle is minified and
        the class is called `ti`. The guard is on the Arrow type id, which survives minification, and on the
        comment that says why — a later reader deleting the magic number would reinstate the bug."""
        js = self._sql_block()
        fn = js[js.index("  function scales(tbl)"):js.index("  function render(tbl)")]
        self.assertIn("typeId === ARROW_DECIMAL", fn, "decimal columns are detected by something else")
        self.assertNotIn("constructor.name", fn, "the minified class name is being matched again")
        self.assertIn("ARROW_DECIMAL = 7", js)
        r = js[js.index("  function render(tbl)"):js.index("  var stopBtn")]
        self.assertIn("descale(row[c]", r, "the rendered value is not descaled")

    def test_the_row_count_shown_is_the_result_s_own(self):
        """The count printed was the render loop's counter, which stops at 501: not the rows the query returned,
        not the rows on screen, a third number that was neither, and the only one the reader saw."""
        js = self._sql_block()
        r = js[js.index("  function render(tbl)"):js.index("  var stopBtn")]
        self.assertIn("tbl.numRows", r, "the total is still counted by the loop")
        self.assertIn("total: total, shown:", r)
        h = js[js.index("var r = render(res);"):js.index("var r = render(res);") + 400]
        self.assertIn("r.total", h)
        self.assertIn("az első ' + r.shown + ' látszik", h, "a truncated table does not say so")

    def test_a_column_s_kind_is_judged_across_the_result(self):
        """shape() read row zero and decided. A LEFT JOIN puts a NULL in the first row as easily as anywhere, and
        the chart then vanished with "no numeric column" over a result full of numbers."""
        import json, subprocess
        cases = {
            "null in the first row": [{"f": "a", "n": None}, {"f": "b", "n": 7}, {"f": "c", "n": 12}],
            "all values null": [{"f": "a", "n": None}, {"f": "b", "n": None}],
        }
        prog = self._chart_fns() + "\nconst C = " + json.dumps(cases) + ";\n" + \
            "const o = {}; for (const k in C) { const s = shape(C[k], Object.keys(C[k][0]));\n" + \
            "  o[k] = s && s.valueCol; }\nconsole.log(JSON.stringify(o));"
        got = json.loads(subprocess.run(["node", "-e", prog], capture_output=True, text=True,
                                        check=True).stdout)
        self.assertEqual(got["null in the first row"], "n", "a NULL in row zero still hides the chart")
        self.assertIsNone(got["all values null"], "a column of nothing was charted as numbers")

    def test_a_dropped_view_comes_back(self):
        """`DROP VIEW szavazatok` is a legal thing for a reader to type in their own database, and it left the
        session answering "does not exist" to everything with no hint that reloading was the cure."""
        js = self._sql_block()
        self.assertIn("function droppedOne(", js)
        self.assertIn("await views(c)", js, "the views are never rebuilt after a catalog error")
        self.assertIn("a táblák visszaálltak", js, "the recovery is silent")

    def test_the_runtime_size_on_the_page_is_the_size_on_the_wire(self):
        """The page promised "~6 MB" for as long as it existed. That was the engine's gzipped size, and the
        engine was not served gzipped: CloudFront will not compress an object over 10 MB, so 35.7 MB crossed the
        wire. The number was right about the bytes and wrong about the download, which is the hardest kind of
        wrong to notice — the config said `Compress: True` and meant it.

        Two halves, so two assertions: the deploy pre-compresses what CloudFront will not, and the page counts
        what the deploy will send rather than carrying a figure somebody measured once."""
        import gzip as _gz
        d = ROOT / "site" / "assets" / "duckdb"
        if not (d / "duckdb-eh.wasm").exists():
            self.skipTest("runtime not vendored")
        from scripts.build_site import runtime_wire_bytes
        mb = runtime_wire_bytes() / 1e6
        self.assertIn(f"~{mb:.1f} MB".replace(".", ","), self.html,
                      "the page's figure is not the measured one")
        raw = (d / "duckdb-eh.wasm").stat().st_size
        self.assertGreater(raw, 10 * 1024 * 1024, "the engine no longer exceeds CloudFront's compression ceiling")
        dep = (ROOT / "scripts" / "deploy_aws.py").read_text(encoding="utf-8")
        self.assertIn("CF_COMPRESS_MAX", dep, "nothing pre-compresses what CloudFront declines to")
        self.assertIn('"ContentEncoding": "gzip"', dep)
        self.assertLess(len(_gz.compress((d / "duckdb-eh.wasm").read_bytes(), 9)), raw / 3,
                        "gzip no longer earns its place here")

    def test_no_count_in_the_page_s_prose_is_typed(self):
        """The lede said "három fájl" after the export grew to eight — the same failure the README gate exists to
        catch, inside the page itself. Every count on this page comes from the manifest."""
        n = len((self.inp.get("parquet") or {}).get("tables") or {})
        self.assertGreater(n, 3)
        for word in ("három fájl", "három tábla", "Három tábla"):
            self.assertNotIn(word, self.html, f"a typed count survives: {word}")
        self.assertIn(f"{n} fájlban van", self.html, "the lede does not count the files it has")

    def test_a_half_built_connection_is_never_published(self):
        """`conn` used to be assigned the moment db.connect() returned, before the extension load and the view
        registration. A failed INSTALL or a missing Parquet file therefore cached a half-built connection for
        the life of the page: boot() handed it back on every later call, no views existed, and every query
        answered "does not exist" until the reader reloaded — which nothing told them to do."""
        js = self._sql_block()
        b = js[js.index("  async function bootOnce("):js.index("  var TABLES =")]
        setup = b[b.index("db.connect()"):b.index("conn = c;")]
        self.assertNotIn("conn =", setup, "the connection is published before it is finished")
        self.assertIn("await views(c)", setup, "the views are registered on something other than the new connection")
        self.assertIn("catch (e)", setup, "a failure during setup leaves the engine half-built")

    def test_a_typo_does_not_masquerade_as_a_dropped_table(self):
        """DuckDB appends 'Did you mean "szavazatok"?' to a plain typo, and the recovery searched the whole
        message — so `FROM szavazatokk` announced "a táblák visszaállítása…" and rebuilt all eight views to fix
        a spelling mistake. Only the name the engine says is missing counts."""
        import json, shutil, subprocess
        if not shutil.which("node"):
            self.skipTest("node not available")
        js = self._sql_block()
        fn = js[js.index("  function droppedOne("):js.index("  // Everything that reaches innerHTML")]
        prog = ("var TABLES=['szavazasok','szavazatok','kepviselok'];\n" + fn +
                "\nconsole.log(JSON.stringify({" +
                "typo: droppedOne('Catalog Error: Table with name szavazatokk does not exist! Did you mean \"szavazatok\"?')," +
                "dropped: droppedOne('Catalog Error: Table with name szavazatok does not exist!')," +
                "unrelated: droppedOne('Binder Error: Referenced column \"x\" not found')}));")
        got = json.loads(subprocess.run(["node", "-e", prog], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(got, {"typo": False, "dropped": True, "unrelated": False})

    def test_the_page_lists_only_tables_that_are_actually_there(self):
        """The manifest is committed and `site/adatok/` is not, so a clone built a page advertising eight
        tables it did not have. The runtime got this check on the first day and the data never did."""
        from scripts.build_site import SITE_DIR
        for n in (self.inp.get("parquet") or {}).get("tables") or {}:
            listed = f'<td class="mono">{n}</td>' in self.html
            there = (SITE_DIR / "adatok" / f"{n}.parquet").exists()
            self.assertEqual(listed, there, f"{n}: listed={listed} but present={there}")

    def test_a_downloaded_chart_carries_its_colours(self):
        """The chart's colours are CSS variables, which mean nothing in a file opened elsewhere. The export has
        to resolve them from the live tree — computing them on the detached clone returns nothing, which is what
        the first version did: a valid SVG with var(--c) in it and no colour outside this page."""
        fn = self.js[self.js.index("function svgText()"):]
        fn = fn[:fn.index("function save(")]
        self.assertIn("getComputedStyle(src[i])", fn, "the export reads styles from the clone, not the original")
        self.assertIn("n.removeAttribute('class')", fn, "class attributes survive and would carry no styling")
        self.assertIn("xmlns", fn)

    def test_the_page_says_what_the_runtime_costs_before_it_is_downloaded(self):
        """This asserted the literal "6 MB" and so passed for months on a page whose figure was wrong by five
        times — a substring test agreeing with a typed number is two mistakes propping each other up. The size
        itself is checked against the wire in test_the_runtime_size_on_the_page_is_the_size_on_the_wire; what
        belongs here is that the reader is told before they wait, and that the cost is stated in the tag they
        will be looking at while it loads."""
        import re as _re
        tag = _re.search(r'id="sqlstate">([^<]*)', self.html)
        self.assertIsNotNone(tag, "the page has no state tag to warn in")
        self.assertRegex(tag.group(1), r"(~\d+,\d+ MB|nincs telepítve)",
                         "the tag names no download size before the reader waits for one")
        self.assertIn("első használatkor", tag.group(1))
        self.assertIn("Kiszolgáló nincs mögötte", self.html)
