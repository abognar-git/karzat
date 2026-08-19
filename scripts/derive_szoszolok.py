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

from karzat.normalise import CYCLE_LABELS, CYCLE_STARTS, parse_kepviselo, parse_szoszolok  # noqa: E402
from karzat.xmlutil import parse_xml, to_dict  # noqa: E402

RAW = ROOT / "data" / "raw"
DERIVED = ROOT / "data" / "derived"
CURRENT_CYCLE = 43


def load(path: Path) -> dict:
    root = parse_xml(path.read_bytes())
    return {root.tag: to_dict(root)}


def cycle_windows() -> dict[int, tuple[str, str]]:
    """cycle → (first day, last day) — the constituent sitting to the day before the next one."""
    order = sorted(CYCLE_STARTS)
    out = {}
    for i, c in enumerate(order):
        nxt = CYCLE_STARTS[order[i + 1]] if i + 1 < len(order) else "9999-12-31"
        out[c] = (CYCLE_STARTS[c], nxt)
    return out


def stints(rec: dict) -> list[dict]:
    """The cycles this spokesperson served in, from their own record's dated rows — the same evidence the older
    cycles' MP rosters come from: a speech-statistics row for the cycle, or a committee membership overlapping it.
    szoszolok.cgi itself is present-tense (today's House), so earlier terms can only be read from the record."""
    wins = cycle_windows()
    by_label = {v: k for k, v in CYCLE_LABELS.items()}
    out: dict[int, dict] = {}
    for st in rec.get("speech_stats") or []:
        c = by_label.get(st["ciklus"])
        if c:
            out.setdefault(c, {"cycle": c, "committees": [], "speeches": None, "speeches_technical": None})
            out[c]["speeches"], out[c]["speeches_technical"] = st["speeches"], st["technical"]
    for m in rec.get("committees") or []:
        frm, to = m.get("from"), m.get("to")
        if not frm:
            continue
        for c, (a, b) in wins.items():
            if frm < b and (not to or to >= a):
                e = out.setdefault(c, {"cycle": c, "committees": [], "speeches": None, "speeches_technical": None})
                e["committees"].append(m)
    for e in out.values():
        e["committees"].sort(key=lambda m: (m.get("to") is not None, m.get("from") or ""))
    return [out[c] for c in sorted(out, reverse=True)]


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
        career = stints(rec)
        now = next((x for x in career if x["cycle"] == CURRENT_CYCLE), None)
        people[azon] = {
            "p_azon": azon, "name": rec.get("name") or r["name"], "nationality": r["nationality"], "order": r["order"],
            "photo_url": r["photo_url"] or rec.get("photo_url"), "website": rec.get("website"),
            "parlament_url": rec.get("parlament_url"),
            "seat": seat or None,
            "committees": (now or {}).get("committees") or [],
            "speeches": (st or {}).get("speeches"), "speeches_technical": (st or {}).get("technical"),
            "cycles": career,                              # every cycle the record shows them working in
            "mandates": rec.get("elections") or [],
            "has_record": bool(rec),
        }
    out = {"cycle": CURRENT_CYCLE, "count": len(people), "with_seat": len(people) - len(no_seat), "people": people}
    path = DERIVED / "szoszolok.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"written {path}: {len(people)} nationality spokespersons, {out['with_seat']} with a seat in the chamber, "
          f"{sum(len(p['cycles']) for p in people.values())} cycle-stints from their records"
          + (f"; no record for {', '.join(no_record)}" if no_record else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
