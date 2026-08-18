"""Cohesion indices and the close-vote finder: formulas on known numbers, and the real cycle-43 shapes."""

import unittest

from karzat.analytics import agreement_index, close_votes, cohesion, rice
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
