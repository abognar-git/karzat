"""Speeches and the MP digest: the parser on a real day, the substantive/procedural rule against the record,
the sitting weeks and their numbers, the pages and the weekly channels."""

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from karzat import feeds as fd
from karzat.export import adatszotar, speeches_csv, weekly_csv
from karzat.normalise import SUBSTANTIVE_KINDS, parse_felszolalas, parse_felszolalasok, parse_kepviselo, parse_szoszolok, parse_ulesnap
from karzat.xmlutil import parse_xml, to_dict
from scripts.build_site import (CAST, build_day_page, build_kepviselom_page, build_mp_page, build_speech_page, build_speech_search_page, build_speeches_page,
                                committees_in_cycle, cycle_weekly_feed, digest_line, fold_tokens, hu_span, load_inputs, mp_weekly_feed, sitting_weeks,
                                speech_id, speech_search_index, speeches_by_mp, speeches_feed, substantive_rows, week_digests)

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parent.parent
NS = {"a": fd.ATOM_NS}


def payload(path: Path) -> dict:
    root = parse_xml(path.read_bytes())
    return {root.tag: to_dict(root)}


class Parser(unittest.TestCase):
    def test_a_real_day_parses_in_order_with_its_agenda_items(self):
        day = parse_felszolalasok(payload(FIXTURES / "real_felszolalasok_ckl43_nap0002.xml"))
        self.assertEqual((day["ulnap"], day["date"]), (2, "2026-05-12"))
        sp = day["speeches"]
        self.assertEqual(len(sp), 9)
        self.assertEqual([x["order"] for x in sp], list(range(1, 10)))                # one running order over the agenda items
        self.assertEqual(sp[0]["event"], "Ülésnap megnyitása")
        self.assertEqual((sp[0]["name"], sp[0]["faction"]), ("Forsthoffer Ágnes", "TISZA"))
        self.assertEqual(sp[0]["kind"], "Az ülésnap megnyitása")
        self.assertTrue(sp[0]["technical"])
        self.assertEqual(sp[0]["duration_s"], 4 * 60 + 4)
        self.assertEqual(sp[2]["kind"], "jegyzői ismertetés")
        self.assertTrue(all(x["speaker_label"] for x in sp))

    def test_the_rows_own_date_wins_over_a_header_one_day_early(self):
        # the old cycles' list headers print the day one day early (cycle 36's first list: 1998.06.17 for the constituent
        # sitting of 18 June) while every row's <felszkezdete> carries the right day — the rows win, the header is kept
        def day(header, start):
            return {"felszolalasok": {"@ulesnap": "1", "@datum": header, "esemeny": [{"#text": "Ülésnap megnyitása", "felszolalas": [
                {"sorszam": "1", "felszolalo": "Dr. Varga László (Fidesz)", "felsztip": "ülésvezetés", "kormbiz": "", "felszkezdete": start, "videoido": ""},
                {"sorszam": "2", "felszolalo": "Gyürk András (Fidesz)", "felsztip": "felszólalás", "kormbiz": "", "felszkezdete": start, "videoido": "3:10"}]}]}}
        d = parse_felszolalasok(day("1998.06.17.", "1998.06.18."))
        self.assertEqual((d["date"], d["date_listed"], d["date_corrected"]), ("1998-06-18", "1998-06-17", True))
        d = parse_felszolalasok(day("2026.05.12.", "2026.05.12."))
        self.assertEqual((d["date"], d["date_corrected"]), ("2026-05-12", False))
        d = parse_felszolalasok(day("2026.05.12.", "2026.05.20."))                          # not a one-day shift: the header stands
        self.assertEqual((d["date"], d["date_corrected"]), ("2026-05-12", False))

    def test_the_substantive_rule_is_named_and_the_record_agrees(self):
        # the rule is a list of kinds; a few that must be on each side
        for k in ("felszólalás", "kétperces felszólalás", "vezérszónoki felszólalás", "kérdés megválaszolva", "ügyrendi javaslat",
                  "expozé", "önálló indítvány indokolása"):                                    # the 1998–2010 lists' words for a speech
            self.assertIn(k, SUBSTANTIVE_KINDS)
        for k in ("ülésvezetés", "jegyzői ismertetés", "az ülésnap megnyitása", "önálló indítvány elfogadva", "személyes érintettség miatti felszólalás"):
            self.assertNotIn(k, SUBSTANTIVE_KINDS)
        # against the record: the derived file carries the check — the vast majority of MPs agree exactly
        inp = load_inputs()
        chk = inp["speeches"]["record_check"]
        self.assertGreaterEqual(chk["agree"] / chk["mps"], 0.95, chk)
        self.assertLessEqual(max(abs(v["ours"] - v["record"]) for v in chk["detail"].values()), 2)   # the rest by one or two

    def test_record_carries_committees_and_speech_stats(self):
        rec = parse_kepviselo(payload(FIXTURES / "real_kepviselo_a011_trimmed.xml"))
        self.assertIn("committees", rec)
        self.assertIn("speech_stats", rec)
        # the untrimmed record (data/raw) carries both; the trimmed fixture may not — the shape is what is pinned here
        self.assertIsInstance(rec["committees"], list)
        self.assertIsInstance(rec["speech_stats"], list)

    def test_ulesnap_dates_are_corrected_by_their_weekday(self):
        # the API's older cycles print the date one day early against their own weekday name
        xml = b'<ulesnapok><ulesnap><datum>1998.06.17.</datum><ulnap>1</ulnap><nap>cs\xc3\xbct\xc3\xb6rt\xc3\xb6k</nap></ulesnap><ulesnap><datum>2026.05.09.</datum><ulnap>1</ulnap><nap>szombat</nap></ulesnap></ulesnapok>'
        days = parse_ulesnap(payload_bytes(xml))
        self.assertEqual([(d["date"], d["date_corrected"]) for d in days], [("1998-06-18", True), ("2026-05-09", False)])
        # a whole file that is shifted takes its two or three weekday slips with it (an unshifted twin would land on its neighbour)
        many = b"<ulesnapok>" + b"".join(f'<ulesnap><datum>1999.06.{d:02d}.</datum><ulnap>{i+1}</ulnap><nap>{w}</nap></ulesnap>'.encode() for i, (d, w) in enumerate([(7, "kedd"), (8, "szerda"), (9, "csütörtök"), (10, "péntek"), (13, "hétfő"), (14, "hétfő"), (15, "szerda"), (16, "csütörtök"), (17, "péntek"), (20, "hétfő")])) + b"</ulesnapok>"
        # 1999-06-07 was a Monday: every row says the next day, except row 6 (06.14 'hétfő' — the 14th was a Monday)
        ds = parse_ulesnap(payload_bytes(many))
        self.assertEqual([d["date"] for d in ds], ["1999-06-08", "1999-06-09", "1999-06-10", "1999-06-11", "1999-06-14", "1999-06-15", "1999-06-16", "1999-06-17", "1999-06-18", "1999-06-21"])
        self.assertEqual(sum(1 for d in ds if d["weekday_conflict"]), 1)                       # the slip is flagged, not silently trusted


