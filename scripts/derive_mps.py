#!/usr/bin/env python3
"""Derive data/derived/mps.json — every person in the roll calls, with what the API knows about them.

    python3 -m scripts.derive_mps                       # cycle 43 (the current list + the roll-call store)
    python3 -m scripts.derive_mps --cycle 42            # cycle 42 → mps_ckl42.json from votes_positions_ckl42.json.gz

For an earlier cycle the population is the roll-call store's members, the faction is the one the
roll calls print (not today's), and mandate / constituency come from the record's <valasztasok>
row for that cycle; the seat is left out entirely — the API publishes seats present-tense only.

Sources (cache only, no network): kepviselok.cgi (list: county, constituency, photo, faction),
kepviselo.cgi records (seat, faction history, elections, motion/speech counts, website), the
Wikidata snapshot (QID, name variants), and the derived roll-call store for the two former MPs
who appear in votes but not in the current list. Personal-data restraint: pay, asset
declarations, education and languages are in the records but deliberately not derived here.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karzat.normalise import parse_kepviselo, parse_kepviselok  # noqa: E402
from karzat.xmlutil import parse_xml, to_dict  # noqa: E402

RAW = ROOT / "data" / "raw"
DERIVED = ROOT / "data" / "derived"


def load(path: Path) -> dict:
    root = parse_xml(path.read_bytes())
    return {root.tag: to_dict(root)}


def load_json(path: Path) -> dict:
    """path.json or path.json.gz, whichever exists (the cycle-42 store is committed gzipped)."""
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    gz = path.with_suffix(path.suffix + ".gz")
    with gzip.open(gz, "rt", encoding="utf-8") as fh:
        return json.load(fh)


CYCLE_LABEL = {43: "2026-", 42: "2022-2026", 41: "2018-2022", 40: "2014-2018"}


def mandate_from_record(rec: dict | None, cycle: int) -> dict:
    """mandate_kind / county / constituency_no for a past cycle, from the record's <valasztasok> row."""
    label = CYCLE_LABEL.get(cycle)
    for e in (rec or {}).get("elections") or []:
        if e.get("ciklus") == label and e.get("constituency"):
            c = e["constituency"]
            span = {"mandate_from": e.get("mandate_from"), "mandate_to": e.get("mandate_to")}
            m = re.match(r"^(.*?)\s+(\d+)\.\s*OEVK$", c)
            if m:
                return {"mandate_kind": "egyeni", "county": m.group(1), "constituency_no": int(m.group(2)), **span}
            return {"mandate_kind": "lista", "county": None, "constituency_no": None, "list_label": c, **span}
    return {"mandate_kind": None, "county": None, "constituency_no": None, "mandate_from": None, "mandate_to": None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycle", type=int, default=43)
    args = ap.parse_args(argv)
    cycle = args.cycle
    sfx = "" if cycle == 43 else f"_ckl{cycle}"
    out_path = DERIVED / f"mps{sfx}.json"

    listed = parse_kepviselok(load(RAW / "kepviselok" / "all.xml"))
    by_azon = {m["p_azon"]: m for m in listed}
    store = load_json(DERIVED / f"votes_positions{sfx}.json")
    qids: dict[str, dict] = {}
    snaps = sorted((DERIVED.parent.parent / "reference" / "wikidata").glob("members_*.json"),
                   key=lambda q: (q.name != f"members_ckl{cycle}.json", q.name))       # this cycle's snapshot first
    for snap in snaps:
        for m in json.loads(snap.read_text(encoding="utf-8"))["members"]:
            for oid in m.get("ogy_ids") or []:
                cur = qids.get(oid)
                # one person, several P39 statements (a group change is a new statement): keep the latest end — open beats closed
                if cur is None:
                    qids[oid] = {"qid": m["qid"], "name_hu": m.get("name_hu"), "name_en": m.get("name_en"),
                                 "district": m.get("district"), "start": m.get("start"), "end": m.get("end")}
                elif cur["qid"] == m["qid"] and (cur.get("end") is not None) and (m.get("end") is None or m.get("end") > cur["end"]):
                    cur["end"] = m.get("end")
    # the faction each person carried in their LAST roll call of the cycle (the store's `members` keeps the first)
    last_faction: dict[str, str] = {}
    for ts in sorted(store["positions"]):
        for key, fi, _code in store["positions"][ts]:
            last_faction[key] = store["factions"][fi]
    if cycle == 43:
        azons = list(by_azon) + [a for a in store["members"] if a not in by_azon]
    else:
        azons = list(store["members"])                       # the people of that cycle's roll calls, nobody else
    out_mps = {}
    for azon in azons:
        rec_path = RAW / "kepviselo" / f"mp_{azon}.xml"
        rec = parse_kepviselo(load(rec_path)) if rec_path.exists() else None
        listed_m = by_azon.get(azon) if cycle == 43 else None
        wd = qids.get(azon) or {}
        cur = None
        if rec:
            cur = next((f for f in rec["factions"] if f.get("to") is None), None) or (rec["factions"][0] if rec["factions"] else None)
        member = store["members"].get(azon, {})
        if cycle == 43:
            mandate = {"mandate_kind": (listed_m or {}).get("mandate_kind"), "county": (listed_m or {}).get("county"),
                       "constituency_no": (listed_m or {}).get("constituency_no")}
            faction = (listed_m or {}).get("faction") or member.get("faction") or (cur or {}).get("faction")
        else:
            mandate = mandate_from_record(rec, cycle)
            faction = last_faction.get(azon) or member.get("faction")   # as the person's last roll call of the cycle prints it
        out_mps[azon] = {
            "p_azon": azon,
            "name": (listed_m or {}).get("name") or (rec or {}).get("name") or member.get("name"),
            "faction": faction,
            "faction_first": member.get("faction"),            # at the first roll call of the cycle (differs for switchers)
            "current": azon in by_azon,                        # sitting today (cycle 43), whatever the page's cycle
            "cycle": cycle,
            **mandate,
            "photo_url": (listed_m or {}).get("photo_url") or (rec or {}).get("photo_url"),
            "website": (rec or {}).get("website"),
            "seat": (rec or {}).get("seat") if cycle == 43 else None,   # seats are present-tense in the API
            "has_record": rec is not None,
            "factions": (rec or {}).get("factions") or [],
            "elections": (rec or {}).get("elections") or [],
            "motion_stats": (rec or {}).get("motion_stats") or [],
            "wikidata_qid": wd.get("qid"),
            "wikidata_end": wd.get("end"),
            "parlament_url": f"https://www.parlament.hu/web/guest/kepviselok_mobil?kepviselo=%7B%22id%22%3A%22{azon}%22%7D",
        }
    out = {"derived_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "cycle": cycle, "count": len(out_mps),
           "current": sum(1 for m in out_mps.values() if m["current"]),
           "with_record": sum(1 for m in out_mps.values() if m["has_record"]), "mps": out_mps}
    OUT = out_path
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"written {OUT}: {out['count']} people, {out['current']} current; with seat {sum(1 for m in out_mps.values() if m['seat'] and m['seat'].get('sector') is not None)}, "
          f"with QID {sum(1 for m in out_mps.values() if m['wikidata_qid'])}, with record {out['with_record']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
