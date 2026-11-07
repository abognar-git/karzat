"""The external watchdog: what it worries about, what it stays calm about, and what it is not allowed to need.

The thresholds are the product here. Too tight and it mails the owner every time the laptop sleeps through
one night; too loose and it repeats the two months it was built to end. Each boundary below is a case the
owner would actually live through.
"""

from __future__ import annotations

import ast
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 7, 8, 17, tzinfo=timezone.utc)


def hb(checked_h: float = 4, published_d: float = 1, refresh: float | None = 7) -> dict:
    d = {"checked_at": (NOW - timedelta(hours=checked_h)).isoformat(),
         "published_at": (NOW - timedelta(days=published_d)).isoformat(),
         "outcome": "published"}
    if refresh is not None:
        d["refresh_days"] = refresh
    return d


class WhatItWorriesAbout(unittest.TestCase):

    def setUp(self):
        from scripts.watch_live import evaluate
        self.ev = evaluate

    def test_a_healthy_night_says_nothing(self):
        self.assertEqual(self.ev(hb(), NOW), [])

    def test_one_missed_night_is_a_closed_laptop_not_an_emergency(self):
        """The plist's job fires on the next wake if the Mac slept through 04:10. One night without a beat
        is ordinary; an email for it would teach the owner to ignore the emails."""
        self.assertEqual(self.ev(hb(checked_h=40), NOW), [])

    def test_two_missed_nights_are_not_an_accident(self):
        p = self.ev(hb(checked_h=61), NOW)
        self.assertEqual(len(p), 1)
        self.assertIn("last finished properly", p[0])

    def test_a_pipeline_that_finishes_but_never_publishes_is_caught(self):
        """The nightly can end well every night ("nothing new") while the site goes stale — that is what a
        broken freshness test would look like. The heartbeat is fresh; the publish is not."""
        p = self.ev(hb(checked_h=2, published_d=10), NOW)
        self.assertEqual(len(p), 1)
        self.assertIn("not publishing", p[0])

    def test_a_quiet_week_in_the_house_is_not_a_fault(self):
        """No sitting, no rebuild, for up to refresh_days — then one forced publish on the next nightly.
        Eight days is inside that promise plus the night it takes to keep it."""
        self.assertEqual(self.ev(hb(published_d=8), NOW), [])

    def test_the_publish_promise_is_the_producers_not_a_copy(self):
        """refresh_days comes from the heartbeat. Change the nightly's policy and the watchdog follows,
        instead of a second constant drifting away from the first."""
        self.assertEqual(self.ev(hb(published_d=12, refresh=14), NOW), [])
        self.assertTrue(self.ev(hb(published_d=12, refresh=7), NOW))

    def test_a_missing_or_nonsense_promise_falls_back_rather_than_switching_the_check_off(self):
        for bad in (None, 0, -3, "soon"):
            with self.subTest(refresh=bad):
                d = hb(published_d=10)
                d["refresh_days"] = bad
                self.assertTrue(self.ev(d, NOW), "a broken refresh_days must not disable the publish check")

    def test_no_heartbeat_at_all_is_a_problem_not_a_pass(self):
        p = self.ev(None, NOW)
        self.assertEqual(len(p), 1)
        self.assertIn("could not be read", p[0])

    def test_the_site_being_down_is_said_on_its_own(self):
        p = self.ev(hb(), NOW, site_status=503)
        self.assertEqual(len(p), 1)
        self.assertIn("503", p[0])
        self.assertTrue(self.ev(hb(), NOW, site_status=None), "no answer at all is also down")

    def test_a_heartbeat_from_the_future_is_a_wrong_clock(self):
        """A clock far enough wrong breaks AWS request signing, which is one way the S3 handover can fail
        with an error that names the key rather than the time. Worth saying, not worth treating as fresh."""
        d = hb()
        d["checked_at"] = (NOW + timedelta(days=2)).isoformat()
        p = self.ev(d, NOW)
        self.assertTrue(any("wrong clock" in x for x in p))

    def test_an_unreadable_timestamp_is_reported_not_skipped(self):
        d = hb()
        d["checked_at"] = "tegnap"
        self.assertTrue(any("checked_at" in x for x in self.ev(d, NOW)))

    def test_never_published_is_a_problem(self):
        d = hb()
        d["published_at"] = ""
        self.assertTrue(any("never been published" in x for x in self.ev(d, NOW)))

    def test_a_naive_timestamp_is_read_as_utc_rather_than_crashing(self):
        d = hb()
        d["checked_at"] = (NOW - timedelta(hours=3)).replace(tzinfo=None).isoformat()
        self.assertEqual(self.ev(d, NOW), [])


