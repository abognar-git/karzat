"""The Atom feeds: valid, deterministic, and the same numbers as the pages."""

import posixpath
import unittest
import xml.etree.ElementTree as ET

from karzat import feeds as fd
from karzat.export import dissents_csv
from karzat import analytics as an
from karzat.export import adatszotar
from scripts.build_site import HERO_TS, build_feed_page, build_index, build_mp_page, channel_events, dissent_events, feed_updated, feed_vote, load_inputs, pick_hero

NS = {"a": fd.ATOM_NS}


def parse(xml: str) -> ET.Element:
    return ET.fromstring(xml.encode("utf-8"))


class Units(unittest.TestCase):
    def test_slug_and_dates(self):
        self.assertEqual(fd.slug("Mi Hazánk"), "mi-hazank")
        self.assertEqual(fd.slug("Nemzetiségi képviselő"), "nemzetisegi-kepviselo")
        self.assertEqual(fd.slug("KDNP"), "kdnp")
        self.assertEqual(fd.rfc3339("2026.06.15.17:20:04"), "2026-06-15T17:20:04+02:00")   # summer time
        self.assertEqual(fd.rfc3339("2026.01.15.10:00:00"), "2026-01-15T10:00:00+01:00")   # winter time
        self.assertEqual(fd.rfc3339_iso("2026-08-18T18:56:12.989859+02:00", "x"), "2026-08-18T18:56:12+02:00")
        self.assertEqual(fd.rfc3339_iso(None, "x"), "x")
        self.assertEqual(fd.rfc3339_iso("garbage", "x"), "x")

    def test_atom_document_is_well_formed_and_escaped(self):
        vote = {"ts": "2026.06.15.17:20:04", "slug": "2026-06-15T17-20-04", "on_date": "2026-06-15", "time": "17:20", "iromany": "T/51",
                "title": 'A <b>"tizenhatodik" & más</b>', "igen": 135, "nem": 50, "tartozkodott": 6, "result": "Elfogadva",
                "rule_label": "⅔ összes", "needed": 133, "base": 199, "margin": 2, "kind": "dontes"}
        ds = [{"azon": "x001", "name": "Kis <Ő> Ödön", "faction": "Mi Hazánk", "position": "nem", "plurality": "igen"}]
        xml = fd.dissent_feed(43, [{"vote": vote, "dissents": ds}], "2026-08-18T18:56:12+02:00", "../", "", "feed/kulonvelemeny.xml", "feed/index.html")
        root = parse(xml)                                                     # well-formed despite <, &, " in the data
        self.assertEqual(root.tag, f"{{{fd.ATOM_NS}}}feed")
        self.assertIsNone(root.get("{http://www.w3.org/XML/1998/namespace}base"))   # links are relative to the feed file: no base needed
        e = root.find("a:entry", NS)
        self.assertEqual(e.find("a:id", NS).text, "urn:karzat:43:kulonvelemeny:2026-06-15T17-20-04")
        self.assertIn('"tizenhatodik" & más', e.find("a:title", NS).text)      # text nodes come back unescaped
        self.assertEqual(e.find("a:link", NS).get("href"), "../szavazas/2026-06-15T17-20-04.html")
        self.assertEqual(e.find("a:updated", NS).text, "2026-06-15T17:20:04+02:00")
        self.assertEqual([c.get("term") for c in e.findall("a:category", NS)], ["Mi Hazánk"])
        content = e.find("a:content", NS)
        self.assertEqual(content.get("type"), "html")
        self.assertIn('<a href="../kepviselo/x001.html">Kis &lt;Ő&gt; Ödön</a> (Mi Hazánk): nem, a frakció többsége igen', content.text)
        # required Atom pieces on the feed
        for tag in ("id", "title", "updated", "author"):
            self.assertIsNotNone(root.find(f"a:{tag}", NS), tag)
        self.assertEqual({l.get("rel") for l in root.findall("a:link", NS)}, {"self", "alternate"})
        # deterministic
        self.assertEqual(xml, fd.dissent_feed(43, [{"vote": vote, "dissents": ds}], "2026-08-18T18:56:12+02:00", "../", "", "feed/kulonvelemeny.xml", "feed/index.html"))
        # with an origin: absolute links
        xml2 = fd.dissent_feed(43, [{"vote": vote, "dissents": ds}], "2026-08-18T18:56:12+02:00", "https://example.org/karzat/", "ckl42/", "feed/kulonvelemeny.xml", "feed/index.html")
        r2 = parse(xml2)
        self.assertEqual(r2.find("a:entry/a:link", NS).get("href"), "https://example.org/karzat/ckl42/szavazas/2026-06-15T17-20-04.html")
        self.assertEqual(r2.find("a:link[@rel='self']", NS).get("href"), "https://example.org/karzat/ckl42/feed/kulonvelemeny.xml")
        # the same vote is a different entry in the cycle, a faction and an MP channel (RFC 4287: same id = same entry)
        f_xml = fd.dissent_feed(43, [{"vote": vote, "dissents": ds}], "2026-08-18T18:56:12+02:00", "../", "", "feed/frakcio-mi-hazank.xml", "feed/index.html", faction="Mi Hazánk")
        m_xml = fd.dissent_feed(43, [{"vote": vote, "dissents": ds}], "2026-08-18T18:56:12+02:00", "../", "", "kepviselo/x001.xml", "kepviselo/x001.html", mp={"azon": "x001", "name": "Kis Ödön"})
        self.assertEqual(parse(f_xml).find("a:entry/a:id", NS).text, "urn:karzat:43:kulonvelemeny:2026-06-15T17-20-04:frakcio:mi-hazank")
        self.assertEqual(parse(m_xml).find("a:entry/a:id", NS).text, "urn:karzat:43:kulonvelemeny:2026-06-15T17-20-04:kepviselo:x001")
        self.assertIn("frakciója (Mi Hazánk) többsége igen", parse(m_xml).find("a:entry/a:title", NS).text)   # no article to get wrong

    def test_a_feed_is_the_recent_past_but_never_less_than_the_last_sitting_days(self):
        d = lambda i: f"2026-06-{15 - i // 40:02d}"                              # 40 items per day, newest first
        items = [{"k": i, "d": d(i)} for i in range(400)]
        sel = fd.recent(items, lambda it: it["d"])
        self.assertGreaterEqual(len(sel), fd.MIN_ENTRIES)
        self.assertEqual(len({it["d"] for it in sel}), fd.RECENT_DAYS)                # five whole days …
        self.assertEqual(len(sel), 200)                                              # … = 200 items here, more than the minimum
        one_day = [{"k": i, "d": "2026-06-15"} for i in range(170)]                  # a marathon day: all of it
        self.assertEqual(len(fd.recent(one_day, lambda it: it["d"])), 170)
        huge = [{"k": i, "d": "2026-06-15"} for i in range(1000)]
        self.assertEqual(len(fd.recent(huge, lambda it: it["d"])), fd.MAX_ENTRIES)   # the ceiling
        few = [{"k": i, "d": d(i)} for i in range(30)]
        self.assertEqual(len(fd.recent(few, lambda it: it["d"])), 30)


