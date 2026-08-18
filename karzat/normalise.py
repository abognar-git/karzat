"""Payload → record adapters, written against real W-API responses (2026-08-18).

Every function takes the plain dict produced by xmlutil.to_dict() over a *root* element and
returns JSON-able records shaped for schema.sql. Nothing here fetches. Field names below are
the API's own; the comments say what was observed, and where an interpretation is mine.

Observed shapes (see tests/fixtures/real_*.xml):
  kepviselok.cgi   <kepviselok><kepviselo sorszam nev kepv_csop p_azon kep vmegye vkorzet/>…
  kepviselo.cgi    <kepviselo><nev/><fenykep/><ulohely szektor sor szek/><kepvcsop-tagsagok><tagsag ciklus kepvcsop tol_datum ig_datum/>…
                   <valasztasok><valasztas ciklus megvalasztas_napja valasztokerulet mandatum_kezdete mandatum_vege/>… (+ more sections)
  szavazasok.cgi   <szavazasok><szavazas idopont><tulajdonsagok><tulajdonsag nev ertek/>…</tulajdonsagok><inditvanyok><inditvany>…
  szavazas.cgi     <szavazas><szavazas idopont> + <kepvcsop_szerint><szavazat frakcio><igen_db/>…</szavazat>
                   + <nev_szerint><szavazat kepviselo="Név (Frakció)" szavazat="Igen"/>…   (secret ballots: empty)
  ulesnap.cgi      <ulesnapok><ulesnap><datum/><ulnap/><nap/><ulszak/><ulesjelleg/><megjegyzes/>
  iromanyok.cgi    <iromanyok><iromany szam izon href><cim/><allapottipus/><benyujto nev/>
  iromany.cgi      <iromany szam izon href><cim/><tulajdonsagok>…<esemenyek><esemeny><datum/><leiras/>…<szavazasok><szavazas><datum/><ok/>…
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .majority import Rule, Tally, classify, evaluate
from .xmlutil import as_list, parse_date, ts_to_slug

# -- vocabularies (observed) --------------------------------------------------------------

# nev_szerint position labels -> schema position values: SEVEN recorded states.
# "Jelen, nem szav." (present, not voting) and "Nem szav." (not voting, presence not registered)
# are distinct in the source; "Előre bejelentett hiányzó" is absence announced in advance and
# "Ig.távol" (igazoltan távol) is excused absence — the seventh state, absent from cycle 43's
# first 259 roll calls and present in 288 of cycle 42's (first seen 2022-10-03). The faction
# rows aggregate them: igtav_db = Ig.távol + Előre bejelentett hiányzó (287 of 288 votes; one
# off-by-one in the source on 2025-05-19), nemszav_db = Nem szav. + Jelen, nem szav.
POSITIONS: dict[str, str] = {
    "Igen": "igen",
    "Nem": "nem",
    "Tart.": "tartozkodott",
    "Jelen, nem szav.": "jelen_nem_szavazott",
    "Nem szav.": "nem_szavazott",
    "Előre bejelentett hiányzó": "bejelentett_hianyzo",
    "Ig.távol": "igazoltan_tavol",
}
ABSENT_POSITIONS = ("nem_szavazott", "bejelentett_hianyzo", "igazoltan_tavol")
PRESENT_POSITIONS = ("igen", "nem", "tartozkodott", "jelen_nem_szavazott")   # the "jelen lévő" base — interpretation, VERIFY legally

RESULTS: dict[str, bool | None] = {"Elfogadva": True, "Elutasítva": False, "Határozatképes": None,
                                   "Határozatképtelen": None,   # quorum check failed (10 times in cycle 42)
                                   "Érvénytelen": None}         # invalid vote (seen 1990, 1998)

SEATS_199_FROM = "2014-05-06"   # the 2014–2018 term is the first with 199 seats; 386 before that


def seats_for(on_date: str | None) -> int:
    """Number of seats of the Országgyűlés on a given ISO date: 386 (1990–2014), 199 since the 2014 term."""
    if on_date and on_date < SEATS_199_FROM:
        return 386
    return 199

HONORIFICS = re.compile(r"^(?:dr\.|prof\.|ifj\.|id\.|özv\.|dr\s)\s*", re.IGNORECASE)


def prop_map(tulajdonsagok: Any) -> dict[str, str]:
    """<tulajdonsagok><tulajdonsag nev ertek/>… -> {nev: ertek}."""
    out: dict[str, str] = {}
    for t in as_list((tulajdonsagok or {}).get("tulajdonsag") if isinstance(tulajdonsagok, dict) else None):
        if isinstance(t, dict) and "@nev" in t:
            out[t["@nev"]] = t.get("@ertek", "") or ""
    return out


def _int(s: Any) -> int | None:
    try:
        return int(str(s).replace(" ", "").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def _hu_date(s: Any) -> str | None:
    """'2026.05.28.' or '2026.05.28' -> '2026-05-28'; '' -> None."""
    if not s:
        return None
    m = re.match(r"^\s*(\d{4})\.(\d{2})\.(\d{2})\.?\s*$", str(s))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def strip_honorific(name: str) -> str:
    return HONORIFICS.sub("", name.strip()).strip()


# -- MPs ------------------------------------------------------------------------------------

def parse_kepviselok(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """kepviselok.cgi -> [{p_azon, name, faction, photo_url, county, constituency_no, mandate_kind, sorszam}]."""
    root = payload.get("kepviselok", payload)
    out = []
    for m in as_list(root.get("kepviselo")):
        county = m.get("@vmegye") or None
        vk = m.get("@vkorzet") or None
        out.append({
            "p_azon": m.get("@p_azon"),
            "name": m.get("@nev"),
            "faction": m.get("@kepv_csop"),
            "photo_url": m.get("@kep"),
            "county": county,
            "constituency_no": _int(vk),
            "mandate_kind": "lista" if county == "Országos lista" else ("egyeni" if vk else "other"),
            "sorszam": _int(m.get("@sorszam")),
        })
    return out


def parse_kepviselo(payload: dict[str, Any]) -> dict[str, Any]:
    """kepviselo.cgi -> the sections the model uses (others kept under 'extra' keys by the caller if wanted)."""
    r = payload.get("kepviselo", payload)
    seat = r.get("ulohely") if isinstance(r.get("ulohely"), dict) else {}
    factions = [{
        "ciklus": t.get("@ciklus"), "faction": t.get("@kepvcsop"),
        "from": _hu_date(t.get("@tol_datum")), "to": _hu_date(t.get("@ig_datum")),
    } for t in as_list((r.get("kepvcsop-tagsagok") or {}).get("tagsag")) if isinstance(t, dict)]
    elections = [{
        "ciklus": v.get("@ciklus"), "elected_on": _hu_date(v.get("@megvalasztas_napja")),
        "constituency": v.get("@valasztokerulet") or None,
        "mandate_from": _hu_date(v.get("@mandatum_kezdete")), "mandate_to": _hu_date(v.get("@mandatum_vege")),
    } for v in as_list((r.get("valasztasok") or {}).get("valasztas")) if isinstance(v, dict)]
    motions = [{"ciklus": s.get("@ciklus"), "onallo": _int(s.get("@onallo_db")), "nem_onallo": _int(s.get("@nem_onallo_db"))}
               for s in as_list((r.get("inditvanyok") or {}).get("stat")) if isinstance(s, dict)]
    return {
        "name": r.get("nev"),
        "photo_url": r.get("fenykep") or None,
        "website": r.get("honlap") or None,
        "seat": {"sector": _int(seat.get("@szektor")), "row": _int(seat.get("@sor")), "seat": _int(seat.get("@szek"))} if seat else None,
        "factions": factions,
        "elections": elections,
        "motion_stats": motions,
    }


# Cycle starts (constitutive sittings; the month is a good-enough boundary) — shared with load.py.
CYCLE_STARTS = {34: "1990-05-02", 35: "1994-06-28", 36: "1998-06-18", 37: "2002-05-15", 38: "2006-05-16",
                39: "2010-05-14", 40: "2014-05-06", 41: "2018-05-08", 42: "2022-05-02", 43: "2026-05-09"}
CYCLE_LABELS = {34: "1990-1994", 35: "1994-1998", 36: "1998-2002", 37: "2002-2006", 38: "2006-2010",
                39: "2010-2014", 40: "2014-2018", 41: "2018-2022", 42: "2022-2026", 43: "2026-"}   # as kepviselo.cgi prints ciklus


def cycle_of_date(on_date: str | None) -> int | None:
    """Cycle by date (34 = 1990–94 … 43 = 2026–)."""
    if not on_date:
        return None
    ckl = None
    for k, d in CYCLE_STARTS.items():
        if on_date >= d:
            ckl = k
    return ckl


class NameResolver:
    """'Név (Frakció)' as printed in nev_szerint -> p_azon.

    Order: (1) the exact 'Név (Frakció)' label against the current MP list (kepviselok.cgi prints
    the same spelling as nev_szerint, honorifics included); (2) the exact printed name against the
    names of cached kepviselo.cgi records — the API's own spelling of every person we have fetched,
    current or former; (3) the honorific-stripped name against every source (current list, records,
    Wikidata name_hu → P4966), and if that yields more than one person, the printed faction against
    each candidate's faction history for the cycle of the vote (records) — a "Dr. Kiss János (Fidesz)"
    in 2025 is not the "Kiss János (TISZA)" of 2026. Still ambiguous → unresolved: the schema keeps
    mp_name for auditing rather than guessing.
    """

    def __init__(self, current: list[dict[str, Any]], aliases: dict[str, str] | None = None,
                 histories: dict[str, dict[str, Any]] | None = None):
        self.exact = {f'{m["name"]} ({m["faction"]})': m["p_azon"] for m in current}
        self.by_bare: dict[str, set[str]] = {}
        for m in current:
            self.by_bare.setdefault(strip_honorific(m["name"]), set()).add(m["p_azon"])
        self.histories = histories or {}                        # azon -> {"name": ..., "factions": [(ciklus_label, faction), ...]}
        self.record_names: dict[str, set[str]] = {}
        for azon, h in self.histories.items():
            if h.get("name"):
                self.record_names.setdefault(h["name"], set()).add(azon)
                self.by_bare.setdefault(strip_honorific(h["name"]), set()).add(azon)
        for k, v in (aliases or {}).items():                    # Wikidata labels: bare, may collide across cycles
            self.by_bare.setdefault(strip_honorific(k), set()).add(v)

    @staticmethod
    def split(label: str) -> tuple[str, str | None]:
        m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", label.strip())
        return (m.group(1).strip(), m.group(2).strip()) if m else (label.strip(), None)

    def _by_faction(self, cands: set[str], faction: str | None, on_date: str | None) -> set[str]:
        """Keep the candidates whose record puts them in `faction` — in the vote's cycle if we know it."""
        if not faction:
            return cands
        label = CYCLE_LABELS.get(cycle_of_date(on_date) or -1)
        keep = set()
        for a in cands:
            rows = self.histories.get(a, {}).get("factions") or []
            if any(f == faction and (label is None or c == label) for c, f in rows):
                keep.add(a)
        return keep

    def _consistent(self, azon: str, faction: str | None, on_date: str | None) -> bool:
        """A candidate with a known history must not contradict the printed faction for the vote's cycle."""
        rows = self.histories.get(azon, {}).get("factions") or []
        if not faction or not rows:
            return True
        label = CYCLE_LABELS.get(cycle_of_date(on_date) or -1)
        in_cycle = [f for c, f in rows if label is None or c == label]
        return faction in in_cycle                           # no row for that cycle → not an MP then → not this person

    def _pick(self, cands: set[str], faction: str | None, on_date: str | None) -> str | None:
        ok = {a for a in cands if self._consistent(a, faction, on_date)}
        if len(ok) == 1:
            return next(iter(ok))
        if len(ok) > 1:
            narrowed = self._by_faction(ok, faction, on_date)
            if len(narrowed) == 1:
                return next(iter(narrowed))
        return None

    def resolve(self, label: str, on_date: str | None = None) -> str | None:
        if label in self.exact:
            return self.exact[label]
        name, faction = self.split(label)
        hit = self._pick(self.record_names.get(name) or set(), faction, on_date)     # the API's own spelling
        if hit:
            return hit
        return self._pick(self.by_bare.get(strip_honorific(name)) or set(), faction, on_date)


