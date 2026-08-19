"""Postcode → settlement, for the "Ki a képviselőm?" box → reference/valasztas/iranyitoszam.json.

    python3 -m scripts.pull_iranyitoszam [--dry-run]

A postcode is the sharpest thing most people know about where they live, but it is not an electoral district: the
electoral-district annex lists settlements (and Budapest districts), and splits two dozen cities street by street.
So this file only ever maps a postcode to a name the annex already knows — the join is by exact name, nothing is
invented — and the page keeps saying that in a split city the address decides.

Two sources, both checkable:
  * Wikidata P281 (postal code) on items whose country is Hungary: a code, or a range like "9000-9030" written on
    the town itself, which is expanded to its codes. Community data — the site names it and the reader can correct it.
  * Budapest by rule, not by data: a code 1XYZ belongs to district XY (01–23), which is how Hungarian postcodes are
    built. That covers all 23 districts exactly, where Wikidata's items would not have joined by name.

Deterministic apart from the network pull; the file records the source and the date.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karzat.wikidata import SPARQL_ENDPOINT, USER_AGENT  # noqa: E402

ANNEX = ROOT / "reference" / "valasztas" / "oevk_telepules.json"
OUT = ROOT / "reference" / "valasztas" / "iranyitoszam.json"
ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV", "XVI",
         "XVII", "XVIII", "XIX", "XX", "XXI", "XXII", "XXIII"]
PAGE = 1500


def sparql(offset: int) -> list[dict]:
    q = ("SELECT ?huLabel ?code WHERE { ?item wdt:P17 wd:Q28 ; wdt:P281 ?code ; rdfs:label ?huLabel . "
         f'FILTER(lang(?huLabel)="hu") }} ORDER BY ?huLabel LIMIT {PAGE} OFFSET {offset}')
    url = SPARQL_ENDPOINT + "?" + urllib.parse.urlencode({"query": q, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as fh:
                return json.loads(fh.read().decode("utf-8"))["results"]["bindings"]
        except Exception as e:                                   # the endpoint drops long responses now and then
            print(f"  retry {offset} ({type(e).__name__})", file=sys.stderr)
            time.sleep(3 * (attempt + 1))
    return []


def codes_of(value: str) -> list[str]:
    """'1054' → ['1054']; '9000-9030' → every code in the range (a town's block, written on the town itself)."""
    v = re.sub(r"\s+", "", value or "")
    m = re.fullmatch(r"(\d{4})", v)
    if m:
        return [m.group(1)]
    m = re.fullmatch(r"(\d{4})[-–—](\d{4})", v)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return [f"{c:04d}" for c in range(a, b + 1)] if 0 < b - a <= 200 else []
    return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    annex = json.loads(ANNEX.read_text(encoding="utf-8"))["settlements"]
    codes: dict[str, set[str]] = {}
    from_range = 0
    seen_rows = 0
    for offset in range(0, 12000, PAGE):
        rows = sparql(offset)
        seen_rows += len(rows)
        if not rows:
            break
        for r in rows:
            name, raw = r["huLabel"]["value"], r["code"]["value"]
            if name not in annex:
                continue                                        # never a name the annex does not list
            cs = codes_of(raw)
            if len(cs) > 1:
                from_range += 1
            for c in cs:
                codes.setdefault(c, set()).add(name)
        print(f"  offset {offset}: {len(rows)} rows, {len(codes)} codes so far")
    # Budapest by the rule the codes are built on: 1XYZ → district XY
    bp = 0
    for d in range(1, 24):
        name = f"Budapest {ROMAN[d - 1]}. kerület"
        if name not in annex:
            continue
        for z in range(0, 10):
            codes.setdefault(f"1{d:02d}{z}", set()).add(name)
            bp += 1
    out = {
        "source": "Wikidata P281 (ország: Magyarország), névre illesztve a választókerületi melléklet településeihez; "
                  "Budapest a kódképzés szabálya szerint (1XYZ → XY. kerület)",
        "fetched": time.strftime("%Y-%m-%d"),
        "note": "Az irányítószám települést (Budapesten kerületet) azonosít, nem választókerületet: ahol a melléklet "
                "utcánként osztja a várost, ott továbbra is a cím dönt. Egy irányítószám több települést is takarhat "
                "(közös posta), ilyenkor mind látszik.",
        "codes": len(codes), "settlements": len({n for v in codes.values() for n in v}),
        "annex_settlements": len(annex),
        "budapest_codes_by_rule": bp, "wikidata_ranges_expanded": from_range,
        "map": {c: sorted(v) for c, v in sorted(codes.items())},
    }
    text = json.dumps(out, ensure_ascii=False, indent=1) + "\n"
    if args.dry_run:
        print(text[:2000])
        return 0
    OUT.write_text(text, encoding="utf-8")
    missing = sorted(set(annex) - {n for v in codes.values() for n in v})
    print(f"written {OUT}: {out['codes']} postcodes → {out['settlements']} of the annex's {len(annex)} settlements "
          f"({bp} Budapest codes by rule, {from_range} ranges expanded); {len(missing)} settlements without a code"
          + (f": {', '.join(missing[:8])}{'…' if len(missing) > 8 else ''}" if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
