"""Wikidata as the identity spine: stable QIDs for MPs, and the crosswalk to parlament.hu.

Why Wikidata: two unknowns of the W-API are whether <nev_szerint> carries p_azon per MP and
whether parlament.hu photos are reusable. Wikidata hedges both. Every MP has a QID; many
carry P4966 "Hungarian National Assembly ID", whose formatter URL is
  https://www.parlament.hu/…ogy_kpv.kepv_adat?p_azon=$1
— i.e. P4966 *is* the W-API's p_azon. Photos (P18) are Commons files with per-file licences
that this module fetches alongside, so a portrait is never used without knowing its terms.

Scope: members of the National Assembly of Hungary (Q17590876) whose position statement
started on/after a cycle start date. Ended statements are kept (with end date), so
resignations and replacements are visible rather than silently dropped.

Data licence: Wikidata is CC0. Commons images carry their own licences (see `licence` per row).
This module never asserts that a QID is right — it records what Wikidata said on which day.

Pure functions here (query text, row parsing, summary); the network lives in
scripts/pull_wikidata.py so tests stay offline.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import quote

MEMBER_POSITION = "Q17590876"          # member of the National Assembly of Hungary
SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "karzat/0.0.1 (https://github.com/abognar-git; civic data project; contact via GitHub)"


def members_query(since: str = "2026-04-01") -> str:
    """SPARQL for one row per (person, position statement) started on/after `since`.

    Multi-valued optionals (several P4966 ids, several images) can multiply rows; parse_rows()
    folds them back into one record per statement.
    """
    return f"""
SELECT ?p ?nameHu ?nameEn ?ogyId ?group ?groupLabel ?start ?end ?district ?districtLabel
       ?party ?partyLabel ?dob ?sex ?sexLabel ?image