def record_histories(cache: "Path") -> dict[str, dict[str, Any]]:
    """p_azon -> {name, factions: [(ciklus_label, faction), ...]} from every cached kepviselo.cgi record.

    The API's own record prints the same spelling as the roll call ("Z. Kárpát Dániel",
    "Czunyiné Dr. Bertalan Judit", "Szabó Timea"), where Wikidata's label differs — so a
    former MP's record, once fetched, resolves every roll call they ever appear in; and the
    faction history per cycle is what tells two people of one name apart.
    """
    from pathlib import Path  # noqa: F811 — local import keeps this module free of a Path dependency at import
    from .xmlutil import parse_xml, to_dict
    out: dict[str, dict[str, Any]] = {}
    folder = Path(cache) / "kepviselo"
    if not folder.exists():
        return out
    for p in sorted(folder.glob("mp_*.xml")):
        try:
            root = parse_xml(p.read_bytes())
            rec = parse_kepviselo({root.tag: to_dict(root)})
        except Exception:      # a truncated download must not take the whole build down
            continue
        if rec.get("name"):
            out[p.stem[3:]] = {"name": rec["name"], "factions": [(f.get("ciklus"), f.get("faction")) for f in rec.get("factions") or []]}
    return out


def record_name_aliases(cache: "Path") -> dict[str, str]:
    """name -> p_azon from the cached records (first wins on a duplicate name; prefer record_histories + NameResolver)."""
    out: dict[str, str] = {}
    for azon, h in record_histories(cache).items():
        out.setdefault(h["name"], azon)
    return out


