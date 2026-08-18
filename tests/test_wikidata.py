"""Tests for the Wikidata identity spine.

Two layers: pure parsing on synthetic SPARQL/Commons JSON (no network), and consistency of
the committed snapshot reference/wikidata/members_ckl43.json with itself and with
config/factions.yml. If the snapshot is re-pulled and the counts move, the config and the
README must move with it — deliberately.
"""

import json
import re
import unittest
from pathlib import Path

from karzat.wikidata import (
    MEMBER_POSITION,
    Member,
    apply_licences,
    commons_licence_query,
    image_thumb_url,
    members_query,
    parse_rows,
    summarise,
)

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "reference" / "wikidata" / "members_ckl43.json"
FACTIONS = ROOT / "config" / "factions.yml"


def B(**kv):
    """SPARQL binding row from plain values."""
    return {k: {"value": v} for k, v in kv.items() if v is not None}


class Query(unittest.TestCase):
    def test_query_mentions_position_and_since(self):
        q = members_query("2026-04-01")
        self.assertIn(f"wd:{MEMBER_POSITION}", q)
        self.assertIn('"2026-04-01T00:00:00Z"^^xsd:dateTime', q)
        for prop in ("pq:P580", "pq:P582", "pq:P4100", "pq:P768", "wdt:P4966", "wdt:P18"):
            self.assertIn(prop, q)


class Parsing(unittest.TestCase):
    def test_rows_fold_into_one_member_per_statement(self):
        rows = [
            B(p="http://www.wikidata.org/entity/Q1", nameHu="Minta Anna", nameEn="Anna Minta",
              start="2026-05-09T00:00:00Z", group="http://www.wikidata.org/entity/Q125418097",
              groupLabel="Tisztelet és Szabadság Párt", ogyId="a018",
              image="http://commons.wikimedia.org/wiki/Special:FilePath/Minta%20Anna.jpg"),
            # second binding for the same statement: another id, another image
            B(p="http://www.wikidata.org/entity/Q1", nameHu="Minta Anna", nameEn="Anna Minta",
              start="2026-05-09T00:00:00Z", group="http://www.wikidata.org/entity/Q125418097",
              groupLabel="Tisztelet és Szabadság Párt", ogyId="a019",
              image="http://commons.wikimedia.org/wiki/Special:FilePath/Minta%20Anna%202.jpg"),
            # a different person, ended statement, no id, no image
            B(p="http://www.wikidata.org/entity/Q2", nameHu="Példa Béla", start="2026-05-09T00:00:00Z",
              end="2026-07-15T00:00:00Z", group="http://www.wikidata.org/entity/Q387006", groupLabel="Fidesz",
              district="http://www.wikidata.org/entity/Q9", districtLabel="Pest vármegyei 8. sz.",
              dob="1970-01-02T00:00:00Z", sexLabel="férfi"),
        ]
        ms = parse_rows(rows)
        self.assertEqual([m.qid for m in ms], ["Q1", "Q2"])
        a = ms[0]
        self.assertEqual(a.ogy_ids, ["a018", "a019"])
        self.assertEqual(a.images, ["Minta Anna.jpg", "Minta Anna 2.jpg"])
        self.assertEqual(a.image_url, image_thumb_url("Minta Anna.jpg"))
        self.assertIn("Minta%20Anna.jpg?width=400", a.image_url)
        self.assertEqual(a.group_qid, "Q125418097")
        self.assertIsNone(a.end)
        b = ms[1]
        self.assertEqual(b.end, "2026-07-15")
        self.assertEqual(b.dob, "1970-01-02")
        self.assertEqual(b.district_qid, "Q9")
        self.assertEqual(b.ogy_ids, [])
        self.assertIsNone(b.image_url)

    def test_same_person_two_statements_are_two_records(self):
        rows = [B(p="http://www.wikidata.org/entity/Q1", nameHu="X", start="2026-05-09T00:00:00Z", end="2026-06-01T00:00:00Z"),
                B(p="http://www.wikidata.org/entity/Q1", nameHu="X", start="2026-07-01T00:00:00Z")]
        ms = parse_rows(rows)
        self.assertEqual(len(ms), 2)
        self.assertEqual([m.start for m in ms], ["2026-05-09", "2026-07-01"])

    def test_summary_counts_current_only_and_flags_duplicate_ids(self):
        ms = [Member("Q1", "A", None, ogy_ids=["x1"], group="G1", start="2026-05-09"),
              Member("Q2", "B", None, ogy_ids=["x1"], group="G1", start="2026-05-09"),
              Member("Q3", "C", None, ogy_ids=[], group="G2", start="2026-05-09", end="2026-07-01",
                     images=["c.jpg"])]
        s = summarise(ms)
        self.assertEqual(s["statements"], 3)
        self.assertEqual(s["current_members"], 2)
        self.assertEqual(s["ended_statements"], 1)
        self.assertEqual(s["by_group_current"], {"G1": 2})
        self.assertEqual(s["with_ogy_id"], 2)
        self.assertEqual(s["with_image"], 0)          # the only image belongs to an ended statement
        self.assertEqual(s["duplicate_ogy_ids"], ["x1"])


