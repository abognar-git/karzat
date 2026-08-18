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


def build() -> list[tuple[str, list]]:
    tc = test_counts()
    seats = config_seats()
    fs = faction_seats()
    sm = snapshot()["summary"]
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