# -- sitting days ---------------------------------------------------------------------------

def parse_ulesnap(payload: dict[str, Any]) -> list[dict[str, Any]]:
    root = payload.get("ulesnapok", payload)
    out = []
    for u in as_list(root.get("ulesnap")):
        if not isinstance(u, dict):
            continue
        out.append({"date": _hu_date(u.get("datum")), "ulnap": _int(u.get("ulnap")), "weekday": u.get("nap") or None,
                    "session": u.get("ulszak") or None, "kind": u.get("ulesjelleg") or None,
                    "remark": u.get("megjegyzes") or None})
    return sorted(out, key=lambda x: (x["date"] or "", x["ulnap"] or 0))


# -- votes ----------------------------------------------------------------------------------

def parse_iromany_szam(szam: str | None) -> dict[str, Any]:
    """'T/71' -> {kind:'T', number:71}; '56/4' -> {kind:'amendment', parent:56, number:4}."""
    if not szam:
        return {"kind": None}
    m = re.match(r"^([A-Za-z]+)/(\d+)$", szam.strip())
    if m:
        return {"kind": m.group(1).upper(), "number": int(m.group(2))}
    m = re.match(r"^(\d+)/(\d+)$", szam.strip())
    if m:
        return {"kind": "amendment", "parent": int(m.group(1)), "number": int(m.group(2))}
    return {"kind": "other", "raw": szam}


