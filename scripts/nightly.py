#!/usr/bin/env python3
"""One run of the whole chain: sync what is new, rebuild, prove it, publish.

    python3 -m scripts.nightly --dry-run          # say what it would do, touch nothing
    python3 -m scripts.nightly                    # sync and stop unless something changed
    python3 -m scripts.nightly --force            # rebuild and publish even with nothing new
    python3 -m scripts.nightly --no-deploy        # everything up to the upload

Written to run unattended, which sets its two rules.

**It stops when nothing happened.** The House sits about a hundred days a year; on the other nights the sync finds
no new vote and no new sitting day, and there is nothing to rebuild. The cheap half runs daily, the expensive half
only when the record moved — which is also what keeps a scheduled build from costing more than the site.

**It refuses to publish what it cannot prove.** The test suite and the README gate run between the build and the
upload, and a failure in either ends the run with the old site still live. A stale site is a smaller harm than a
wrong one, and an unattended job is exactly where that trade has to be made in advance.

Every step is the same command a person would type; nothing here reimplements the pipeline, so what runs nightly
is what was tested by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FRESHNESS = ROOT / "data" / "derived" / "first_light.json"
CURRENT_CYCLE = 43
MIN_FREE_GB = 3.0                       # a full build writes about 6 GB; two builds died on a full volume


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}Z] {msg}", flush=True)


def run(cmd: list[str], *, dry: bool, allow_fail: bool = False) -> int:
    log("$ " + " ".join(cmd))
    if dry:
        return 0
    t0 = time.time()
    p = subprocess.run(cmd, cwd=ROOT)
    log(f"  → exit {p.returncode} in {time.time() - t0:.0f}s")
    if p.returncode and not allow_fail:
        raise SystemExit(f"step failed: {' '.join(cmd)}")
    return p.returncode


def corpus_state() -> tuple[int, str]:
    """(votes listed, newest sitting day) for the current cycle — the two things a sitting changes."""
    if not FRESHNESS.exists():
        return (0, "")
    d = json.loads(FRESHNESS.read_text(encoding="utf-8"))
    return (int(d.get("votes") or 0), str((d.get("sitting_days") or {}).get("last") or ""))


def free_gb() -> float:
    return shutil.disk_usage(ROOT).free / 1e9


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the steps, run none of them")
    ap.add_argument("--force", action="store_true", help="rebuild and publish even when nothing changed")
    ap.add_argument("--no-deploy", action="store_true", help="stop before the upload")
    ap.add_argument("--no-sync", action="store_true",
                    help="skip every API call and work from the cache — how the pipeline is exercised while a "
                         "long fetch already holds the one polite request stream")
    ap.add_argument("--days", type=int, default=10, help="how far back to re-list votes (default 10)")
    ap.add_argument("--profile", default=os.environ.get("KARZAT_AWS_PROFILE", "karzat"),
                    help='named AWS profile; pass "" in a role-based environment such as the scheduled build')
    ap.add_argument("--bucket", default=os.environ.get("KARZAT_BUCKET", "karzat-hu"))
    args = ap.parse_args(argv)
    dry = args.dry_run
    py = [sys.executable]

    log(f"karzat nightly · {free_gb():.1f} GB free · cycle {CURRENT_CYCLE}")
    before = corpus_state()
    log(f"before: {before[0]:,} votes listed, last sitting day {before[1] or '—'}")

    # ---- the cheap half: ask the API what is new -------------------------------------------------
    if args.no_sync:
        log("--no-sync: not calling the API at all; working from the cache as it stands")
    # a window rather than "since yesterday": the listing is re-read for the last few days because a sitting day's
    # rows can be completed after the fact, and re-listing a day that has not changed costs one call
    today = datetime.now(timezone.utc).date()
    since = today - timedelta(days=args.days)
    if not args.no_sync:
        run(py + ["-m", "karzat", "sync-votes", "--from", str(since), "--to", str(today)], dry=dry)
        run(py + ["-m", "karzat", "sync-speeches", "--ckl", str(CURRENT_CYCLE)], dry=dry)
        run(py + ["-m", "karzat", "sync-speech-texts", "--ckl", str(CURRENT_CYCLE)], dry=dry, allow_fail=True)
        run(py + ["-m", "karzat", "sync-committees"], dry=dry, allow_fail=True)   # they carry the next sitting's invitation
        run(py + ["-m", "karzat", "sync-bills", "--cached"], dry=dry, allow_fail=True)

    # ---- did anything move? ----------------------------------------------------------------------
    run(py + ["-m", "scripts.derive_first_light"], dry=dry)
    after = corpus_state()
    log(f"after:  {after[0]:,} votes listed, last sitting day {after[1] or '—'}")
    if after == before and not args.force and not dry:
        log("nothing new — no build, no upload. (--force overrides)")
        return 0
    log("dry run: every step below would run" if dry else ("the record moved" if after != before else "forced"))

    if free_gb() < MIN_FREE_GB and not dry:
        raise SystemExit(f"only {free_gb():.1f} GB free, want {MIN_FREE_GB} — stopping before the build")

    # ---- the expensive half ----------------------------------------------------------------------
    # Every derivation the site reads from, not a subset. The subset was the bug: for a while this ran seven of the
    # twelve, so a nightly refreshed the votes and the speeches while the footer's facts, the ministerial bench, the
    # seating plan and the repeated-passage index kept yesterday's answer — and the page still said "frissítve ma".
    # A stale number under a fresh timestamp is the one failure this project is built to prevent, and the pipeline
    # was quietly producing it. tests/test_site.py::NightlyRunsEveryDerivation now fails if a new derive script is
    # added without being wired in here.
    for step in (["-m", "scripts.derive_mps"], ["-m", "scripts.derive_seating"], ["-m", "scripts.derive_speeches"],
                 ["-m", "scripts.derive_speech_texts", "--cycle", str(CURRENT_CYCLE)],
                 ["-m", "scripts.derive_committees"], ["-m", "scripts.derive_szoszolok"],
                 ["-m", "scripts.derive_kormany"], ["-m", "scripts.derive_bills"],
                 ["-m", "scripts.derive_echo", "--cycle", str(CURRENT_CYCLE)],
                 ["-m", "scripts.derive_facts"]):
        run(py + step, dry=dry, allow_fail=True)
    run(py + ["-m", "karzat", "freshness"], dry=dry, allow_fail=True)
    run(py + ["-m", "scripts.build_site"], dry=dry)

    # ---- prove it before publishing --------------------------------------------------------------
    run(py + ["-m", "unittest", "discover", "-s", "tests", "-t", "."], dry=dry)
    run(py + ["-m", "scripts.check_readme"], dry=dry)

    if args.no_deploy:
        log("built and proven; --no-deploy, so stopping here")
        return 0
    deploy = ["-m", "scripts.deploy_aws", "--bucket", args.bucket, "--workers", "24"]
    if args.profile:
        deploy += ["--profile", args.profile]
    run(py + deploy, dry=dry)
    log("published")
    return 0


if __name__ == "__main__":
    sys.exit(main())
