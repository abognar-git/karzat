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
import json as _json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karzat.api import SERVICES, WebApi  # noqa: E402
from karzat.majority import Rule, Tally, needed  # noqa: E402

_PQ = _json.loads((ROOT / "data" / "derived" / "parquet.json").read_text(encoding="utf-8"))["tables"] \
    if (ROOT / "data" / "derived" / "parquet.json").exists() else {}


def pqt(name: str) -> int:
    """Rows in one exported table, from the manifest the derivation writes."""
    return _PQ.get(name, {}).get("rows", 0)


def pqb(name: str | None = None) -> float:
    """Megabytes of one table, or of all of them."""
    return (_PQ.get(name, {}).get("bytes", 0) if name else sum(v["bytes"] for v in _PQ.values())) / 1e6


# Votes naming more than one motion: the link table has one row per motion, so the excess over the vote count
# is exactly the number the single `iromany` column used to drop.
PQ_MULTI = 0
if _PQ:
    import pyarrow.parquet as _pqf
    _lp = ROOT / "site" / "adatok" / "szavazas_iromany.parquet"
    if _lp.exists():
        _t = _pqf.read_table(_lp, columns=["ciklus", "szavazas_id", "sorszam"])
        PQ_MULTI = sum(1 for n in _t.column("sorszam") if n.as_py() == 2)

_SB = _json.loads((ROOT / "data" / "derived" / "search_bench.json").read_text(encoding="utf-8")) \
    if (ROOT / "data" / "derived" / "search_bench.json").exists() else {"cycles": {}}


def sb(cycle: int, which: str, field: str) -> float:
    """A figure the search benchmark measured. Typed once into the page, drifted by three points, and the
    test that guarded it asserted the drifted value — so the README's copy is gated like every other number."""
    c = (_SB.get("cycles") or {}).get(str(cycle)) or {}
    v = (c.get(which) or {}).get(field, 0)
    return 100 * v if field == "recall10" else v

_MONTHS: dict = {}


def months(cycle: int) -> dict:
    """Votes and sitting days per month, from the committed vote index rather than `analytics.monthly`.

    The Számok page's per-sitting-day view exists because two months can carry the same bar and mean different
    things, and the README says which two. `monthly()` would give the same figures but wants the alignment
    behind it; these need only the index, which is committed, so the gate stays a few seconds.

    The sittings come from the index's own `sitting_days` and are counted BY NUMBER rather than by distinct
    date, because the House numbers two sittings on one date separately — the rule `sitting_weeks()` follows.
    The first version of this counted the dates a vote was taken on and called them sitting days, which is how
    the README came to print a pace 1.5x too fast: a gate that recomputes a figure the wrong way agrees with
    a page that prints it the wrong way, and the agreement reads as verification."""
    if cycle in _MONTHS:
        return _MONTHS[cycle]
    import gzip as _g
    f = ROOT / "data" / "derived" / f"votes_index_ckl{cycle}.json.gz"
    out: dict[str, dict] = {}
    if f.exists():
        idx = _json.loads(_g.decompress(f.read_bytes()))
        for v in idx["votes"]:
            d = v.get("on_date")
            if not d:
                continue
            m = out.setdefault(d[:7], {"votes": 0, "vote_days": set(), "sittings": 0})
            m["votes"] += 1
            m["vote_days"].add(d)
        for r in idx.get("sitting_days") or []:
            if r.get("date") and r["date"][:7] in out:
                out[r["date"][:7]]["sittings"] += 1
        for m in out.values():
            m["vote_days"] = len(m["vote_days"])
            m["per_sitting"] = (m["votes"] / m["sittings"]) if m["sittings"] else None
    _MONTHS[cycle] = out
    return out


def mo(cycle: int, month: str, field: str) -> float:
    return (months(cycle).get(month) or {}).get(field, 0)


_FLOOR: dict = {}


