"""Seating reconstruction — synthetic seats for the logic, the committed derived plan for the facts."""

import json
import math
import unittest
from pathlib import Path

from karzat.seating import analyse, infer_orientation, layout, seats_from_records

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "data" / "derived" / "seating.json"


def rec(azon, sec, row, seat, faction):
    return {"p_azon": azon, "faction": faction, "name": azon, "seat": {"sector": sec, "row": row, "seat": seat}}


class Logic(unittest.TestCase):
    def setUp(self):
        # three sectors: 1 = opposition (2 rows × 2), 2 = mixed, 3 = government (3 rows, widest 3)
        recs = [rec("a1", 1, 1, 1, "OPP"), rec("a2", 1, 1, 2, "OPP"), rec("a3", 1, 2, 1, "OPP"), rec("a4", 1, 2, 2, "OPP"),
                rec("b1", 2, 1, 1, "OPP"), rec("b2", 2, 1, 2, "GOV"),
                rec("c1", 3, 1, 1, "GOV"), rec("c2", 3, 1, 2, "GOV"), rec("c3", 3, 2, 1, "GOV"), rec("c4", 3, 2, 2, "GOV"),
                rec("c5", 3, 3, 1, "GOV"), rec("c6", 3, 3, 2, "GOV"), rec("c7", 3, 3, 3, "GOV"),
                {"p_azon": "z9", "faction": "GOV", "name": "no seat", "seat": None}]
        self.seats = seats_from_records(recs)

    def test_analysis(self):
        a = analyse(self.seats)
        self.assertEqual(a["n_seated"], 13)
        self.assertEqual(sorted(a["sectors"]), [1, 2, 3])
        self.assertEqual(a["sectors"][3]["widest_row"], 3)
        self.assertEqual(a["sectors"][3]["max_row"], 3)
        self.assertEqual(a["sectors"][1]["factions"], {"OPP": 4})
        self.assertEqual(a["duplicates"], [])

    def test_orientation_keeps_order_when_government_is_high_numbered(self):
        o = infer_orientation(analyse(self.seats), government="GOV")
        self.assertFalse(o["flipped"])
        self.assertEqual(o["sector_order"], [1, 2, 3])

    def test_orientation_flips_when_government_is_low_numbered(self):
        o = infer_orientation(analyse(self.seats), government="OPP")
        self.assertTrue(o["flipped"])
        self.assertEqual(o["sector_order"], [3, 2, 1])

    def test_layout_puts_first_sector_on_the_left_and_rows_outward(self):
        a = analyse(self.seats)
        L = layout(self.seats, a, [1, 2, 3])
        c = L["coords"]
        self.assertEqual(len(c), 13)
        self.assertLess(c["a1"]["x"], 0)            # sector 1 → Speaker's left → negative x
        self.assertGreater(c["c1"]["x"], 0)         # sector 3 → right
        self.assertTrue(all(v["y"] <= 0.01 for v in c.values()))   # above the axis (SVG y down)
        r1 = math.hypot(c["c1"]["x"], c["c1"]["y"]); r3 = math.hypot(c["c5"]["x"], c["c5"]["y"])
        self.assertAlmostEqual(r3 - r1, 2 * L["geometry"]["row_gap"], places=1)   # row 3 is two gaps out
        # seat numbers run left→right within a row: seat 1 is left of seat 3 in sector 3 row 3
        self.assertLess(c["c5"]["x"], c["c7"]["x"])
        self.assertEqual(L["geometry"]["sector_order_left_to_right"], [1, 2, 3])
        self.assertTrue(L["geometry"]["assumptions"])

    def test_duplicates_are_reported(self):
        seats = dict(self.seats)
        seats["dup"] = {"sector": 1, "row": 1, "seat": 1, "faction": "OPP", "name": "dup"}
        self.assertEqual(analyse(seats)["duplicates"], [[1, 1, 1]])


@unittest.skipUnless(PLAN.exists(), "derived seating plan not present")
class RealPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads(PLAN.read_text(encoding="utf-8"))

    def test_every_seated_mp_has_coordinates_and_no_duplicates(self):
        p = self.plan
        self.assertEqual(len(p["coords"]), p["analysis"]["n_seated"])
        self.assertEqual(p["analysis"]["duplicates"], [])
        self.assertGreaterEqual(p["analysis"]["n_seated"], 190)

    def test_government_sits_to_the_speakers_right(self):
        p = self.plan
        gov = p["government"]
        xs = [c["x"] for azon, c in p["coords"].items() if p["seats"][azon]["faction"] == gov]
        self.assertGreater(sum(xs) / len(xs), 0)


if __name__ == "__main__":
    unittest.main()
