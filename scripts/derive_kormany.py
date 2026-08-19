"""Derive the ministerial bench: the people who sit in front of the Speaker without a mandate → data/derived/kormany.json.

    python3 -m scripts.derive_kormany [--cycle 43]

The ministerial bench (sector 0, the horseshoe in front of the Speaker) is not an MPs-only bench: a minister need not
be a member of the Assembly. Those ministers are in none of the lists karzat reads — kepviselok.cgi is the mandate
holders, szoszolok.cgi the spokespersons — but they do speak, and a speech payload names its speaker by id
(`<felszolalo href=…{"id":"0053"}>`). So this script harvests the ids that appear in the cycle's speech texts and are
in neither list, fetches nothing itself, and keeps the ones whose own kepviselo.cgi record gives them a seat: they
are the people on the bench. The office comes from what the record of the speech prints (`beosztas`), never guessed.

What this cannot find: a government member who has not spoken in the cycle. The site says so rather than implying
the bench is complete.

Deterministic: no clock, no network — the records must already be cached (karzat sync-mps, sync-speech-texts).
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karzat.normalise import parse_kepviselo  # noqa: E402
from karzat.xmlutil import parse_xml, to_dict  # noqa: E402

RAW = ROOT / "data" / "raw"
DERIVED = ROOT / "data" / "derived"
CURRENT_CYCLE = 43


def load(path: Path) -> dict:
    root = parse_xml(path.read_bytes())
    return {root.tag: to_dict(root)}


def load_gz(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycle", type=int, default=CURRENT_CYCLE)
    args = ap.parse_args(argv)
    cycle = args.cycle
    sfx = "" if cycle == CURRENT_CYCLE else f"_ckl{cycle}"
    tx_path = DERIVED / f"speech_texts{sfx}.json.gz"
    if not tx_path.exists():
        print(f"no speech texts for cycle {cycle} — run: python3 -m karzat sync-speech-texts --ckl {cycle}", file=sys.stderr)
        return 1
    mps = set(json.loads((DERIVED / f"mps{sfx}.json").read_text(encoding="utf-8"))["mps"])
    sz_path = DERIVED / "szoszolok.json"
    sz_people = json.loads(sz_path.read_text(encoding="utf-8"))["people"] if sz_path.exists() else {}
    szoszolok = set(sz_people)
    # every seat already spoken for: a sitting MP's or a spokesperson's. A record can carry a seat it no longer holds
    # (a former MP's record does, and the seat is someone else's now) — such a claim is dropped, not drawn.
    plan = json.loads((DERIVED / "seating.json").read_text(encoding="utf-8"))
    taken = {(c["sector"], c["row"], c["seat"]) for c in plan["coords"].values()}
    taken |= {(r["seat"]["sector"], r["seat"]["row"], r["seat"]["seat"]) for r in sz_people.values() if (r.get("seat") or {}).get("sector") is not None}

    seen: dict[str, dict] = {}
    for t in load_gz(tx_path)["texts"].values():
        azon = t.get("azon")
        if not azon or azon in mps or azon in szoszolok:
            continue
        e = seen.setdefault(azon, {"p_azon": azon, "name": t.get("speaker"), "offices": collections.Counter(), "speeches": 0, "last_spoke": None})
        e["speeches"] += 1
        if t.get("position"):
            e["offices"][t["position"]] += 1
        if t.get("date") and (e["last_spoke"] is None or t["date"] > e["last_spoke"]):
            e["last_spoke"] = t["date"]

    photos = {}
    ph_path = ROOT / "reference" / "wikidata" / "kormany_photos.json"
    if ph_path.exists():
        photos = json.loads(ph_path.read_text(encoding="utf-8"))["people"]
    people, no_seat, stale = {}, [], []
    for azon, e in seen.items():
        rec_path = RAW / "kepviselo" / f"mp_{azon}.xml"
        rec = parse_kepviselo(load(rec_path)) if rec_path.exists() else {}
        seat = (rec.get("seat") or {}) if rec else {}
        if seat.get("sector") is None:
            no_seat.append((azon, e["name"]))
            continue                                   # speaks in the House but has no seat of their own there
        if (seat["sector"], seat["row"], seat["seat"]) in taken or seat["sector"] != 0:
            # either the record kept a seat that is now someone else's, or the seat is out on the floor rather than on
            # the ministerial bench — a former MP's record keeps its old seat, and that is not a placement to draw
            stale.append((azon, e["name"]))
            continue
        offices = [o for o, _ in e["offices"].most_common()]
        # parlament.hu answers 404 for their portrait (it photographs the members it lists), so the picture — where
        # one exists at all — is a Commons file found by scripts/pull_kormany_photos.py, shown with its attribution
        ph = photos.get(azon) or {}
        people[azon] = {"p_azon": azon, "name": rec.get("name") or e["name"], "offices": offices,
                        "office": offices[0] if offices else None, "seat": seat,
                        "photo_url": ph.get("url"), "photo_credit": ph.get("credit"), "photo_qid": ph.get("qid"),
                        "photo_licence": ph.get("licence"), "photo_licence_url": ph.get("licence_url"),
                        "parlament_url": rec.get("parlament_url"),
                        "speeches": e["speeches"], "last_spoke": e["last_spoke"],
                        "front_bench": seat.get("sector") == 0}
    out = {"cycle": cycle, "count": len(people), "front_bench": sum(1 for p in people.values() if p["front_bench"]),
           "with_photo": sum(1 for p in people.values() if p.get("photo_url")),
           "seen_without_seat": sorted(n for _, n in no_seat), "seat_not_on_the_bench": sorted(n for _, n in stale),
           "people": dict(sorted(people.items(), key=lambda kv: (kv[1]["seat"]["sector"], kv[1]["seat"]["row"], kv[1]["seat"]["seat"])))}
    path = DERIVED / "kormany.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"written {path}: {len(people)} people in the House without a mandate but with a seat "
          f"({out['front_bench']} on the ministerial bench); {len(no_seat)} spoke without a seat of their own "
          f"({', '.join(n for _, n in no_seat[:4])}{'…' if len(no_seat) > 4 else ''})"
          + (f"; {len(stale)} record claims a seat off the bench or already held ({', '.join(n for _, n in stale)})" if stale else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
