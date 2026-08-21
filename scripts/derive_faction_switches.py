"""Every mid-term change of faction since 1990, and what the roll-call name lists say about it.

    python3 -m scripts.derive_faction_switches
    python3 -m scripts.derive_faction_switches --report

Two records of the same fact, kept by the same institution and never reconciled with each other:

  **the member's own record** (`kepviselo.cgi`) carries dated faction rows — from this day to that day, this
  faction — and a change of faction inside one mandate is two rows of the same `ciklus` naming different ones;
  **the roll-call name list** labels every member with a faction at every recorded vote, independently.

Where both exist for the same change, they can be made to disagree, and that is the point: a claim this project
publishes should be checkable against a second source rather than trusted because the API returned it.

Three things this measurement got wrong before it got them right, all recorded here because each is a trap the
next person will walk into:

  A change of faction is not the same as a change *between* mandates. Counting every adjacent pair of rows gives
  985, because a member returned across eight elections has seven boundaries and no switches at all.
  The window after a change has to end at the *next* change. Left open, a member who went A → B → C looks like a
  disagreement at A → B, because the list correctly labels them C later on.
  A change has to be tested against the votes of its own cycle. Grouping by the record's `ciklus` string while
  iterating another cycle's roll calls tests a 2013 departure against 2018 ballots, which is nonsense that looks
  like a year-long discrepancy.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DERIVED = ROOT / "data" / "derived"
OUT = DERIVED / "faction_switches.json"


def mps_file(c: int) -> Path:
    return DERIVED / ("mps.json" if c == 43 else f"mps_ckl{c}.json")


def collect(cycles: list[int]) -> tuple[list[dict], dict]:
    """Every mid-term switch, once, however many cycle files list the person."""
    rows, name = defaultdict(dict), {}
    for c in cycles:
        f = mps_file(c)
        if not f.exists():
            continue
        for azon, v in json.loads(f.read_text(encoding="utf-8"))["mps"].items():
            name[azon] = v.get("name")
            for r in (v.get("factions") or []):
                rows[azon][(r.get("ciklus"), r.get("from"), r.get("to"), r.get("faction"))] = True
    out = []
    for azon, keys in rows.items():
        by = defaultdict(list)
        for cik, frm, _to, fac in keys:
            by[cik].append((frm or "", fac))
        for cik, seq in by.items():
            seq = sorted(set(seq))
            for i in range(len(seq) - 1):
                (d1, a), (d2, b) = seq[i], seq[i + 1]
                if a == b:
                    continue
                out.append({"azon": azon, "name": name.get(azon), "ciklus": cik, "from": a, "to": b,
                            "since": d1, "on": d2,
                            "until": seq[i + 2][0] if i + 2 < len(seq) else None})
    return sorted(out, key=lambda s: (s["on"], s["name"] or "")), name


def verify(switches: list[dict], cycles: list[int]) -> None:
    """Ask each cycle's own roll calls what faction they gave the member, before and after the change."""
    from scripts.build_site import load_inputs
    first_year = {}
    for c in cycles:
        idx = load_inputs(c)["idx"]
        days = [v["on_date"] for v in idx["votes"] if v.get("on_date")]
        if days:
            first_year[min(days)[:4]] = c
    for s in switches:
        s["verdict"] = "nincs név szerinti lista"
        s["lag_days"] = None
        s["listed_before"] = s["listed_after"] = []
    for c in cycles:
        inp = load_inputs(c)
        st, idx = inp["store"], inp["idx"]
        if not (st.get("positions") or {}):
            continue
        day = {v["ts"]: v["on_date"] for v in idx["votes"]}
        facs = st["factions"]
        seen = defaultdict(list)
        for ts, rs in st["positions"].items():
            d = day.get(ts)
            if d:
                for r in rs:
                    seen[r[0]].append((d, facs[r[1]]))
        for s in switches:
            m = re.match(r"^(\d{4})", s["ciklus"] or "")
            if not m or first_year.get(m.group(1)) != c:
                continue
            end = s["until"] or "9999"
            before = {fc for d, fc in seen.get(s["azon"], []) if s["since"] <= d < s["on"]}
            after = [(d, fc) for d, fc in seen.get(s["azon"], []) if s["on"] <= d < end]
            s["listed_before"], s["listed_after"] = sorted(before), sorted({fc for _, fc in after})
            if not before or not after:
                continue
            if before != {s["from"]} or (set(s["listed_after"]) - {s["from"], s["to"]}):
                s["verdict"] = "más frakciót nevez meg"
                continue
            olds = [d for d, fc in after if fc == s["from"]]
            if not olds:
                s["verdict"] = "egyezik"
            else:
                s["lag_days"] = (date.fromisoformat(max(olds)) - date.fromisoformat(s["on"])).days
                s["verdict"] = "a váltás napján még a régi" if s["lag_days"] == 0 else "tovább tartja a régit"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    from scripts.build_site import available_cycles
    cycles = available_cycles()
    switches, _ = collect(cycles)
    verify(switches, cycles)
    tally = Counter(s["verdict"] for s in switches)
    testable = sum(v for k, v in tally.items() if k != "nincs név szerinti lista")
    out = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
           "switches": len(switches),
           "people": len({s["azon"] for s in switches}),
           "person_cycles": len({(s["azon"], s["ciklus"]) for s in switches}),
           "testable": testable, "verdicts": dict(tally),
           "by_cycle": dict(sorted(Counter(s["ciklus"] for s in switches).items())),
           "items": switches}
    print(f"{len(switches)} mid-term switches · {out['people']} people · {out['person_cycles']} person-cycles")
    print(f"  testable against the roll-call lists: {testable}")
    for k, v in tally.most_common():
        print(f"    {v:4d}  {k}")
    if a.report:
        for s in switches:
            if s["verdict"] in ("tovább tartja a régit", "más frakciót nevez meg"):
                lag = f" ({s['lag_days']} nap)" if s["lag_days"] else ""
                print(f"    · {s['name']} · {s['ciklus']} · {s['from']}→{s['to']} {s['on']} · "
                      f"{s['verdict']}{lag} · lista: {s['listed_after']}")
    else:
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("written", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
