#!/usr/bin/env python3
"""Derive data/derived/mps.json — every person in the roll calls, with what the API knows about them.

    python3 -m scripts.derive_mps

Sources (cache only, no network): kepviselok.cgi (list: county, constituency, photo, faction),
kepviselo.cgi records (seat, faction history, elections, motion/speech counts, website), the
Wikidata snapshot (QID, name variants), and the derived roll-call store for the two former MPs
who appear in votes but not in the current list. Personal-data restraint: pay, asset
declarations, education and languages are in the records but deliberately not derived here.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karzat.normalise import parse_kepviselo, parse_kepviselok  # noqa: E402
from karzat.xmlutil import parse_xml, to_dict  # noqa: E402

RAW = ROOT / "data" / "raw"
DERIVED = ROOT / "data" / "derived"
OUT = DERIVED / "mps.json"


def load(path: Path) -> dict:
    root = parse_xml(path.read_bytes())
    return {root.tag: to_dict(root)}


def main() -> int:
    listed = parse_kepviselok(load(RAW / "kepviselok" / "all.xml"))
    by_azon = {m["p_azon"]: m for m in listed}
    store = json.loads((DERIVED / "votes_positions.json").read_text(encoding="utf-8"))
    qids: dict[str, dict] = {}
    snap = DERIVED.parent.parent / "reference" / "wikidata" / "members_ckl43.json"
    if snap.exists():
        for m in json.loads(snap.read_text(encoding="utf-8"))["members"]:
            for oid in m.get("ogy_ids") or []:
                qids.setdefault(oid, {"qid": m["qid"], "name_hu": m.get("name_hu"), "name_en": m.get("name_en"),
                                      "district": m.get("district"), "start": m.get("start"), "end": m.get("end")})
    azons = list(by_azon) + [a for a in store["members"] if a not in by_azon]
    out_mps = {}
    for azon in azons:
        rec_path = RAW / "kepviselo" / f"mp_{azon}.xml"
        rec = parse_kepviselo(load(rec_path)) if rec_path.exists() else None
        listed_m = by_azon.get(azon)
        wd = qids.get(azon) or {}
        cur = None
        if rec:
            cur = next((f for f in rec["factions"] if f.get("to") is None), None) or (rec["factions"][0] if rec["factions"] else None)
        out_mps[azon] = {
            "p_azon": azon,
            "name": (listed_m or {}).get("name") or (rec or {}).get("name") or store["members"].get(azon, {}).get("name"),
            "faction": (listed_m or {}).get("faction") or store["members"].get(azon, {}).get("faction") or (cur or {}).get("faction"),
            "current": azon in by_azon,
            "county": (listed_m or {}).get("county"),
            "constituency_no": (listed_m or {}).get("constituency_no"),
            "mandate_kind": (listed_m or {}).get("mandate_kind"),
            "photo_url": (listed_m or {}).get("photo_url") or (rec or {}).get("photo_url"),
            "website": (rec or {}).get("website"),
            "seat": (rec or {}).get("seat"),
            "factions": (rec or {}).get("factions") or [],
            "elections": (rec or {}).get("elections") or [],
            "motion_stats": (rec or {}).get("motion_stats") or [],
            "wikidata_qid": wd.get("qid"),
            "wikidata_end": wd.get("end"),
            "parlament_url": f"https://www.parlament.hu/web/guest/kepviselok_mobil?kepviselo=%7B%22id%22%3A%22{azon}%22%7D",
        }
    out = {"derived_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "count": len(out_mps),
           "current": sum(1 for m in out_mps.values() if m["current"]), "mps": out_mps}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"written {OUT}: {out['count']} people, {out['current']} current; with seat {sum(1 for m in out_mps.values() if m['seat'] and m['seat'].get('sector') is not None)}, "
          f"with QID {sum(1 for m in out_mps.values() if m['wikidata_qid'])}, with record {sum(1 for m in out_mps.values() if m['factions'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
