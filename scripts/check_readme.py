#!/usr/bin/env python3
"""Verify the README's numbers against the code, config and tests they describe.

A figure quoted in prose is correct on the day it is typed and wrong after the next change,
and nothing complains. This is the same gate the sibling repos carry (pyrite, poisoned-well,
trigger-discipline): each entry in CLAIMS is a fragment copied **verbatim from the README**
with its numbers replaced by format specs, paired with the values recomputed right now. The
fragment is turned into a regex that matches the README whatever the current numbers are, so
the README's own text is what is checked — not a second copy of the number kept here.

    python3 -m scripts.check_readme            # verify (also run by tests/test_check_readme.py)
    python3 -m scripts.check_readme --sync     # rewrite the registered figures in place

Rules, all enforced: a registered fragment must match exactly once (a duplicate would rot
behind a green gate); a fragment that stops appearing fails even under --sync; a number typed
into the prose that nobody registered is not checked at all — so register it.

Today the recomputed values come from three places: `karzat.majority` (thresholds), the
faction config (Wikidata seat counts and their sum), and unittest discovery (test counts).
When derived data exists (data/*.sqlite, data/derived/*.json), the census numbers of the
site join this list; the mechanism does not change.
"""

from __future__ import annotations

import argparse
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karzat.api import SERVICES, WebApi  # noqa: E402
from karzat.majority import Rule, Tally, needed  # noqa: E402

README = ROOT / "README.md"
FACTIONS = ROOT / "config" / "factions.yml"


# -- values recomputed now ------------------------------------------------------------------

def test_counts() -> dict[str, int]:
    """Total tests and per-module counts, by the same discovery `python -m unittest` uses."""
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    per: dict[str, int] = {}

    def walk(s):
        for t in s:
            if isinstance(t, unittest.TestSuite):
                walk(t)
            else:
                mod = t.__class__.__module__.rsplit(".", 1)[-1]
                per[mod] = per.get(mod, 0) + 1
    walk(suite)
    per["_total"] = sum(per.values())
    return per


SNAPSHOT = ROOT / "reference" / "wikidata" / "members_ckl43.json"


def snapshot() -> dict:
    """The Wikidata member snapshot — the source of the README's census numbers.
    (config/factions.yml restates its per-group counts; tests/test_wikidata.py keeps them equal.)"""
    import json
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def faction_seats() -> dict[str, int]:
    """Per-group current members from the snapshot, keyed by the config's short ids."""
    by_label = snapshot()["summary"]["by_group_current"]
    text = FACTIONS.read_text(encoding="utf-8")
    ids: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"\s*-\s*id:\s*\"?([^\"#]+?)\"?\s*(?:#.*)?$", line)
        if m:
            current = m.group(1).strip()
            continue
        m = re.match(r"\s*wikidata_group:\s*(.+?)\s*$", line)
        if m and current:
            ids[current] = m.group(1)
    return {short: by_label.get(label, 0) for short, label in ids.items()}


def config_seats() -> int:
    m = re.search(r"^seats:\s*(\d+)", FACTIONS.read_text(encoding="utf-8"), re.M)
    return int(m.group(1)) if m else 199


FIRST_LIGHT = ROOT / "data" / "derived" / "first_light.json"


def first_light() -> dict:
    import json
    return json.loads(FIRST_LIGHT.read_text(encoding="utf-8"))


def t71() -> dict:
    """The T/71 fixture, normalised — the README's worked example of speed of legislation."""
    from karzat.normalise import parse_iromany
    from karzat.xmlutil import parse_xml, to_dict
    root = parse_xml((ROOT / "tests" / "fixtures" / "real_iromany_71.xml").read_bytes())
    return parse_iromany({root.tag: to_dict(root)})