class Licences(unittest.TestCase):
    def test_commons_query_batches_titles(self):
        q = commons_licence_query(["A.jpg", "B C.png"])
        self.assertEqual(q["titles"], "File:A.jpg|File:B C.png")
        self.assertIn("extmetadata", q["iiprop"])

    def test_apply_licences_handles_normalised_titles_and_html_authors(self):
        ms = [Member("Q1", "A", None, images=["Minta_Anna.jpg"]), Member("Q2", "B", None, images=["Nope.jpg"])]
        commons = {"query": {
            "normalized": [{"from": "File:Minta_Anna.jpg", "to": "File:Minta Anna.jpg"}],
            "pages": {"1": {"title": "File:Minta Anna.jpg", "imageinfo": [{"extmetadata": {
                "LicenseShortName": {"value": "CC BY-SA 4.0"},
                "Artist": {"value": '<a href="//x">Fotó Ferenc</a>'}}}]},
                      "2": {"title": "File:Nope.jpg", "missing": ""}}}}
        apply_licences(ms, commons)
        self.assertEqual(ms[0].licence, "CC BY-SA 4.0")
        self.assertEqual(ms[0].image_author, "Fotó Ferenc")
        self.assertIsNone(ms[1].licence)


class Snapshot(unittest.TestCase):
    """The committed snapshot must agree with itself and with the config."""

    @classmethod
    def setUpClass(cls):
        cls.snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        cls.members = [Member(**m) for m in cls.snap["members"]]

    def test_summary_matches_recomputation(self):
        self.assertEqual(summarise(self.members), self.snap["summary"])

    def test_current_members_fill_the_chamber_and_ids_are_unique(self):
        s = self.snap["summary"]
        seats = int(re.search(r"^seats:\s*(\d+)", FACTIONS.read_text(encoding="utf-8"), re.M).group(1))
        self.assertEqual(s["current_members"], seats)
        self.assertEqual(s["duplicate_ogy_ids"], [])
        # every id looks like a p_azon (alnum, per P4966's format constraint)
        for m in self.members:
            for oid in m.ogy_ids:
                self.assertRegex(oid, r"^[A-Za-z0-9]+$")

    def test_config_seat_counts_equal_snapshot_groups(self):
        text = FACTIONS.read_text(encoding="utf-8")
        cfg = {}
        cur = None
        for line in text.splitlines():
            m = re.match(r"\s*wikidata_group:\s*(.+?)\s*$", line)
            if m:
                cur = m.group(1)
            m = re.match(r"\s*wikidata_seats_2026_08_18:\s*(\d+)", line)
            if m and cur:
                cfg[cur] = int(m.group(1))
                cur = None
        self.assertEqual(cfg, self.snap["summary"]["by_group_current"])

    def test_constituency_count_is_the_statutory_number_of_single_member_districts(self):
        # 106 egyéni választókerület (2011. évi CCIII. törvény, Vjt.). If Wikidata's P768 coverage
        # is complete, the count of current MPs with a constituency is exactly that.
        self.assertEqual(self.snap["summary"]["with_district"], 106)

    def test_every_current_member_has_a_group_and_a_name(self):
        for m in self.members:
            if m.end is None:
                self.assertTrue(m.group, m.qid)
                self.assertTrue(m.name_hu or m.name_en, m.qid)


if __name__ == "__main__":
    unittest.main()
