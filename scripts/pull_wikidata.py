#!/usr/bin/env python3
"""Pull the current members of the Országgyűlés from Wikidata into reference/wikidata/.

    python3 -m scripts.pull_wikidata                 # writes reference/wikidata/members_ckl43.json
    python3 -m scripts.pull_wikidata --since 2022-05-01 --out reference/wikidata/members_ckl42.json

One SPARQL query (people with a P39 "member of the National Assembly of Hungary" statement
started on/after --since, ended or not) and up to ceil(n/50) Commons calls for image
licences. Output is a dated snapshot: what Wikidata said, on which day, under which query.
Wikidata is CC0; each image row carries its own Commons licence string.

Etiquette: identifies itself with a User-Agent, one query, no retries in a loop. Re-run
deliberately (a re-pull that changes counts should also update config/factions.yml and the
README, and the gate will say so).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karzat.wikidata import (  # noqa: E402
    COMMONS_API,
    SPARQL_ENDPOINT,
    USER_AGENT,
    apply_licences,
    commons_licence_query,
    members_query,
    parse_rows,
    summarise,
)


def _get_json(url: str, params: dict[str, str], accept: str = "application/json") -> dict:
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2026-04-01", help="P39 start date lower bound (default 2026-04-01 = ciklus 43)")
    ap.add_argument("--out", type=Path, default=ROOT / "reference" / "wikidata" / "members_ckl43.json")
    ap.add_argument("--no-licences", action="store_true", help="skip the Commons licence lookups")
    args = ap.parse_args(argv)

    query = members_query(args.since)
    t0 = time.monotonic()
    data = _get_json(SPARQL_ENDPOINT, {"query": query, "format": "json"}, accept="application/sparql-results+json")
    bindings = data["results"]["bindings"]
    members = parse_rows(bindings)
    print(f"sparql: {len(bindings)} bindings -> {len(members)} statements in {time.monotonic() - t0:.1f}s")

    if not args.no_licences:
        names = sorted({m.images[0] for m in members if m.images})
        for i in range(0, len(names), 50):
            batch = names[i:i + 50]
            try:
                cj = _get_json(COMMONS_API, commons_licence_query(batch))
                apply_licences(members, cj)
            except Exception as e:  # noqa: BLE001 — a licence lookup failing must not lose the pull
                print(f"commons batch {i // 50}: {e}", file=sys.stderr)
            time.sleep(0.5)
        print(f"commons: licences looked up for {len(names)} images")

    summary = summarise(members)
    out = {
        "source": "Wikidata (CC0) + Wikimedia Commons extmetadata for image licences",
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "since": args.since,
        "sparql": query,
        "summary": summary,
        "members": [m.to_dict() for m in members],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"written {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