def build() -> list[tuple[str, list]]:
    tc = test_counts()
    seats = config_seats()
    fs = faction_seats()
    sm = snapshot()["summary"]
    fl = first_light()
    bm = fl["by_mode"]
    bill = t71()
    import json as _json
    vi = _json.loads((ROOT / "data" / "derived" / "votes_index.json").read_text(encoding="utf-8"))
    hero = _json.loads((ROOT / "data" / "derived" / "hero_vote.json").read_text(encoding="utf-8"))
    seat_plan = _json.loads((ROOT / "data" / "derived" / "seating.json").read_text(encoding="utf-8"))
    dbs = _json.loads((ROOT / "data" / "derived" / "db_summary.json").read_text(encoding="utf-8"))
    f42 = _json.loads((ROOT / "data" / "derived" / "first_light_ckl42.json").read_text(encoding="utf-8"))
    m42 = _json.loads((ROOT / "data" / "derived" / "mps_ckl42.json").read_text(encoding="utf-8"))
    m43 = _json.loads((ROOT / "data" / "derived" / "mps.json").read_text(encoding="utf-8"))
    h42 = _json.loads((ROOT / "data" / "derived" / "hero_vote_ckl42.json").read_text(encoding="utf-8"))
    full = Tally(yes=0, no=0, present=seats, seats=seats)
    p176 = Tally(yes=0, no=0, present=176, seats=seats)
    n = lambda rule, t: needed(Rule(rule), t)  # noqa: E731

    return [
        # Run it / status
        ("# offline; {:,.0f} tests", [tc["_total"]]),
        ("classifier with provenance; {:,.0f} tests", [tc.get("test_majority", 0)]),
        ("newest vote, last sync; {:,.0f} tests", [tc.get("test_freshness", 0)]),
        ("Live requests are paced at ≥ {:.1f} s", [WebApi.__dataclass_fields__["min_interval"].default]),

        # One-page section
        ("One chamber, {:,.0f} seats.", [seats]),
        ("two-thirds of all {:,.0f} (Alaptörvény)", [seats]),

        # Majority table: needed at full attendance / at 176 present
        ("| `egyszeru` | present | {:,.0f} / {:,.0f} |", [n("egyszeru", full), n("egyszeru", p176)]),
        ("| `abszolut` | all MPs | {:,.0f} |", [n("abszolut", full)]),
        ("| `ketharmad_jelenlevo` | present | {:,.0f} / {:,.0f} |",
         [n("ketharmad_jelenlevo", full), n("ketharmad_jelenlevo", p176)]),
        ("| `ketharmad_osszes` | all MPs | {:,.0f} |", [n("ketharmad_osszes", full)]),
        ("| `negyotod_jelenlevo` | present | {:,.0f} / {:,.0f} |",
         [n("negyotod_jelenlevo", full), n("negyotod_jelenlevo", p176)]),
        ("(N={:,.0f} / present 176)", [seats]),

        # Wikidata census — from the snapshot
        ("the {:,.0f} members of cycle 43 sit in four groups", [sm["current_members"]]),
        ("TISZA {:,.0f},\nFidesz {:,.0f}, KDNP {:,.0f}, Mi Hazánk {:,.0f}",
         [fs.get("TISZA", 0), fs.get("Fidesz", 0), fs.get("KDNP", 0), fs.get("Mi Hazánk", 0)]),
        ("above the\n{:,.0f}-seat line", [n("ketharmad_osszes", full)]),
        ("`reference/wikidata/members_ckl43.json`): {:,.0f} members, four groups, and the P4966 ↔ `p_azon` crosswalk for {:,.0f} of them",
         [sm["current_members"], sm["with_ogy_id"]]),
        ("On the first pull: {:,.0f} statements, {:,.0f} current members and {:,.0f} ended",
         [sm["statements"], sm["current_members"], sm["ended_statements"]]),
        ("{:,.0f} of the {:,.0f} with a P4966 id", [sm["with_ogy_id"], sm["current_members"]]),
        ("{:,.0f} with a\nconstituency (P768", [sm["with_district"]]),
        ("and {:,.0f} with a Commons portrait", [sm["with_image"]]),

        # First light — data/derived/first_light.json
        ("the Országgyűlés recorded {:,.0f} votes over {:,.0f} sitting\ndays: {:,.0f} decisions and {:,.0f} quorum check, {:,.0f} of them secret ballots",
         [fl["votes"], fl["sitting_days"]["count"], fl["decisions"], fl["quorum_checks"], fl["secret_ballots"]]),
        ("{:,.0f} were plain\n\"Listás\" votes; {:,.0f} needed two-thirds of those present, {:,.0f} two-thirds of all 199, {:,.0f}\nmore than half of all 199, {:,.0f} four-fifths of those present — {:,.0f} qualified-majority\nvotes",
         [bm.get("Listás", 0), bm.get("Listás a jelenlevők 2/3-ával", 0), bm.get("Listás az összes képviselő 2/3-ával", 0),
          bm.get("Listás az összes képviselő felével", 0), bm.get("Listás a jelenlevők 4/5-ével", 0), fl["qualified_majority_votes"]]),
        ("{:,.0f} votes ordered exceptional procedure", [fl["kiveteles_votes"]]),
        ("{:,.0f} ordered urgent procedure, {:,.0f} approved a departure from the\nHouse rules, and {:,.0f} accepted an interpellation answer",
         [fl["surgos_votes"], fl["hazszabalytol_elteres_votes"], fl["interpellation_answers"]]),
        ("The chamber holds {:,.0f} members:\n{:,.0f} elected in single-member districts and {:,.0f} from the national list",
         [fl["mps"]["total"], fl["mps"]["by_mandate"].get("egyeni", 0), fl["mps"]["by_mandate"].get("lista", 0)]),
        # T/71 — from the fixture via the normaliser
        ("voted through in exceptional procedure in {:,.0f} days", [bill["days_submission_to_final_vote"]]),
        ("Magyar Közlöny {:,.0f} on 28 May: {:,.0f} days from\nsubmission to promulgation",
         [int(bill["promulgation"]["mk_issue"]), bill["days_submission_to_promulgation"]]),
        # window: day numbers gated; if the month words stop matching, the fragment fails and says so
        ("Between {:,.0f} May and {:,.0f} August 2026", [int(fl["window"]["from"][8:10]), int(fl["window"]["to"][8:10])]),
        # agreement of computed verdicts with the recorded results — from votes_index.json
        ("on {:,.0f} of {:,.0f} — {:,.0f} disagreements", [vi["details_cached"] - len(vi["disagreements"]), vi["details_cached"], len(vi["disagreements"])]),
        ("All {:,.0f} cycle-43 vote details synced ({:,.0f} calls)", [vi["details_cached"], 254]),
        ("With all {:,.0f} roll\ncalls in hand", [vi["details_cached"]]),
        ("needed {:,.0f}, {:,.0f} igen, margin +{:,.0f}, and", [hero["majority"]["needed"], hero["igen"], hero["majority"]["margin"]]),
        # the database — data/derived/db_summary.json (python3 -m karzat stats --json)
        ("{:,.0f} votes, {:,.0f} roll-call positions with {:,.0f} unresolved names, {:,.0f} faction-history rows, {:,.0f} mandates across cycles, {:,.0f} bills",
         [dbs["votes"], dbs["positions"], dbs["positions_unresolved"], dbs["mp_faction_rows"], dbs["mp_mandates"], dbs["bills"]]),
        ("Loaded today: {:,.0f} MPs ({:,.0f} with a seat in their record — the {:,.0f}\nsitting, and one former MP whose record still carries a leftover seat — {:,.0f} with a Wikidata\nQID, plus {:,.0f} rows for former MPs",
         [dbs["mp"], dbs["mp_with_seat"], fl["mps"]["total"], dbs["mp_with_qid"], dbs["mp"] - fl["mps"]["total"]]),
        ("(of {:,.0f} former MPs' records exactly one still\ncarries a seat", [m42["count"] - m42["current"]]),
        ("{:,.0f}\nfaction-history rows and {:,.0f} mandates back to 1990, {:,.0f} sitting days, {:,.0f} votes of which\n{:,.0f} carry a roll call — {:,.0f} positions, {:,.0f} unresolved names — {:,.0f} motions, {:,.0f} bills, and\n{:,.0f} rows of `vote_faction_majority`",
         [dbs["mp_faction_rows"], dbs["mp_mandates"], dbs["sitting_days"], dbs["votes"], dbs["votes_with_roll_call"], dbs["positions"],
          dbs["positions_unresolved"], dbs["vote_motions"], dbs["bills"], dbs["faction_majorities"]]),
        ("`stats`: {:,.0f} votes where the computed threshold and the\nrecorded result disagree — now across two cycles and {:,.0f} roll calls, not {:,.0f}", [dbs["rule_source_disagreements"], dbs["votes_with_roll_call"], vi["details_cached"]]),
        # motions on the MP pages — data/derived/mps.json + first_light.json (bills listed)
        ("(the current cycle's {:,.0f} irományok, submitters as \"Név (Frakció)\") is resolved to people with the same resolver and listed under each current-cycle MP: {:,.0f} submissions, and for every one of the {:,.0f} people",
         [dbs["bills"], sum(len(m["motions"]) for m in m43["mps"].values()), m43["count"]]),
        ("{:,.0f} submissions across the current cycle's {:,.0f}\nirományok, and for every one of the {:,.0f} people the list count equals",
         [sum(len(m["motions"]) for m in m43["mps"].values()), dbs["bills"], m43["count"]]),
        # the chamber geometry — data/derived/seating.json
        ("row sits at one common pitch ({:.1f} units here); the seat numbers a row skips are the room's\nempty seats and are drawn faint ({:,.0f} of them:",
         [seat_plan["geometry"]["seat_pitch"], len(seat_plan["empty_seats"])]),
        # cycle 42 pages — data/derived/first_light_ckl42.json + mps_ckl42.json + hero_vote_ckl42.json
        ("its own index, {:,.0f} vote pages and {:,.0f} MP pages, a cycle switch", [f42["votes"], m42["count"]]),
        ("all\n{:,.0f} votes in the directory, the 15th Alaptörvény amendment's final vote — {:,.0f}–{:,.0f}–{:,.0f},\nneeded {:,.0f} — as the hero), {:,.0f} vote pages and {:,.0f} MP pages",
         [f42["votes"], h42["igen"], h42["nem"], h42["tartozkodott"], h42["majority"]["needed"], f42["votes"], m42["count"]]),
        ("The roster is everyone in the cycle's roll calls — {:,.0f} people with the replacements", [m42["count"]]),
        ("({:,.0f} people end the cycle in a different group\nthan they began it)", [sum(1 for m in m42["mps"].values() if m.get("faction_first") and m["faction_first"] != m["faction"])]),
        ("`mps_ckl42.json` ({:,.0f} of the {:,.0f} are not in the cycle-43 roster", [len(set(m42["mps"]) - set(m43["mps"])), m42["count"]]),
        ("({:,.0f} of the {:,.0f} people are not in the cycle-43 roster); factions attributed by each person's last roll call ({:,.0f} switchers)",
         [len(set(m42["mps"]) - set(m43["mps"])), m42["count"], sum(1 for m in m42["mps"].values() if m.get("faction_first") and m["faction_first"] != m["faction"])]),
        # cycle 42 vs 43 — data/derived/first_light_ckl42.json + first_light.json
        ("Cycle 42:\n{:,.0f} votes over {:,.0f} sitting days — {:.1f} per sitting day — of which {:,.0f} decisions and {:,.0f}\nquorum checks ({:,.0f} of them inquorate",
         [f42["votes"], f42["sitting_days"]["count"], f42["votes"] / f42["sitting_days"]["count"], f42["decisions"], f42["quorum_checks"],
          f42["by_result"].get("Határozatképtelen", 0)]),
        ("{:,.0f} secret ballots; {:,.0f}\nqualified-majority votes, {:,.0f}% of decisions; {:,.0f} exceptional-procedure orders and {:,.0f} urgent\nones in four years; {:,.0f} interpellation answers accepted; {:,.0f} departures from the House\nrules",
         [f42["secret_ballots"], f42["qualified_majority_votes"], round(100 * f42["qualified_majority_votes"] / f42["decisions"]),
          f42["kiveteles_votes"], f42["surgos_votes"], f42["interpellation_answers"], f42["hazszabalytol_elteres_votes"]]),
        ("Cycle 43 so far: {:,.0f} votes over {:,.0f} sitting days — {:.1f} per sitting day — {:,.0f}\nqualified-majority votes, {:,.0f}% of decisions; {:,.0f} exceptional-procedure orders and {:,.0f} urgent ones\nin the first three months",
         [fl["votes"], fl["sitting_days"]["count"], fl["votes"] / fl["sitting_days"]["count"], fl["qualified_majority_votes"],
          round(100 * fl["qualified_majority_votes"] / fl["decisions"]), fl["kiveteles_votes"], fl["surgos_votes"]]),
        ("**Cycle 42 backfilled** (2022-05-02 → 2026-05-08): {:,.0f} votes over {:,.0f} sitting days", [f42["votes"], f42["sitting_days"]["count"]]),
        # the chamber — data/derived/seating.json
        ("widest rows {:,.0f} · {:,.0f} · {:,.0f} · {:,.0f} · {:,.0f} · {:,.0f}",
         [seat_plan["analysis"]["sectors"][str(k)]["widest_row"] for k in (1, 2, 3, 4, 5, 6)]),
        ("ministerial front bench ({:,.0f} TISZA MPs, seat numbers up to {:,.0f})",
         [seat_plan["analysis"]["sectors"]["0"]["count"], seat_plan["analysis"]["sectors"]["0"]["widest_row"]]),
        ("{:,.0f} of the hero vote's {:,.0f} placed, the {:,.0f} MPs whose mandates",
         [sum(1 for pp in hero["positions"] if pp.get("mp_azon") in seat_plan["coords"]), len(hero["positions"]),
          sum(1 for pp in hero["positions"] if pp.get("mp_azon") not in seat_plan["coords"])]),
    ]


