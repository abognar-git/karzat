"""Tests for karzat.majority — pure arithmetic and classification, no network, no XML.

The legal citations in the module were read against njt.hu on 2026-08-19; these tests pin the *arithmetic* those
citations imply ("több mint a fele" = floor(N/2)+1, "kétharmada" = ceil(2N/3), "négyötöde" =
ceil(4N/5)) and the modelling choice that abstainers are present.
"""

import unittest

from karzat.majority import (
    KEYWORD_RULES,
    PAYLOAD_RULES,
    RULES,
    Rule,
    Tally,
    classify,
    describe,
    evaluate,
    evaluate_all,
    needed,
)


class Needed(unittest.TestCase):
    def test_full_house_of_199(self):
        t = Tally(yes=0, no=0, present=199, seats=199)
        self.assertEqual(needed(Rule.EGYSZERU, t), 100)
        self.assertEqual(needed(Rule.ABSZOLUT, t), 100)
        self.assertEqual(needed(Rule.KETHARMAD_JELENLEVO, t), 133)   # ceil(132.67)
        self.assertEqual(needed(Rule.KETHARMAD_OSSZES, t), 133)
        self.assertEqual(needed(Rule.NEGYOTOD_JELENLEVO, t), 160)    # ceil(159.2)
        self.assertEqual(needed(Rule.NEGYOTOD_OSSZES, t), 160)
        self.assertIsNone(needed(Rule.RELATIV, t))

    def test_present_based_rules_track_attendance_seat_based_do_not(self):
        t = Tally(yes=0, no=0, present=176, seats=199)
        self.assertEqual(needed(Rule.EGYSZERU, t), 89)                # floor(88)+1
        self.assertEqual(needed(Rule.KETHARMAD_JELENLEVO, t), 118)   # ceil(117.33)
        self.assertEqual(needed(Rule.NEGYOTOD_JELENLEVO, t), 141)    # ceil(140.8)
        self.assertEqual(needed(Rule.ABSZOLUT, t), 100)
        self.assertEqual(needed(Rule.KETHARMAD_OSSZES, t), 133)
        self.assertEqual(needed(Rule.NEGYOTOD_OSSZES, t), 160)      # attendance-independent

    def test_exact_fractions_are_not_rounded_up(self):
        t = Tally(yes=0, no=0, present=150)
        self.assertEqual(needed(Rule.KETHARMAD_JELENLEVO, t), 100)   # 2/3 of 150 exactly
        t = Tally(yes=0, no=0, present=100)
        self.assertEqual(needed(Rule.NEGYOTOD_JELENLEVO, t), 80)     # 4/5 of 100 exactly
        self.assertEqual(needed(Rule.EGYSZERU, t), 51)               # "more than half" of 100

    def test_odd_and_even_present_for_more_than_half(self):
        self.assertEqual(needed(Rule.EGYSZERU, Tally(0, 0, present=101)), 51)
        self.assertEqual(needed(Rule.EGYSZERU, Tally(0, 0, present=102)), 52)

    def test_vacant_seats_move_the_all_mps_bases(self):
        t = Tally(yes=0, no=0, present=190, seats=198)
        self.assertEqual(needed(Rule.KETHARMAD_OSSZES, t), 132)      # ceil(132.0)
        self.assertEqual(needed(Rule.ABSZOLUT, t), 100)              # floor(99)+1


class Evaluate(unittest.TestCase):
    def test_abstention_counts_against_under_present_rules(self):
        # 100 yes, 90 no, 9 abstain: voting-present 199 -> simple majority needs 100 -> passes by 0.
        v = evaluate(Rule.EGYSZERU, Tally(yes=100, no=90, abstain=9))
        self.assertTrue(v.passed)
        self.assertEqual(v.margin, 0)
        # One more abstainer and the same 100 yes no longer clears it (needs 101 of 200)... but 200 > 199 seats,
        # so shrink: 99 yes, 90 no, 9 abstain -> present 198 -> needs 100 -> fails by 1.
        v = evaluate(Rule.EGYSZERU, Tally(yes=99, no=90, abstain=9))
        self.assertFalse(v.passed)
        self.assertEqual(v.margin, -1)

    def test_not_voting_does_not_enter_the_fallback_present(self):
        v = evaluate(Rule.EGYSZERU, Tally(yes=90, no=80, abstain=0, not_voting=20))
        self.assertEqual(v.base, 170)
        self.assertTrue(v.present_assumed)
        self.assertIn("the votes cast", v.note)                      # the base is named, not flagged

    def test_stated_present_is_used_verbatim(self):
        v = evaluate(Rule.EGYSZERU, Tally(yes=90, no=80, not_voting=20, present=190))
        self.assertEqual(v.base, 190)
        self.assertEqual(v.needed, 96)
        self.assertFalse(v.present_assumed)
        self.assertEqual(v.note, "")

    def test_two_thirds_of_all_ignores_attendance(self):
        v = evaluate(Rule.KETHARMAD_OSSZES, Tally(yes=133, no=40, present=180))
        self.assertTrue(v.passed)
        self.assertEqual(v.base, 199)
        v = evaluate(Rule.KETHARMAD_OSSZES, Tally(yes=132, no=40, present=180))
        self.assertFalse(v.passed)
        self.assertEqual(v.margin, -1)

    def test_same_tally_different_rules(self):
        t = Tally(yes=120, no=60, abstain=10, present=190)
        all_v = evaluate_all(t)
        self.assertTrue(all_v["egyszeru"].passed)              # needs 96
        self.assertTrue(all_v["abszolut"].passed)              # needs 100
        self.assertFalse(all_v["ketharmad_jelenlevo"].passed)  # needs 127
        self.assertFalse(all_v["ketharmad_osszes"].passed)     # needs 133
        self.assertFalse(all_v["negyotod_jelenlevo"].passed)   # needs 152
        self.assertFalse(all_v["negyotod_osszes"].passed)      # needs 160
        self.assertNotIn("relativ", all_v)

    def test_relativ_is_not_decidable_from_yes_no(self):
        v = evaluate(Rule.RELATIV, Tally(yes=90, no=80))
        self.assertIsNone(v.passed)
        self.assertIsNone(v.needed)
        self.assertIn("plurality", v.note)

    def test_verdict_serialises_and_carries_citations(self):
        d = evaluate("ketharmad_jelenlevo", Tally(yes=130, no=30, present=170)).to_dict()
        self.assertEqual(d["rule"], "ketharmad_jelenlevo")
        self.assertEqual(d["needed"], 114)
        self.assertTrue(any("T) cikk" in b for b in d["basis"]))
        self.assertFalse(any("VERIFY" in b for b in d["basis"]))  # read against njt.hu on 2026-08-19: the flags are gone here


