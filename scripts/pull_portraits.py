#!/usr/bin/env python3
"""Fetch every portrait the rosters name → data/raw/portre/<p_azon>.<ext>.

    python3 -m scripts.pull_portraits              # only what is missing
    python3 -m scripts.pull_portraits --refresh    # re-fetch everything

Until now the pages linked the picture where it lives and never copied it. Serving them ourselves is a different
act — redistribution rather than a hotlink — and parlament.hu states no terms of use for them, so this runs on the
owner's decision, recorded in the README. The three Commons pictures of non-MP government members carry their own
free licence and their author is shown on the page either way.

One request at a time with the same pacing as the API client: these are the same servers, and the point of the
polite interval is that they stay unbothered. Nothing here writes to site/ — `scripts.make_portraits` does that.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DERIVED = ROOT / "data" / "derived"
OUT = ROOT / "data" / "raw" / "portre"
PACE = 0.6                                  # seconds between requests, as karzat.api uses
EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


def wanted() -> dict[str, str]:
    """p_azon → picture URL, from every roster the site builds from."""
    out: dict[str, str] = {}
    sources = sorted(DERIVED.glob("mps*.json")) + [DERIVED / "szoszolok.json", DERIVED / "kormany.json"]
    for p in sources:
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        for azon, m in (d.get("mps") or d.get("people") or {}).items():
            if m.get("photo_url"):
                out.setdefault(azon, m["photo_url"])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="re-fetch pictures already cached")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many live fetches (for a trial run)")
    args = ap.parse_args(argv)
    import requests

    OUT.mkdir(parents=True, exist_ok=True)
    people = wanted()
    have = {p.stem for p in OUT.iterdir() if p.is_file()} if OUT.exists() else set()
    todo = [(a, u) for a, u in sorted(people.items()) if args.refresh or a not in have]
    print(f"{len(people):,} portraits named, {len(have):,} already cached, {len(todo):,} to fetch"
          f" (~{len(todo) * PACE / 60:.0f} min at the polite pace)")
    sess = requests.Session()
    sess.headers["User-Agent"] = "karzat/1.0 (+https://github.com/abognar-git)"
    ok = failed = 0
    t0 = time.time()
    for i, (azon, url) in enumerate(todo, 1):
        if args.limit and ok >= args.limit:
            break
        try:
            time.sleep(PACE)
            r = sess.get(url, timeout=30)
            if r.status_code != 200 or not r.content:
                failed += 1
                print(f"  {azon}: HTTP {r.status_code}", file=sys.stderr)
                continue
            ext = EXT.get((r.headers.get("content-type") or "").split(";")[0].strip(), ".jpg")
            (OUT / f"{azon}{ext}").write_bytes(r.content)
            ok += 1
        except Exception as e:                                  # noqa: BLE001 — one bad picture must not stop the run
            failed += 1
            print(f"  {azon}: {type(e).__name__}: {e}", file=sys.stderr)
        if i % 200 == 0:
            el = time.time() - t0
            print(f"  {i}/{len(todo)} · ok {ok} · failed {failed} · {el / 60:.0f} min · ~{(el / i) * (len(todo) - i) / 60:.0f} min left", flush=True)
    size = sum(p.stat().st_size for p in OUT.iterdir() if p.is_file())
    print(f"done: {ok} fetched, {failed} failed; {len(list(OUT.iterdir())):,} files, {size / 1e6:.1f} MB in {OUT}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
