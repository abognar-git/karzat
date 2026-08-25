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


def publish_age_days() -> float:
    """Days since the last successful publish, by this pipeline's own receipt; infinity when it never published."""
    f = ROOT / "data" / "last_publish.json"
    if not f.exists():
        return float("inf")
    try:
        t = datetime.fromisoformat(json.loads(f.read_text(encoding="utf-8"))["published_at"])
        return (datetime.now(timezone.utc) - t).total_seconds() / 86400
    except Exception:
        return float("inf")


def upload_cache(profile: str, bucket: str) -> None:
    """data/raw → s3://<ops>/cache and the sync state → state/, size-compared like the buildspec's own sync.

    boto3 rather than the aws CLI for the same reason deploy_aws uses it: it is the one AWS client this
    repository already depends on, and the machine this runs on has no working CLI to promise."""
    import boto3
    sess = boto3.Session(profile_name=profile) if profile else boto3.Session()
    s3 = sess.client("s3")
    have: dict[str, int] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="cache/"):
        for o in page.get("Contents") or []:
            have[o["Key"]] = o["Size"]
    raw = ROOT / "data" / "raw"
    todo = [p for p in raw.rglob("*") if p.is_file()
            and have.get(f"cache/{p.relative_to(raw)}") != p.stat().st_size]
    log(f"cache upload: {len(todo):,} changed of {sum(1 for p in raw.rglob('*') if p.is_file()):,} files")
    import concurrent.futures as _cf
    with _cf.ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(lambda p: s3.upload_file(str(p), bucket, f"cache/{p.relative_to(raw)}"), todo))
    for name in ("sync_state.json", "last_publish.json"):
        f = ROOT / "data" / name
        if f.exists():
            s3.upload_file(str(f), bucket, f"state/{name}")
    log(f"handed over to s3://{bucket}/ (cache + state)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the steps, run none of them")
    ap.add_argument("--force", action="store_true", help="rebuild and publish even when nothing changed")
    ap.add_argument("--no-deploy", action="store_true", help="stop before the upload")
    ap.add_argument("--no-sync", action="store_true",
                    help="skip every API call and work from the cache — how the pipeline is exercised while a "
                         "long fetch already holds the one polite request stream")
    ap.add_argument("--sync-only", action="store_true",
                    help="run the cheap half and hand it over: sync, then upload data/raw and the sync state to "
                         "the ops bucket for the scheduled build — for the machine whose network the API answers, "
                         "because parlament.hu serves the API to the reader's network and not to the datacenter's")
    ap.add_argument("--ops-bucket", default=os.environ.get("KARZAT_OPS_BUCKET", "karzat-ops"),
                    help="the bucket the scheduled build reads its cache and state from")
    ap.add_argument("--max-state-age", type=float, default=0, metavar="HOURS",
                    help="with --no-sync: refuse to run when the sync state is older than this many hours — the "
                         "guard that turns a silently dead local sync into a loud scheduled failure (0 = off)")
    ap.add_argument("--handover", action="store_true",
                    help="after the sync, also upload data/raw and the state to the ops bucket on a full run — "
                         "keeps the scheduled build's cache current while the whole chain runs locally")
    ap.add_argument("--sns-topic", default="",
                    help="publish a failure alert to this SNS topic ARN when the run dies — the property the "
                         "whole nightly is built around is that failure is loud, wherever the run happens to live")
    ap.add_argument("--refresh-days", type=float, default=0, metavar="DAYS",
                    help="rebuild and publish even with nothing new once the last publish is older than this many "
                         "days, so the freshness stamp ages only so far through a recess (0 = off)")
    ap.add_argument("--days", type=int, default=10, help="how far back to re-list votes (default 10)")
    ap.add_argument("--profile", default=os.environ.get("KARZAT_AWS_PROFILE", "karzat"),
                    help='named AWS profile; pass "" in a role-based environment such as the scheduled build')
    ap.add_argument("--bucket", default=os.environ.get("KARZAT_BUCKET", "karzat-hu"))
    args = ap.parse_args(argv)
    try:
        return _run(args)
    except BaseException as e:
        code = e.code if isinstance(e, SystemExit) else 1
        if args.sns_topic and not args.dry_run and code not in (0, None):
            try:
                import boto3
                sess = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
                sess.client("sns", region_name="eu-central-1").publish(
                    TopicArn=args.sns_topic, Subject="karzat: az éjszakai futás elbukott",
                    Message=f"A helyi futás hibára futott: {e}\nAz oldal változatlan maradt. "
                            f"Napló: ~/Library/Logs/karzat-sync.log")
                log("failure alert published")
            except Exception as pub:
                log(f"the failure alert itself failed: {pub}")
        raise