class Texts(unittest.TestCase):
    def test_a_real_speech_text_parses_with_its_mp_id(self):
        d = parse_felszolalas((FIXTURES / "real_felszolalas_ckl43_nap0006_f0002.xml").read_bytes())
        self.assertEqual((d["ulnap"], d["date"], d["seq"]), (6, "2026-06-08", 2))
        self.assertEqual((d["speaker"], d["azon"], d["position"], d["kind"]), ("Magyar Péter", "m076", "miniszterelnök", "napirend előtti felszólalás"))
        self.assertEqual(d["duration_s"], 25 * 60 + 40)
        self.assertEqual(len(d["paragraphs"]), 32)
        self.assertTrue(d["paragraphs"][0].startswith("MAGYAR PÉTER miniszterelnök: Házelnök Asszony!"))
        self.assertNotIn("<", " ".join(d["paragraphs"]))                                        # markup gone, words kept
        self.assertEqual(d["chars"], sum(len(x) for x in d["paragraphs"]))

    def test_tokens_fold_like_the_page_script(self):
        self.assertEqual(fold_tokens("Az OKTATÁSÜGY, a MÁV és a gyermekvédelem — 2026-ban!"), ["oktatasugy", "mav", "gyermekvedelem", "2026", "ban"])
        self.assertEqual(fold_tokens("ő ű ö ü á é í ó ú"), [])                                  # single letters are not tokens

    def test_pages_index_and_channel(self):
        inp = load_inputs()
        subs = substantive_rows(inp)
        self.assertEqual(len(subs), inp["speeches"]["substantive"])
        self.assertTrue(all(not r["technical"] and not r.get("reprint") for r in subs))
        meta, shards = speech_search_index(inp)
        self.assertEqual(len(meta), len(subs))
        # Sharded by the first two letters, and a shard over the size limit is split by the third — which
        # cycle 43 did not do until the postings grew a frequency beside each document. The assertion used to
        # be that every key is two letters, so the split broke it; the contract is that a key is two letters
        # or three, and that a two-letter key holding a split marker holds nothing else.
        self.assertTrue(all(len(k) in (2, 3) for k in shards), f"odd shard keys: {[k for k in shards if len(k) not in (2, 3)]}")
        for key, toks in shards.items():
            if "__split" in toks:
                self.assertEqual(len(key), 2, "a three-letter shard cannot be split again")
                self.assertEqual(set(toks), {"__split"}, f"{key} is both split and holds tokens")

        def postings(tok):
            """Follow the shards the way the page's script does, and read the pairs as pairs.

            `assertIn(i, shards[tok[:2]][tok])` was the old check and it could pass for the wrong reason: in a
            flat [doc, tf, doc, tf, …] list a document number equal to some other posting's frequency reads as
            present. It also could not see a token that had moved into a three-letter shard."""
            sh = shards.get(tok[:2]) or {}
            if "__split" in sh:
                sh = shards.get(tok[:3]) or {}
            arr = sh.get(tok)
            self.assertIsNotNone(arr, f"{tok!r} is in no shard the script would look in")
            self.assertEqual(len(arr) % 2, 0, f"{tok!r}: a posting list of odd length is not pairs")
            return {arr[j]: arr[j + 1] for j in range(0, len(arr), 2)}

        texts = (inp["texts"] or {}).get("texts") or {}
        if texts:
            # every token of a fetched text points at its speech's position in the meta, with a real count
            sid, t = next(iter(texts.items()))
            i = next(k for k, m in enumerate(meta) if m[0] == sid)
            body = fold_tokens(" ".join(t["paragraphs"]))
            import collections as _c
            counted = _c.Counter(body)
            for tok in body[:20]:
                post = postings(tok)
                self.assertIn(i, post, f"{tok!r} does not point at the speech it came from")
                self.assertEqual(post[i], min(counted[tok], 255), f"{tok!r}: the frequency is not the count")
            page = build_speech_page(inp, next(r for r in subs if speech_id(r) == sid), t, None, None, None)
            self.assertIn('<div class="speech">', page)
            self.assertIn("A jegyzőkönyv szövege", page)
        r0 = subs[0]
        page = build_speech_page(inp, r0, None, None, subs[1], None)
        self.assertIn("A szöveg nincs betöltve." if speech_id(r0) not in texts else "Szöveg", page)
        self.assertIn(f'href="{speech_id(subs[1])}.html"', page)                                # next
        day = build_day_page(inp, r0["ulnap"], [r for r in inp["speeches"]["speeches"] if r["ulnap"] == r0["ulnap"]], texts)
        self.assertIn('data-rowf="data-sub:1"', day)
        self.assertIn('<tr class="grp">', day)
        srch = build_speech_search_page(inp, len(meta), len(texts))
        self.assertIn('id="spq"', srch); self.assertIn("<noscript>", srch)
        xml = speeches_feed(inp, "2026-08-18T18:56:12+02:00", "../")
        root = ET.fromstring(xml.encode("utf-8"))
        ents = root.findall("a:entry", NS)
        self.assertLessEqual(len(ents), 200)
        if ents:
            self.assertIn("<p>", ents[0].find("a:content", NS).text)                              # the full text travels in the channel
            self.assertRegex(ents[0].find("a:updated", NS).text, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+0[12]:00$")
        self.assertEqual(xml, speeches_feed(inp, "2026-08-18T18:56:12+02:00", "../"))         # deterministic


def payload_bytes(raw: bytes) -> dict:
    root = parse_xml(raw)
    return {root.tag: to_dict(root)}


class Digest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inp = load_inputs()
        cls.weeks = sitting_weeks(cls.inp)
        cls.dg = week_digests(cls.inp)

    def test_weeks_cover_every_sitting_day_and_add_up(self):
        days = {d["date"] for d in self.inp["idx"]["sitting_days"]}
        self.assertEqual(sum(len(w["days"]) for w in self.weeks), len(days))
        self.assertEqual([w["key"] for w in self.weeks], sorted((w["key"] for w in self.weeks), reverse=True))    # newest first
        self.assertEqual(hu_span("2026-08-10", "2026-08-11"), "2026. augusztus 10–11.")
        self.assertEqual(hu_span("2026-07-27", "2026-08-02"), "2026. július 27. – augusztus 2.")
        self.assertEqual(hu_span("2026-06-01", "2026-06-01"), "2026. június 1.")
        # per-MP numbers over the weeks add up to the cycle record
        per_mp = self.inp["alignment"]["per_mp"]
        for azon in ("a011", "0026"):
            rec = per_mp[azon]
            self.assertEqual(sum((d["mps"].get(azon) or {}).get("in_roll", 0) for d in self.dg.values()), rec["in_roll"])
            self.assertEqual(sum((d["mps"].get(azon) or {}).get("cast", 0) for d in self.dg.values()), rec["cast"])
            self.assertEqual(sum((d["mps"].get(azon) or {}).get("against", 0) for d in self.dg.values()), rec["against"])
            self.assertEqual(sum(len((d["mps"].get(azon) or {}).get("speeches") or []) for d in self.dg.values()),
                             sum(1 for r in speeches_by_mp(self.inp).get(azon, []) if not r["technical"]))
        # cycle totals
        self.assertEqual(sum(d["cycle"]["roll_calls"] for d in self.dg.values()), sum(1 for ts in self.inp["order"] if self.inp["store"]["positions"].get(ts)))
        self.assertEqual(sum(d["cycle"]["dissents"] for d in self.dg.values()), sum(r["against"] for r in per_mp.values()))
        self.assertEqual(sum(d["cycle"]["speeches"] for d in self.dg.values()), self.inp["speeches"]["substantive"])   # every substantive speech, non-MPs included
        # the digest line is label form: no suffix on a numeral
        self.assertEqual(digest_line({"in_roll": 12, "cast": 11, "against": 1, "speeches": [1, 2]}, 14), "névsorban 12 / 14 · leadott 11 · frakciója ellen 1 · felszólalás 2")
        self.assertEqual(digest_line(None, 14), "névsorban 0 / 14 · felszólalás 0")
        self.assertEqual(digest_line({"in_roll": 3, "cast": 3, "against": 0, "independent": True, "speeches": []}, 5), "névsorban 3 / 5 · leadott 3 · frakciója ellen — · felszólalás 0")   # no faction: no dissent number
        self.assertEqual(digest_line({"in_roll": 3, "cast": 3, "against": 0, "speeches": []}, 5, speeches_known=False), "névsorban 3 / 5 · leadott 3 · frakciója ellen 0 · felszólalás —")   # lists not loaded for the week

    def test_mp_page_has_the_digest_speeches_and_committees(self):
        page = build_mp_page(self.inp, "0026")
        self.assertIn("Hétről hétre", page)
        self.assertIn('href="0026-heti.xml" type="application/atom+xml"', page)
        self.assertIn('<link rel="alternate" type="application/atom+xml" href="0026-heti.xml"', page)
        self.assertIn("Érdemi felszólalásai", page)
        self.assertIn("Bizottságok", page)
        self.assertIn("Törvényalkotási Bizottság", page)
        self.assertIn('data-filter-table="speeches"', page)
        # committees are filtered to the cycle
        coms = committees_in_cycle(self.inp["mps"]["a011"], 43)
        self.assertTrue(all((c["from"] or "") >= "2026-05-09" or c["to"] is None or c["to"] >= "2026-05-09" for c in coms))
        self.assertTrue(any(c["committee"].startswith("Pénzügyi") for c in coms))
        self.assertFalse(any((c["to"] or "9999") < "2026-05-09" for c in coms))

    def test_weekly_feeds_are_valid_and_count_the_weeks(self):
        updated = "2026-08-18T18:56:12+02:00"
        xml = mp_weekly_feed(self.inp, "0026", updated, "../")
        root = ET.fromstring(xml.encode("utf-8"))
        ents = root.findall("a:entry", NS)
        self.assertEqual(len(ents), len(self.weeks))                                        # 13 sitting weeks so far, all within the window
        self.assertEqual(ents[0].find("a:updated", NS).text, updated)                       # the newest week may still grow: the sync stamp
        self.assertRegex(ents[1].find("a:updated", NS).text, r"T23:59:59\+0[12]:00$")
        self.assertIn("névsorban", ents[0].find("a:title", NS).text)
        self.assertEqual(ents[0].find("a:link", NS).get("href"), "../ckl43/kepviselo/0026.html")      # root + the cycle's own directory
        ids = [e.find("a:id", NS).text for e in ents]
        self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(xml, mp_weekly_feed(self.inp, "0026", updated, "../"))            # deterministic
        cx = ET.fromstring(cycle_weekly_feed(self.inp, updated, "../").encode("utf-8"))
        self.assertEqual(len(cx.findall("a:entry", NS)), len(self.weeks))
        self.assertIn("különvélemény", cx.find("a:entry/a:title", NS).text)

    def test_pages_and_exports(self):
        k = build_kepviselom_page(self.inp)
        # 106 constituencies is fixed by law; the list seats are whoever holds one today, and that moves —
        # the roster lost a member on 2026-08-27 and this assertion, pinned at 93, refused the publish.
        self.assertEqual(k.count("OEVK</td>"), 106)
        def _con(m):                      # the seat this cycle, from the member's own dated election rows
            return next((e.get("constituency") or "" for e in (m.get("elections") or [])
                         if (e.get("ciklus") or "").startswith("2026")), "")
        n_list = sum(1 for m in self.inp["mps"].values()
                     if m.get("current") and "lista" in _con(m).lower())
        self.assertEqual(k.count("országos lista</td>"), n_list)
        self.assertIn('id="town"', k)                                                        # the town box (the annex is loaded)
        s = build_speeches_page(self.inp)
        self.assertIn("4 651 sor", s)
        self.assertIn("érdemi", s)
        csv = speeches_csv(self.inp["speeches"]["speeches"], 43)
        self.assertEqual(len(csv.splitlines()) - 1, 4651)
        cols = csv.splitlines()[0].lstrip("﻿").split(",")
        doc = " ".join(c for title, pairs in adatszotar() if title.startswith("felszolalasok.csv") for c, _ in pairs)
        for c in cols:
            self.assertIn(c, doc, c)
        w = weekly_csv(self.weeks, self.dg, self.inp["mps"], 43)
        self.assertEqual(len(w.splitlines()) - 1, len(self.weeks) * len(self.inp["mps"]))
        wcols = w.splitlines()[0].lstrip("﻿").split(",")
        wdoc = " ".join(c for title, pairs in adatszotar() if title.startswith("heti.csv") for c, _ in pairs)
        for c in wcols:
            self.assertIn(c, wdoc, c)


if __name__ == "__main__":
    unittest.main()


class LooseEnds(unittest.TestCase):
    """Portraits, committees, the district annex, the citations."""

    def test_portraits_are_our_own_copy_black_and_white_and_attributed(self):
        """The picture used to be linked where it lives; it is now a grey WebP of ours, the size it is drawn at.
        What has not changed: lazy, no referrer, attributed in the title, and nothing at all when there is no
        picture.

        What has: a person the manifest does not carry no longer falls back to the record's own URL. That
        fallback put `<img src="https://www.parlament.hu/…">` on 59 pages, for 22 people — and 21 of those
        URLs answer 404, so a reader's browser called a third party in order to be told nothing and `onerror`
        removed the empty image before anyone noticed. The site's standing claim is that a visit is known to
        nobody but its own log. Our copy, or no picture."""
        from scripts.build_site import PHOTO_BASE, PORTRAITS, build_assets, build_mp_index, portrait_html
        inp = load_inputs()
        h = portrait_html(inp["mps"]["a011"], "hero")
        self.assertIn("a011", PORTRAITS, "the fixture MP needs a local portrait for this to test anything")
        self.assertIn('src="/assets/portre/a011.webp"', h)
        self.assertIn('width="192" height="256"', h)
        # somebody with a photo_url the manifest does not carry gets nothing, not a hotlink
        self.assertEqual(portrait_html({"p_azon": "zzz", "name": "x", "photo_url": PHOTO_BASE + "zzz"}), "")
        self.assertIn('referrerpolicy="no-referrer"', h)
        self.assertIn('loading="lazy"', h)
        self.assertIn('title="fénykép: parlament.hu"', h)
        self.assertEqual(portrait_html({"name": "x"}), "")                              # no photo, no tag
        css = build_assets()["karzat.css"]
        self.assertIn(".portrait{filter:grayscale(1)", css)                             # still grey for remote ones
        page = build_mp_page(inp, "a011")
        self.assertIn('class="portrait hero"', page)
        have = sum(1 for az in inp["mps"] if az in PORTRAITS)
        self.assertEqual(build_mp_index(inp).count('class="portrait thumb"'), have)
        self.assertGreater(have, len(inp["mps"]) * 0.95, "the portrait cache has gone stale")
        self.assertNotIn(PHOTO_BASE, build_mp_index(inp))                               # no third party, ever
        self.assertIn("PHOTO_BASE + esc(az) + PHOTO_EXT", build_assets()["karzat.js"])  # the inspector too

    def test_committees_from_records_and_the_api(self):
        from scripts.build_site import build_committee_index, build_committee_page, committee_roster, committees_feed
        inp = load_inputs()
        cm = committee_roster(inp)
        self.assertEqual(len(cm), inp["committees"]["count"])                              # every API committee, subcommittees under their parent
        self.assertTrue(all(e["api"] for e in cm.values()))
        agr = next(e for e in cm.values() if e["name"].startswith("Agrár"))
        self.assertEqual(agr["api"]["chair"]["azon"], "0038")
        self.assertTrue(any(m["azon"] == "0038" and m["role"] == "elnök" for m in agr["members"]))   # the record's history agrees
        idx = build_committee_index(inp)
        self.assertIn("29" if False else "26 bizottság", idx)
        page = build_committee_page(inp, "agrar-es-elelmiszergazdasagi-bizottsag", agr)
        self.assertIn("Mai összetétel", page)
        self.assertIn("Kitűzött ülés az API szerint most nincs.", page)
        xml = committees_feed(inp, "2026-08-18T18:56:12+02:00", "../")
        root = ET.fromstring(xml.encode("utf-8"))
        self.assertEqual(len(root.findall("a:entry", NS)), sum(1 for e in cm.values() if (e["api"] or {}).get("next_meeting")))
        # a closed cycle: from the records alone
        inp42 = load_inputs(42)
        cm42 = committee_roster(inp42)
        self.assertGreater(len(cm42), 10)
        self.assertTrue(all(e["api"] is None for e in cm42.values()))

    def test_district_annex_and_the_town_box(self):
        from scripts.build_site import build_kepviselom_page, oevk_settlements
        d = oevk_settlements()
        self.assertEqual(len(d["oevk"]), 106)
        self.assertEqual({o["county"] for o in d["oevk"]}, set(d["counties"]))
        self.assertEqual(len(d["counties"]), 20)
        self.assertEqual(d["settlements"]["Szombathely"], [{"county": "Vas", "no": 1, "partial": False}])
        self.assertEqual(sorted(x["no"] for x in d["settlements"]["Budapest XI. kerület"]), [1, 3, 9, 10])
        self.assertTrue(all(x["partial"] for x in d["settlements"]["Budapest XI. kerület"]))
        # every OEVK of the annex has an MP in the current cycle
        inp = load_inputs()
        from scripts.build_site import county_name
        have = {(county_name(m["county"]), m["constituency_no"]) for m in inp["mps"].values() if m.get("mandate_kind") == "egyeni"}   # the API says "Csongrád", the annex "Csongrád-Csanád"
        self.assertTrue(all((o["county"], o["no"]) in have for o in d["oevk"]))
        page = build_kepviselom_page(inp)
        self.assertIn('id="town"', page)
        self.assertIn('id="oevk-map"', page)
        self.assertNotIn("Település szerint keresni még nem lehet", page)

    def test_citations_are_read_not_remembered(self):
        from karzat.majority import NO_BASIS, RULES, Rule
        # Keyed on the constant the page prints, not on a word inside it: the flag used to be the English
        # "(VERIFY)" — a note to myself that reached the reader in a table of Hungarian legal citations — and
        # translating it silently unhooked this test.
        flagged = [r for r, info in RULES.items() if any(NO_BASIS in b for b in info.basis)]
        self.assertEqual(flagged, [Rule.NEGYOTOD_OSSZES])                                  # the one with no basis found
        self.assertIn("HHSZ 62. § (1)", " ".join(RULES[Rule.ABSZOLUT].basis))
        self.assertIn("HHSZ 60. § (7)", " ".join(RULES[Rule.KETHARMAD_JELENLEVO].basis))
        self.assertIn("HHSZ 65. § (1)", " ".join(RULES[Rule.NEGYOTOD_JELENLEVO].basis))


class ArchiveSpeeches(unittest.TestCase):
    """Cycles 36–41 have their speech lists but (for now) no texts: no page per speech there — the rows live on the
    day pages and every link points at the row; the live cycles keep a page per substantive speech."""

    def test_archive_rows_link_to_the_day_page_anchor_unless_the_text_is_loaded(self):
        from scripts.build_site import speech_has_page, speech_href
        r = {"ulnap": 12, "seq": 7, "date": "1999-03-02"}
        live = {"archive": False, "texts": None}
        arch = {"archive": True, "texts": None}
        self.assertTrue(speech_has_page(live, r))
        self.assertEqual(speech_href(live, r, "../felszolalas/"), "../felszolalas/12-7.html")
        self.assertFalse(speech_has_page(arch, r))
        self.assertEqual(speech_href(arch, r, "../felszolalas/"), "../felszolalas/nap12.html#s12-7")
        arch_t = {"archive": True, "texts": {"texts": {"12-7": {"paragraphs": ["x"]}}}}
        self.assertTrue(speech_has_page(arch_t, r))                                       # with the text, a page of its own

    def test_an_archive_day_page_carries_row_anchors_and_no_dead_links(self):
        from scripts.build_site import available_cycles, build_day_page
        cycles = [c for c in available_cycles() if c <= 41 and (ROOT / "data" / "derived" / f"speeches_ckl{c}.json.gz").exists()]
        if not cycles:
            self.skipTest("no archive cycle has its speech lists yet")
        inp = load_inputs(cycles[0])
        rows = [r for r in inp["speeches"]["speeches"] if r["ulnap"] == inp["speeches"]["speeches"][0]["ulnap"]]
        page = build_day_page(inp, rows[0]["ulnap"], rows, {})
        self.assertIn(f'id="s{rows[0]["ulnap"]}-', page)
        self.assertNotRegex(page, r'href="\d+-\d+\.html"')                              # no link to a page that is not built


class Postcodes(unittest.TestCase):
    """A postcode is the sharpest thing most people know about where they live — but it names a settlement, not an
    electoral district, and the finder has to keep saying so where the annex splits a city street by street."""

    def test_every_postcode_points_at_a_settlement_the_annex_lists(self):
        import json as _json
        from scripts.build_site import ROOT as _R, oevk_settlements, postcodes
        pc = postcodes()
        if not pc:
            self.skipTest("no postcode table")
        annex = oevk_settlements()["settlements"]
        for code, names in pc["map"].items():
            self.assertRegex(code, r"^\d{4}$")
            self.assertTrue(names)
            for n in names:
                self.assertIn(n, annex)                                       # never a name the annex does not know
        self.assertEqual(pc["codes"], len(pc["map"]))
        # Budapest by the rule the codes are built on, all 23 districts
        for d, code in ((1, "1011"), (5, "1054"), (11, "1114"), (23, "1239")):
            self.assertTrue(any("kerület" in n for n in pc["map"][code]), code)
        self.assertEqual(pc["map"]["1114"], ["Budapest XI. kerület"])
        self.assertEqual(pc["map"]["9021"], ["Győr"])
        self.assertGreater(len(pc["map"]), 2500)

    def test_the_finder_ships_the_table_and_handles_both_kinds_of_query(self):
        import json as _json
        from scripts.build_site import SITE_DIR, build_assets, build_kepviselom_page, postcodes
        inp = load_inputs()
        page = build_kepviselom_page(inp)
        self.assertIn("Irányítószám vagy település", page)
        self.assertIn('placeholder="pl. 1114 · 9021 · Szombathely · Budapest XI. kerület"', page)
        self.assertIn("nem a választókerületet", page)                        # the caveat is on the page, not only in the code
        f = SITE_DIR / "ckl43" / "kepviselom" / "iranyitoszam.json"
        if not f.exists():
            self.skipTest("site not built")
        shipped = _json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(set(shipped) <= set(postcodes()["map"]), True)
        for code, names in shipped.items():
            self.assertTrue(names)
        js = build_assets()["karzat.js"]
        self.assertIn("iranyitoszam.json", js)
        self.assertIn("Az irányítószám négy számjegy", js)                    # a three-digit number is answered, not ignored
        self.assertIn("közös posta", js)                                      # one code, several villages: all of them shown


class Spokespersons(unittest.TestCase):
    """The nationality spokespersons (nemzetiségi szószólók) sit in the chamber and speak, but cast no vote — so they
    are drawn on the landing chamber as rings and kept out of every roster count and alignment."""

    def test_the_list_parses_with_nationality_and_id(self):
        rows = parse_szoszolok(payload(FIXTURES / "real_szoszolok_trimmed.xml"))
        self.assertEqual([(r["p_azon"], r["name"], r["nationality"]) for r in rows],
                         [("004O", "Aba-Horváth István", "roma"), ("a024", "Akopjan Nikogosz", "örmény")])
        self.assertTrue(all(r["photo_url"].endswith(r["p_azon"]) for r in rows))
        self.assertEqual([r["order"] for r in rows], [1, 2])

    def test_they_are_on_the_chamber_but_in_no_roster(self):
        from scripts.build_site import build_assets, build_landing, hall_data, szoszolo_seats
        inp = load_inputs()
        sz = (inp["szoszolok"] or {}).get("people") or {}
        if not sz:
            self.skipTest("no spokesperson list derived")
        self.assertTrue(all(r["seat"] and r["seat"].get("sector") is not None for r in sz.values()))
        seats = szoszolo_seats(inp)
        self.assertEqual(len(seats), len(sz))                                   # one seat each, none shared
        self.assertFalse(set(sz) & set(inp["mps"]))                             # never in the MP roster
        self.assertFalse(set(sz) & set(inp["alignment"]["per_mp"]))             # never in an alignment
        self.assertFalse(set(sz) & set(inp["plan"]["coords"]))                  # the MP seat plan does not carry them
        page = build_landing()
        self.assertEqual(page.count('class="seat sz"'), len(sz))
        self.assertIn("nemzetiségi szószóló", page)
        data = hall_data(inp)
        for azon, r in sz.items():
            row = data[azon]
            self.assertEqual(row[1], "szószóló")
            self.assertEqual(row[4:8], [0, 0, 0, 0])                            # no vote of any kind, ever
            self.assertIn(r["nationality"], row[2])
        self.assertIn("nem szavaz — a szószóló felszólalhat", build_assets()["karzat.js"])

    def test_they_have_a_career_page_and_are_findable(self):
        from scripts.build_site import (CURRENT_CYCLE, available_cycles, build_person_index, build_spokesperson_page,
                                        factions, load_inputs)
        inp = load_inputs()
        sz = (inp["szoszolok"] or {}).get("people") or {}
        if not sz:
            self.skipTest("no spokesperson list derived")
        inp["facs_all"] = {}
        for c in available_cycles():
            for f in factions(c):
                inp["facs_all"].setdefault(f["id"], f["colour"])
        azon, r = next(iter(sz.items()))
        page = build_spokesperson_page(inp, azon, r)
        self.assertIn(f'<title>{r["name"]} — nemzetiségi szószóló · karzat</title>', page)
        self.assertIn("de nem szavaz", page)
        self.assertNotIn("Frakciójával", page)                                   # no discipline figure can exist
        self.assertNotIn("Leadott", page)
        self.assertIn(f'szemely/{azon}.html', page)                              # cites its own address
        for st in r["cycles"]:
            self.assertIn(f'{st["cycle"]}. ciklus', page)
        idx = build_person_index(inp, {}, sz)
        for a, x in sz.items():
            self.assertIn(f'<a href="{a}.html">{x["name"]}</a>', idx)
        self.assertEqual(idx.count('data-f="szószóló"'), len(sz))
        self.assertIn(f'{len(sz)} nemzetiségi szószóló', idx)

    def test_the_search_index_carries_them_with_their_own_kind(self):
        import json as _json
        from scripts.build_site import SITE_DIR, load_inputs
        path = SITE_DIR / "kereses" / "index.json"
        if not path.exists():
            self.skipTest("search index not built")
        items = _json.loads(path.read_text(encoding="utf-8"))
        sz = (load_inputs()["szoszolok"] or {}).get("people") or {}
        rows = [it for it in items if it["k"] == "szoszolo"]
        self.assertEqual(len(rows), len(sz))
        for it in rows:
            self.assertTrue(it["u"].startswith("szemely/") and it["u"].endswith(".html"))
            self.assertIn("nemzetiségi szószóló", it["s"])
            self.assertTrue((SITE_DIR / it["u"]).exists(), it["u"])


class MinisterialBench(unittest.TestCase):
    """A minister need not be an MP. Those who are not hold seats on the ministerial bench, speak, and never vote —
    karzat finds them by the id their own speeches carry, and says what it cannot find."""

    def test_the_bench_members_are_not_mps_and_hold_bench_seats(self):
        from scripts.build_site import build_landing, hall_data, kormany_seats, load_inputs
        inp = load_inputs()
        km = (inp["kormany"] or {}).get("people") or {}
        if not km:
            self.skipTest("no government bench derived")
        sz = (inp["szoszolok"] or {}).get("people") or {}
        for azon, r in km.items():
            self.assertNotIn(azon, inp["mps"])                                  # they hold no mandate
            self.assertNotIn(azon, sz)
            self.assertEqual(r["seat"]["sector"], 0)                            # the ministerial bench, nowhere else
            self.assertTrue(r["office"])
            self.assertGreaterEqual(r["speeches"], 1)                           # found because they spoke
        seats = kormany_seats(inp)
        self.assertEqual(len(seats), len(km))
        mp_seats = {(c["sector"], c["row"], c["seat"]) for c in inp["plan"]["coords"].values()}
        sz_seats = {(r["seat"]["sector"], r["seat"]["row"], r["seat"]["seat"]) for r in sz.values() if r.get("seat")}
        self.assertFalse(set(seats) & mp_seats)                                 # nobody is drawn on someone else's seat
        self.assertFalse(set(seats) & sz_seats)
        page = build_landing()
        self.assertEqual(page.count('class="seat km"'), len(km))
        self.assertIn("mandátum nélküli kormánytag", page)
        data = hall_data(inp)
        for azon, r in km.items():
            self.assertEqual(data[azon][1], "kormánytag")
            self.assertEqual(data[azon][4:8], [0, 0, 0, 0])                     # no vote exists to show

    def test_a_stale_seat_claim_is_dropped(self):
        # a former MP's record keeps its old seat; that is not a placement. The derive records the fact instead.
        import json as _json
        from scripts.build_site import DERIVED
        path = DERIVED / "kormany.json"
        if not path.exists():
            self.skipTest("no government bench derived")
        d = _json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("seat_not_on_the_bench", d)
        self.assertIn("seen_without_seat", d)
        self.assertTrue(all(p["seat"]["sector"] == 0 for p in d["people"].values()))

    def test_their_photographs_are_licensed_credited_and_black_and_white(self):
        import json as _json
        from scripts.build_site import DERIVED, ROOT as _R, build_assets, build_kormany_page, hall_data, load_inputs, portrait_html
        inp = load_inputs()
        km = (inp["kormany"] or {}).get("people") or {}
        if not km:
            self.skipTest("no government bench derived")
        snap = _json.loads((ROOT / "reference" / "wikidata" / "kormany_photos.json").read_text(encoding="utf-8"))
        with_photo = {a: r for a, r in km.items() if r.get("photo_url")}
        self.assertEqual(set(with_photo), set(snap["people"]))                      # only what the snapshot found
        for azon, r in with_photo.items():
            self.assertIn("commons.wikimedia.org", r["photo_url"])                  # never parlament.hu: it 404s for them
            self.assertTrue(r["photo_credit"] and r["photo_licence"])               # attribution is the licence's price
            self.assertIn("Wikimedia Commons", r["photo_credit"])
            html = portrait_html(r, "hero")
            self.assertIn('class="portrait hero"', html)                            # the grayscale class, like every portrait
            self.assertIn(f'title="{r["photo_credit"]}"', html)
            page = build_kormany_page(inp, azon, r)
            self.assertIn(r["photo_licence"], page)                                 # the licence is named on the page too
        for azon, r in km.items():
            if not r.get("photo_url"):
                self.assertIn("Nincs:", build_kormany_page(inp, azon, r))           # says there is none, rather than a broken frame
        data = hall_data(inp)
        for azon, r in with_photo.items():
            # the same picture, but our own grey copy where one was made — the card must not go to Commons for it
            self.assertIn(data[azon][10], (r["photo_url"], f"/assets/portre/{azon}.webp"))
            self.assertEqual(data[azon][11], r["photo_credit"])
        js = build_assets()["karzat.js"]
        self.assertIn("d[10] || (PHOTO_BASE", js)                                   # …and falls back to the id only when there is none
        self.assertIn('class="portrait insp"', js)
        self.assertRegex(build_assets()["karzat.css"], r"\.portrait\{filter:grayscale\(1\)")

    def test_they_have_a_page_and_are_findable(self):
        import json as _json
        from scripts.build_site import CURRENT_CYCLE, SITE_DIR, available_cycles, build_kormany_page, factions, load_inputs
        inp = load_inputs()
        km = (inp["kormany"] or {}).get("people") or {}
        if not km:
            self.skipTest("no government bench derived")
        inp["facs_all"] = {}
        for c in available_cycles():
            for f in factions(c):
                inp["facs_all"].setdefault(f["id"], f["colour"])
        azon, r = next(iter(km.items()))
        page = build_kormany_page(inp, azon, r)
        self.assertIn("kormánytag, nem képviselő", page)
        self.assertIn("nem szavaz", page)
        self.assertIn(f'miniszteri pad, {r["seat"]["seat"]}. szék', page)
        self.assertNotIn("Frakciójával", page)
        path = SITE_DIR / "kereses" / "index.json"
        if path.exists():
            rows = [it for it in _json.loads(path.read_text(encoding="utf-8")) if it["k"] == "kormany"]
            self.assertEqual(len(rows), len(km))
            for it in rows:
                self.assertTrue((SITE_DIR / it["u"]).exists(), it["u"])