def _submitters(ind: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Names and (when the href carries it) p_azon ids of an inditvany's submitters."""
    names, ids = [], []
    b = ind.get("benyujtok")
    if isinstance(b, dict):
        for x in as_list(b.get("benyujto")):
            if isinstance(x, dict):
                if x.get("#text"):
                    names.append(x["#text"])
                m = re.search(r"%22id%22%3A%22([^%]+)%22", x.get("@href", ""))
                if m:
                    ids.append(m.group(1))
            elif isinstance(x, str) and x:
                names.append(x)
    single = ind.get("benyujto")
    if isinstance(single, dict) and single.get("@nev"):
        names.append(single["@nev"])
    elif isinstance(single, str) and single:
        names.append(single)
    return names, ids


def submitter_kind(names: list[str]) -> str | None:
    if not names:
        return None
    joined = " ".join(names).casefold()
    if joined.startswith("kormány") or "miniszter" in joined:
        return "kormany"
    if "bizottság" in joined:
        return "bizottsag"
    if "korelnök" in joined or "elnök" in joined and "(" not in joined:
        return "other"
    return "kepviselo"


def _motions(inditvanyok: Any) -> list[dict[str, Any]]:
    out = []
    if not isinstance(inditvanyok, dict):
        return out
    for ind in as_list(inditvanyok.get("inditvany")):
        if not isinstance(ind, dict):
            continue
        names, ids = _submitters(ind)
        out.append({"iromany": ind.get("iromany") or None, "point": ind.get("pont") or None,
                    "title": ind.get("cim") or None, "outcome": ind.get("ok") or None,
                    "submitters": names, "submitter_ids": ids, "submitter_kind": submitter_kind(names),
                    **{"iromany_parsed": parse_iromany_szam(ind.get("iromany"))}})
    return out


def _vote_core(el: dict[str, Any]) -> dict[str, Any]:
    ts = el.get("@idopont")
    p = prop_map(el.get("tulajdonsagok"))
    mode = p.get("Szavazási mód", "")
    igen, nem, tart = _int(p.get('"Igen"-ek száma')), _int(p.get('"Nem"-ek száma')), _int(p.get("Tartózkodások"))
    total = _int(p.get("Összes szavazat"))
    result_raw = p.get("Elfogadás", "")
    is_quorum = mode.startswith("Jelenlét")
    return {
        "ts": ts, "slug": ts_to_slug(ts) if ts else None,
        "on_date": ts[:10].replace(".", "-") if ts else None,
        "mode": mode, "secret": mode.startswith("Titkos"),
        "kind": "jelenlet" if is_quorum else "dontes",
        "igen": igen, "nem": nem, "tartozkodott": tart, "osszes_szavazat": total,
        "result_raw": result_raw, "passed": RESULTS.get(result_raw),
        "remark": p.get("Megjegyzés") or None,
        "motions": _motions(el.get("inditvanyok")),
    }


def parse_szavazasok(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """szavazasok.cgi list -> summary records (no per-MP positions)."""
    root = payload.get("szavazasok", payload)
    if not isinstance(root, dict):          # an empty month: <szavazasok/> parses to ""
        return []
    return [_vote_core(el) for el in as_list(root.get("szavazas")) if isinstance(el, dict)]


def _faction_tallies(kcs: Any) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
    rows, totals = [], None
    if not isinstance(kcs, dict):
        return rows, totals
    for s in as_list(kcs.get("szavazat")):
        if not isinstance(s, dict):
            continue
        rec = {"faction": s.get("@frakcio"), "igen": _int(s.get("igen_db")), "nem": _int(s.get("nem_db")),
               "tartozkodott": _int(s.get("tart_db")),
               "igazoltan_tavol": _int(s.get("igtav_db")),        # = Ig.távol + Előre bejelentett hiányzó (see POSITIONS)
               "nem_szavazott": _int(s.get("nemszav_db")),        # = Nem szav. + Jelen, nem szav.
               "osszesen": _int(s.get("osszesen"))}
        if (rec["faction"] or "").startswith("Összesen"):
            totals = {k: v for k, v in rec.items() if k != "faction"}
        else:
            rows.append(rec)
    return rows, totals


def parse_szavazas(payload: dict[str, Any], resolver: NameResolver | None = None,
                   seats: int | None = None) -> dict[str, Any]:
    """szavazas.cgi -> full vote record with faction tallies, per-MP positions and the majority verdict."""
    outer = payload.get("szavazas", payload)
    el = outer.get("szavazas", outer) if isinstance(outer, dict) and "szavazas" in outer else outer
    rec = _vote_core(el)
    if seats is None:
        seats = seats_for(rec["on_date"])
    rec["seats"] = seats
    tallies, totals = _faction_tallies(el.get("kepvcsop_szerint"))
    positions = []
    counts: dict[str, int] = {}
    ns = el.get("nev_szerint")
    if isinstance(ns, dict):
        for s in as_list(ns.get("szavazat")):
            if not isinstance(s, dict):
                continue
            label = s.get("@kepviselo", "")
            name, faction = NameResolver.split(label)
            pos = POSITIONS.get(s.get("@szavazat", ""), "unknown:" + str(s.get("@szavazat")))
            counts[pos] = counts.get(pos, 0) + 1
            positions.append({"name_raw": label, "name": name, "faction": faction, "position": pos,
                              "mp_azon": resolver.resolve(label, rec.get("on_date")) if resolver else None})
    rec["faction_tallies"] = tallies
    rec["totals_row"] = totals
    rec["positions"] = positions
    rec["position_counts"] = counts
    rec["roll_call_available"] = bool(positions)

    # present base: MPs who voted or registered as present-not-voting (interpretation; VERIFY)
    if positions:
        present = sum(counts.get(k, 0) for k in PRESENT_POSITIONS)
        rec["present"] = present
        rec["present_basis"] = "nev_szerint: igen+nem+tart+jelen_nem_szav"
    elif rec["osszes_szavazat"] is not None:
        rec["present"] = rec["osszes_szavazat"]
        rec["present_basis"] = "tulajdonsagok: Összes szavazat (no roll call)"
    else:
        rec["present"] = None
        rec["present_basis"] = None

    # majority verdict
    if rec["kind"] == "jelenlet" or rec["igen"] is None:
        rec["majority"] = None
    else:
        cls = classify(payload_rule=rec["mode"], subject=" ".join(m.get("title") or "" for m in rec["motions"]))
        tally = Tally(yes=rec["igen"], no=rec["nem"] or 0, abstain=rec["tartozkodott"] or 0,
                      not_voting=(counts.get("nem_szavazott", 0) + counts.get("jelen_nem_szavazott", 0)),
                      present=rec["present"], seats=seats)
        v = evaluate(cls.rule, tally)
        rec["majority"] = {"rule": v.rule, "source": cls.source, "evidence": cls.evidence, "base": v.base,
                           "needed": v.needed, "margin": v.margin, "passed_by_rule": v.passed,
                           "agrees_with_source": (v.passed == rec["passed"]) if rec["passed"] is not None and v.passed is not None else None,
                           "present_assumed": v.present_assumed}
    return rec


# -- bills ----------------------------------------------------------------------------------

ROMAN = re.compile(r"^[IVXLCDM]+$")


def parse_iromanyok(payload: dict[str, Any]) -> list[dict[str, Any]]:
    root = payload.get("iromanyok", payload)
    out = []
    for i in as_list(root.get("iromany")):
        if not isinstance(i, dict):
            continue
        names = []
        for b in as_list(i.get("benyujto")):                 # one element or several: every submitter counts
            if isinstance(b, dict) and b.get("@nev"):
                names.append(b["@nev"])
            elif isinstance(b, str) and b:
                names.append(b)
        out.append({"szam": i.get("@szam"), "izon": i.get("@izon"), "href": i.get("@href") or None, "title": i.get("cim") or None,
                    "status_type": i.get("allapottipus") or None, "submitters": names,
                    "submitter_kind": submitter_kind(names), **{"szam_parsed": parse_iromany_szam(i.get("@szam"))}})
    return out


def parse_iromany(payload: dict[str, Any]) -> dict[str, Any]:
    r = payload.get("iromany", payload)
    p = prop_map(r.get("tulajdonsagok"))
    events = [{"date": _hu_date(e.get("datum")), "text": e.get("leiras") or None, "related": e.get("kapcsolodik") or None,
               "remark": e.get("megjegyzes") or None, "speech": e.get("felszolalas") or None}
              for e in as_list((r.get("esemenyek") or {}).get("esemeny")) if isinstance(e, dict)]
    votes = [{"ts": v.get("datum"), "outcome": v.get("ok") or None, "igen": _int(v.get("igen_db")), "nem": _int(v.get("nem_db")),
              "tartozkodott": _int(v.get("tart_db")), "result_raw": v.get("eredmeny") or None}
             for v in as_list((r.get("szavazasok") or {}).get("szavazas")) if isinstance(v, dict)]
    committees = [{"name": c.get("nev"), "role": c.get("jogcim") or None, "rule": c.get("jogszab") or None}
                  for c in as_list((r.get("targyalao_bizottsagok") or r.get("targyalo_bizottsagok") or {}).get("bizottsag"))
                  if isinstance(c, dict)]
    submitted = _hu_date(p.get("Benyújtva"))
    promulgated_on = _hu_date(p.get("Kihirdetés dátuma"))
    number = (p.get("Kihirdetés száma") or "").strip() or None
    main_type = p.get("Főtípus") or None
    law_ref = None
    if number and promulgated_on and main_type == "törvényjavaslat" and ROMAN.match(number):
        law_ref = f"{promulgated_on[:4]}. évi {number}. törvény"
    submitters = [s.strip() for s in (p.get("Benyújtó(k)") or "").split(",") if s.strip()]
    procedure = (p.get("Tárgyalási mód") or "").strip() or None
    rec = {
        "szam": r.get("@szam"), "izon": r.get("@izon"), "title": r.get("cim") or None,
        "type": p.get("Típus") or None, "main_type": main_type, "nature": p.get("Jelleg") or None,
        "submitted_on": submitted, "status": p.get("Állapot") or None,
        "procedure_mode": procedure,
        "procedure_kind": ("kiveteles" if procedure and "kivételes" in procedure else
                           "surgos" if procedure and "sürgős" in procedure else
                           "normal" if procedure else None),
        "promulgation": {"number": number, "mk_issue": (p.get("MK száma") or "").strip() or None,
                         "date": promulgated_on, "law_ref": law_ref} if number or promulgated_on else None,
        "submitters": submitters, "submitter_kind": submitter_kind(submitters),
        "text_url": p.get("Irományszöveg") or None,
        "events": events, "votes": votes, "committees": committees,
        "szam_parsed": parse_iromany_szam(r.get("@szam")),
    }
    rec["days_submission_to_promulgation"] = _days(submitted, promulgated_on)
    final = next((v["ts"] for v in reversed(votes) if (v.get("outcome") or "").startswith("önálló indítvány")), None)
    rec["final_vote_ts"] = final
    rec["days_submission_to_final_vote"] = _days(submitted, final[:10].replace(".", "-") if final else None)
    return rec


def _days(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    return (date.fromisoformat(b) - date.fromisoformat(a)).days
