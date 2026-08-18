#!/usr/bin/env python3
"""Reconstruct the seating plan from the cached kepviselo.cgi records → data/derived/seating.json.

    python3 -m scripts.derive_seating [--government TISZA]

Reads data/raw/kepviselo/mp_*.xml (one per MP; from `karzat sync-mps`) and kepviselok.cgi for
factions; writes seats, per-sector analysis, the orientation decision, coordinates and the
geometry record with every assumption named. No network.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karzat.normalise import parse_kepviselo, parse_kepviselok  # noqa: E402
from karzat.seating import analyse, infer_orientation, layout, seats_from_records  # noqa: E402
from karzat.xmlutil import parse_xml, to_dict  # noqa: E402

RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "derived" / "seating.json"


def load(path: Path) -> dict:
    root = parse_xml(path.read_bytes())
    return {root.tag: to_dict(root)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--government", default=None, help="governing faction id (default: the largest faction)")
    args = ap.parse_args(argv)

    listed = parse_kepviselok(load(RAW / "kepviselok" / "all.xml"))
    by_azon = {m["p_azon"]: m for m in listed}
    records = []
    missing = []
    for m in listed:
        p = RAW / "kepviselo" / f"mp_{m['p_azon']}.xml"
        if not p.exists():
            missing.append(m["p_azon"])
            continue
        r = parse_kepviselo(load(p))
        records.append({"p_azon": m["p_azon"], "name": m["name"], "faction": m["faction"], "seat": r["seat"]})
    seats = seats_from_records(records)
    analysis = analyse(seats)
    if args.government:
        gov = args.government
    else:
        gov = max({m["faction"] for m in listed}, key=lambda f: sum(1 for m in listed if m["faction"] == f))
    orient = infer_orientation(analysis, gov)
    L = layout(seats, analysis, orient["sector_order"])
    out = {
        "derived_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "government": gov, "government_note": "largest faction unless --government given",
        "mps_listed": len(listed), "records_read": len(records), "records_missing": missing,
        "seated": len(seats), "unseated": [m["p_azon"] for m in records if m["p_azon"] not in seats],
        "analysis": analysis, "orientation": orient,
        "geometry": L["geometry"],
        "seats": {a: {**s} for a, s in seats.items()},
        "coords": L["coords"],
        "empty_seats": L["empty_seats"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"listed {len(listed)} · records {len(records)} · seated {len(seats)} · sectors {sorted(analysis['sectors'])} · government {gov}")
    print(orient["note"])
    for k, v in analysis["sectors"].items():
        print(f"  sector {k}: {v['count']} MPs, rows {min(v['rows'])}-{v['max_row']}, widest row {v['widest_row']}, factions {v['factions']}")
    print(f"duplicates: {analysis['duplicates']}")
    print(f"written {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
