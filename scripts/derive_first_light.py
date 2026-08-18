#!/usr/bin/env python3
"""Derive the committed inputs of the site and the README from the cached W-API payloads.

    python3 -m scripts.derive_first_light [--hero 2026.06.15.17:20:04]

Writes, from data/raw/* (no network):
  data/derived/first_light.json   counts the README quotes (modes, results, procedures, MPs, sitting days)
  data/derived/votes_index.json   every listed vote with its motion, majority verdict and — where the
                                  detail is cached — the roll-call counts; plus sitting days and bill links
  data/derived/hero_vote.json     one vote in full (positions, factions, verdict) for the seat chart

The raw cache is git-ignored; these files are not, so a clean checkout can rebuild the site and
verify the README. Re-run after every sync; the README gate then says which sentences moved.
"""

from __future__ import annotations

import collections
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karzat.majority import RULES, Rule, Tally, classify, evaluate  # noqa: E402
from karzat.normalise import (  # noqa: E402
    NameResolver,
    parse_kepviselok,
    parse_szavazas,
    parse_szavazasok,
    parse_ulesnap,
    seats_for,
)
from karzat.xmlutil import parse_xml, to_dict, ts_to_slug  # noqa: E402

RAW = ROOT / "data" / "raw"
DERIVED = ROOT / "data" / "derived"
OUT = DERIVED / "first_light.json"
HERO_DEFAULT = "2026.06.15.17:20:04"   # T/51, Alaptörvény 16. módosítása — 135–50–6, 2/3 of all, margin +2


def load(path: Path) -> dict:
    root = parse_xml(path.read_bytes())
    return {root.tag: to_dict(root)}


