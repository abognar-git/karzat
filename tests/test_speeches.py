"""Speeches and the MP digest: the parser on a real day, the substantive/procedural rule against the record,
the sitting weeks and their numbers, the pages and the weekly channels."""

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from karzat import feeds as fd
from karzat.export import adatszotar, speeches_csv, weekly_csv
from karzat.normalise import SUBSTANTIVE_KINDS, parse_felszolalas, parse_felszolalasok, parse_kepviselo, parse_ulesnap
from karzat.xmlutil import parse_xml, to_dict
from scripts.build_site import (CAST, build_day_page, build_kepviselom_page, build_mp_page, build_speech_page, build_speech_search_page, build_speeches_page,
                                committees_in_cycle, cycle_weekly_feed, digest_line, fold_tokens, hu_span, load_inputs, mp_weekly_feed, sitting_weeks,
                                speech_id, speech_search_index, speeches_by_mp, speeches_feed, substantive_rows, week_digests)

FIXTURES = Path(__file__).parent / "fixtures"
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

    def test_the_substantive_rule_is_named_and_the_record_agrees(self):
        # the rule is a list of kinds; a few that must be on each side
        for k in ("felszólalás", "kétperces felszólalás", "vezérszónoki felszólalás", "kérdés megválaszolva", "ügyrendi javaslat"):
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
        self.assertTrue(all(len(k) == 2 for k in shards))                                        # sharded by the first two letters
        texts = (inp["texts"] or {}).get("texts") or {}
        if texts:
            # every token of a fetched text points at its speech's position in the meta
            sid, t = next(iter(texts.items()))
            i = next(k for k, m in enumerate(meta) if m[0] == sid)
            for tok in fold_tokens(" ".join(t["paragraphs"]))[:20]:
                self.assertIn(i, shards[tok[:2]][tok])
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
        self.assertEqual(ents[0].find("a:link", NS).get("href"), "../kepviselo/0026.html")
        ids = [e.find("a:id", NS).text for e in ents]
        self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(xml, mp_weekly_feed(self.inp, "0026", updated, "../"))            # deterministic
        cx = ET.fromstring(cycle_weekly_feed(self.inp, updated, "../").encode("utf-8"))
        self.assertEqual(len(cx.findall("a:entry", NS)), len(self.weeks))
        self.assertIn("különvélemény", cx.find("a:entry/a:title", NS).text)

    def test_pages_and_exports(self):
        k = build_kepviselom_page(self.inp)
        self.assertEqual(k.count("OEVK</td>"), 106)
        self.assertEqual(k.count("országos lista</td>"), 93)
        self.assertIn("Település szerint keresni még nem lehet", k)
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
