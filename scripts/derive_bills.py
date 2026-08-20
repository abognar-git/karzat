#!/usr/bin/env python3
"""Derive the motion records from the cache → data/derived/iromany_records.json.

    python3 -m scripts.derive_bills

One entry per motion the current cycle's list knows, keyed by its irományszám (T/366, H/12 …): when it was
submitted, every vote the record dates, the committees assigned to it, the full event log, and — where it became
law — the promulgation and the law reference.

Current cycle only, and that is the service's limit rather than a choice. iromanyok.cgi has no cycle parameter
(p_ckl was probed: the response does not change), and iromany.cgi is addressed by izon, a per-cycle sequence, so an
earlier cycle's T/1220 cannot be requested at all — the vote payloads carry the irományszám but never the izon.
The 14,056 motions the older cycles' votes refer to therefore stay out of reach, and the cycle pages say so.

Deterministic: reads data/raw/iromany/*.xml only, no clock, no network.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karzat.normalise import parse_iromany, parse_iromanyok  # noqa: E402
from karzat.xmlutil import parse_xml, to_dict  # noqa: E402

RAW = ROOT / "data" / "raw" / "iromany"
LIST = ROOT / "data" / "raw" / "iromanyok" / "all.xml"
OUT = ROOT / "data" / "derived" / "iromany_records.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    files = sorted(RAW.glob("*.xml"))
    if not files:
        print("no cached motion records — run: python3 -m karzat sync-bills", file=sys.stderr)
        return 1
    recs: dict[str, dict] = {}
    newest = ""
    for p in files:
        r = parse_iromany(to_dict(parse_xml(p.read_bytes())))
        if not r.get("szam"):
            continue
        for e in r["events"]:
            newest = max(newest, e.get("date") or "")
        recs[r["szam"]] = r
    laws = sum(1 for r in recs.values() if (r.get("promulgation") or {}).get("law_ref"))
    events = sum(len(r["events"]) for r in recs.values())
    votes = sum(len(r["votes"]) for r in recs.values())
    listed = len(parse_iromanyok({"iromanyok": to_dict(parse_xml(LIST.read_bytes()))})) if LIST.exists() else 0
    out = {"derived_at": newest or None, "count": len(recs), "listed": listed,
           "note": "Csak a jelenlegi ciklus: az irománylista nem fogad ciklusparamétert, az iromány-adatlap pedig "
                   "ciklusonként újrakezdődő izon szerint címezhető, így korábbi ciklus irománya nem kérhető le.",
           "records": recs}
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"written {args.out}: {len(recs)} motions, {events:,} events, {votes:,} dated votes, {laws} promulgated laws")
    return 0


if __name__ == "__main__":
    sys.exit(main())
