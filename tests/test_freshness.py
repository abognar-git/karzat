"""Tests for the freshness contract. Pure: fixed `now`, no network, no files (except the CLI test,
which uses a temp cache).

The scenario numbers are Konzol's own failure, transposed: newest vote 21 July, Parliament
sat on 22 and 23 July, sync last ran on 22 July, and the page is rendered on 18 August.
"""

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from karzat.freshness import (
    BUDAPEST,
    FORBIDDEN_WORDS,
    assess,
    fmt_date_en,
    fmt_date_hu,
    sitting_days_from_ulesnap,
)

NOW = datetime(2026, 8, 18, 9, 0, tzinfo=BUDAPEST)
V = lambda *a: datetime(*a, tzinfo=BUDAPEST)  # noqa: E731


class Sentences(unittest.TestCase):
    def test_konzol_scenario_is_behind_and_names_the_days(self):
        fr = assess(
            sitting_days=[date(2026, 7, 20), date(2026, 7, 21), date(2026, 7, 22), date(2026, 7, 23)],
            newest_vote_at=V(2026, 7, 21, 22, 13),
            last_sync_at=V(2026, 7, 22, 16, 19),
            now=NOW,
        )
        self.assertEqual(fr.status, "behind")
        self.assertEqual(fr.missed_sitting_days, (date(2026, 7, 22), date(2026, 7, 23)))
        self.assertTrue(fr.sync_stale)
        self.assertIn("Votes through 21 July 2026.", fr.sentence_en)
        self.assertIn("Parliament sat on 22 and 23 July 2026; the votes from those 2 sitting days are not here yet.",
                      fr.sentence_en)
        self.assertIn("Synced 26 days ago. The sync has not run on schedule.", fr.sentence_en)
        self.assertIn("Szavazások 2026. július 21-ig.", fr.sentence_hu)
        self.assertIn("Az Országgyűlés ülésezett 2026. július 22-én és 23-án;", fr.sentence_hu)
        self.assertIn("Frissítve 26 napja. A frissítés nem futott le a menetrend szerint.", fr.sentence_hu)

    def test_recess_is_not_staleness(self):
        # Newest vote on the latest sitting day, sync ran this morning, no sitting since -> current.
        fr = assess(
            sitting_days=[date(2026, 7, 21), date(2026, 7, 22), date(2026, 7, 23)],
            newest_vote_at=V(2026, 7, 23, 18, 5),
            last_sync_at=NOW - timedelta(hours=3),
            now=NOW,
        )
        self.assertEqual(fr.status, "current")
        self.assertEqual(fr.missed_sitting_days, ())
        self.assertIn("That was the most recent sitting day.", fr.sentence_en)
        self.assertIn("Synced 3 hours ago.", fr.sentence_en)
        self.assertNotIn("not run", fr.sentence_en)

    def test_stale_sync_with_no_missed_sittings(self):
        fr = assess(
            sitting_days=[date(2026, 7, 23)],
            newest_vote_at=V(2026, 7, 23, 18, 5),
            last_sync_at=NOW - timedelta(days=5),
            now=NOW,
        )
        self.assertEqual(fr.status, "stale")
        self.assertIn("Synced 5 days ago. The sync has not run on schedule.", fr.sentence_en)

    def test_absolute_sync_stamp_for_static_pages(self):
        # a static page read a week later must not say "15 perce": the stamp is a Budapest timestamp
        fr = assess(sitting_days=[date(2026, 8, 11)], newest_vote_at=V(2026, 8, 11, 14, 13),
                    last_sync_at=datetime(2026, 8, 18, 8, 49, tzinfo=timezone.utc), now=NOW + timedelta(hours=2), absolute_sync=True)
        self.assertEqual(fr.status, "current")
        self.assertIn("Frissítve: 2026. augusztus 18. 10:49 (budapesti idő).", fr.sentence_hu)
        self.assertIn("Synced 18 August 2026 10:49 Budapest time.", fr.sentence_en)
        self.assertNotIn("perce", fr.sentence_hu)
        # the age wording is unchanged when the flag is off
        fr2 = assess(sitting_days=[date(2026, 8, 11)], newest_vote_at=V(2026, 8, 11, 14, 13),
                     last_sync_at=NOW - timedelta(minutes=15), now=NOW)
        self.assertIn("Frissítve 15 perce.", fr2.sentence_hu)

    def test_future_sitting_days_are_announced_not_missed(self):
        fr = assess(
            sitting_days=[date(2026, 7, 23), date(2026, 9, 14)],
            newest_vote_at=V(2026, 7, 23, 18, 5),
            last_sync_at=NOW - timedelta(hours=1),
            now=NOW,
        )
        self.assertEqual(fr.status, "current")
        self.assertEqual(fr.scheduled_sitting_days, (date(2026, 9, 14),))
        self.assertIn("Next sitting day announced: 14 September 2026.", fr.sentence_en)
        self.assertIn("Következő bejelentett ülésnap: 2026. szeptember 14.", fr.sentence_hu)

    def test_unknown_when_no_sitting_days(self):
        fr = assess(sitting_days=None, newest_vote_at=V(2026, 7, 21, 22, 13),
                    last_sync_at=NOW - timedelta(hours=2), now=NOW)
        self.assertEqual(fr.status, "unknown")
        self.assertIn("cannot be told", fr.sentence_en)
        self.assertIn("nem állapítható meg", fr.sentence_hu)

    def test_empty_when_no_votes(self):
        fr = assess(sitting_days=[date(2026, 7, 23)], newest_vote_at=None, last_sync_at=None, now=NOW)
        self.assertEqual(fr.status, "empty")
        self.assertIn("No votes are held yet.", fr.sentence_en)
        self.assertIn("The sync has never completed.", fr.sentence_en)

    def test_single_missed_day_wording(self):
        fr = assess(sitting_days=[date(2026, 7, 21), date(2026, 7, 22)],
                    newest_vote_at=V(2026, 7, 21, 22, 13), last_sync_at=NOW, now=NOW)
        self.assertIn("Parliament sat on 22 July 2026; the votes from that day are not here yet.", fr.sentence_en)
        self.assertIn("annak a napnak a szavazásai még hiányoznak", fr.sentence_hu)

    def test_missed_days_across_months(self):
        fr = assess(sitting_days=[date(2026, 6, 30), date(2026, 7, 1)],
                    newest_vote_at=V(2026, 6, 15, 10, 0), last_sync_at=NOW, now=NOW)
        self.assertIn("30 June 2026 and 1 July 2026", fr.sentence_en)
        self.assertIn("2026. június 30-án és 2026. július 1-jén", fr.sentence_hu)

    def test_hungarian_day_suffixes_follow_vowel_harmony(self):
        from karzat.freshness import fmt_date_hu_en, fmt_date_hu_ig
        expect = {1: "1-jén", 2: "2-án", 3: "3-án", 4: "4-én", 5: "5-én", 6: "6-án", 7: "7-én", 8: "8-án",
                  9: "9-én", 10: "10-én", 11: "11-én", 12: "12-én", 13: "13-án", 16: "16-án", 20: "20-án",
                  21: "21-én", 22: "22-én", 23: "23-án", 26: "26-án", 28: "28-án", 30: "30-án", 31: "31-én"}
        for day, suffix in expect.items():
            with self.subTest(day=day):
                self.assertTrue(fmt_date_hu_en(date(2026, 1, day)).endswith(suffix))
        self.assertEqual(fmt_date_hu_ig(date(2026, 5, 1)), "2026. május 1-jéig")
        self.assertEqual(fmt_date_hu_ig(date(2026, 7, 21)), "2026. július 21-ig")

    def test_forbidden_words_never_appear(self):
        for kwargs in (
            dict(sitting_days=[date(2026, 7, 23)], newest_vote_at=V(2026, 7, 23, 1), last_sync_at=NOW, now=NOW),
            dict(sitting_days=None, newest_vote_at=V(2026, 7, 23, 1), last_sync_at=None, now=NOW),
            dict(sitting_days=[], newest_vote_at=None, last_sync_at=None, now=NOW),
        ):
            fr = assess(**kwargs)
            for w in FORBIDDEN_WORDS:
                self.assertNotIn(w, fr.sentence_en.casefold())
                self.assertNotIn(w, fr.sentence_hu.casefold())

    def test_age_wording_ladder(self):
        base = dict(sitting_days=[date(2026, 8, 18)], newest_vote_at=V(2026, 8, 18, 8, 0), now=NOW)
        self.assertIn("Synced 5 minutes ago.", assess(last_sync_at=NOW - timedelta(minutes=5), **base).sentence_en)
        self.assertIn("Synced 1 minute ago.", assess(last_sync_at=NOW - timedelta(seconds=20), **base).sentence_en)
        self.assertIn("Synced 26 hours ago.", assess(last_sync_at=NOW - timedelta(hours=26), **base).sentence_en)
        self.assertIn("Synced 3 days ago.", assess(last_sync_at=NOW - timedelta(days=3), **base).sentence_en)

    def test_to_dict_is_json_serialisable(self):
        fr = assess(sitting_days=[date(2026, 7, 23)], newest_vote_at=V(2026, 7, 23, 1),
                    last_sync_at=NOW - timedelta(hours=1), now=NOW)
        d = json.loads(json.dumps(fr.to_dict()))
        self.assertEqual(d["status"], "current")
        self.assertEqual(d["newest_vote_day"], "2026-07-23")
        self.assertEqual(d["sync_age_seconds"], 3600)


