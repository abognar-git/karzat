#!/usr/bin/env python3
"""Derive a small, committed summary of what the cached W-API payloads say — the numbers the
README quotes about cycle 43 come from here, never from prose.

    python3 -m scripts.derive_first_light        # data/raw/* -> data/derived/first_light.json

Reads only the local cache (no network). Re-run after a sync; the README gate then tells you
which sentences moved. The raw cache is git-ignored; this file is not, so a clean checkout
can still verify the README.
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

from karzat.normalise import parse_kepviselok, parse_szavazasok, parse_ulesnap  # noqa: E402
from karzat.xmlutil import parse_xml, to_dict  # noqa: E402

RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "derived" / "first_light.json"


def load(path: Path) -> dict:
    root = parse_xml(path.read_bytes())
    return {root.tag: to_dict(root)}


def main() -> int:
    votes = []
    for f in sorted((RAW / "szavazasok").glob("*.xml")):
        votes.extend(parse_szavazasok(load(f)))
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