class WhatItDoesAboutIt(unittest.TestCase):

    def test_the_issue_lifecycle(self):
        from scripts.watch_live import decide
        self.assertEqual(decide(["x"], None), "open")
        self.assertEqual(decide(["x"], {"number": 1}), "update")
        self.assertEqual(decide([], {"number": 1}), "close")
        self.assertEqual(decide([], None), "none")

    def test_a_standing_problem_mails_once_not_every_day(self):
        """An open issue is rewritten in place. A daily comment would be a daily email, and an alarm that
        rings every morning is an alarm nobody hears by the second week."""
        src = (ROOT / "scripts" / "watch_live.py").read_text(encoding="utf-8")
        upd = src[src.index('if action == "update":'):src.index('if action == "close":')]
        self.assertIn('"PATCH"', upd)
        self.assertNotIn("/comments", upd)

    def test_a_red_run_is_the_second_channel(self):
        from scripts.watch_live import main
        import scripts.watch_live as w
        keep = w.fetch_heartbeat
        try:
            w.fetch_heartbeat = lambda url=None: (None, 200)
            self.assertEqual(main([]), 1, "a problem must also fail the run")
            w.fetch_heartbeat = lambda url=None: ({"checked_at": datetime.now(timezone.utc).isoformat(),
                                                  "published_at": datetime.now(timezone.utc).isoformat(),
                                                  "refresh_days": 7}, 200)
            self.assertEqual(main([]), 0)
        finally:
            w.fetch_heartbeat = keep


class WhatItIsNotAllowedToNeed(unittest.TestCase):
    """The whole point is independence. Everything below is a way it could quietly lose it."""

    def test_it_imports_nothing_the_runner_would_have_to_install(self):
        tree = ast.parse((ROOT / "scripts" / "watch_live.py").read_text(encoding="utf-8"))
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module.split(".")[0])
        allowed = {"__future__", "argparse", "json", "os", "sys", "urllib", "datetime"}
        self.assertEqual(mods - allowed, set(), "the watchdog grew a dependency")

    def test_it_reaches_for_nothing_on_the_build_machine(self):
        src = (ROOT / "scripts" / "watch_live.py").read_text(encoding="utf-8")
        for forbidden in ("boto3", "karzat.", "scripts.", "data/derived", "~/Library", "parlament.hu/cgi"):
            code = "\n".join(l for l in src.splitlines() if not l.strip().startswith(("#", '"', "'")))
            self.assertNotIn(forbidden, code.split('"""', 2)[-1], f"the watchdog depends on {forbidden}")

    def test_the_workflow_holds_only_the_permissions_it_uses(self):
        y = (ROOT / ".github" / "workflows" / "freshness-watch.yml").read_text(encoding="utf-8")
        block = y[y.index("permissions:"):y.index("concurrency:")]
        perms = dict(re.findall(r"^\s+([a-z-]+):\s*(read|write|none)", block, re.M))
        self.assertEqual(perms, {"contents": "read", "issues": "write", "actions": "write"})

    def test_third_party_code_is_pinned_to_a_commit(self):
        """A tag can be moved under a workflow that holds a write token."""
        y = (ROOT / ".github" / "workflows" / "freshness-watch.yml").read_text(encoding="utf-8")
        uses = re.findall(r"uses:\s*([^\s#]+)", y)
        self.assertTrue(uses)
        for u in uses:
            with self.subTest(action=u):
                self.assertRegex(u, r"@[0-9a-f]{40}$")

    def test_it_runs_on_a_schedule_and_on_demand(self):
        y = (ROOT / ".github" / "workflows" / "freshness-watch.yml").read_text(encoding="utf-8")
        self.assertRegex(y, r'cron:\s*"\d+ \d+ \* \* \*"')
        self.assertIn("workflow_dispatch:", y)
        self.assertIn("scripts/watch_live.py --github", y)

    def test_the_schedule_cannot_be_switched_off_by_two_quiet_months(self):
        """GitHub disables a public repo's scheduled workflows after 60 days without activity, and the
        nightly never pushes. Two quiet months is the exact failure this watchdog exists to report."""
        src = (ROOT / "scripts" / "watch_live.py").read_text(encoding="utf-8")
        main = src[src.index("def main("):]
        self.assertIn("keep_schedule_alive(", main)