WHERE {{
  ?p p:P39 ?st .
  ?st ps:P39 wd:{MEMBER_POSITION} ;
      pq:P580 ?start .
  FILTER(?start >= "{since}T00:00:00Z"^^xsd:dateTime)
  OPTIONAL {{ ?st pq:P582 ?end }}
  OPTIONAL {{ ?st pq:P4100 ?group }}
  OPTIONAL {{ ?st pq:P768 ?district }}
  OPTIONAL {{ ?p wdt:P4966 ?ogyId }}
  OPTIONAL {{ ?p wdt:P102 ?party }}
  OPTIONAL {{ ?p wdt:P569 ?dob }}
  OPTIONAL {{ ?p wdt:P21 ?sex }}
  OPTIONAL {{ ?p wdt:P18 ?image }}
  OPTIONAL {{ ?p rdfs:label ?nameHu FILTER(LANG(?nameHu) = "hu") }}
  OPTIONAL {{ ?p rdfs:label ?nameEn FILTER(LANG(?nameEn) = "en") }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "hu,en" . }}
}}
ORDER BY ?nameHu ?start
""".strip()


@dataclass
class Member:
    qid: str
    name_hu: str | None
    name_en: str | None
    ogy_ids: list[str] = field(default_factory=list)      # P4966 = parlament.hu p_azon (VERIFY per id)
    group_qid: str | None = None
    group: str | None = None
    start: str | None = None                                # ISO date of the P39 statement
    end: str | None = None
    district_qid: str | None = None
    district: str | None = None
    party_qid: str | None = None
    party: str | None = None
    dob: str | None = None
    sex: str | None = None
    images: list[str] = field(default_factory=list)       # Commons file names (no "File:")
    image_url: str | None = None                            # first image, Special:FilePath at width 400
    licence: str | None = None                              # filled from Commons extmetadata
    image_author: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _qid(uri: str | None) -> str | None:
    if not uri:
        return None
    return uri.rsplit("/", 1)[-1]


def _date(v: str | None) -> str | None:
    """'2026-05-09T00:00:00Z' -> '2026-05-09'; leaves odd precisions alone."""
    if not v:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", v)
    return m.group(1) if m else v


def _image_name(uri: str | None) -> str | None:
    """'http://commons.wikimedia.org/wiki/Special:FilePath/Foo%20Bar.jpg' -> 'Foo Bar.jpg'."""
    if not uri:
        return None
    from urllib.parse import unquote
    return unquote(uri.rsplit("/", 1)[-1])


def image_thumb_url(name: str, width: int = 400) -> str:
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(name)}?width={width}"


def parse_rows(bindings: list[dict[str, dict[str, str]]]) -> list[Member]:
    """Fold SPARQL bindings into one Member per (person, statement start)."""
    out: dict[tuple[str, str | None], Member] = {}
    v = lambda row, k: (row.get(k) or {}).get("value")  # noqa: E731
    for row in bindings:
        qid = _qid(v(row, "p"))
        start = _date(v(row, "start"))
        key = (qid, start)
        m = out.get(key)
        if m is None:
            m = Member(qid=qid, name_hu=v(row, "nameHu"), name_en=v(row, "nameEn"),
                       group_qid=_qid(v(row, "group")), group=v(row, "groupLabel"),
                       start=start, end=_date(v(row, "end")),
                       district_qid=_qid(v(row, "district")), district=v(row, "districtLabel"),
                       party_qid=_qid(v(row, "party")), party=v(row, "partyLabel"),
                       dob=_date(v(row, "dob")), sex=v(row, "sexLabel"))
            out[key] = m
        oid = v(row, "ogyId")
        if oid and oid not in m.ogy_ids:
            m.ogy_ids.append(oid)
        img = _image_name(v(row, "image"))
        if img and img not in m.images:
            m.images.append(img)
        # fill blanks from later rows (labels can be missing on the first binding seen)
        for attr, key_ in (("name_hu", "nameHu"), ("name_en", "nameEn"), ("group", "groupLabel"),
                           ("district", "districtLabel"), ("party", "partyLabel"), ("sex", "sexLabel")):
            if getattr(m, attr) is None and v(row, key_):
                setattr(m, attr, v(row, key_))
        if m.group_qid is None and v(row, "group"):
            m.group_qid = _qid(v(row, "group"))
    members = list(out.values())
    for m in members:
        if m.images:
            m.image_url = image_thumb_url(m.images[0])
    members.sort(key=lambda m: ((m.name_hu or m.name_en or ""), m.start or ""))
    return members


def summarise(members: list[Member]) -> dict[str, Any]:
    current = [m for m in members if m.end is None]
    by_group = Counter(m.group or "(no group)" for m in current)
    return {
        "statements": len(members),
        "current_members": len(current),
        "ended_statements": len(members) - len(current),
        "by_group_current": dict(sorted(by_group.items(), key=lambda kv: -kv[1])),
        "with_ogy_id": sum(1 for m in current if m.ogy_ids),
        "with_image": sum(1 for m in current if m.images),
        "with_district": sum(1 for m in current if m.district),
        "duplicate_ogy_ids": sorted(oid for oid, n in Counter(o for m in current for o in m.ogy_ids).items() if n > 1),
    }


def commons_licence_query(names: list[str]) -> dict[str, str]:
    """Parameters for one Commons API call covering up to 50 files."""
    return {
        "action": "query", "format": "json", "prop": "imageinfo",
        "iiprop": "extmetadata", "iiextmetadatafilter": "LicenseShortName|Artist|Credit|LicenseUrl",
        "titles": "|".join("File:" + n for n in names),
    }


def apply_licences(members: list[Member], commons_json: dict[str, Any]) -> None:
    """Fill licence/author from a Commons imageinfo response (normalised titles handled)."""
    pages = (commons_json.get("query") or {}).get("pages") or {}
    norm = {}
    for n in (commons_json.get("query") or {}).get("normalized", []) or []:
        norm[n["to"]] = n["from"]
    by_title: dict[str, dict[str, Any]] = {}
    for page in pages.values():
        title = page.get("title", "")
        info = (page.get("imageinfo") or [{}])[0].get("extmetadata") or {}
        rec = {"licence": (info.get("LicenseShortName") or {}).get("value"),
               "author": re.sub(r"<[^>]+>", "", (info.get("Artist") or {}).get("value") or "").strip() or None}
        by_title[title] = rec
        if title in norm:
            by_title[norm[title]] = rec
    for m in members:
        if not m.images:
            continue
        rec = by_title.get("File:" + m.images[0]) or by_title.get("File:" + m.images[0].replace("_", " "))
        if rec:
            m.licence = rec["licence"]
            m.image_author = rec["author"]