# -- matching engine (shared pattern) -------------------------------------------------------

SPEC = re.compile(r"\{[^}]*\}")
NUMPAT = r"[-−+]?[\d,]+(?:\.\d+)?"


def as_regex(template: str) -> re.Pattern:
    """Template -> whitespace-tolerant regex with a number pattern wherever a spec sits."""
    parts = [re.escape(p) for p in SPEC.split(template)]
    rx = parts[0]
    for part in parts[1:]:
        rx += NUMPAT + part
    return re.compile(re.sub(r"(?:\\ | |\\\n|\n)+", r"\\s+", rx))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sync", action="store_true", help="rewrite registered figures in the README")
    args = ap.parse_args(argv)

    text = README.read_text(encoding="utf-8")
    errors: list[str] = []
    synced: list[str] = []
    checked = 0

    for template, values in build():
        rx = as_regex(template)
        hits = list(rx.finditer(text))
        if len(hits) > 1:
            errors.append(f"{template!r} matches the README {len(hits)} times; a registered fragment must be unique")
            continue
        if not hits:
            errors.append(f"README no longer contains: {template!r}")
            continue
        m = hits[0]
        want = template.format(*values)
        have = m.group(0)
        norm = lambda s: re.sub(r"\s+", " ", s)  # noqa: E731
        if norm(have) != norm(want):
            if args.sync:
                # keep the README's own line breaks: rewrite only the numbers, in order
                new = _rewrite_numbers(have, want)
                text = text[:m.start()] + new + text[m.end():]
                synced.append(f"{norm(have)}  ->  {norm(want)}")
                checked += len(values)
                continue
            errors.append(f"{template!r}\n      README: {norm(have)}\n      now:    {norm(want)}")
        else:
            checked += len(values)

    if args.sync and synced:
        README.write_text(text, encoding="utf-8")
        for s in synced:
            print(" sync  " + s)

    for e in errors:
        print(" FAIL  " + e)
    print(f"{'FAILED' if errors else 'ok'}: {checked} numbers checked, {len(errors)} problems"
          + (f", {len(synced)} rewritten" if synced else ""))
    return 1 if errors else 0


def _rewrite_numbers(have: str, want: str) -> str:
    """Replace the numeric literals of `have` with those of `want`, positionally, keeping the
    README's own whitespace/line breaks around them."""
    want_nums = re.findall(NUMPAT, want)
    out, i, pos = [], 0, 0
    for m in re.finditer(NUMPAT, have):
        out.append(have[pos:m.start()])
        out.append(want_nums[i] if i < len(want_nums) else m.group(0))
        i += 1
        pos = m.end()
    out.append(have[pos:])
    return "".join(out)


if __name__ == "__main__":
    sys.exit(main())
