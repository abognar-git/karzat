"""Cohesion indices and the close-vote finder: formulas on known numbers, and the real cycle-43 shapes."""

import unittest

from karzat.analytics import agreement_index, bill_key, bills, close_votes, cohesion, monthly, motion_is_own_stage, rice
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
        self.assertEqual(bill_key("Ü/2217"), (2217, "Ü"))                            # accented prefixes exist (cycle 42)
        self.assertEqual(bill_key(None), (None, None))

    def test_a_votes_stage_is_told_by_the_outcome_not_the_number_form(self):
        # the closing vote on T/122's egységes javaslat is printed as '122/12' with an 'önálló indítvány' outcome
        self.assertTrue(motion_is_own_stage({"iromany": "122/12", "outcome": "önálló indítvány elfogadva"}, None))
        self.assertFalse(motion_is_own_stage({"iromany": "122/3", "outcome": "módosító javaslat elutasítva"}, None))
        self.assertTrue(motion_is_own_stage({"iromany": "T/122", "outcome": "kivételességi javaslat elfogadva"}, "T"))
        self.assertFalse(motion_is_own_stage({"iromany": "122/4", "outcome": ""}, None))
        inp = load_inputs()
        b = bills(inp)[122]
        self.assertEqual(b["own_votes"] + b["amendment_votes"], len(b["votes"]))       # counted per vote, not per motion
        self.assertGreater(b["own_votes"], 0)                                          # …and its own passage is its own

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

    def test_ties_are_no_plurality_and_nobody_dissents(self):
        # per-faction "kisebbségi szavazatok" must equal the sum of the MP pages' "frakció ellen": both apply the
        # tie rule (no plurality → nobody against) and both leave quorum checks out
        from scripts.build_site import compute_alignment
        al = compute_alignment(self.inp)
        against = {}
        for rec in al["per_mp"].values():
            for v in rec["votes"]:
                if v["align"] == "against":
                    against[v["faction"]] = against.get(v["faction"], 0) + 1
        for f, v in self.co["per_faction"].items():
            self.assertEqual(v["dissenting_votes"], against.get(f, 0), f)
        self.assertEqual(against.get("Mi Hazánk"), 3)                     # was 4 while the quorum check counted as a vote
        # the quorum check of 2026-05-09 has a roll call but no pluralities
        self.assertEqual(al["plurality_by_vote"].get("2026.05.09.10:45:47"), {})
        self.assertNotIn("2026.05.09.10:45:47", self.co["per_vote"])

    def test_absence_counterfactual_does_not_double_count_the_present(self):
        # a 2/3-of-present decision: 126–51–4 with 5 present-not-voting and 13 absent among the roll call
        # hypothetical presence = 186 + 13 (not 186 + 18): the present-not-voting are already in the base
        for r in self.cl["decisions"]:
            if r["hypo_igen"] is not None and r["rule"] in ("ketharmad_jelenlevo", "egyszeru", "negyotod_jelenlevo"):
                self.assertLessEqual(r["hypo_igen"], 199)
        # cycle 42's H/13745 (2026-03-10 10:35) was the reviewer's repro: it must not be listed as absence-decisive
        inp42 = load_inputs(42)
        co42 = cohesion(inp42); cl42 = close_votes(inp42, co42["plurality"])
        self.assertNotIn("2026.03.10.10:35:41", [r["ts"] for r in cl42["absence_decisive"]])
        for r in cl42["decisions"]:
            if r["ts"].startswith("2026.03.10.10:35"):
                self.assertFalse(r["flips_if_all_voted"], r)

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
