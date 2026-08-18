"""Normaliser tests against real W-API payloads captured on 2026-08-18 (tests/fixtures/real_*).

These pin *facts about the payloads* as much as code behaviour: vocabularies, counts, and the
majority verdicts agreeing with the Országgyűlés's own Elfogadva/Elutasítva. A failure after a
re-capture means the API changed shape or vocabulary — which is precisely what to be told.
"""

import unittest
from pathlib import Path

from karzat.freshness import sitting_days_from_ulesnap
from karzat.majority import Rule, classify
from karzat.normalise import (
    NameResolver,
    parse_iromany,
    parse_iromany_szam,
    parse_iromanyok,
    parse_kepviselo,
    parse_kepviselok,
    parse_szavazas,
    parse_szavazasok,
    parse_ulesnap,
    strip_honorific,
    submitter_kind,
)
from karzat.xmlutil import parse_xml, to_dict

FX = Path(__file__).parent / "fixtures"


def load(name):
    root = parse_xml((FX / name).read_bytes())
    return {root.tag: to_dict(root)}


class Members(unittest.TestCase):
    def test_kepviselok_list_fields(self):
        mps = parse_kepviselok(load("real_kepviselok_head5.xml"))
        self.assertEqual(len(mps), 5)
        first = mps[0]
        self.assertEqual(first["p_azon"], "a011")
        self.assertEqual(first["name"], "Ágh Péter")
        self.assertEqual(first["faction"], "Fidesz")
        self.assertEqual(first["county"], "Vas")
        self.assertEqual(first["constituency_no"], 2)
        self.assertEqual(first["mandate_kind"], "egyeni")
        self.assertTrue(first["photo_url"].endswith("/kepviselo-kepek/a011"))
        kinds = {m["mandate_kind"] for m in mps}
        self.assertTrue(kinds <= {"egyeni", "lista"})
        lista = [m for m in mps if m["mandate_kind"] == "lista"]
        self.assertTrue(all(m["county"] == "Országos lista" and m["constituency_no"] is None for m in lista))

    def test_kepviselo_record_seat_factions_elections(self):
        r = parse_kepviselo(load("real_kepviselo_a011_trimmed.xml"))
        self.assertEqual(r["name"], "Ágh Péter")
        self.assertEqual(r["seat"], {"sector": 2, "row": 5, "seat": 7})
        self.assertEqual(r["factions"][0], {"ciklus": "2026-", "faction": "Fidesz", "from": "2026-05-09", "to": None})
        self.assertEqual(r["factions"][1]["to"], "2026-05-08")
        self.assertEqual(r["elections"][0]["constituency"], "Vas 2. OEVK")
        self.assertEqual(r["elections"][0]["elected_on"], "2026-04-12")
        self.assertEqual(r["elections"][0]["mandate_from"], "2026-05-09")
        self.assertEqual(r["motion_stats"][0], {"ciklus": "2026-", "onallo": 5, "nem_onallo": 0})

    def test_name_resolver_exact_bare_and_alias(self):
        mps = parse_kepviselok(load("real_kepviselok_head5.xml"))
        res = NameResolver(mps, aliases={"Szijjártó Péter": "s999"})
        self.assertEqual(res.resolve("Ágh Péter (Fidesz)"), "a011")
        # honorific-stripped fallback
        self.assertEqual(res.resolve("Dr. Ágh Péter (Fidesz)"), "a011")
        # alias table covers former MPs printed with an honorific
        self.assertEqual(res.resolve("Dr. Szijjártó Péter (Fidesz)"), "s999")
        self.assertIsNone(res.resolve("Senki Sehol (TISZA)"))
        self.assertEqual(strip_honorific("Dr. Árvay Nikolett"), "Árvay Nikolett")
        self.assertEqual(NameResolver.split("Dr. Árvay Nikolett (TISZA)"), ("Dr. Árvay Nikolett", "TISZA"))


class SittingDays(unittest.TestCase):
    def test_ulesnap_43(self):
        days = parse_ulesnap(load("real_ulesnap_43.xml"))
        self.assertEqual(len(days), 23)
        self.assertEqual(days[0], {"date": "2026-05-09", "ulnap": 1, "weekday": "szombat", "session": "2026. tavaszi",
                                   "kind": "Rendes", "remark": None})
        self.assertEqual(days[-1]["date"], "2026-08-11")
        self.assertEqual(days[-1]["session"], "2026. nyári")
        self.assertEqual(days[-1]["kind"], "Rendkívüli")

    def test_freshness_adapter_reads_real_payload(self):
        from datetime import date
        d = sitting_days_from_ulesnap(load("real_ulesnap_43.xml"))
        self.assertEqual(len(d), 23)
        self.assertEqual(d[0], date(2026, 5, 9))
        self.assertEqual(d[-1], date(2026, 8, 11))


