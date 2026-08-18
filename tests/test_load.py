"""SQLite loader — driven by the real fixtures into an in-memory database."""

import sqlite3
import unittest
from pathlib import Path

from karzat.load import Loader, cycle_number, cycle_of_date, mandate_kind
from karzat.normalise import NameResolver, parse_kepviselok
from karzat.xmlutil import parse_xml, to_dict

FX = Path(__file__).parent / "fixtures"


def load(name):
    root = parse_xml((FX / name).read_bytes())
    return {root.tag: to_dict(root)}


class Helpers(unittest.TestCase):
    def test_cycle_numbers(self):
        self.assertEqual(cycle_number("2026-"), 43)
        self.assertEqual(cycle_number("2022-2026"), 42)
        self.assertEqual(cycle_number("2014-2018"), 40)
        self.assertEqual(cycle_number("1990-1994"), 34)
        self.assertIsNone(cycle_number(""))
        self.assertEqual(cycle_of_date("2026-06-15"), 43)
        self.assertEqual(cycle_of_date("2026-05-08"), 42)   # the day before the constitutive sitting
        self.assertEqual(cycle_of_date("1990-05-31"), 34)
        self.assertEqual(cycle_of_date("2014-05-26"), 40)

    def test_mandate_kind(self):
        self.assertEqual(mandate_kind("Vas 2. OEVK"), "egyeni")
        self.assertEqual(mandate_kind("Országos lista"), "lista")
        self.assertEqual(mandate_kind(None), "other")


class LoadFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = sqlite3.connect(":memory:")
        L = Loader(cls.conn)
        L.create_schema()
        kl = load("real_kepviselok_head5.xml")
        L.load_kepviselok(kl)
        L.load_kepviselo("a011", load("real_kepviselo_a011_trimmed.xml"), wikidata_qid="Q999")
        L.load_ulesnap(load("real_ulesnap_43.xml"))
        L.load_vote_list(load("real_szavazasok_2026-05_head3.xml"))
        # resolver over the 5 listed MPs plus an alias, as build_from_cache does
        resolver = NameResolver(parse_kepviselok(kl), aliases={"Budai Gyula": "b076"})
        for f in ("real_szavazas_2026-05-26T16-31-45.xml", "real_szavazas_2026-06-15T17-20-04.xml",
                  "real_szavazas_2026-05-09T11-50-00.xml", "real_szavazas_2026-05-09T10-45-47.xml"):
            L.load_vote_detail(load(f), resolver)
        L.load_iromanyok(load("real_iromanyok_head3.xml"))
        L.load_iromany(load("real_iromany_71.xml"))
        L.record_sync_run("test")
        cls.problems = L.integrity()
        cls.stats = dict(L.stats)
        cls.conn.commit()

    def q(self, sql, *args):
        return self.conn.execute(sql, args).fetchall()

    def test_no_foreign_key_problems(self):
        self.assertEqual(self.problems, [])

    def test_mps_seats_factions_mandates(self):
        self.assertEqual(self.q("SELECT COUNT(*) FROM mp")[0][0], 6)          # 5 listed + a stub for b076 (resolved former MP)
        self.assertEqual(self.q("SELECT name, raw_json FROM mp WHERE azon='b076'")[0], ("Dr. Budai Gyula", None))
        row = self.q("SELECT seat_sector, seat_row, seat_no, wikidata_qid, county, constituency_no FROM mp WHERE azon='a011'")[0]
        self.assertEqual(row, (2, 5, 7, "Q999", "Vas", 2))
        self.assertGreaterEqual(self.q("SELECT COUNT(*) FROM mp_faction WHERE mp_azon='a011'")[0][0], 4)
        self.assertEqual(self.q("SELECT ckl, from_date, to_date FROM mp_faction WHERE mp_azon='a011' AND ckl=43")[0], (43, "2026-05-09", None))
        self.assertEqual(self.q("SELECT kind, constituency FROM mp_mandate WHERE mp_azon='a011' AND ckl=43")[0], ("egyeni", "Vas 2. OEVK"))
        self.assertIn(("Fidesz",), self.q("SELECT id FROM faction"))

    def test_sitting_days(self):
        self.assertEqual(self.q("SELECT COUNT(*) FROM sitting_day")[0][0], 23)
        self.assertEqual(self.q("SELECT session_label, kind FROM sitting_day WHERE on_date='2026-08-11'")[0], ("2026. nyári", "Rendkívüli"))

    def test_votes_positions_tallies_majority(self):
        # 3 list entries + 4 details, overlapping on none of the fixtures' timestamps except possibly the May 9 ones
        n_votes = self.q("SELECT COUNT(*) FROM vote")[0][0]
        self.assertGreaterEqual(n_votes, 4)
        row = self.q("SELECT igen, nem, tartozkodott, present, majority_rule, needed, margin, passed, kind, secret, sitting_nap FROM vote WHERE ts='2026.06.15.17:20:04'")[0]
        self.assertEqual(row, (135, 50, 6, 195, "ketharmad_osszes", 133, 2, 1, "dontes", 0, self.q("SELECT nap FROM sitting_day WHERE on_date='2026-06-15'")[0][0]))
        self.assertEqual(self.q("SELECT COUNT(*) FROM vote_position WHERE vote_ts='2026.06.15.17:20:04'")[0][0], 199)
        self.assertEqual(self.q("SELECT COUNT(*) FROM vote_faction_tally WHERE vote_ts='2026.06.15.17:20:04'")[0][0], 4)
        # positions resolved for the 5 listed MPs (+ Budai via alias) — the rest are NULL and kept by name
        resolved = self.q("SELECT COUNT(*) FROM vote_position WHERE vote_ts='2026.06.15.17:20:04' AND mp_azon IS NOT NULL")[0][0]
        self.assertGreaterEqual(resolved, 5)
        self.assertEqual(self.q("SELECT mp_azon FROM vote_position WHERE vote_ts='2026.06.15.17:20:04' AND mp_name='Dr. Budai Gyula (Fidesz)'")[0][0], "b076")
        # quorum check: kind jelenlet, no rule
        self.assertEqual(self.q("SELECT kind, majority_rule, passed FROM vote WHERE ts='2026.05.09.10:45:47'")[0], ("jelenlet", None, None))
        # secret ballot: no positions
        self.assertEqual(self.q("SELECT secret, (SELECT COUNT(*) FROM vote_position WHERE vote_ts='2026.05.09.11:50:00') FROM vote WHERE ts='2026.05.09.11:50:00'")[0], (1, 0))
        # faction plurality table
        self.assertEqual(self.q("SELECT majority_position, cast_votes FROM vote_faction_majority WHERE vote_ts='2026.06.15.17:20:04' AND faction_id='Fidesz'")[0], ("nem", 42))
        self.assertEqual(self.q("SELECT majority_position FROM vote_faction_majority WHERE vote_ts='2026.06.15.17:20:04' AND faction_id='TISZA'")[0][0], "igen")

    def test_motions_and_bills(self):
        self.assertEqual(self.q("SELECT iromany, outcome, submitter_kind FROM vote_motion WHERE vote_ts='2026.06.15.17:20:04'")[0],
                         ("T/51", "önálló indítvány minősített többséget igénylő része elfogadva", "kepviselo"))
        b = self.q("SELECT number, main_type, status, procedure_kind, promulgated_as, mk_issue, promulgated_on, days_to_final_vote, days_to_promulgation, submitter_kind FROM bill WHERE izon='71'")[0]
        self.assertEqual(b, ("T/71", "törvényjavaslat", "kihirdetve", "kiveteles", "2026. évi XV. törvény", "60", "2026-05-28", 2, 3, "kormany"))
        self.assertGreater(self.q("SELECT COUNT(*) FROM bill_event WHERE bill_key='71'")[0][0], 5)
        self.assertEqual(self.q("SELECT number, status_type FROM bill WHERE izon='505'")[0], ("T/505", "folyamatban"))

    def test_views_work(self):
        rows = self.q("SELECT ts, iromany, majority_rule, needed FROM v_vote WHERE ts='2026.06.15.17:20:04'")
        self.assertEqual(rows[0], ("2026.06.15.17:20:04", "T/51", "ketharmad_osszes", 133))
        al = self.q("SELECT name, faction_id, cast_votes, with_faction, against_faction FROM v_mp_alignment WHERE mp_azon='a011'")
        self.assertEqual(len(al), 1)
        self.assertEqual(al[0][1], "Fidesz")
        self.assertGreaterEqual(al[0][2], 1)

    def test_stats_counter(self):
        self.assertEqual(self.stats["votes_detailed"], 4)
        self.assertEqual(self.stats["positions"], 199 * 3)   # three roll calls (the secret ballot has none)


if __name__ == "__main__":
    unittest.main()