class Formatting(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(fmt_date_hu(date(2026, 7, 21)), "2026. július 21.")
        self.assertEqual(fmt_date_en(date(2026, 7, 21)), "21 July 2026")


class UlesnapAdapter(unittest.TestCase):
    def test_finds_dates_under_ulesnap_regardless_of_key(self):
        payload = {"ulesnapok": {"ulesnap": [
            {"@sorszam": "1", "@datum": "2026.05.09"},
            {"@sorszam": "2", "datum": "2026.05.12."},
            {"@sorszam": "3", "adatok": {"nap": "2026.05.13", "megjegyzes": "rendkívüli"}},
        ]}}
        self.assertEqual(sitting_days_from_ulesnap(payload),
                         [date(2026, 5, 9), date(2026, 5, 12), date(2026, 5, 13)])

    def test_single_ulesnap_and_no_dates(self):
        self.assertEqual(sitting_days_from_ulesnap({"ulesnapok": {"ulesnap": {"@datum": "2026.05.09"}}}),
                         [date(2026, 5, 9)])
        self.assertEqual(sitting_days_from_ulesnap({"ulesnapok": {"ulesnap": {"@x": "nope"}}}), [])
        self.assertEqual(sitting_days_from_ulesnap({"other": "2026.05.09"}), [])   # only under <ulesnap>


class Cli(unittest.TestCase):
    def test_freshness_command_runs_offline_from_cache_and_state(self):
        from karzat.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "raw"
            (cache / "szavazas").mkdir(parents=True)
            (cache / "szavazas" / "2026-07-21T22-13-00.xml").write_bytes(b"<szavazas/>")
            (cache / "ulesnap").mkdir()
            (cache / "ulesnap" / "e6b1c1c8f6d1c1a1.xml").write_bytes(b"<x/>")  # wrong name: not the p_ckl=43 key
            (Path(tmp) / "sync_state.json").write_text(json.dumps({"last_sync_at": "2026-07-22T16:19:00+02:00"}))
            rc = main(["--cache", str(cache), "freshness", "--ckl", "43"])
            self.assertEqual(rc, 0)
            out = json.loads((Path(tmp) / "freshness.json").read_text())
            self.assertEqual(out["newest_vote_day"], "2026-07-21")
            self.assertEqual(out["status"], "unknown")   # no ulesnap payload for ckl 43 in cache
            self.assertTrue(out["sync_stale"])


if __name__ == "__main__":
    unittest.main()
