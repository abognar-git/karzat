"""Cohesion indices and the close-vote finder: formulas on known numbers, and the real cycle-43 shapes."""

import unittest

from karzat.analytics import agreement_index, bill_key, bills, close_votes, cohesion, monthly, rice
from scripts.build_site import HERO_TS, load_inputs


class Formulas(unittest.TestCase):
    def test_rice_and_agreement_index(self):
        self.assertEqual(rice(135, 0), 1.0)
        self.assertEqual(rice(10, 10), 0.0)
        self.assertIsNone(rice(0, 0))
        self.assertEqual(agreement_index(135, 0, 0), 1.0)
        self.assertAlmostEqual(agreement_index(0, 23, 16), (23 - 16 / 2) / 39, places=6)      # Fidesz on 2026-07-28 10:35
        self.assertAlmostEqual(agreement_index(10, 10, 10), 0.0, places=9)
        self.assertIsNone(agreement_index(0, 0, 0))


class Bills(unittest.TestCase):
    def test_bill_key_reads_own_numbers_and_amendments(self):
        self.assertEqual(bill_key("T/51"), (51, "T"))
        self.assertEqual(bill_key("11152/8"), (11152, None))
        self.assertEqual(bill_key("S/482"), (482, "S"))
        self.assertEqual(bill_key(None), (None, None))

    def test_bills_group_every_roll_call_by_number(self):
        inp = load_inputs()
        bs = bills(inp)
        self.assertEqual(bs[51]["label"], "T/51")
        self.assertEqual(len(bs[51]["votes"]), 2)                                     # the 16th amendment: two roll calls
        self.assertIn("tizenhatodik", bs[51]["title"])
        self.assertTrue(all(b["first"] <= b["last"] for b in bs.values()))
        # every vote with a motion number lands in exactly one bill
        n_votes = sum(1 for ts in inp["order"] if inp["by_ts"][ts].get("motions") and bill_key(inp["by_ts"][ts]["motions"][0].get("iromany"))[0] is not None)
        self.assertEqual(sum(len(b["votes"]) for b in bs.values()), n_votes)


class Monthly(unittest.TestCase):
    def test_months_add_up(self):
        inp = load_inputs()
        co = cohesion(inp)
        ms = monthly(inp, co)["months"]
        self.assertEqual([d["month"] for d in ms], ["2026-05", "2026-06", "2026-07", "2026-08"])
        self.assertEqual(sum(d["votes"] for d in ms), 259)
        self.assertEqual(sum(d["roll_calls"] for d in ms), 256)
        for d in ms:
            for f, x in d["factions"].items():
                self.assertLessEqual(x["cast"], x["in_roll"])
                self.assertLessEqual(x["with"] + x["against"], x["cast"])
                if x["participation"] is not None:
                    self.assertTrue(0 <= x["participation"] <= 1)


class Cycle43(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inp = load_inputs()
        cls.co = cohesion(cls.inp)
        cls.cl = close_votes(cls.inp, cls.co["plurality"])

    def test_cohesion_shapes(self):
        pf = self.co["per_faction"]
        self.assertEqual(set(pf), {"TISZA", "Fidesz", "KDNP", "Mi Hazánk"})
        for f, v in pf.items():
            self.assertTrue(0 <= v["ai"] <= 1 and 0 <= v["rice"] <= 1, f)
            self.assertEqual(v["unanimous_votes"] + v["votes_with_dissent"], v["votes"], f)
        hero = self.co["per_vote"][HERO_TS]
        self.assertEqual(hero["TISZA"]["ai"], 1.0)                                    # 135–0–0
        self.assertEqual(hero["Fidesz"]["ai"], 1.0)                                   # 0–42–0
        pairs = self.co["faction_pairs"]
        fk = pairs.get("TISZA", {}).get("Fidesz") or pairs.get("Fidesz", {}).get("TISZA")
        self.assertTrue(fk and 0 <= fk["share"] <= 1 and fk["votes"] > 200)
        self.assertTrue(all(r["shared"] >= 20 for rows in self.co["mp_pairs"].values() for r in rows))

    def test_close_votes_shapes(self):
        cl = self.cl
        self.assertEqual(len(cl["decisions"]), 258)                                   # 259 votes minus the one quorum check; secret ballots count (threshold from the totals)
        self.assertEqual(cl["closest"][0]["margin"], 1)                               # the tightest pass of the cycle
        self.assertTrue(all(abs(cl["closest"][i]["margin"]) <= abs(cl["closest"][i + 1]["margin"]) for i in range(len(cl["closest"]) - 1)))
        for r in cl["sunk_by_threshold"]:
            self.assertTrue(r["qualified"] and not r["passed"] and r["igen"] > r["nem"])
        for r in cl["absence_decisive"]:
            self.assertIsNotNone(r["hypo_igen"])
            self.assertTrue(r["flips_if_all_voted"])


if __name__ == "__main__":
    unittest.main()