def _run(args: argparse.Namespace) -> int:
    dry = args.dry_run
    py = [sys.executable]

    log(f"karzat nightly · {free_gb():.1f} GB free · cycle {CURRENT_CYCLE}")
    before = corpus_state()
    log(f"before: {before[0]:,} votes listed, last sitting day {before[1] or '—'}")

    # ---- the cheap half: ask the API what is new -------------------------------------------------
    if args.no_sync:
        log("--no-sync: not calling the API at all; working from the cache as it stands")
        if args.max_state_age and not dry:
            st = ROOT / "data" / "sync_state.json"
            last = datetime.fromisoformat(json.loads(st.read_text(encoding="utf-8"))["last_sync_at"])                 if st.exists() else None
            age_h = (datetime.now(timezone.utc) - last).total_seconds() / 3600 if last else float("inf")
            if age_h > args.max_state_age:
                raise SystemExit(f"the sync state is {age_h:.0f} hours old (limit {args.max_state_age:.0f}) — "
                                 "the local sync that feeds this build has not run; stopping loudly")
            log(f"sync state is {age_h:.1f} hours old — within the {args.max_state_age:.0f}-hour limit")
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
        # Ask the source whether it still says what it said. A closed vote's detail is a historical fact, so a
        # change there is news and nobody else is looking; 120 a night is a two-year sweep of the 79,829 cached
        # records, which is the right pace for an integrity check running on somebody else's server.
        run(py + ["-m", "scripts.watch_source", "--calls", "120"], dry=dry, allow_fail=True)

    if args.sync_only or args.handover:
        if dry:
            log("would upload data/raw and the sync state to the ops bucket")
        else:
            upload_cache(args.profile, args.ops_bucket)
    if args.sync_only:
        log("synced and handed over; the scheduled build takes it from here" if not dry else "--sync-only: would stop here")
        return 0

    # ---- did anything move? ----------------------------------------------------------------------
    run(py + ["-m", "scripts.derive_first_light"], dry=dry)
    after = corpus_state()
    log(f"after:  {after[0]:,} votes listed, last sitting day {after[1] or '—'}")
    stamp_age = publish_age_days()
    if after == before and not args.force and not dry:
        if args.refresh_days and stamp_age > args.refresh_days:
            log(f"nothing new, but the last publish is {stamp_age:.1f} days old (limit {args.refresh_days:.0f}) — "
                "rebuilding so the freshness stamp stays honest about being checked")
        else:
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
                 ["-m", "scripts.derive_faction_switches"],
                 ["-m", "scripts.derive_facts"], ["-m", "scripts.derive_parquet"],
                 # after the parquet, which it reads; and only the open cycle, because a closed parliament's
                 # votes are finished and refitting all eight takes a quarter of an hour every night
                 ["-m", "scripts.derive_idealpoints", "--cycle", str(CURRENT_CYCLE)],
                 # last of all: the receipt hashes what everything above just wrote
                 ["-m", "scripts.derive_receipt"]):
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
    if not dry:
        (ROOT / "data" / "last_publish.json").write_text(
            json.dumps({"published_at": datetime.now(timezone.utc).isoformat()}) + "\n", encoding="utf-8")
    log("published")
    return 0


if __name__ == "__main__":
    sys.exit(main())
