"""Seating reconstruction — synthetic seats for the logic, the committed derived plan for the facts."""

import json
import math
import unittest
from pathlib import Path

from karzat.seating import analyse, infer_orientation, layout, layout_from_plan, path_points, seats_from_records

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


class RealPlan(unittest.TestCase):
    """parlament.hu's own floor plan (reference/parlament/patko_seats.json) lays every MP on a real seat."""

    @classmethod
    def setUpClass(cls):
        cls.ref = json.loads((ROOT / "reference" / "parlament" / "patko_seats.json").read_text(encoding="utf-8"))
        cls.plan = json.loads((ROOT / "data" / "derived" / "seating.json").read_text(encoding="utf-8"))

    def test_path_parser_handles_the_plans_commands(self):
        pts = path_points("M 0,0 10,0 10,5 0,5z")
        self.assertEqual(pts, [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)])
        curved = path_points("M0,0l10,0c0,5,10,5,10,10l-20,0z", samples=2)
        self.assertEqual(curved[0], (0.0, 0.0)); self.assertEqual(curved[1], (10.0, 0.0))
        self.assertEqual(curved[-1], (0.0, 10.0))                       # the last l lands back on the left edge
        for seat in self.ref["seats"]:
            self.assertGreaterEqual(len(path_points(seat["svgpath"])), 4, seat["id"])

    def test_every_mp_sits_on_a_real_seat(self):
        self.assertEqual(self.ref["count"], 274)
        self.assertEqual(len(self.plan["seat_outlines"]), 274)
        self.assertEqual(self.plan["unmatched"], [])
        self.assertEqual(len(self.plan["coords"]) + len(self.plan["empty_seats"]), 274)
        self.assertEqual(sum(1 for o in self.plan["seat_outlines"] if o["sector"] == 0), 22)      # the ministerial patkó
        self.assertEqual(self.plan["geometry"]["source"], "parlament.hu patko-queries/kepviselo-helye-apatkoban")
        L = layout_from_plan(self.plan["seats"], self.ref["seats"])
        self.assertEqual(len(L["coords"]), len(self.plan["seats"]))
        # a known seat: Ágh Péter 2/5/7 is on the Speaker's left, in the arc, above the platform
        a = L["coords"]["a011"]
        self.assertEqual((a["sector"], a["row"], a["seat"]), (2, 5, 7))
        self.assertLess(a["x"], 0); self.assertLess(a["y"], 0)
        # glyphs sized from the plan's own pitch never touch
        self.assertGreater(L["geometry"]["seat_pitch"], 2 * L["geometry"]["node_radius"])


if __name__ == "__main__":
    unittest.main()
