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

from karzat.normalise import NameResolver, parse_iromanyok, parse_kepviselo, parse_kepviselok, record_histories  # noqa: E402
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


from karzat.normalise import CYCLE_LABELS as CYCLE_LABEL  # noqa: E402  — 34: "1990-1994" … 43: "2026-" as kepviselo.cgi prints ciklus


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
        if len(azons) < 50:
            # before 1998 the API prints no names in the roll calls: the roster then comes from the records themselves —
            # everyone whose faction history has a row for the cycle's label ("1990-94", "1994-98")
            label = CYCLE_LABEL.get(cycle)
            azons = sorted(azon for azon, h in record_histories(RAW).items() if any(c == label for c, _ in h["factions"]))
            print(f"roster from the records: {len(azons)} people with a faction row for {label!r} (no roll calls print names in this cycle)")
    # the current cycle's irományok by submitter (iromanyok.cgi lists the current cycle only): the items behind
    # the record's <inditvanyok> "önálló" count — the two reconcile MP by MP (tests/test_site.py)
    motions_by_azon: dict[str, list] = {}
    il = RAW / "iromanyok" / "all.xml"
    if cycle == 43 and il.exists():
        resolver = NameResolver(listed, histories=record_histories(RAW))
        for b in parse_iromanyok(load(il)):
            people = [n for n in b["submitters"] if "(" in n and not n.startswith("kormány")]
            for n in people:
                az = resolver.resolve(n, "2026-08-18")
                if not az:
                    continue
                motions_by_azon.setdefault(az, []).append({
                    "szam": b["szam"], "kind": (b.get("szam_parsed") or {}).get("kind"), "number": (b.get("szam_parsed") or {}).get("number"),
                    "title": b["title"], "status": b["status_type"], "href": b.get("href"),
                    "co_submitters": len(people) - 1})
        for lst in motions_by_azon.values():
            lst.sort(key=lambda m: -(m["number"] or 0))
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
            span = mandate_from_record(rec, cycle)                  # the record's own row for this cycle: when the mandate began and, if it did, ended
            mandate = {"mandate_kind": (listed_m or {}).get("mandate_kind") or span.get("mandate_kind"), "county": (listed_m or {}).get("county") or span.get("county"),
                       "constituency_no": (listed_m or {}).get("constituency_no") or span.get("constituency_no"),
                       "mandate_from": span.get("mandate_from"), "mandate_to": span.get("mandate_to")}
            faction = (listed_m or {}).get("faction") or member.get("faction") or (cur or {}).get("faction")
        else:
            mandate = mandate_from_record(rec, cycle)
            faction = last_faction.get(azon) or member.get("faction")   # as the person's last roll call of the cycle prints it
            if not faction and rec:
                # no roll call names this person in the cycle (before 1998 none do): the record's own faction rows for the
                # cycle's label — the record lists them latest first
                rows = [f for f in rec["factions"] if f.get("ciklus") == CYCLE_LABEL.get(cycle) and f.get("faction")]
                faction = rows[0]["faction"] if rows else None
                if rows:
                    member = dict(member, faction=rows[-1]["faction"])   # the first faction of the cycle, for faction_first
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
            "speech_stats": (rec or {}).get("speech_stats") or [],       # the record's own per-cycle felszólalás / technikai counts
            "committees": (rec or {}).get("committees") or [],           # bizottsági tagságok with dates, every cycle
            "offices": (rec or {}).get("offices") or [],                 # tisztségek with dates: Speaker, deputies, clerks, state secretaries…
            "highest_degree": (rec or {}).get("highest_degree"),
            "schools": (rec or {}).get("schools") or [],                 # iskolák: degree, institution, faculty, year
            "languages": (rec or {}).get("languages") or [],
            "declarations": (rec or {}).get("declarations") or [],       # vagyonnyilatkozatok: the published PDFs and the ones due
            "remuneration": (rec or {}).get("remuneration") or [],       # javadalmazás: the one month the record carries
            "local_offices": (rec or {}).get("local_offices") or [],     # egyéb tagságok: local government seats, by year
            "biography_url": (rec or {}).get("biography_url"),
            "motions": motions_by_azon.get(azon, []),           # this cycle's irományok (current cycle only; empty for earlier cycles)
            "wikidata_qid": wd.get("qid"),
            "wikidata_end": wd.get("end"),
            "parlament_url": f"https://www.parlament.hu/web/guest/kepviselok_mobil?kepviselo=%7B%22id%22%3A%22{azon}%22%7D",
        }
    sync_state = ROOT / "data" / "sync_state.json"
    try:
        last_sync = json.loads(sync_state.read_text(encoding="utf-8")).get("last_sync_at") if sync_state.exists() else None
    except json.JSONDecodeError:
        last_sync = None
    out = {"derived_at": last_sync or datetime.now(timezone.utc).isoformat(timespec="seconds"), "cycle": cycle, "count": len(out_mps),
           "current": sum(1 for m in out_mps.values() if m["current"]),
           "with_record": sum(1 for m in out_mps.values() if m["has_record"]), "mps": out_mps}
    OUT = out_path
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"written {OUT}: {out['count']} people, {out['current']} current; with seat {sum(1 for m in out_mps.values() if m['seat'] and m['seat'].get('sector') is not None)}, "
          f"with QID {sum(1 for m in out_mps.values() if m['wikidata_qid'])}, with record {out['with_record']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