class TheProducerSide(unittest.TestCase):
    """The heartbeat means "a run reached a healthy end". It is worth exactly as much as that is true."""

    def setUp(self):
        self.src = (ROOT / "scripts" / "nightly.py").read_text(encoding="utf-8")

    def test_both_healthy_exits_beat_and_nothing_else_does(self):
        calls = [m.start() for m in re.finditer(r"\n\s+beat\(args, ", self.src)]
        self.assertEqual(len(calls), 2, "the heartbeat is written somewhere other than the two healthy exits")
        quiet = self.src.index('log("nothing new — no build, no upload.')
        pub = self.src.index('log("published")')
        for at in calls:
            near = min(abs(at - quiet), abs(at - pub))
            self.assertLess(near, 200, "a beat sits away from both healthy exits")

    def test_a_sync_only_run_does_not_claim_to_have_published(self):
        sync_only = self.src[self.src.index("if args.sync_only:\n        log("):]
        sync_only = sync_only[:sync_only.index("return 0")]
        self.assertNotIn("beat(", sync_only)

    def test_a_failed_run_never_beats(self):
        handler = self.src[self.src.index("except BaseException as e:"):self.src.index("def warn_locally")]
        self.assertNotIn("beat(", handler)

    def test_the_heartbeat_is_never_cached_at_the_edge(self):
        """Under the default a .json is held for a day, which would let a dead pipeline look alive for 24
        hours to the one reader whose only question is whether it is alive."""
        from scripts.deploy_aws import HEARTBEAT, HEARTBEAT_CACHE, CACHE_DEFAULT
        self.assertIn("no-cache", HEARTBEAT_CACHE)
        self.assertNotEqual(HEARTBEAT_CACHE, CACHE_DEFAULT)
        dep = (ROOT / "scripts" / "deploy_aws.py").read_text(encoding="utf-8")
        self.assertIn("HEARTBEAT_CACHE if key == HEARTBEAT", dep, "a deploy would re-upload it cacheable")
        self.assertIn("CacheControl=HEARTBEAT_CACHE", self.src, "the nightly's own upload would be cacheable")

    def test_the_heartbeat_does_not_claim_a_sitting_that_has_not_happened(self):
        """The index lists sittings already scheduled. A field called last_sitting_day carried 9 November
        on the 7th; it is named for what it is."""
        beat = self.src[self.src.index("def beat("):self.src.index("def _run(")]
        self.assertNotIn('"last_sitting_day"', beat)

    def test_the_heartbeat_never_leaves_this_repository(self):
        """It changes every night; committing it would put a line of noise into the history every day."""
        import subprocess
        r = subprocess.run(["git", "check-ignore", "-q", "site/heartbeat.json"], cwd=ROOT)
        if r.returncode == 128:
            self.skipTest("not a git checkout")
        self.assertEqual(r.returncode, 0, "site/heartbeat.json is not ignored")


if __name__ == "__main__":
    unittest.main()