def list_majority(v: dict) -> dict | None:
    """Majority verdict from list-level numbers only (present = Összes szavazat; flagged)."""
    if v["kind"] != "dontes" or v["igen"] is None:
        return None
    cls = classify(payload_rule=v["mode"])
    tally = Tally(yes=v["igen"], no=v["nem"] or 0, abstain=v["tartozkodott"] or 0, present=v["osszes_szavazat"],
                  seats=seats_for(v["on_date"]))
    r = evaluate(cls.rule, tally)
    return {"rule": r.rule, "label_hu": r.label_hu, "source": cls.source, "base": r.base, "needed": r.needed,
            "margin": r.margin, "passed_by_rule": r.passed,
            "agrees_with_source": (r.passed == v["passed"]) if v["passed"] is not None and r.passed is not None else None,
            "present_basis": "list: Összes szavazat"}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hero", default=HERO_DEFAULT, help="idopont of the vote to write out in full")
    ap.add_argument("--since", default="2026-05-01", help="ignore listed votes before this ISO date (cycle 43 starts 2026-05-09)")
    ap.add_argument("--until", default=None, help="ignore listed votes after this ISO date")
    args = ap.parse_args(argv)

    votes = []
    for f in sorted((RAW / "szavazasok").glob("*.xml")):
        votes.extend(parse_szavazasok(load(f)))
    votes = [v for v in votes if v["on_date"] and v["on_date"] >= args.since and (not args.until or v["on_date"] <= args.until)]
    votes.sort(key=lambda v: v["ts"] or "")
    if not votes:
        print("no cached vote lists under data/raw/szavazasok — run sync-votes first", file=sys.stderr)
        return 2
    by_mode = collections.Counter(v["mode"] for v in votes)
    by_result = collections.Counter(v["result_raw"] for v in votes)
    outcomes = collections.Counter(m["outcome"] for v in votes for m in v["motions"] if m.get("outcome"))
    prefixes = collections.Counter((m["iromany_parsed"].get("kind") or "?") for v in votes for m in v["motions"])
    by_month = collections.Counter(v["on_date"][:7] for v in votes if v["on_date"])
    decisions = [v for v in votes if v["kind"] == "dontes"]
    qualified = sum(1 for v in decisions if v["mode"] != "Listás" and not v["mode"].startswith("Titkos"))

    mps = parse_kepviselok(load(RAW / "kepviselok" / "all.xml")) if (RAW / "kepviselok" / "all.xml").exists() else []
    days = parse_ulesnap(load(next((RAW / "ulesnap").glob("*.xml")))) if (RAW / "ulesnap").exists() and any((RAW / "ulesnap").glob("*.xml")) else []

    out = {
        "derived_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": {"from": votes[0]["on_date"], "to": votes[-1]["on_date"], "list_files": len(list((RAW / "szavazasok").glob("*.xml")))},
        "votes": len(votes),
        "decisions": len(decisions),
        "quorum_checks": sum(1 for v in votes if v["kind"] == "jelenlet"),
        "secret_ballots": sum(1 for v in votes if v["secret"]),
        "qualified_majority_votes": qualified,
        "by_mode": dict(by_mode.most_common()),
        "by_result": dict(by_result.most_common()),
        "by_month": dict(sorted(by_month.items())),
        "motion_outcomes_top": dict(outcomes.most_common(12)),
        "motion_prefixes": dict(prefixes.most_common()),
        "kiveteles_votes": outcomes.get("kivételességi javaslat elfogadva", 0),
        "surgos_votes": outcomes.get("sürgősségi javaslat elfogadva", 0),
        "interpellation_answers": outcomes.get("Országgyűlés az interpellációs választ elfogadta", 0),
        "hazszabalytol_elteres_votes": sum(n for k, n in outcomes.items() if "házszabályi rendelkezésektől való eltérés" in k),
        "mps": {"total": len(mps),
                "by_faction": dict(collections.Counter(m["faction"] for m in mps).most_common()),
                "by_mandate": dict(collections.Counter(m["mandate_kind"] for m in mps).most_common())} if mps else None,
        "sitting_days": {"count": len(days), "first": days[0]["date"] if days else None, "last": days[-1]["date"] if days else None,
                         "by_session": dict(collections.Counter(d["session"] for d in days).most_common()),
                         "by_kind": dict(collections.Counter(d["kind"] for d in days).most_common())} if days else None,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"written {OUT}")

    # -- votes_index.json ---------------------------------------------------------------
    resolver = NameResolver(mps) if mps else None
    bill_links = {}
    if (RAW / "iromanyok" / "all.xml").exists():
        root = parse_xml((RAW / "iromanyok" / "all.xml").read_bytes())
        for el in root:
            if el.get("szam") and el.get("href"):
                bill_links[el.get("szam")] = el.get("href")
    index_rows = []
    details_cached = 0
    for v in votes:
        row = {k: v[k] for k in ("ts", "slug", "on_date", "mode", "secret", "kind", "igen", "nem", "tartozkodott",
                                 "osszes_szavazat", "result_raw", "passed", "remark")}
        row["time"] = v["ts"][11:16] if v["ts"] else None
        row["motions"] = [{"iromany": m["iromany"], "title": m["title"], "outcome": m["outcome"],
                           "submitters": m["submitters"], "submitter_kind": m["submitter_kind"],
                           "kind": m["iromany_parsed"].get("kind"), "href": bill_links.get(m["iromany"] or "")}
                          for m in v["motions"]]
        detail_path = RAW / "szavazas" / f"{ts_to_slug(v['ts'])}.xml"
        if detail_path.exists():
            d = parse_szavazas(load(detail_path), resolver=resolver)
            details_cached += 1
            row["detail"] = True
            row["position_counts"] = d["position_counts"]
            row["present"] = d["present"]
            row["present_basis"] = d["present_basis"]
            row["faction_tallies"] = d["faction_tallies"]
            m = d["majority"]
            row["majority"] = ({"rule": m["rule"], "label_hu": RULES[Rule(m["rule"])].label_hu,
                                "source": m["source"], "base": m["base"], "needed": m["needed"], "margin": m["margin"],
                                "passed_by_rule": m["passed_by_rule"], "agrees_with_source": m["agrees_with_source"],
                                "present_basis": d["present_basis"]} if m else None)
        else:
            row["detail"] = False
            row["majority"] = list_majority(v)
        index_rows.append(row)
    sync_state = ROOT / "data" / "sync_state.json"
    last_sync_at = None
    if sync_state.exists():
        try:
            last_sync_at = json.loads(sync_state.read_text(encoding="utf-8")).get("last_sync_at")
        except json.JSONDecodeError:
            last_sync_at = None
    idx = {
        "derived_at": out["derived_at"], "last_sync_at": last_sync_at, "seats": seats_for(votes[0]["on_date"]),
        "since": args.since, "window": out["window"],
        "votes": index_rows, "details_cached": details_cached,
        "sitting_days": days,
        "disagreements": [r["ts"] for r in index_rows if r.get("majority") and r["majority"]["agrees_with_source"] is False],
    }
    (DERIVED / "votes_index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"written {DERIVED / 'votes_index.json'}: {len(index_rows)} votes, {details_cached} with details, "
          f"{len(idx['disagreements'])} rule/source disagreements")

    # -- hero_vote.json -----------------------------------------------------------------
    hero_path = RAW / "szavazas" / f"{ts_to_slug(args.hero)}.xml"
    if hero_path.exists():
        hero = parse_szavazas(load(hero_path), resolver=resolver)
        hero["derived_at"] = out["derived_at"]
        for m in hero["motions"]:
            m["href"] = bill_links.get(m["iromany"] or "")
        (DERIVED / "hero_vote.json").write_text(json.dumps(hero, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"written {DERIVED / 'hero_vote.json'}: {hero['ts']} {hero['motions'][0]['iromany'] if hero['motions'] else ''} "
              f"{hero['igen']}-{hero['nem']}-{hero['tartozkodott']} rule={hero['majority']['rule'] if hero['majority'] else None}")
    else:
        print(f"hero vote {args.hero} not in cache — run sync-votes first", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
