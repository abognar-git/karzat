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
    record_histories,
    seats_in_force,
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


def write_json(path: Path, obj, indent=1, sort_keys=False) -> None:
    """Plain or gzip by extension; gzip with mtime=0 so a rebuild from the same inputs is byte-identical."""
    text = json.dumps(obj, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":"), sort_keys=sort_keys) + "\n"
    if path.suffix == ".gz":
        import gzip
        with gzip.GzipFile(filename="", mode="wb", fileobj=path.open("wb"), mtime=0) as gz:
            gz.write(text.encode("utf-8"))
    else:
        path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hero", default=HERO_DEFAULT, help="idopont of the vote to write out in full; 'auto' = the window's last decision passed with two thirds of all MPs, else its last roll call")
    ap.add_argument("--since", default="2026-05-01", help="ignore listed votes before this ISO date (cycle 43 starts 2026-05-09)")
    ap.add_argument("--until", default=None, help="ignore listed votes after this ISO date")
    ap.add_argument("--suffix", default="", help="write first_light_<suffix>.json instead of first_light.json")
    ap.add_argument("--summary-only", action="store_true", help="only the first_light summary (no votes_index / positions / hero)")
    ap.add_argument("--gzip", action="store_true", help="write votes_index / votes_positions as .json.gz (a whole cycle is ~16 MB raw); deterministic (mtime=0)")
    args = ap.parse_args(argv)
    global OUT
    sfx = f"_{args.suffix}" if args.suffix else ""
    if args.suffix:
        OUT = DERIVED / f"first_light{sfx}.json"
    ext = ".json.gz" if args.gzip else ".json"
    p_index, p_positions, p_hero = DERIVED / f"votes_index{sfx}{ext}", DERIVED / f"votes_positions{sfx}{ext}", DERIVED / f"hero_vote{sfx}.json"

    # The cache holds two kinds of listing for the same period: the monthly calls, and the day-by-day re-listing that
    # recovered what the service's 400-row cap had cut (reference/parlament/listakorlat.json). They overlap heavily —
    # a vote the month listed is listed again by its own day — so the listings are merged on the vote's timestamp,
    # which is the API's own key for a vote. Without this the archive cycles count 22,254 votes twice.
    seen: dict[str, dict] = {}
    for f in sorted((RAW / "szavazasok").glob("*.xml")):
        for v in parse_szavazasok(load(f)):
            if v["ts"]:
                seen.setdefault(v["ts"], v)
    votes = [v for v in seen.values() if v["on_date"] and v["on_date"] >= args.since and (not args.until or v["on_date"] <= args.until)]
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
    # by the rule the mode names, not by the mode's spelling: a secret ballot with a two-thirds rule is qualified too
    qualified = sum(1 for v in decisions if classify(payload_rule=v["mode"]).rule not in (Rule.EGYSZERU, Rule.RELATIV))

    mps = parse_kepviselok(load(RAW / "kepviselok" / "all.xml")) if (RAW / "kepviselok" / "all.xml").exists() else []
    days = []
    for f in sorted((RAW / "ulesnap").glob("*.xml")) if (RAW / "ulesnap").exists() else []:
        days.extend(parse_ulesnap(load(f)))
    days = sorted([d for d in days if d["date"] and d["date"] >= args.since and (not args.until or d["date"] <= args.until)], key=lambda x: (x["date"], x["ulnap"] or 0))

    # "as of" the last sync, not the wall clock: a re-derive from the same cache is then byte-identical
    sync_state = ROOT / "data" / "sync_state.json"
    last_sync_at = None
    if sync_state.exists():
        try:
            last_sync_at = json.loads(sync_state.read_text(encoding="utf-8")).get("last_sync_at")
        except json.JSONDecodeError:
            last_sync_at = None
    out = {
        "derived_at": last_sync_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
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
        # accepted only, like the exceptional-procedure and urgency cards beside it
        "hazszabalytol_elteres_votes": sum(n for k, n in outcomes.items() if "házszabályi rendelkezésektől való eltérés" in k and k.endswith("elfogadva")),
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
    # aliases from the Wikidata snapshot cover former MPs (ended statements) — P4966 = p_azon
    aliases = {}
    for snap_path in sorted((ROOT / "reference" / "wikidata").glob("members_*.json")):
        for m in json.loads(snap_path.read_text(encoding="utf-8"))["members"]:
            if m.get("name_hu") and m.get("ogy_ids"):
                aliases.setdefault(m["name_hu"], m["ogy_ids"][0])
    histories = record_histories(RAW)                      # the API's own spelling + faction history per cycle
    resolver = NameResolver(mps, aliases=aliases, histories=histories) if mps else None
    seats_fn = seats_in_force(histories)                   # the all-MP majorities' base: mandates in force on the day
    if args.summary_only:
        return 0
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
            d = parse_szavazas(load(detail_path), resolver=resolver, seats_fn=seats_fn)
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
    idx = {
        "derived_at": out["derived_at"], "last_sync_at": last_sync_at, "seats": seats_for(votes[0]["on_date"]),
        "since": args.since, "window": out["window"],
        "votes": index_rows, "details_cached": details_cached,
        "sitting_days": days,
        "disagreements": [r["ts"] for r in index_rows if r.get("majority") and r["majority"]["agrees_with_source"] is False],
    }
    write_json(p_index, idx, indent=None if args.gzip else 1)
    print(f"written {p_index}: {len(index_rows)} votes, {details_cached} with details, "
          f"{len(idx['disagreements'])} rule/source disagreements")

    # -- votes_positions.json: every roll call, compact -----------------------------------
    # positions: {ts: [[azon_or_name, faction_index, code], ...]}; codes: i/n/t/j/s/h/v (v = igazoltan távol, cycle 42 onwards)
    CODES = {"igen": "i", "nem": "n", "tartozkodott": "t", "jelen_nem_szavazott": "j", "nem_szavazott": "s", "bejelentett_hianyzo": "h", "igazoltan_tavol": "v"}
    factions_list: list[str] = []
    members: dict[str, dict] = {}
    positions: dict[str, list] = {}
    for v in votes:
        detail_path = RAW / "szavazas" / f"{ts_to_slug(v['ts'])}.xml"
        if not detail_path.exists():
            continue
        d = parse_szavazas(load(detail_path), resolver=resolver, seats_fn=seats_fn)
        rows = []
        for pnt in d["positions"]:
            f = pnt["faction"] or ""
            if f not in factions_list:
                factions_list.append(f)
            key = pnt.get("mp_azon") or pnt["name"]
            if pnt.get("mp_azon"):
                members.setdefault(pnt["mp_azon"], {"name": pnt["name"], "faction": f})
            rows.append([key, factions_list.index(f), CODES.get(pnt["position"], "?")])
        positions[v["ts"]] = rows
    write_json(p_positions, {"derived_at": out["derived_at"], "codes": {v: k for k, v in CODES.items()}, "factions": factions_list,
                             "members": members, "positions": positions}, indent=None, sort_keys=True)
    print(f"written {p_positions}: {len(positions)} roll calls, {len(members)} members")

    # -- hero_vote.json -----------------------------------------------------------------
    if args.hero == "auto":
        pick = None
        for r in reversed(index_rows):
            m = r.get("majority") or {}
            if m.get("rule") == "ketharmad_osszes" and r.get("passed") and positions.get(r["ts"]):
                pick = r["ts"]; break
        if pick is None:
            pick = next((r["ts"] for r in reversed(index_rows) if positions.get(r["ts"])), index_rows[-1]["ts"] if index_rows else HERO_DEFAULT)
        args.hero = pick
        print(f"hero (auto): {pick}")
    hero_path = RAW / "szavazas" / f"{ts_to_slug(args.hero)}.xml"
    if hero_path.exists():
        hero = parse_szavazas(load(hero_path), resolver=resolver, seats_fn=seats_fn)
        hero["derived_at"] = out["derived_at"]
        for m in hero["motions"]:
            m["href"] = bill_links.get(m["iromany"] or "")
        p_hero.write_text(json.dumps(hero, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"written {p_hero}: {hero['ts']} {hero['motions'][0]['iromany'] if hero['motions'] else ''} "
              f"{hero['igen']}-{hero['nem']}-{hero['tartozkodott']} rule={hero['majority']['rule'] if hero['majority'] else None}")
    else:
        print(f"hero vote {args.hero} not in cache — run sync-votes first", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
