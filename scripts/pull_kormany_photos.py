"""Find a freely licensed photograph for the House's non-MP members → reference/wikidata/kormany_photos.json.

    python3 -m scripts.pull_kormany_photos [--dry-run]

parlament.hu photographs only the members it lists: a minister who is not an MP gets a 404 from the portrait
endpoint (the record still prints the URL). Wikidata carries a picture for some of them, and a Commons picture comes
with a licence and an author — a better footing than the parlament.hu photos, whose terms are not stated anywhere.

The match must be defensible, so each person is accepted only when the item is a human whose Hungarian label (or an
alias) is the name the record prints, and either the item carries our own p_azon (P4966) or one of its offices (P39)
is the office the record of their speeches names. The file records which of the two decided it, so the next reader
can check. Whoever has no item, no picture, or no such evidence stays without a photograph, and the site says so.

Network: the Wikidata SPARQL endpoint, wbgetentities, and one Commons imageinfo call — nothing from parlament.hu.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karzat.normalise import strip_honorific  # noqa: E402
from karzat.wikidata import USER_AGENT, commons_licence_query, image_thumb_url  # noqa: E402

DERIVED = ROOT / "data" / "derived"
OUT = ROOT / "reference" / "wikidata" / "kormany_photos.json"
WD_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"


def get_json(base: str, params: dict) -> dict:
    req = urllib.request.Request(base + "?" + urllib.parse.urlencode(params), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=90) as fh:
        return json.loads(fh.read().decode("utf-8"))


def search(name: str) -> list[str]:
    d = get_json(WD_API, {"action": "wbsearchentities", "search": name, "language": "hu", "uselang": "hu",
                          "format": "json", "limit": 5, "type": "item"})
    return [r["id"] for r in d.get("search", [])]


def entities(qids: list[str]) -> dict:
    if not qids:
        return {}
    d = get_json(WD_API, {"action": "wbgetentities", "ids": "|".join(sorted(set(qids))[:50]),
                          "props": "claims|labels|descriptions", "languages": "hu|en", "format": "json"})
    return d.get("entities") or {}


def claim_ids(ent: dict, prop: str) -> list[str]:
    out = []
    for c in (ent.get("claims") or {}).get(prop, []):
        v = (c.get("mainsnak") or {}).get("datavalue", {}).get("value")
        if isinstance(v, dict) and v.get("id"):
            out.append(v["id"])
        elif isinstance(v, str):
            out.append(v)
    return out


_TITLES = re.compile(r"\b(?:dr|prof|ifj|id|özv|dr\.)\.?\s*", re.I)


def fold(s: str) -> str:
    """'Dr. Törőcsikné Dr. Görög Márta' → 'törőcsikné görög márta' — every honorific, not only the leading one
    (strip_honorific removes the first; a married name can carry a second)."""
    return re.sub(r"\s+", " ", _TITLES.sub("", strip_honorific(s or ""))).casefold().strip()


def same_person_name(record_name: str, label: str) -> bool:
    """The record prints every given name ('Vitézy Dávid László'), Wikidata often one ('Vitézy Dávid'), and the
    English label flips the order ('Dávid Vitézy'). Accept when one name's words contain the other's and at least
    two words match — a surname alone is never enough."""
    a, b = set(fold(record_name).split()), set(fold(label).split())
    return bool(a) and bool(b) and (a <= b or b <= a) and len(a & b) >= 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print what would be written, write nothing")
    args = ap.parse_args(argv)
    people = json.loads((DERIVED / "kormany.json").read_text(encoding="utf-8"))["people"]
    found, missing = {}, []
    for azon, p in people.items():
        name = p["name"]
        cands = search(name) + search(fold(name)) + search(strip_honorific(name))
        tokens = fold(name).split()
        if len(tokens) > 2:
            cands += search(" ".join(tokens[-2:])) + search(" ".join([tokens[0]] + tokens[-1:]))   # 'Görög Márta', 'Törőcsikné Márta'
        ents = entities(cands)
        best = None
        for qid, ent in ents.items():
            if "Q5" not in claim_ids(ent, "P31"):
                continue                                   # only a human, never an office or an organisation
            labels = [(ent.get("labels") or {}).get(lang, {}).get("value", "") for lang in ("hu", "en")]
            if not any(same_person_name(name, x) for x in labels if x):
                continue
            evidence = None
            if azon in claim_ids(ent, "P4966"):
                evidence = "P4966 = a saját p_azon"
            else:
                office_labels = [(entities(claim_ids(ent, "P39")) or {}).get(q, {}) for q in claim_ids(ent, "P39")]
                names_hu = [fold((o.get("labels") or {}).get("hu", {}).get("value", "")) for o in office_labels]
                want = fold(p.get("office") or "")
                if want and any(want in n or n in want for n in names_hu if n):
                    evidence = f'P39 = {p["office"]}'
            if not evidence:
                continue
            img = claim_ids(ent, "P18")
            if not img:
                missing.append((azon, name, qid, "nincs kép a Wikidatán"))
                continue
            best = {"p_azon": azon, "name": name, "qid": qid, "image": img[0], "evidence": evidence,
                    "url": image_thumb_url(img[0], 400)}
            break
        if best:
            found[azon] = best
        elif not any(m[0] == azon for m in missing):
            missing.append((azon, name, None, "nincs egyértelmű Wikidata-tétel"))

    if found:
        d = get_json(COMMONS_API, commons_licence_query([f["image"] for f in found.values()]))
        by_title = {}
        for page in ((d.get("query") or {}).get("pages") or {}).values():
            md = (page.get("imageinfo") or [{}])[0].get("extmetadata") or {}
            by_title[page.get("title", "")] = {
                "licence": (md.get("LicenseShortName") or {}).get("value"),
                "licence_url": (md.get("LicenseUrl") or {}).get("value"),
                "author": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", (md.get("Artist") or {}).get("value") or "")).strip() or None,
            }
        for f in found.values():
            rec = by_title.get("File:" + f["image"]) or by_title.get("File:" + f["image"].replace("_", " ")) or {}
            f.update({k: rec.get(k) for k in ("licence", "licence_url", "author")})
            f["credit"] = " · ".join(x for x in [f"fénykép: {f.get('author')}" if f.get("author") else "fénykép",
                                                 "Wikimedia Commons", f.get("licence")] if x)

    out = {"source": "Wikidata (P18) + Wikimedia Commons imageinfo; matched by name and either P4966 or the office the record prints",
           "note": "parlament.hu has no photograph of a minister who is not an MP (its portrait endpoint answers 404); "
                   "these are Commons files, each with its licence and author, shown with attribution.",
           "count": len(found), "without_photo": [{"p_azon": a, "name": n, "qid": q, "why": w} for a, n, q, w in missing],
           "people": dict(sorted(found.items()))}
    text = json.dumps(out, ensure_ascii=False, indent=1) + "\n"
    if args.dry_run:
        print(text)
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"written {OUT}: {len(found)} photographs found, {len(missing)} without one "
          f"({', '.join(n for _, n, _, _ in missing)})")
    for f in found.values():
        print(f"  {f['p_azon']} {f['name']} ← {f['qid']} ({f['evidence']}) · {f.get('licence')} · {f.get('author')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