class VoteLists(unittest.TestCase):
    def test_list_records(self):
        votes = parse_szavazasok(load("real_szavazasok_2026-05_head3.xml"))
        self.assertEqual(len(votes), 3)
        v = votes[0]
        for k in ("ts", "slug", "on_date", "mode", "igen", "nem", "tartozkodott", "osszes_szavazat", "result_raw", "passed", "motions"):
            self.assertIn(k, v)
        self.assertEqual(v["osszes_szavazat"], v["igen"] + v["nem"] + v["tartozkodott"])
        self.assertRegex(v["slug"], r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$")
        self.assertEqual(v["on_date"], v["ts"][:10].replace(".", "-"))
        for m in v["motions"]:
            self.assertIn("iromany_parsed", m)


class VoteDetails(unittest.TestCase):
    def test_two_thirds_of_present_rejected_mentelmi(self):
        v = parse_szavazas(load("real_szavazas_2026-05-26T16-31-45.xml"))
        self.assertEqual(v["mode"], "Listás a jelenlevők 2/3-ával")
        self.assertEqual((v["igen"], v["nem"], v["tartozkodott"], v["osszes_szavazat"]), (9, 178, 5, 192))
        self.assertFalse(v["passed"])
        self.assertEqual(v["motions"][0]["iromany"], "H/84")
        self.assertEqual(v["motions"][0]["submitters"], ["Mentelmi Bizottság"])
        self.assertEqual(v["motions"][0]["submitter_kind"], "bizottsag")
        self.assertEqual(v["motions"][0]["outcome"], "mentelmi jog fenntartva")
        self.assertEqual(len(v["positions"]), 199)
        self.assertEqual(v["position_counts"], {"nem": 178, "igen": 9, "tartozkodott": 5, "bejelentett_hianyzo": 5, "nem_szavazott": 2})
        self.assertEqual(v["present"], 192)
        self.assertEqual([t["faction"] for t in v["faction_tallies"]], ["TISZA", "Fidesz", "KDNP", "Mi Hazánk"])
        self.assertEqual(v["totals_row"]["osszesen"], 199)
        # tallies agree with the roll call
        self.assertEqual(sum(t["igen"] for t in v["faction_tallies"]), 9)
        self.assertEqual(sum(t["bejelentett_hianyzo"] for t in v["faction_tallies"]), 5)
        m = v["majority"]
        self.assertEqual(m["rule"], "ketharmad_jelenlevo")
        self.assertEqual(m["source"], "payload")
        self.assertEqual(m["needed"], 128)          # ceil(2*192/3)
        self.assertFalse(m["passed_by_rule"])
        self.assertTrue(m["agrees_with_source"])
        self.assertFalse(m["present_assumed"])

    def test_alaptorveny_amendment_two_thirds_of_all(self):
        v = parse_szavazas(load("real_szavazas_2026-06-15T17-20-04.xml"))
        self.assertEqual(v["mode"], "Listás az összes képviselő 2/3-ával")
        self.assertEqual((v["igen"], v["nem"], v["tartozkodott"]), (135, 50, 6))
        self.assertTrue(v["passed"])
        self.assertEqual(v["motions"][0]["iromany"], "T/51")
        self.assertIn("tizenhatodik módosítása", v["motions"][0]["title"])
        self.assertEqual(v["motions"][0]["submitter_kind"], "kepviselo")
        m = v["majority"]
        self.assertEqual(m["rule"], "ketharmad_osszes")
        self.assertEqual(m["needed"], 133)
        self.assertEqual(m["margin"], 2)
        self.assertTrue(m["passed_by_rule"])
        self.assertTrue(m["agrees_with_source"])

    def test_secret_ballot_has_no_roll_call(self):
        v = parse_szavazas(load("real_szavazas_2026-05-09T11-50-00.xml"))
        self.assertTrue(v["secret"])
        self.assertEqual(v["mode"], "Titkos")
        self.assertFalse(v["roll_call_available"])
        self.assertEqual(v["positions"], [])
        self.assertEqual(v["present"], 195)
        self.assertEqual(v["present_basis"], "tulajdonsagok: Összes szavazat (no roll call)")
        self.assertEqual(v["motions"][0]["iromany"], "S/3")
        self.assertEqual(v["majority"]["rule"], "egyszeru")
        self.assertTrue(v["majority"]["agrees_with_source"])

    def test_quorum_check_is_not_a_decision(self):
        v = parse_szavazas(load("real_szavazas_2026-05-09T10-45-47.xml"))
        self.assertEqual(v["kind"], "jelenlet")
        self.assertEqual(v["result_raw"], "Határozatképes")
        self.assertIsNone(v["passed"])
        self.assertIsNone(v["majority"])
        self.assertIn("jelen_nem_szavazott", v["position_counts"])
        self.assertEqual(v["position_counts"]["jelen_nem_szavazott"], 8)
        self.assertEqual(v["present"], 171 + 14 + 4 + 8)
        self.assertEqual(v["motions"], [])

    def test_no_unknown_position_labels_in_any_fixture(self):
        for name in ["real_szavazas_2026-05-26T16-31-45.xml", "real_szavazas_2026-05-09T10-45-47.xml",
                     "real_szavazas_2026-06-15T17-20-04.xml"]:
            v = parse_szavazas(load(name))
            self.assertFalse([k for k in v["position_counts"] if k.startswith("unknown")], name)


class Bills(unittest.TestCase):
    def test_iromany_T71_promulgation_and_speed(self):
        b = parse_iromany(load("real_iromany_71.xml"))
        self.assertEqual(b["szam"], "T/71")
        self.assertEqual(b["main_type"], "törvényjavaslat")
        self.assertEqual(b["type"], "törvényjavaslat nemzetközi szerződésről")
        self.assertEqual(b["submitted_on"], "2026-05-25")
        self.assertEqual(b["status"], "kihirdetve")
        self.assertEqual(b["procedure_mode"], "kivételes tárgyalásban")
        self.assertEqual(b["procedure_kind"], "kiveteles")
        self.assertEqual(b["promulgation"], {"number": "XV", "mk_issue": "60", "date": "2026-05-28",
                                              "law_ref": "2026. évi XV. törvény"})
        self.assertEqual(b["submitters"], ["kormány (igazságügyi miniszter)"])
        self.assertEqual(b["submitter_kind"], "kormany")
        self.assertEqual(b["days_submission_to_promulgation"], 3)
        self.assertEqual(b["final_vote_ts"], "2026.05.27.10:34:43")
        self.assertEqual(b["days_submission_to_final_vote"], 2)
        self.assertEqual(len(b["votes"]), 3)
        self.assertEqual(b["votes"][0]["outcome"], "kivételességi javaslat elfogadva")
        self.assertTrue(any(e["text"] == "kivételességi javaslat elfogadva" for e in b["events"]))
        self.assertEqual(b["committees"][0]["name"], "Törvényalkotási Bizottság")
        self.assertEqual(b["committees"][0]["rule"], "62. § (5) bekezdés")

    def test_iromanyok_list(self):
        rows = parse_iromanyok(load("real_iromanyok_head3.xml"))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["szam"], "T/505")
        self.assertEqual(rows[0]["izon"], "505")
        self.assertEqual(rows[0]["status_type"], "folyamatban")
        self.assertEqual(rows[0]["submitter_kind"], "kormany")
        self.assertEqual(rows[0]["szam_parsed"], {"kind": "T", "number": 505})

    def test_iromany_szam_parsing(self):
        self.assertEqual(parse_iromany_szam("T/71"), {"kind": "T", "number": 71})
        self.assertEqual(parse_iromany_szam("H/84"), {"kind": "H", "number": 84})
        self.assertEqual(parse_iromany_szam("56/4"), {"kind": "amendment", "parent": 56, "number": 4})
        self.assertEqual(parse_iromany_szam(None), {"kind": None})
        self.assertEqual(parse_iromany_szam("x")["kind"], "other")

    def test_submitter_kind(self):
        self.assertEqual(submitter_kind(["kormány (igazságügyi miniszter)"]), "kormany")
        self.assertEqual(submitter_kind(["Mentelmi Bizottság"]), "bizottsag")
        self.assertEqual(submitter_kind(["Melléthei-Barna Márton (TISZA), Dr. Hantosi István (TISZA)"]), "kepviselo")
        self.assertEqual(submitter_kind(["az Országgyűlés korelnöke"]), "other")
        self.assertIsNone(submitter_kind([]))


class RealModeVocabulary(unittest.TestCase):
    def test_every_observed_mode_classifies_from_payload(self):
        expect = {
            "Listás": Rule.EGYSZERU,
            "Listás a jelenlevők 2/3-ával": Rule.KETHARMAD_JELENLEVO,
            "Listás az összes képviselő 2/3-ával": Rule.KETHARMAD_OSSZES,
            "Listás az összes képviselő felével": Rule.ABSZOLUT,
            "Listás a jelenlevők 4/5-ével": Rule.NEGYOTOD_JELENLEVO,
            "Titkos az összes képviselő 2/3-ával": Rule.KETHARMAD_OSSZES,
            "Titkos": Rule.EGYSZERU,
        }
        for mode, rule in expect.items():
            with self.subTest(mode=mode):
                c = classify(payload_rule=mode)
                self.assertEqual(c.rule, rule)
                self.assertEqual(c.source, "payload")


if __name__ == "__main__":
    unittest.main()