class TallyValidation(unittest.TestCase):
    def test_present_cannot_be_below_voters(self):
        with self.assertRaises(ValueError):
            Tally(yes=100, no=50, abstain=10, present=150)

    def test_present_cannot_exceed_seats(self):
        with self.assertRaises(ValueError):
            Tally(yes=1, no=1, present=200)

    def test_negative_or_non_int_rejected(self):
        with self.assertRaises(ValueError):
            Tally(yes=-1, no=0)
        with self.assertRaises(ValueError):
            Tally(yes=1.5, no=0)  # type: ignore[arg-type]

    def test_voters_cannot_exceed_seats(self):
        with self.assertRaises(ValueError):
            Tally(yes=150, no=50)


class Classify(unittest.TestCase):
    def test_default_is_simple_and_says_so(self):
        c = classify(subject="Zárószavazás a T/123. számú törvényjavaslatról")
        self.assertEqual(c.rule, Rule.EGYSZERU)
        self.assertEqual(c.source, "default")

    def test_payload_statement_beats_keywords(self):
        c = classify(payload_rule="Jelen lévő képviselők kétharmada", subject="alaptörvény módosítása")
        self.assertEqual(c.rule, Rule.KETHARMAD_JELENLEVO)
        self.assertEqual(c.source, "payload")

    def test_keyword_hits_report_evidence(self):
        cases = {
            "Magyarország Alaptörvényének tizenötödik módosítása — zárószavazás": Rule.KETHARMAD_OSSZES,
            "A törvényjavaslat minősített többséget igénylő rendelkezéseiről": Rule.KETHARMAD_JELENLEVO,
            "Sarkalatos rendelkezések zárószavazása": Rule.KETHARMAD_JELENLEVO,
            "Kivételes eljárásban történő tárgyalás elrendelése": Rule.ABSZOLUT,
            "Sürgős tárgyalás elrendelése": Rule.KETHARMAD_JELENLEVO,
            "Házszabálytól való eltérés": Rule.NEGYOTOD_JELENLEVO,
            "Alkotmánybírák megválasztása": Rule.KETHARMAD_OSSZES,
            "Miniszterelnök megválasztása": Rule.ABSZOLUT,
            "Bizalmatlansági indítvány": Rule.ABSZOLUT,
        }
        for subject, rule in cases.items():
            with self.subTest(subject=subject):
                c = classify(subject=subject)
                self.assertEqual(c.rule, rule)
                self.assertEqual(c.source, "keyword")
                self.assertTrue(c.evidence)

    def test_matching_is_case_and_normalisation_insensitive(self):
        import unicodedata
        nfd = unicodedata.normalize("NFD", "KÉTHARMAD")
        c = classify(subject=nfd)
        self.assertEqual(c.rule, Rule.KETHARMAD_JELENLEVO)

    def test_house_rules_deviation_outranks_two_thirds_keyword(self):
        # "Házszabálytól eltérés ... kétharmad" must resolve to four-fifths (first match wins).
        c = classify(subject="Házszabálytól eltérés a kétharmados törvény tárgyalásához")
        self.assertEqual(c.rule, Rule.NEGYOTOD_JELENLEVO)


class Metadata(unittest.TestCase):
    def test_every_rule_has_info_and_flags(self):
        for r in Rule:
            info = RULES[r]
            self.assertIn(info.base, ("present", "seats", "candidates"))
            self.assertTrue(info.basis)
            d = describe(r)
            self.assertEqual(d["rule"], r.value)
            self.assertIn("basis", d)

    def test_payload_and_keyword_tables_are_well_formed(self):
        for k, v in PAYLOAD_RULES.items():
            self.assertEqual(k, k.casefold())
            self.assertIsInstance(v, Rule)
        for pattern, rule in KEYWORD_RULES:
            self.assertIsInstance(rule, Rule)
            self.assertTrue(pattern)


if __name__ == "__main__":
    unittest.main()