def floor() -> dict:
    """The Beszédidő figures, recomputed from the same two inputs the pages use.

    `analytics.floor_time` reads only `speeches` and `store`, so this needs neither `load_inputs` nor the
    alignment — eight cycles of gzip and eight position files, a few seconds, and the gate stays runnable.
    Everything the README says about the feature comes from here, including the claim that nothing fails to
    partition: that one is the whole method resting on a `str.partition`, so it is the last figure that
    should be taken on trust."""
    if _FLOOR:
        return _FLOOR
    import gzip as _g
    from karzat.analytics import floor_time, split_event
    D = ROOT / "data" / "derived"
    hours = rows = cycles = named = unsplit = long_days = 0
    lo = hi = None
    y23 = None
    for ck in (36, 37, 38, 39, 40, 41, 42, None):
        sfx = f"_ckl{ck}" if ck else ""
        sp = D / f"speeches{sfx}.json.gz"
        if not sp.exists():
            continue
        speeches = _json.loads(_g.decompress(sp.read_bytes()))
        sub = [r for r in speeches.get("speeches") or []
               if not r.get("technical") and r.get("duration_s") and r.get("date")]
        if not sub:
            continue
        cycles += 1
        rows += len(sub)
        hours += sum(r["duration_s"] for r in sub)
        dates = [r["date"] for r in sub]
        lo = min(dates) if lo is None else min(lo, min(dates))
        hi = max(dates) if hi is None else max(hi, max(dates))
        per_day: dict[str, int] = {}
        for r in sub:
            per_day[r["date"]] = per_day.get(r["date"], 0) + r["duration_s"]
            if r.get("iromany"):
                named += 1
                unsplit += 1 if split_event(r.get("event"), r["iromany"])[0] is None else 0
        long_days += sum(1 for s in per_day.values() if s > 24 * 3600)
        if ck == 42:
            # the archive cycles' stores are gzipped and the current one is not, the same as everywhere else
            _s = D / "votes_positions_ckl42.json"
            store = (_json.loads(_g.decompress((_s.with_suffix(".json.gz")).read_bytes()))
                     if not _s.exists() else _json.loads(_s.read_text(encoding="utf-8")))
            ft = floor_time({"speeches": speeches, "store": store})
            y23 = next(y for y in ft["years"] if y["year"] == "2023")
    # The size confound, recomputed rather than remembered. The ratio a faction shows is not a free choice:
    # a vezérszónoki slot belongs to a faction whatever its size, so a large faction's per-member figure is
    # pushed down arithmetically. The README states how much of the gap that accounts for, so the gate has
    # to be able to recompute both the bands and the two eras' largest factions.
    pts = []
    for ck2 in (36, 37, 38, 39, 40, 41, 42, None):
        s2 = D / f"speeches{'_ckl' + str(ck2) if ck2 else ''}.json.gz"
        if not s2.exists():
            continue
        p2 = D / f"votes_positions{'_ckl' + str(ck2) if ck2 else ''}.json"
        st2 = (_json.loads(_g.decompress(p2.with_suffix(".json.gz").read_bytes())) if not p2.exists()
               else _json.loads(p2.read_text(encoding="utf-8")))
        for y in floor_time({"speeches": _json.loads(_g.decompress(s2.read_bytes())), "store": st2})["years"]:
            if y["days"] < 30:                       # a part-year says nothing about a habit
                continue
            tot2 = sum(y["factions"].values())
            etot = sum(y["expected"].get(f, 0.0) for f in y["factions"]) or 1
            for f, sec in y["factions"].items():
                e = y["expected"].get(f)
                if e:
                    size = e / etot
                    pts.append((int(y["year"]), size, (sec / tot2) / size, y["seats"].get(f) or 0.0))
    small = [p[2] for p in pts if p[1] < 0.05]
    large = [p[2] for p in pts if p[1] >= 0.50]
    biggest = {}
    for yr, size, ratio, n2 in pts:                  # the largest faction of each year
        if yr not in biggest or n2 > biggest[yr][2]:
            biggest[yr] = (size, ratio, n2)
    pre = [v for yr, v in biggest.items() if yr < 2010]
    post = [v for yr, v in biggest.items() if yr >= 2010]
    pre_sz, pre_r = sum(v[0] for v in pre) / len(pre), sum(v[1] for v in pre) / len(pre)
    post_sz, post_r = sum(v[0] for v in post) / len(post), sum(v[1] for v in post) / len(post)
    _FLOOR.update(faction_years=len(pts), band_small=sum(small) / len(small), band_large=sum(large) / len(large),
                  pre_ratio=pre_r, pre_size=100 * pre_sz, post_ratio=post_r, post_size=100 * post_sz,
                  mech=pre_r * pre_sz / post_sz)      # what a purely per-faction rule alone would predict
    # The one assumption under every hour on those pages: that videoido is a stopwatch and not a clip.
    # The transcript is the outside witness, so the figure the README quotes for it is recomputed here too.
    tpts = []
    for sfx in ("_ckl42", ""):
        tf = D / f"speech_texts{sfx}.json.gz"
        if not tf.exists():
            continue
        for t in _json.loads(_g.decompress(tf.read_bytes()))["texts"].values():
            if t.get("duration_s") and t.get("chars") and 20 <= t["duration_s"] <= 3600:
                tpts.append((t["duration_s"], t["chars"]))
    tmx = sum(d for d, _ in tpts) / len(tpts)
    tmy = sum(c for _, c in tpts) / len(tpts)
    corr = (sum((d - tmx) * (c - tmy) for d, c in tpts)
            / (sum((d - tmx) ** 2 for d, _ in tpts) * sum((c - tmy) ** 2 for _, c in tpts)) ** 0.5)
    trates = sorted(c / (d / 60) for d, c in tpts)
    _FLOOR.update(text_pairs=len(tpts), text_r=corr, text_rate=trates[len(trates) // 2])

    fac_total = sum(y23["factions"].values())
    e23 = sum(y23["expected"].get(f, 0.0) for f in y23["factions"]) or 1
    gov = sum(s for f, s in y23["factions"].items() if f in ("Fidesz", "KDNP"))
    gov_e = sum(y23["expected"].get(f, 0.0) for f in ("Fidesz", "KDNP"))
    gov_share, gov_size = 100 * gov / fac_total, 100 * gov_e / e23
    slot = next(s for s in y23["slots"] if "előtti" in s["event"])
    _FLOOR.update(cycles=cycles, rows=rows, hours=hours / 3600, first=lo, last=hi,
                  named=named, unsplit=unsplit, long_days=long_days,
                  gov_share=gov_share, gov_size=gov_size, gov_ratio=gov_share / gov_size,
                  slot_share=100 * slot["seconds"] / y23["seconds"],
                  vezer=100 * y23["forms"]["vezérszónoki felszólalás"] / y23["seconds"])
    return _FLOOR



_GATE = _json.loads((ROOT / "data" / "derived" / "a11y.json").read_text(encoding="utf-8")) \
    if (ROOT / "data" / "derived" / "a11y.json").exists() else {}


def gate(k: str):
    """What the accessibility gate recorded about its own coverage — written by
    `python3 -m scripts.audit_a11y --write`, not asserted here. Reading it rather than recomputing keeps this
    check runnable: counting off-origin references means opening every one of the site's HTML files."""
    return _GATE.get(k, 0)


README = ROOT / "README.md"
ENGINEERING = ROOT / "docs" / "ENGINEERING.md"     # the build log: the front page moved off it, the gate did not
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
    _landing_cache = {}
    def _landing():
        if "p" not in _landing_cache:
            from scripts.build_site import build_landing
            _landing_cache["p"] = build_landing()
        return _landing_cache["p"]
    f42 = _json.loads((ROOT / "data" / "derived" / "first_light_ckl42.json").read_text(encoding="utf-8"))
    m42 = _json.loads((ROOT / "data" / "derived" / "mps_ckl42.json").read_text(encoding="utf-8"))
    m43 = _json.loads((ROOT / "data" / "derived" / "mps.json").read_text(encoding="utf-8"))
    h42 = _json.loads((ROOT / "data" / "derived" / "hero_vote_ckl42.json").read_text(encoding="utf-8"))
    _pf = ROOT / "data" / "derived" / "portre.json"
    def _portre():
        return _json.loads(_pf.read_text(encoding="utf-8")) if _pf.exists() else {}
    cap = _json.loads((ROOT / "reference" / "parlament" / "listakorlat.json").read_text(encoding="utf-8"))
    f34 = _json.loads((ROOT / "data" / "derived" / "first_light_ckl34.json").read_text(encoding="utf-8"))
    f35 = _json.loads((ROOT / "data" / "derived" / "first_light_ckl35.json").read_text(encoding="utf-8"))
    irp = (ROOT / "data" / "derived" / "iromany_records.json")
    _OV = ("kérdés", "azonnali kérdés", "interpelláció")
    ir = _json.loads(irp.read_text(encoding="utf-8")) if irp.exists() else {"count": 0, "records": {}}
    ir_events = sum(len(r["events"]) for r in ir["records"].values())
    ir_laws = sum(1 for r in ir["records"].values() if (r.get("promulgation") or {}).get("law_ref"))
    # the motion pages' own figures, recomputed here so the README's numbers cannot drift from the corpus
    import gzip as _gz
    _OV = ("kérdés", "azonnali kérdés", "interpelláció")
    _tp = ROOT / "data" / "derived" / "speech_texts.json.gz"
    _tx = _json.loads(_gz.decompress(_tp.read_bytes()))["texts"] if _tp.exists() else {}
    _refs = [(k, e) for k, r in ir["records"].items() for e in (r.get("events") or []) if e.get("speech")]
    _held = [(k, e) for k, e in _refs if e["speech"].replace("/", "-") in _tx]
    _ovk = {k for k, r in ir["records"].items() if r.get("main_type") in _OV}
    _ovt = [(k, e) for k, e in _held if k in _ovk]
    ir_refs, ir_held = len(_refs), len(_held)
    ir_ov_total, ir_ov_shown, ir_ov_turns = len(_ovk), len({k for k, _ in _ovt}), len(_ovt)
    ir_ov_chars = sum(_tx[e["speech"].replace("/", "-")]["chars"] for _, e in _ovt)
    ir_leg_turns = len(_held) - len(_ovt)
    ir_leg_recs = len({k for k, _ in _held} - {k for k, _ in _ovt})
    ir_rejected = sum(1 for r in ir["records"].values()
                      if any("elutasította a választ" in (e.get("text") or "") for e in r.get("events") or []))
    ir_addressee = sum(1 for r in ir["records"].values() if r.get("addressee"))
    from scripts.build_site import dissent_events, load_inputs   # the feeds' numbers come from the builder's own alignment
    inp43 = load_inputs()
    ev43 = dissent_events(inp43)
    sp43 = inp43["speeches"] or {}
    sp42 = load_inputs(42)["speeches"] or {}
    sp_old = {c: (load_inputs(c)["speeches"] or {}) for c in (36, 37, 38, 39, 40, 41)}       # the archive cycles' lists, when derived
    old_rows = sum(s.get("count", 0) for s in sp_old.values())
    old_sub = sum(s.get("substantive", 0) for s in sp_old.values())
    old_days = sum(len(s.get("days") or []) for s in sp_old.values())
    old_res = sum(s.get("resolved", 0) for s in sp_old.values())
    old_fix = sum(s.get("days_date_corrected", 0) for s in sp_old.values())
    old_agree = sum(s.get("record_check", {}).get("agree", 0) for s in sp_old.values())
    old_mps = sum(s.get("record_check", {}).get("mps", 0) for s in sp_old.values())
    full = Tally(yes=0, no=0, present=seats, seats=seats)
    p176 = Tally(yes=0, no=0, present=176, seats=seats)
    n = lambda rule, t: needed(Rule(rule), t)  # noqa: E731

    def _jav():
        from scripts.build_site import OGYTV_SCHEDULE
        mps = _json.loads((ROOT / "data" / "derived" / "mps.json").read_text(encoding="utf-8"))["mps"]
        gs = [x["gross"] for m in mps.values() for x in m.get("remuneration") or [] if x.get("gross")]
        mults = [float(m.replace(",", ".")) for _r, m, _c in OGYTV_SCHEDULE]
        best = max({g for g in gs}, key=lambda b: sum(1 for g in gs if any(abs(g - b * mu) <= 1 for mu in mults)))
        cov = sum(1 for g in gs if any(abs(g - best * mu) <= 1 for mu in mults))
        return {"rows": len(gs), "covered": cov}
    jav = _jav()

    return [
        ("its statutory multiples land on {:,.0f} of the month's {:,.0f} stated amounts to the forint",
         [jav["covered"], jav["rows"]]),
        # The Számok page's per-day view: the pair that makes the case for it, from the committed vote index.
        ("December\n2023 and December 2025 are {:,.0f} and {:,.0f} votes, which is the same bar twice, and\n"
         "{:.1f} against {:.1f} votes a sitting day",
         [mo(42, "2023-12", "votes"), mo(42, "2025-12", "votes"),
          mo(42, "2023-12", "per_sitting"), mo(42, "2025-12", "per_sitting")]),
        # The month whose two readings differ most, and the one that made the wrong denominator visible.
        ("April 2023 carried {:,.0f} votes across {:,.0f} sitting days, a pace of {:.1f}",
         [mo(42, "2023-04", "votes"), mo(42, "2023-04", "sittings"), mo(42, "2023-04", "per_sitting")]),
        # Run it / status
        # The Parquet section's figures were typed rather than generated — the one part of this README that
        # was outside its own gate. They come from the manifest the derivation writes.
        ("in one piece — {:,.0f} votes, {:,.0f} cast positions,\n{:,.0f} member-cycles, and five more tables: the\n{:,.0f} speeches from the floor",
         [pqt("szavazasok"), pqt("szavazatok"), pqt("kepviselok"), pqt("felszolalasok")]),
        ("Seventeen million positions come to\n{:.1f} MB and all eight files together to {:.1f} MB",
         [pqb("szavazatok"), pqb()]),
        ("drops the rest: {:,.0f} of\n{:,.0f} name more than one", [PQ_MULTI, pqt("szavazasok")]),
        ("carries all {:,.0f} of\nthem", [pqt("szavazas_iromany")]),
        ("| every word required, newest first | {:.1f}% | {:.3f} | — |",
         [sb(42, "was", "recall10"), sb(42, "was", "mrr")]),
        ("| **the same index, scored by BM25** | **{:.1f}%** | **{:.3f}** | **nothing** |",
         [sb(42, "now", "recall10"), sb(42, "now", "mrr")]),
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
        ("`stats`: {:,.0f} votes where the computed threshold and the\nrecorded result disagree — now across ten cycles and {:,.0f} roll calls, not {:,.0f} (each of the\n{:,.0f} is marked", [dbs["rule_source_disagreements"], dbs["votes_with_roll_call"], vi["details_cached"], dbs["rule_source_disagreements"]]),
        # speeches — data/derived/speeches*.json.gz
        ("agrees with it for {:,.0f} of {:,.0f} cycle-43 MPs and {:,.0f} of {:,.0f} in cycle 42",
         [sp43.get("record_check", {}).get("agree", 0), sp43.get("record_check", {}).get("mps", 0), sp42.get("record_check", {}).get("agree", 0), sp42.get("record_check", {}).get("mps", 0)]),
        ("Cycle 43: {:,.0f} rows over {:,.0f} sitting days, {:,.0f} substantive; cycle 42: {:,.0f} rows over {:,.0f} days",
         [sp43.get("count", 0), len(sp43.get("days") or []), sp43.get("substantive", 0), sp42.get("count", 0), len(sp42.get("days") or [])]),
        # Beszédidő — karzat.analytics.floor_time(), recomputed by floor() above
        ("of the {:,.0f} that do, **{:,.0f}** fail to partition on the number",
         [floor()["named"], floor()["unsplit"]]),
        ("napirend előtti alone is {:.1f}% of 2023", [floor()["slot_share"]]),
        # the years rather than the exact dates: NUMPAT reads a figure, not an ISO date, and the day-level
        # span of the record is the coverage page's job anyway
        ("{:,.0f} cycles carry it — {:,.0f} substantive speeches, {:,.0f} hours, {} to {}",
         [floor()["cycles"], floor()["rows"], floor()["hours"], int(floor()["first"][:4]), int(floor()["last"][:4])]),
        ("the governing parties took {:.1f}% of the floor time on {:.1f}% of the roster, {:.2f}×",
         [floor()["gov_share"], floor()["gov_size"], floor()["gov_ratio"]]),
        ("not per member — is {:.1f}% of that year", [floor()["vezer"]]),
        ("across {:,.0f} faction-years since 1998 the ratio falls with size", [floor()["faction_years"]]),
        ("factions under a twentieth of the House average {:.2f}× and those over half average {:.2f}×",
         [floor()["band_small"], floor()["band_large"]]),
        ("before 2010 it ran {:.2f}× at {:.1f}% of the House, from 2010 on {:.2f}× at {:.1f}%, where a purely per-faction rule alone would predict {:.2f}×",
         [floor()["pre_ratio"], floor()["pre_size"], floor()["post_ratio"], floor()["post_size"], floor()["mech"]]),
        ("{:,.0f} sitting days whose own durations add up to more than a calendar day",
         [floor()["long_days"]]),
        ("over the {:,.0f} speeches carrying both, characters against seconds correlate at {:.2f} and the median rate is {:,.0f} characters a minute",
         [floor()["text_pairs"], floor()["text_r"], floor()["text_rate"]]),
        # the accessibility and performance gate — scripts/audit_a11y.py, tests/test_a11y.py
        ("{:,.0f} HTML files is not something anyone audits, and nobody needs to: it is {:,.0f} templates",
         [gate("html"), gate("kinds")]),
        ("axe-core {:g} is vendored and SHA-256 pinned", [float(".".join(str(gate("axe")).split(".")[:2]))]),
        ("The built site now makes {:,.0f} requests off its own origin", [gate("offsite")]),
        # the accessibility and performance gate — recorded by scripts/audit_a11y.py --write
        ("{:,.0f} HTML files is not something anyone audits, and nobody needs to: it is {:,.0f} templates",
         [gate("html"), gate("kinds")]),
        ("axe-core {:g} is vendored and SHA-256 pinned", [float(".".join(str(gate("axe")).split(".")[:2]))]),
        ("The built site now makes {:,.0f} requests off its own origin", [gate("offsite")]),
        # the landing page — scripts.build_site.build_landing()
        ("the arrow keys walk the seats; then the ten cycles since 1990 as stacked composition bars (mandates in force on each constituent sitting, read from the records' dated rows — {:,.0f} in the strip)",
         [_landing().count('<a class="cyc')]),
        ("and their own kind in the search index ({:,.0f} entries)",
         [sum(1 for it in _json.loads((ROOT / "site" / "kereses" / "index.json").read_text(encoding="utf-8")) if it["k"] == "szoszolo")]),
        ("with them the **{:,.0f} nationality spokespersons**", [_json.loads((ROOT / "data" / "derived" / "szoszolok.json").read_text(encoding="utf-8"))["count"]]),
        ("all {:,.0f} mandate-holders placed where their own record puts them,", [_landing().count('<a class="seat" href=')]),
        ("The {:,.0f} seats still left as outlines",
         [_landing().count('polygon points') - _landing().count('class="seatshape occ"')]),
        # the ministerial bench — data/derived/kormany.json
        ("takes the office from what the record of the speech prints. {:,.0f} of them (the deputy PM among them)",
         [_json.loads((ROOT / "data" / "derived" / "kormany.json").read_text(encoding="utf-8"))["count"]]),
        ("stays invisible, so {:,.0f} bench seats are still outlines",
         [22 - len(_json.loads((ROOT / "data" / "derived" / "kormany.json").read_text(encoding="utf-8"))["people"])
          - sum(1 for c in _json.loads((ROOT / "data" / "derived" / "seating.json").read_text(encoding="utf-8"))["coords"].values() if c["sector"] == 0)]),
        ("takes the Commons picture where one exists ({:,.0f} of the {:,.0f})",
         [_json.loads((ROOT / "reference" / "wikidata" / "kormany_photos.json").read_text(encoding="utf-8"))["count"],
          _json.loads((ROOT / "data" / "derived" / "kormany.json").read_text(encoding="utf-8"))["count"]]),
        # the portraits — data/derived/portre.json
        ("renders each to a {:,.0f}×{:,.0f} grey WebP", [_portre().get("width", 0), _portre().get("height", 0)]),
        ("caches the {:,.0f} that answer", [_portre().get("count", 0)]),
        ("averaging about {:,.0f} kB", [round(_portre().get("bytes", 0) / max(_portre().get("count", 1), 1) / 1024)]),
        # the 400-row listing cap and what the day-level rescan recovered — reference/parlament/listakorlat.json
        ("turned up {:,.0f} months holding exactly 400 votes", [len(cap["months_originally_truncated"])]),
        ("recovered **{:,.0f} votes**", [cap["votes_recovered_by_day_scan"]]),
        ("took {:,.0f} more calls", [cap["votes_recovered_by_day_scan"]]),
        ("**{:,.0f} votes, {:,.0f} of them naming the members, {:,.0f} roll-call positions** and {:,.0f} unresolved names",
         [dbs["votes"], dbs["votes_with_roll_call"], dbs["positions"], dbs["positions_unresolved"]]),
        ("cycle 34 alone went from 13,746 to {:,.0f} votes", [f34["votes"]]),
        ("**{:,.0f} sitting days** had more than 400 votes in a single day", [len(cap["days_still_truncated"])]),
        # the coverage page — the same derived inputs the cycle pages use
        ("cycles 34 and 35 hold {:,.0f} votes and one name-level list between them", [f34["votes"] + f35["votes"]]),
        ("the {:,.0f} permanently short days and the months containing a secret ballot", [len(cap["days_still_truncated"])]),
        ("and the {:,.0f} secret ballots have nothing to download by definition",
         [sum(_json.loads(p.read_text(encoding="utf-8")).get("secret_ballots", 0)
              for p in sorted((ROOT / "data" / "derived").glob("first_light*.json")))]),
        # the motion records — data/derived/iromany_records.json
        ("{:,.0f} records carry `képviselő elutasította a választ` in their log", [ir_rejected]),
        ("The registry holds\n{:,.0f} full motion records for cycle 43 — the listing names {:,.0f}", [ir["count"], ir.get("listed", 0)]),
        ("and {:,.0f} of\nthem had no page at all", [ir["count"] - 129]),
        ("All {:,.0f} of\nthem resolve to a row, and {:,.0f} resolve to a speech whose text the site already holds", [ir_refs, ir_held]),
        ("{:,.0f} of the {:,.0f} oversight motions carry the exchange as it was spoken — {:,.0f}\nturns, {:,.0f} characters",
         [ir_ov_shown, ir_ov_total, ir_ov_turns, ir_ov_chars]),
        ("would add\n{:,.0f} records and {:,.0f} turns", [ir_leg_recs, ir_leg_turns]),
        ("filled on {:,.0f} of the {:,.0f} oversight instruments", [ir_addressee, ir_ov_total]),
        ("including all {:,.0f} where", [ir_rejected]),
        ("({:,.0f} motions, {:,.0f} records, ~5 minutes)", [ir.get("listed", 0), ir["count"]]),
        ("{:,.0f} events across the cycle, {:,.0f} promulgated laws", [ir_events, ir_laws]),
        ("**joined to the annex by exact name only** ({:,.0f} codes → {:,.0f} of the annex's {:,.0f} settlements",
         [_json.loads((ROOT / "reference" / "valasztas" / "iranyitoszam.json").read_text(encoding="utf-8"))["codes"],
          _json.loads((ROOT / "reference" / "valasztas" / "iranyitoszam.json").read_text(encoding="utf-8"))["settlements"],
          _json.loads((ROOT / "reference" / "valasztas" / "iranyitoszam.json").read_text(encoding="utf-8"))["annex_settlements"]]),
        # the archive cycles' speech lists — data/derived/speeches_ckl36..41.json.gz
        ("cycles 36–41 synced: {:,.0f} day lists, {:,.0f} rows, {:,.0f} substantive, {:,.0f} rows resolved to MPs",
         [old_days, old_rows, old_sub, old_res]),
        ("the record agrees with the substantive count for {:,.0f} of {:,.0f} MP-cycles", [old_agree, old_mps]),
        ("{:,.0f} of the {:,.0f} day lists print the day one day early", [old_fix, old_days]),
        ("The texts of these cycles are not fetched: {:,.0f} calls at the polite pace, about {:,.0f} hours", [old_sub, round(old_sub * 0.6 / 3600)]),
        # the whole corpus — scripts.build_site.site_totals()
        ("{:,.0f} votes over ten cycles", [__import__("scripts.build_site", fromlist=["site_totals"]).site_totals()["votes"]]),
        ("{:,.0f} of them roll calls that name the members", [__import__("scripts.build_site", fromlist=["site_totals"]).site_totals()["roll_calls"]]),
        ("their rosters come from the records ({:,.0f} and {:,.0f} people); {:,.0f} people in all",
         [len(_json.loads((ROOT / "data" / "derived" / "mps_ckl34.json").read_text(encoding="utf-8"))["mps"]), len(_json.loads((ROOT / "data" / "derived" / "mps_ckl35.json").read_text(encoding="utf-8"))["mps"]),
          __import__("scripts.build_site", fromlist=["site_totals"]).site_totals()["people"]]),
        ("Rule vs source across the corpus: {:,.0f} disagreements in {:,.0f}", [__import__("scripts.build_site", fromlist=["site_totals"]).site_totals()["disagreements"], __import__("scripts.build_site", fromlist=["site_totals"]).site_totals()["votes"]]),
        # committees and the district annex
        ("caches the {:,.0f} committees' records", [len(_json.loads((ROOT / "data" / "derived" / "committees.json").read_text(encoding="utf-8"))["list"])]),
        ("parsed into {:,.0f} OEVK and {:,.0f} settlement/district names; the {:,.0f} cities and districts the annex splits by streets",
         [len(_json.loads((ROOT / "reference" / "valasztas" / "oevk_telepules.json").read_text(encoding="utf-8"))["oevk"]),
          len(_json.loads((ROOT / "reference" / "valasztas" / "oevk_telepules.json").read_text(encoding="utf-8"))["settlements"]),
          sum(1 for v in _json.loads((ROOT / "reference" / "valasztas" / "oevk_telepules.json").read_text(encoding="utf-8"))["settlements"].values() if any(x["partial"] for x in v))]),
        # speech texts — data/derived/speech_texts.json.gz
        ("Cycle 43: {:,.0f} texts, {:,.0f} characters. SQLite v4", [(inp43["texts"] or {}).get("count", 0), (inp43["texts"] or {}).get("chars", 0)]),
        # feeds — the same alignment record the MP pages print
        ("Cycle 43: {:,.0f} roll calls with a dissent, {:,.0f} dissents in all", [len(ev43), sum(len(e["dissents"]) for e in ev43)]),
        # motions on the MP pages — data/derived/mps.json + first_light.json (bills listed)
        ("(the current cycle's {:,.0f} irományok, submitters as \"Név (Frakció)\") is resolved to people with the same resolver and listed under each current-cycle MP: {:,.0f} submissions, and for every one of the {:,.0f} people",
         [dbs["bills"], sum(len(m["motions"]) for m in m43["mps"].values()), m43["count"]]),
        ("{:,.0f} submissions across the current cycle's {:,.0f}\nirományok, and for every one of the {:,.0f} people the list count equals",
         [sum(len(m["motions"]) for m in m43["mps"].values()), dbs["bills"], m43["count"]]),
        # the chamber geometry — data/derived/seating.json
        ("returns all {:,.0f} seats of the\nképviselőházi ülésterem with their outlines: the ministerial *patkó* as sector 0 ({:,.0f} seats",
         [len(seat_plan["seat_outlines"]), sum(1 for o in seat_plan["seat_outlines"] if o["sector"] == 0)]),
        ("lays every MP on their real seat: {:,.0f} of {:,.0f} match, {:,.0f} seats stay\nempty",
         [len(seat_plan["coords"]), seat_plan["seated"], len(seat_plan["empty_seats"])]),
        ("What `szektipus` `p` ({:,.0f} seats at the outer ends", [sum(1 for o in seat_plan["seat_outlines"] if o["type"] == "p")]),
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
        ("{:,.0f} of the hero vote's {:,.0f} placed, the {:,.0f} MPs whose mandates",
         [sum(1 for pp in hero["positions"] if pp.get("mp_azon") in seat_plan["coords"]), len(hero["positions"]),
          sum(1 for pp in hero["positions"] if pp.get("mp_azon") not in seat_plan["coords"])]),
        # -- the front page's table: born as zeros, filled by --sync, so no number there was ever typed
        ("| Recorded votes, 1990 → today | **{:,.0f}** |", [dbs["votes"]]),
        ("| Roll calls naming every member | **{:,.0f}** |", [dbs["votes_with_roll_call"]]),
        ("| Individual voting positions | **{:,.0f}** |", [dbs["positions"]]),
        ("| Unresolved names among them | **{:,.0f}** |", [dbs["positions_unresolved"]]),
        # NOT dbs["mp"]: db_summary is a snapshot of the last SQLite load and its person count predates a
        # resolver fix; the roster union across the committed per-cycle files is the current, tested truth
        ("| People who ever held a mandate | **{:,.0f}** |",
         [len({a for f in sorted((ROOT / "data" / "derived").glob("mps*.json"))
               for a in _json.loads(f.read_text(encoding="utf-8"))["mps"]})]),
        ("| Floor speeches indexed | **{:,.0f}** |", [dbs["speeches"]]),
        ("| Sitting days | **{:,.0f}** |", [dbs["sitting_days"]]),
        ("| Offline tests guarding all of it | **{:,.0f}** |", [tc["_total"]]),
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

    files = [f for f in (README, ENGINEERING) if f.exists()]
    texts = {f: f.read_text(encoding="utf-8") for f in files}
    dirty: set = set()
    errors: list[str] = []
    synced: list[str] = []
    checked = 0

    for template, values in build():
        rx = as_regex(template)
        hits = [(f, m) for f in files for m in rx.finditer(texts[f])]
        if len(hits) > 1:
            errors.append(f"{template!r} matches {len(hits)} times across the README and the log; "
                          "a registered fragment must be unique")
            continue
        if not hits:
            errors.append(f"neither the README nor the log contains: {template!r}")
            continue
        f, m = hits[0]
        want = template.format(*values)
        have = m.group(0)
        norm = lambda s: re.sub(r"\s+", " ", s)  # noqa: E731
        if norm(have) != norm(want):
            if args.sync:
                # keep the file's own line breaks: rewrite only the numbers, in order
                new = _rewrite_numbers(have, want)
                texts[f] = texts[f][:m.start()] + new + texts[f][m.end():]
                dirty.add(f)
                synced.append(f"{norm(have)}  ->  {norm(want)}")
                checked += len(values)
                continue
            errors.append(f"{template!r}\n      {f.name}: {norm(have)}\n      now:    {norm(want)}")
        else:
            checked += len(values)

    if args.sync and synced:
        for f in dirty:
            f.write_text(texts[f], encoding="utf-8")
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
