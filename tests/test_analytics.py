"""Cohesion indices and the close-vote finder: formulas on known numbers, and the real cycle-43 shapes."""

import re
import unittest

from karzat.analytics import (agreement_index, bill_key, bills, close_votes, cohesion, floor_time, monthly,
                              motion_is_own_stage, rice, split_event)
from scripts.build_site import HERO_TS, has_floor, load_inputs, long_days



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


class FloorTime(unittest.TestCase):
    """The floor-time breakdown, checked where it can lie: the totals, and what happens to a joint debate."""

    # Six of the eight cycles that carry this page hold 74% of the corpus and had no invariant coverage at
    # all while only 43 and 42 were exercised — and the older ones are where the vocabulary, the House size
    # and the record's habits all differ.
    CYCLES = (43, 42, 41, 40, 39, 38, 37, 36)

    @classmethod
    def setUpClass(cls):
        cls.cycles = {c: load_inputs(c) for c in cls.CYCLES}
        cls.ft = {c: floor_time(inp) for c, inp in cls.cycles.items()}

    def test_every_kind_the_record_uses_has_a_plain_word_definition(self):
        """The jogcím popup and glossary explain the record's labels; a label without an explanation is a
        hover that shows nothing and a glossary with a hole. New vocabulary arrives when old cycles are
        loaded or the House renames a form — this fails the build instead of shipping the hole."""
        from scripts.build_site import KIND_DEFS
        missing = {k for ft in self.ft.values() for k in ft["forms"] if k not in KIND_DEFS}
        self.assertEqual(sorted(missing), [], "kinds with no definition: add them to build_site.KIND_DEFS")


    def test_every_second_lands_in_exactly_one_row(self):
        """The page states shares of a year, so a second counted twice or not at all is a wrong percentage.

        Attribution is the one place this could go wrong quietly: a joint debate names several bills, and the
        obvious implementations either charge it to each of them (shares over 100%) or to the first (the rest
        understated). Neither shows up as an error — both produce a plausible table."""
        for c, ft in self.ft.items():
            for y in ft["years"]:
                self.assertEqual(sum(d["seconds"] for d in y["debates"]) + sum(d["seconds"] for d in y["slots"]),
                                 y["seconds"], f"ckl{c} {y['year']}")
                self.assertEqual(sum(d["speeches"] for d in y["debates"]) + sum(d["speeches"] for d in y["slots"]),
                                 y["speeches"], f"ckl{c} {y['year']}")
                self.assertEqual(sum(d["seconds"] for d in y["debates"]), y["attributed"], f"ckl{c} {y['year']}")
            self.assertEqual(sum(y["seconds"] for y in ft["years"]), ft["seconds"], f"ckl{c}")
            self.assertEqual(sum(y["speeches"] for y in ft["years"]), ft["speeches"], f"ckl{c}")

    def test_the_totals_are_the_substantive_rows_and_nothing_else(self):
        """Independently of the grouping: the same filter, summed flat, must give the same number."""
        for c, inp in self.cycles.items():
            flat = sum(r["duration_s"] for r in (inp["speeches"] or {}).get("speeches") or []
                       if not r.get("technical") and r.get("duration_s") and r.get("date"))
            self.assertEqual(self.ft[c]["seconds"], flat, f"ckl{c}")

    def test_a_joint_debate_is_one_row_wearing_every_number(self):
        """One row per bill set, and a bill may belong to more than one set — which is the record, not a bug.

        I first asserted that T/637 appears in exactly one row and it appears in two, because the House took
        Finland's NATO accession jointly with Sweden's at the general debate (3.9 h, both numbers) and on its
        own at the committee-report stage (1.0 h). Two agenda items, two bill sets, two rows. What must not
        happen is the same bill set splitting into several rows for the same year, and it never does."""
        joint = [d for y in self.ft[42]["years"] for d in y["debates"] if len(d["bills"]) > 1]
        self.assertTrue(joint, "cycle 42 has joint debates; if this is empty the grouping key changed")
        for d in joint:
            self.assertEqual(len(d["bills"]), len(set(d["bills"])), d["bills"])
        nato = [d for d in joint if "T/637" in d["bills"]]
        self.assertEqual(len(nato), 1)
        self.assertEqual(nato[0]["bills"], ["T/637", "T/638"])
        # keyed on the SET, not the joined string: an ordering difference would slip past a string key, and
        # the property this test is named for is about the set
        for c, ft in self.ft.items():
            for y in ft["years"]:
                keys = [frozenset(d["bills"]) for d in y["debates"]]
                self.assertEqual(len(keys), len(set(keys)), f"ckl{c} {y['year']}: a bill set split into two rows")

    def test_the_sub_splits_do_not_exceed_the_row_they_divide(self):
        for c, ft in self.ft.items():
            for y in ft["years"]:
                for d in y["debates"] + y["slots"]:
                    self.assertEqual(sum(d["forms"].values()), d["seconds"], d)     # every row has a kind
                    self.assertLessEqual(sum(d["factions"].values()), d["seconds"])  # a minister has none
                    if d.get("stages"):
                        self.assertEqual(sum(d["stages"].values()), d["seconds"], d)

    def test_split_event_partitions_on_the_number_and_loses_nothing(self):
        self.assertEqual(split_event("Általános vita folytatása T/4181 Magyarország 2024. évi költségvetéséről", "T/4181"),
                         ("Általános vita folytatása", "Magyarország 2024. évi költségvetéséről"))
        self.assertEqual(split_event("Napirend előtti felszólalások", None), (None, "Napirend előtti felszólalások"))
        self.assertEqual(split_event(None, "T/1"), (None, None))
        # a joint debate: the split is on the FIRST number, and everything after it is the shared subject
        stage, subject = split_event("Általános vita lefolytatása T/637 Finnország T/638 Svédország", "T/637 · T/638")
        self.assertEqual(stage, "Általános vita lefolytatása")
        self.assertIn("T/638", subject)
        # Against the real corpus. "Every event contains its bill number" is true by construction — the
        # parser cut `iromany` out of `event` in the first place — so the evidence that matters is the shape
        # of what comes out: a closed stage vocabulary the House reuses, no subject that is empty or opens
        # with a number, and no partition landing inside a longer one.
        stages: set[str] = set()
        for c, inp in self.cycles.items():
            bad_split, bad_title, mid_number = [], [], []
            for r in (inp["speeches"] or {}).get("speeches") or []:
                if r.get("technical") or not r.get("duration_s") or not r.get("iromany"):
                    continue
                stage, subject = split_event(r.get("event"), r["iromany"])
                if stage is None:
                    bad_split.append(r["event"])
                    continue
                stages.add(stage)
                if not subject:
                    bad_title.append(r["event"])
                # A subject may legitimately open with a digit — "A/167 1,3 millióval csökkent a
                # szegénységben élők száma" is the House's own question. The real failure is finer: the
                # character immediately after the matched number being another digit, which means "T/12"
                # was found inside "T/123" and the split cut a number in half.
                num = r["iromany"].split(" · ")[0].strip()
                after = r["event"][r["event"].index(num) + len(num):]
                if after[:1].isdigit():
                    mid_number.append(r["event"])
            self.assertEqual(bad_split[:3], [], f"ckl{c}: {len(bad_split)} items do not carry their bill number")
            self.assertEqual(bad_title[:3], [], f"ckl{c}: {len(bad_title)} items partition to an empty subject")
            self.assertEqual(mid_number[:3], [], f"ckl{c}: {len(mid_number)} partitions land inside a number")
        # a stage is a phrase the House reuses; if this ever ran into the hundreds the split would be
        # slicing titles rather than stages, which is the failure mode no per-row check would show
        self.assertLess(len(stages), 90, sorted(stages))
        self.assertIn("Általános vita lefolytatása", stages)

    def test_the_ratio_denominator_is_time_resolved_and_is_a_house(self):
        """The denominator must be a chamber that existed, at the moments the speaking happened.

        A union of the year's members is neither: it charges a faction that dissolved in March with twelve
        months of size, and summing per-faction member sets double-counts anyone who switched, so the total
        could exceed the House. Both showed up as published figures — the MDF ran 0.51× in 2009 against a
        year it did not finish, where the House it actually sat in makes it 3.3×."""
        for c, ft in self.ft.items():
            for y in ft["years"]:
                self.assertAlmostEqual(sum(y["expected"].values()), 1.0, places=6, msg=f"ckl{c} {y['year']}")
                self.assertAlmostEqual(sum(y["seats"].values()), y["house"], places=6)
                self.assertTrue(all(e > 0 for e in y["expected"].values()))
                # 199 seats from the 40th cycle, 386 before it — and the boundary is the cycle, not the
                # calendar: the 386-seat House sat until the 2014 election, so cycle 39's 2014 is still 382
                seats = 199 if c >= 40 else 386
                self.assertLessEqual(y["house"], seats + 1, f"ckl{c} {y['year']}")
                self.assertGreater(y["house"], seats * 0.75, f"ckl{c} {y['year']}")

    def test_a_faction_that_left_mid_year_is_measured_against_the_house_it_sat_in(self):
        """The MDF appears in no cycle-38 roll call after 2009-03-16; the ratio must reflect that."""
        inp = load_inputs(38)
        y = next(x for x in floor_time(inp)["years"] if x["year"] == "2009")
        etot = sum(y["expected"].get(f, 0.0) for f in y["factions"])
        share = y["factions"]["MDF"] / sum(y["factions"].values())
        self.assertGreater(share / (y["expected"]["MDF"] / etot), 2.5)      # was 0.51 against a whole year
        self.assertLess(y["seats"]["MDF"], 4)                              # time-weighted, not a March headcount

    def test_the_page_and_the_nav_agree_about_whether_it_exists(self):
        """has_floor decides the link, floor_time decides the page; disagreement is a 404 in the cycle nav."""
        for c in (43, 42, 41, 36, 35, 34):
            try:
                inp = load_inputs(c)
            except (FileNotFoundError, KeyError):
                continue
            self.assertEqual(has_floor(inp), bool(floor_time(inp)["years"]), f"ckl{c}")

    def test_the_clock_measures_speaking_and_not_video(self):
        """Every hour on these pages rests on `videoido` being time at the microphone rather than a clip.

        If it carried the walk to the podium or padding, every figure would be wrong in the same direction
        and no internal sum would show it — they would all still tie. The transcript is the outside witness:
        length against character count, over every speech that has both. A padded clock would yield fewer
        characters per second on short speeches than on long ones, because the padding is a fixed cost."""
        import gzip
        import json as _json
        from pathlib import Path
        pts = []
        for sfx in ("_ckl42", ""):
            f = Path(load_inputs.__globals__["DERIVED"]) / f"speech_texts{sfx}.json.gz"
            if not f.exists():
                continue
            for t in _json.loads(gzip.decompress(f.read_bytes()))["texts"].values():
                if t.get("duration_s") and t.get("chars") and 20 <= t["duration_s"] <= 3600:
                    pts.append((t["duration_s"], t["chars"]))
        self.assertGreater(len(pts), 10_000)
        mx = sum(d for d, _ in pts) / len(pts)
        my = sum(c for _, c in pts) / len(pts)
        r = (sum((d - mx) * (c - my) for d, c in pts)
             / (sum((d - mx) ** 2 for d, _ in pts) * sum((c - my) ** 2 for _, c in pts)) ** 0.5)
        self.assertGreater(r, 0.95, "length and transcript size have come apart")
        rates = sorted(c / (d / 60) for d, c in pts)
        self.assertTrue(700 < rates[len(rates) // 2] < 1100, "the median rate is not human speech")
        short = [c / (d / 60) for d, c in pts if d < 60]
        long_ = [c / (d / 60) for d, c in pts if d >= 600]
        self.assertGreater(sum(short) / len(short), sum(long_) / len(long_),
                           "short speeches yield fewer characters per second — the shape of a padded clip")

    def test_the_sitting_days_longer_than_a_day_are_the_three_the_docstring_names(self):
        """long_days' docstring names three days and their lengths; a docstring cannot be run, so this is.

        If the source is re-synced and a fourth appears — or one of these moves — the note the page prints
        about them is out of date, and this is the only thing that would say so."""
        found = {}
        for c in (43, 42, 41, 40, 39, 38, 37, 36):
            try:
                inp = load_inputs(c)
            except (FileNotFoundError, KeyError):
                continue
            for d, s in long_days(inp):
                found[d] = round(s / 3600, 1)
        self.assertEqual(found, {"2011-12-01": 28.0, "2019-11-20": 25.9, "2022-10-26": 25.0})


if __name__ == "__main__":
    unittest.main()
