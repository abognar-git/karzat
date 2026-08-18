"""Derive one cycle's speeches from the cached felszolalasok.cgi lists.

    python3 -m scripts.derive_speeches --cycle 43        → data/derived/speeches.json.gz
    python3 -m scripts.derive_speeches --cycle 42        → data/derived/speeches_ckl42.json.gz

Every row is one line of a sitting day's speech list: the agenda item it belongs to (title, iromány number when the
title carries one), the speaker as printed ("Név (Frakció)"), the person it resolves to (the same NameResolver as
the roll calls; ministers and the President who are not MPs stay unresolved, with their role), the kind, the role
the list prints, the length. `technical` follows karzat.normalise.SUBSTANTIVE_KINDS. The record check compares our
substantive count per MP with the record's own felszólalás count for the cycle.

Deterministic: the stamp is the newest speech day, never the clock; gzip with mtime=0.
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

from karzat.normalise import CYCLE_LABELS, NameResolver, parse_felszolalasok, parse_kepviselo, parse_kepviselok, record_histories  # noqa: E402
from karzat.xmlutil import parse_xml, to_dict  # noqa: E402

RAW = ROOT / "data" / "raw"
DERIVED = ROOT / "data" / "derived"


def load(path: Path) -> dict:
    root = parse_xml(path.read_bytes())
    return {root.tag: to_dict(root)}


def write_gz(path: Path, obj) -> None:
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
    with gzip.GzipFile(filename="", mode="wb", fileobj=path.open("wb"), mtime=0) as gz:
        gz.write(text.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycle", type=int, required=True)
    args = ap.parse_args(argv)
    cycle = args.cycle
    files = sorted((RAW / "felszolalasok").glob(f"ckl{cycle}_nap*.xml"))
    if not files:
        print(f"no cached speech lists for cycle {cycle} — run: python3 -m karzat sync-speeches --ckl {cycle}", file=sys.stderr)
        return 1
    mps = parse_kepviselok(load(RAW / "kepviselok" / "all.xml")) if (RAW / "kepviselok" / "all.xml").exists() else []
    resolver = NameResolver(mps, histories=record_histories(RAW))
    days, rows = [], []
    kinds: collections.Counter = collections.Counter()
    for f in files:
        day = parse_felszolalasok(load(f))
        n_sub = 0
        for sp in day["speeches"]:
            azon = resolver.resolve(sp["speaker_label"], on_date=day["date"]) if sp["speaker_label"] else None
            row = {"ulnap": day["ulnap"], "date": day["date"], **sp, "azon": azon}
            rows.append(row)
            kinds[sp["kind"] or ""] += 1
            n_sub += 0 if sp["technical"] else 1
        days.append({"ulnap": day["ulnap"], "date": day["date"], "speeches": len(day["speeches"]), "substantive": n_sub})
    # the record check: our substantive count per MP vs the record's felszólalás count for this cycle
    label = CYCLE_LABELS.get(cycle)
    ours: collections.Counter = collections.Counter(r["azon"] for r in rows if r["azon"] and not r["technical"])
    check = {}
    for p in sorted((RAW / "kepviselo").glob("mp_*.xml")):
        azon = p.stem[3:]
        rec = parse_kepviselo(load(p))
        for st in rec.get("speech_stats") or []:
            if st["ciklus"] == label and (st["speeches"] or ours.get(azon)):
                check[azon] = {"ours": ours.get(azon, 0), "record": st["speeches"], "record_technical": st["technical"]}
    agree = sum(1 for v in check.values() if v["ours"] == v["record"])
    out = {"cycle": cycle, "as_of": max((d["date"] for d in days if d["date"]), default=None),
           "days": days, "count": len(rows), "substantive": sum(1 for r in rows if not r["technical"]),
           "resolved": sum(1 for r in rows if r["azon"]), "kinds": dict(kinds.most_common()),
           "record_check": {"mps": len(check), "agree": agree, "detail": {a: v for a, v in check.items() if v["ours"] != v["record"]}},
           "speeches": rows}
    sfx = "" if cycle == 43 else f"_ckl{cycle}"
    path = DERIVED / f"speeches{sfx}.json.gz"
    write_gz(path, out)
    print(f"written {path}: {len(rows)} speeches over {len(days)} sitting days, {out['substantive']} substantive, {out['resolved']} resolved to MPs; "
          f"record check: {agree}/{len(check)} MPs agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
