"""Derive the nationality spokespersons from the cached szoszolok.cgi list and their kepviselo.cgi records.

    python3 -m scripts.derive_szoszolok      → data/derived/szoszolok.json

A nemzetiségi szószóló is elected by a nationality's list: they sit in the chamber, may speak and sit on committees,
but cast no vote — the roll calls never name them. They are kept out of `mps.json` for exactly that reason (a roster
count, a participation rate or an alignment would be nonsense for them) and live here instead, with the seat their
own record gives, their committees and the speech counts the record prints per cycle.

Present-tense, like the committee list: szoszolok.cgi has no cycle parameter — it describes today's House.
Deterministic: no clock; the stamp is the newest date the records mention.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karzat.normalise import CYCLE_LABELS, parse_kepviselo, parse_szoszolok  # noqa: E402
from karzat.xmlutil import parse_xml, to_dict  # noqa: E402

RAW = ROOT / "data" / "raw"
DERIVED = ROOT / "data" / "derived"
CURRENT_CYCLE = 43


def load(path: Path) -> dict:
    root = parse_xml(path.read_bytes())
    return {root.tag: to_dict(root)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycle", type=int, default=CURRENT_CYCLE)
    ap.parse_args(argv)
    path_in = RAW / "szoszolok" / "all.xml"
    if not path_in.exists():
        print("no cached spokesperson list — run: python3 -m karzat sync-mps", file=sys.stderr)
        return 1
    label = CYCLE_LABELS.get(CURRENT_CYCLE)
    people, no_seat, no_record = {}, [], []
    for r in parse_szoszolok(load(path_in)):
        azon = r["p_azon"]
        rec_path = RAW / "kepviselo" / f"mp_{azon}.xml"
        rec = parse_kepviselo(load(rec_path)) if rec_path.exists() else {}
        if not rec:
            no_record.append(azon)
        seat = rec.get("seat") or {}
        if seat.get("sector") is None:
            no_seat.append(azon)
        st = next((x for x in rec.get("speech_stats") or [] if x["ciklus"] == label), None)
        people[azon] = {
            "p_azon": azon, "name": rec.get("name") or r["name"], "nationality": r["nationality"], "order": r["order"],
            "photo_url": r["photo_url"] or rec.get("photo_url"),
            "seat": seat or None,
            "committees": [c for c in rec.get("committees") or [] if c.get("ciklus") in (None, label)],
            "speeches": (st or {}).get("speeches"), "speeches_technical": (st or {}).get("technical"),
            "mandates": rec.get("elections") or [],
            "has_record": bool(rec),
        }
    out = {"cycle": CURRENT_CYCLE, "count": len(people), "with_seat": len(people) - len(no_seat), "people": people}
    path = DERIVED / "szoszolok.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"written {path}: {len(people)} nationality spokespersons, {out['with_seat']} with a seat in the chamber"
          + (f"; no record for {', '.join(no_record)}" if no_record else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
