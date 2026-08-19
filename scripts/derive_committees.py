"""Derive the committees of the current cycle from the cached bizottsagok.cgi / bizottsag.cgi payloads.

    python3 -m scripts.derive_committees      → data/derived/committees.json

The API is present-tense here (no cycle parameter): the list and the records describe today's committees — type,
chair, vice-chairs, members by p_azon and faction, subcommittees, contact, and the next sitting's invitation date
and URL when one is out. The earlier cycles' committees come from the MP records' membership histories instead
(the builder assembles those; this file is only for the current cycle).

Deterministic: no clock — the stamp is the newest `letrehozas` date seen.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karzat.normalise import parse_bizottsag, parse_bizottsagok  # noqa: E402
from karzat.xmlutil import parse_xml, to_dict  # noqa: E402

RAW = ROOT / "data" / "raw"
DERIVED = ROOT / "data" / "derived"


def load(path: Path) -> dict:
    root = parse_xml(path.read_bytes())
    return {root.tag: to_dict(root)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args(argv)
    lst_path = RAW / "bizottsagok" / "all.xml"
    if not lst_path.exists():
        print("no cached committee list — run: python3 -m karzat sync-committees", file=sys.stderr)
        return 1
    lst = parse_bizottsagok(load(lst_path))
    records = {}
    for p in sorted((RAW / "bizottsag").glob("biz_*.xml")):
        m = re.match(r"biz_(.+)$", p.stem)
        if not m:
            continue
        try:
            records[m.group(1)] = parse_bizottsag(load(p))
        except Exception as e:                        # one broken payload must not take the derive down
            print(f"skip {p.name}: {e}", file=sys.stderr)
    out = {"as_of": max((r.get("created") or "" for r in records.values()), default=None), "count": len(lst),
           "list": lst, "records": records}
    path = DERIVED / "committees.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    n_meet = sum(1 for r in records.values() if r.get("next_meeting"))
    print(f"written {path}: {len(lst)} committees, {len(records)} records ({sum(1 for r in records.values() if r.get('type') == 'Albizottság')} subcommittees), {n_meet} with a next sitting announced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