class Cycle43(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inp = load_inputs()
        cls.events = dissent_events(cls.inp)

    def test_events_are_the_pages_dissents(self):
        # the sum over the feed's events equals the sum of every MP page's "frakció ellen"
        against = sum(rec["against"] for rec in self.inp["alignment"]["per_mp"].values())
        self.assertEqual(sum(len(e["dissents"]) for e in self.events), against)
        self.assertGreater(against, 0)
        # newest first, decisions only (a quorum check is presence), and every dissent really disagrees with a plurality
        ts = [e["vote"]["ts"] for e in self.events]
        self.assertEqual(ts, sorted(ts, reverse=True))
        for e in self.events:
            self.assertEqual(e["vote"]["kind"], "dontes")
            for d in e["dissents"]:
                self.assertIn(d["position"], ("igen", "nem", "tartozkodott"))
                self.assertNotEqual(d["position"], d["plurality"])
                self.assertIsNotNone(d["plurality"])
        # a011 (Ágh Péter): 7 on his page, 7 in the events
        self.assertEqual(sum(1 for e in self.events for d in e["dissents"] if d["azon"] == "a011"), 7)

    def test_feeds_and_page_agree_and_links_resolve(self):
        inp = self.inp
        updated = feed_updated(inp)
        self.assertRegex(updated, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")
        events = channel_events(self.events)
        self.assertEqual(events, self.events)                                        # no independents in cycle 43
        xml = fd.dissent_feed(43, events, updated, "../", "", "feed/kulonvelemeny.xml", "feed/index.html")
        root = parse(xml)
        ents = root.findall("a:entry", NS)
        self.assertEqual(len(ents), len(fd.recent(events, lambda e: e["vote"]["on_date"])))
        ids = [e.find("a:id", NS).text for e in ents]
        self.assertEqual(len(ids), len(set(ids)))
        # every entry link, resolved against the feed's own path (as a reader does), points at a vote page of the cycle
        slugs = {inp["by_ts"][t]["slug"] for t in inp["order"]}
        for e in ents:
            resolved = posixpath.normpath(posixpath.join("feed/", e.find("a:link", NS).get("href")))
            self.assertRegex(resolved, r"^szavazas/[^/]+\.html$")
            self.assertIn(resolved.split("/")[1][:-5], slugs)
        # one cycle down, the feed climbs two levels
        deep = parse(fd.dissent_feed(42, events[:3], updated, "../../", "ckl42/", "feed/kulonvelemeny.xml", "feed/index.html"))
        self.assertEqual(posixpath.normpath(posixpath.join("ckl42/feed/", deep.find("a:entry/a:link", NS).get("href"))), f'ckl42/szavazas/{events[0]["vote"]["slug"]}.html')
        # a faction feed carries only that faction's dissenters, and as many as the cohesion page counts for it
        co = an.cohesion(inp)
        for f in ("Fidesz", "KDNP", "Mi Hazánk", "TISZA"):
            ev_f = [{"vote": e["vote"], "dissents": [d for d in e["dissents"] if d["faction"] == f]} for e in events if any(d["faction"] == f for d in e["dissents"])]
            self.assertEqual(sum(len(e["dissents"]) for e in ev_f), co["per_faction"][f]["dissenting_votes"], f)
            fx = parse(fd.dissent_feed(43, ev_f, updated, "../", "", f"feed/frakcio-{fd.slug(f)}.xml", "feed/index.html", faction=f))
            self.assertEqual({c.get("term") for c in fx.findall("a:entry/a:category", NS)}, {f})
        # the per-MP feed has as many entries as the page has dissents, and the page announces it
        mp_events = [{"vote": e["vote"], "dissents": [d]} for e in events for d in e["dissents"] if d["azon"] == "a011"]
        mp_xml = fd.dissent_feed(43, mp_events, updated, "../", "", "kepviselo/a011.xml", "kepviselo/a011.html", mp={"azon": "a011", "name": "Ágh Péter"})
        self.assertEqual(len(parse(mp_xml).findall("a:entry", NS)), 7)
        page = build_mp_page(inp, "a011")
        self.assertIn('<link rel="alternate" type="application/atom+xml" href="a011.xml"', page)
        self.assertIn('href="a011.xml" type="application/atom+xml"', page)
        # the index announces the cycle feeds; the feed page counts what the feed carries
        idx = build_index(inp, pick_hero(inp))
        self.assertIn('href="feed/kulonvelemeny.xml"', idx)
        self.assertIn('href="feed/index.html">értesítések</a>', idx)
        fac_counts = {}
        for e in self.events:
            for d in e["dissents"]:
                fac_counts[d["faction"]] = fac_counts.get(d["faction"], 0) + 1
        fpage = build_feed_page(inp, events, fac_counts)
        self.assertIn(f"{len(events)} szavazás · {sum(fac_counts.values())} különvélemény", fpage)
        self.assertIn('href="frakcio-mi-hazank.xml"', fpage)
        self.assertIn("Döntetlennél nincs többség", fpage)
        self.assertIn("a szinkronnal frissülnek", fpage)                             # an open cycle
        self.assertNotIn("xml:base", fpage)                                          # nothing for developers on the page
        # the votes feed: newest first, the recent window, secret ballots marked, and it counts the dissents of each vote
        votes = [dict(feed_vote(inp, ts), n_dissent=0, secret=inp["by_ts"][ts].get("secret")) for ts in reversed(inp["order"])]
        vroot = parse(fd.votes_feed(43, votes, updated, "../", "", "feed/szavazasok.xml"))
        vents = vroot.findall("a:entry", NS)
        self.assertEqual(len(vents), len(fd.recent(votes, lambda v: v["on_date"])))
        self.assertEqual(vents[0].find("a:updated", NS).text, fd.rfc3339(inp["order"][-1]))
        self.assertIn("A ciklus minden szavazása", vroot.find("a:subtitle", NS).text)
        secret_titles = [e.find("a:title", NS).text for e in vents if "titkos szavazás" in (e.find("a:title", NS).text or "")]
        self.assertEqual(len(secret_titles), sum(1 for v in fd.recent(votes, lambda v: v["on_date"]) if v.get("secret")))
        # the CSV is the complete record, and its columns are the data dictionary's
        csv = dissents_csv(self.events, 43)
        self.assertTrue(csv.startswith("\ufeff"))
        self.assertEqual(len(csv.splitlines()) - 1, sum(len(e["dissents"]) for e in self.events))
        cols = csv.splitlines()[0].lstrip("\ufeff").split(",")
        doc = " ".join(c for title, pairs in adatszotar() if title.startswith("kulonvelemenyek.csv") for c, _ in pairs)
        for c in cols:
            self.assertIn(c, doc, c)
        # nothing depends on the clock
        self.assertEqual(xml, fd.dissent_feed(43, events, updated, "../", "", "feed/kulonvelemeny.xml", "feed/index.html"))


if __name__ == "__main__":
    unittest.main()
