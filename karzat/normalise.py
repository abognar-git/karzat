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
# The "jelen lévő" base of the present-based majorities is the votes cast (igen + nem + tartózkodott) — the API's own
# "Összes szavazat". Evidence, not interpretation: with the present-not-voting ("Jelen, nem szav.") counted in, the
# computed outcome contradicts the House's own Elfogadva/Elutasítva 272 times in cycle 38 (2006–2010), 15 in 39, 4 in
# 40, 2 in 41; with the votes cast as the base, never (cycles 38–43, 25 000 present-based decisions; one plain
# secret ballot in 2017 aside). So "jelen, nem szavazott" is participation, not part of the base.
PRESENT_POSITIONS = ("igen", "nem", "tartozkodott")

RESULTS: dict[str, bool | None] = {"Elfogadva": True, "Elutasítva": False, "Határozatképes": None,
                                   "Határozatképtelen": None,   # quorum check failed (10 times in cycle 42)
                                   "Érvénytelen": None}         # invalid vote (seen 1990, 1998)

SEATS_199_FROM = "2014-05-06"   # the 2014–2018 term is the first with 199 seats; 386 before that


def seats_for(on_date: str | None) -> int:
    """Number of seats of the Országgyűlés on a given ISO date: 386 (1990–2014), 199 since the 2014 term. This is the
    fallback; the all-MP majorities are computed against the mandates in force on the day when the records are at hand
    (seats_in_force) — the House counts them so: a two-thirds-of-all secret ballot on 2014-12-01 passed with 132 yes,
    two-thirds of the 197 mandates then in force, not of 199."""
    if on_date and on_date < SEATS_199_FROM:
        return 386
    return 199


class SeatsInForce:
    """Mandates in force per day, from the MP records' election rows; falls back to seats_for when it knows nothing about
    a date's era (no record covers it) or the count is implausibly low (a gap in the records must not shrink the base)."""
    def __init__(self, mandates: list[tuple[str, str | None]]):
        self.mandates = [(a, b or "9999-12-31") for a, b in mandates if a]
        self._cache: dict[str, int] = {}

    def __call__(self, on_date: str | None) -> int:
        base = seats_for(on_date)
        if not on_date or not self.mandates:
            return base
        if on_date in self._cache:
            return self._cache[on_date]
        n = sum(1 for a, b in self.mandates if a <= on_date <= b)
        val = n if base * 0.9 <= n <= base else base      # a few vacancies at most; anything else is the records' gap, not the House's
        self._cache[on_date] = val
        return val

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
    speech_stats = [{"ciklus": s.get("@ciklus"), "speeches": _int(s.get("@felszolalas_db")), "technical": _int(s.get("@technikai_db"))}
                    for s in as_list((r.get("felszolalasok") or {}).get("stat")) if isinstance(s, dict)]
    committees = [{"committee": t.get("@bizottsag") or None, "subcommittee": t.get("@albizottsag") or None, "role": t.get("@tisztseg") or None,
                   "from": _hu_date(t.get("@tol")), "to": _hu_date(t.get("@ig"))}
                  for t in as_list((r.get("bizottsagi-tagsagok") or {}).get("tagsag")) if isinstance(t, dict)]
    return {
        "name": r.get("nev"),
        "photo_url": r.get("fenykep") or None,
        "website": r.get("honlap") or None,
        "seat": {"sector": _int(seat.get("@szektor")), "row": _int(seat.get("@sor")), "seat": _int(seat.get("@szek"))} if seat else None,
        "factions": factions,
        "elections": elections,
        "motion_stats": motions,
        "speech_stats": speech_stats,        # per cycle: felszólalás / technikai counts, as parlament.hu counts them
        "committees": committees,            # bizottsági tagságok with dates (subcommittees named)
    }


# Cycle starts (constitutive sittings; the month is a good-enough boundary) — shared with load.py.
CYCLE_STARTS = {34: "1990-05-02", 35: "1994-06-28", 36: "1998-06-18", 37: "2002-05-15", 38: "2006-05-16",
                39: "2010-05-14", 40: "2014-05-06", 41: "2018-05-08", 42: "2022-05-02", 43: "2026-05-09"}
CYCLE_LABELS = {34: "1990-94", 35: "1994-98", 36: "1998-2002", 37: "2002-2006", 38: "2006-2010",      # as kepviselo.cgi prints ciklus: two-digit end years before 1998
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
        """A candidate with a known history must not contradict the printed faction for the vote's cycle. A label with no
        faction at all (a speech list prints ministers, officials, guests that way — an MP speaks with a faction) is
        taken for a known person only if that person had a faction row in the cycle of the date, i.e. sat then; a
        namesake outside parliament (an oath-taking Monetary Council member) stays unresolved, with the role."""
        h = self.histories.get(azon, {})
        rows = h.get("factions") or []
        if not rows:
            return True
        label = CYCLE_LABELS.get(cycle_of_date(on_date) or -1)
        in_cycle = [f for c, f in rows if label is None or c == label]
        if not faction:
            return (bool(in_cycle) or label in (h.get("speech_cycles") or [])) if label else True
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
        if not faction and self.histories:
            return None                                       # a faction-less label (not an MP's line) is taken only for a record spelled exactly so
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
            out[p.stem[3:]] = {"name": rec["name"], "factions": [(f.get("ciklus"), f.get("faction")) for f in rec.get("factions") or []],
                               # cycles in which parlament.hu credits speeches to this record — a former MP speaking as a state secretary is still that person
                               "speech_cycles": [st.get("ciklus") for st in rec.get("speech_stats") or [] if (st.get("speeches") or st.get("technical"))],
                               "mandates": [(e.get("mandate_from"), e.get("mandate_to")) for e in rec.get("elections") or [] if e.get("mandate_from")]}
    return out


def seats_in_force(histories: dict[str, dict[str, Any]]) -> "SeatsInForce":
    """The mandate spans of every cached record → a callable date → seats (see SeatsInForce)."""
    return SeatsInForce([m for h in histories.values() for m in h.get("mandates") or []])


# -- sitting days ---------------------------------------------------------------------------

_WEEKDAYS_HU = ("hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap")


def parse_ulesnap(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """ulesnap.cgi → sitting days. The API prints each day's date and weekday name; for the older cycles (36, 37 seen)
    the date is one day early against its own weekday (1998-06-17 'csütörtök' — the 17th was a Wednesday), for the
    recent cycles the two agree. Where the weekday names the next day, the date is moved to it and the row is flagged
    date_corrected — the weekday is the evidence, the shift is not silent."""
    from datetime import date as _d, timedelta as _td
    root = payload.get("ulesnapok", payload)
    rows = []
    for u in as_list(root.get("ulesnap")):
        if not isinstance(u, dict):
            continue
        d = _hu_date(u.get("datum"))
        wd = (u.get("nap") or "").strip().lower() or None
        shift = None                                   # per row: does the weekday name say the printed date, or the next day?
        if d and wd in _WEEKDAYS_HU:
            dt = _d.fromisoformat(d)
            if _WEEKDAYS_HU[dt.weekday()] == wd:
                shift = 0
            elif _WEEKDAYS_HU[(dt + _td(days=1)).weekday()] == wd:
                shift = 1
        rows.append((u, d, wd, shift))
    # the shift is a property of the file (one cycle), not of a row: when nearly every dated row says "next day", the two
    # or three that do not are the weekday names' own slips (an unshifted twin would land on its neighbour's date)
    dated = [r for r in rows if r[3] is not None]
    systematic = bool(dated) and sum(1 for r in dated if r[3] == 1) >= 0.9 * len(dated)
    out = []
    for u, d, wd, shift in rows:
        corrected = False
        if d and (systematic or shift == 1):
            d = (_d.fromisoformat(d) + _td(days=1)).isoformat()
            corrected = True
        out.append({"date": d, "ulnap": _int(u.get("ulnap")), "weekday": u.get("nap") or None,
                    "session": u.get("ulszak") or None, "kind": u.get("ulesjelleg") or None,
                    "remark": u.get("megjegyzes") or None, "date_corrected": corrected,
                    "weekday_conflict": bool(d and wd in _WEEKDAYS_HU and _WEEKDAYS_HU[_d.fromisoformat(d).weekday()] != wd)})
    return sorted(out, key=lambda x: (x["date"] or "", x["ulnap"] or 0))


# -- speeches -------------------------------------------------------------------------------
#
#   felszolalasok.cgi  <felszolalasok ulesnap datum><esemeny>Napirendi pont címe<felszolalas><sorszam/><felszolalo>Név (Frakció)
#                      </felszolalo><felsztip/><kormbiz/><felszkezdete/><videoido/></felszolalas>…</esemeny>…
#   One <esemeny> is one agenda item (its text is the title, often carrying the iromány number); its children are the
#   speeches in order. felsztip is the kind ("felszólalás", "ülésvezetés", "kérdés megválaszolva" …), kormbiz the
#   speaker's role when not an MP's own ("miniszterelnök", "az Országgyűlés alelnöke"), videoido the length mm:ss.

# A speech is *substantive* when someone speaks on the matter — a speech, a two-minute one, a lead speaker's, before/after
# the agenda, an interpellation/question and its answers and rejoinders, a rapporteur's or committee's report, a point of
# order. Everything else the list prints under a person's name is procedure: chairing (ülésvezetés), the clerk's
# announcements, opening/closing the day, adopting the agenda, the oath, and the chair's result/status lines ("… elfogadva",
# "… vita megkezdve/lezárva", immunity decisions). parlament.hu's own per-MP record splits the same way ("felszólalás" vs
# "technikai"): with this list our count equals the record's for all but a handful of MPs in each cycle (the derive prints the
# check; the MP page prints the record's number beside ours when they differ). Personal-involvement remarks (személyes érintettség) are
# on the record's technical side, so they are here. A speech printed under several agenda items (a deputy speaker's one
# announcement covering several items) is one speech: parse_felszolalasok flags the later printings `reprint`, and every
# count treats a reprint as non-substantive while still listing it under each item.
SUBSTANTIVE_KINDS = {
    "felszólalás", "kétperces felszólalás", "vezérszónoki felszólalás", "napirend előtti felszólalás", "napirend előttihez hozzászólás",
    "napirend utáni felszólalás", "napirend utánihoz hozzászólás", "elhangzik az interpelláció/kérdés/azonnali kérdés", "kérdés megválaszolva",
    "interpelláció szóban megválaszolva", "azonnali kérdésre adott képviselői viszonválasz", "azonnali kérdésre adott miniszteri viszonválasz",
    "képviselő elfogadta a választ", "képviselő elutasította a választ", "előadói válasz", "előterjesztő nyitóbeszéde",
    "ismerteti a bizottság véleményét", "bizottság kisebbségi véleményének ismertetése",
    "ügyrendi javaslat", "ügyrendi kérdés", "bejelentés helyettes válaszadó elutasításáról",
}
# ("egyéb felszólalás" is on the record's technical side too — six rows in two cycles, all matching the record only that way.)


def _duration_s(v: str | None) -> int | None:
    """'25:40' → 1540; '1:02:03' → 3723; '' → None."""
    if not v or ":" not in v:
        return None
    parts = [int(x) for x in v.strip().split(":") if x.strip().isdigit()]
    if not parts:
        return None
    total = 0
    for x in parts:
        total = total * 60 + x
    return total


def parse_felszolalasok(payload: dict[str, Any]) -> dict[str, Any]:
    """felszolalasok.cgi → {ulnap, date, speeches: [...]}, speeches in the day's order with the agenda item they belong to."""
    root = payload.get("felszolalasok", payload)
    out = {"ulnap": _int(root.get("@ulesnap")), "date": _hu_date(root.get("@datum")), "speeches": []}
    order = 0
    seen: set[tuple] = set()                          # (sorszam, speaker) already printed today under an earlier agenda item
    for ev in as_list(root.get("esemeny")):
        if isinstance(ev, str):                       # an agenda item without speeches: to_dict collapsed it to its title
            continue
        title = re.sub(r"\s+", " ", (ev.get("#text") or "")).strip() or None
        nums: list[str] = []
        for m in re.finditer(r"\b([A-ZÁÉÍÓÖŐÚÜŰ]{1,3}/\d+)\b", title or ""):
            if m.group(1) not in nums:
                nums.append(m.group(1))               # a joint debate names every bill: keep them all, in order
        num = " · ".join(nums) if nums else None
        for f in as_list(ev.get("felszolalas")):
            if not isinstance(f, dict):
                continue
            label = (f.get("felszolalo") or "").strip()
            name, faction = NameResolver.split(label) if label else (None, None)
            kind = (f.get("felsztip") or "").strip() or None
            seq = _int(f.get("sorszam"))
            key = (seq, label)
            reprint = seq is not None and key in seen
            seen.add(key)
            order += 1
            out["speeches"].append({
                "order": order, "seq": seq, "event": title, "iromany": num,
                "speaker_label": label or None, "name": name, "faction": faction,
                "kind": kind, "role": (f.get("kormbiz") or "").strip() or None,
                "start": _hu_date(f.get("felszkezdete")), "duration_s": _duration_s(f.get("videoido")),
                "reprint": reprint,                    # the same speech (same sorszam, same speaker) printed under an earlier item today
                "technical": reprint or (kind or "").casefold() not in SUBSTANTIVE_KINDS,
            })
    return out


def parse_felszolalas(raw: bytes) -> dict[str, Any]:
    """felszolalas.cgi (one speech, with its text). The text comes as HTML *elements* inside <felsz_szovege> (div/p/span),
    not as escaped text — so this reads the element tree directly: paragraphs → plain text, the speaker's href → p_azon.

      <felszolalas ulnap datum sorsz><felszolalo href="/web/guest/kepviselok_mobil?kepviselo={"id":"m076"}">Név</felszolalo>
      <beosztas/><bizottsagi_eloado/><felsz_oka/><felsz_ideje/><felsz_szovege><div><p><span>…</span></p>…</div></felsz_szovege>
    """
    from .xmlutil import parse_xml
    import html as _html
    from urllib.parse import unquote
    root = parse_xml(raw)
    if root.tag != "felszolalas":
        root = root.find(".//felszolalas") or root
    sp = root.find("felszolalo")
    href = (sp.get("href") if sp is not None else None) or None
    azon = None
    if href:
        m = re.search(r'"id"\s*:\s*"([^"]+)"', unquote(href))
        azon = m.group(1) if m else None
    body = root.find("felsz_szovege")
    paras: list[str] = []
    if body is not None:
        for p_ in body.iter():
            if p_.tag.lower() == "p":
                txt = re.sub(r"\s+", " ", "".join(p_.itertext())).strip()
                if txt:
                    paras.append(_html.unescape(txt))
        if not paras:                                             # no <p>: whatever text there is
            txt = re.sub(r"\s+", " ", "".join(body.itertext())).strip()
            if txt:
                paras.append(_html.unescape(txt))
    def _t(tag):
        el = root.find(tag)
        return (el.text or "").strip() if el is not None and el.text else None
    return {
        "ulnap": _int(root.get("ulnap")), "date": _hu_date(root.get("datum")), "seq": _int(root.get("sorsz")),
        "speaker": (sp.text or "").strip() if sp is not None and sp.text else None, "azon": azon, "href": href,
        "position": _t("beosztas"), "committee_rapporteur": _t("bizottsagi_eloado"), "kind": _t("felsz_oka"),
        "duration_s": _duration_s(_t("felsz_ideje")), "paragraphs": paras, "chars": sum(len(x) for x in paras),
    }


# -- committees -----------------------------------------------------------------------------
#
#   bizottsagok.cgi  <bizottsagok><bizottsag id title><albizottsagok><albizottsag id title/>…</albizottsagok></bizottsag>…
#   bizottsag.cgi    <bizottsag nev tipus letrehozas><fobizottsag id nev/><elerhetoseg email telefon/><elnok p_azon nev frakcio/>
#                    <alelnokok><alelnok …/></alelnokok><tagok><tag …/></tagok><albizottsagok/><meghivo_datum/><meghivo_URL/></bizottsag>

def parse_bizottsagok(payload: dict[str, Any]) -> list[dict[str, Any]]:
    root = payload.get("bizottsagok", payload)
    out = []
    for b in as_list(root.get("bizottsag")):
        if not isinstance(b, dict):
            continue
        subs = [{"id": a.get("@id"), "title": a.get("@title")} for a in as_list((b.get("albizottsagok") or {}).get("albizottsag") if isinstance(b.get("albizottsagok"), dict) else []) if isinstance(a, dict)]
        out.append({"id": b.get("@id"), "title": b.get("@title"), "subcommittees": subs})
    return out


def parse_bizottsag(payload: dict[str, Any]) -> dict[str, Any]:
    r = payload.get("bizottsag", payload)
    def person(x):
        return {"azon": x.get("@p_azon") or None, "name": x.get("@nev") or None, "faction": x.get("@frakcio") or None} if isinstance(x, dict) else None
    fob = r.get("fobizottsag") if isinstance(r.get("fobizottsag"), dict) else {}
    el = r.get("elerhetoseg") if isinstance(r.get("elerhetoseg"), dict) else {}
    return {
        "name": r.get("@nev"), "type": r.get("@tipus") or None, "created": _hu_date(r.get("@letrehozas")),
        "parent": {"id": fob.get("@id") or None, "name": fob.get("@nev") or None} if fob.get("@id") else None,
        "email": el.get("@email") or None, "phone": el.get("@telefon") or None,
        "chair": person(r.get("elnok")),
        "vice_chairs": [p for p in (person(x) for x in as_list((r.get("alelnokok") or {}).get("alelnok") if isinstance(r.get("alelnokok"), dict) else [])) if p],
        "members": [p for p in (person(x) for x in as_list((r.get("tagok") or {}).get("tag") if isinstance(r.get("tagok"), dict) else [])) if p],
        "subcommittees": [{"id": a.get("@id"), "title": a.get("@title") or a.get("@nev")} for a in as_list((r.get("albizottsagok") or {}).get("albizottsag") if isinstance(r.get("albizottsagok"), dict) else []) if isinstance(a, dict)],
        "next_meeting": _hu_date(r.get("meghivo_datum")) if r.get("meghivo_datum") else None,
        "next_meeting_url": (r.get("meghivo_URL") or "").strip() or None,
    }


# -- votes ----------------------------------------------------------------------------------

def parse_iromany_szam(szam: str | None) -> dict[str, Any]:
    """'T/71' -> {kind:'T', number:71}; '56/4' -> {kind:'amendment', parent:56, number:4}."""
    if not szam:
        return {"kind": None}
    m = re.match(r"^([A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű]+)/(\d+)$", szam.strip())
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


def _faction_label(f: str | None) -> str | None:
    """The faction as the roll call prints it; an empty or missing label (an MP between two groups — 119 rows in 2002–2010)
    is read as 'független' by the roll-call parser, because that is what no group means on the day."""
    if f is None:
        return None
    f = f.strip()
    return f if f else "független"


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
        rec = {"faction": _faction_label(s.get("@frakcio")), "igen": _int(s.get("igen_db")), "nem": _int(s.get("nem_db")),
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
                   seats: int | None = None, seats_fn=None) -> dict[str, Any]:
    """szavazas.cgi -> full vote record with faction tallies, per-MP positions and the majority verdict.
    `seats_fn(on_date)` gives the mandates in force (SeatsInForce); without it the era's seat count."""
    outer = payload.get("szavazas", payload)
    el = outer.get("szavazas", outer) if isinstance(outer, dict) and "szavazas" in outer else outer
    rec = _vote_core(el)
    if seats is None:
        seats = seats_fn(rec["on_date"]) if seats_fn else seats_for(rec["on_date"])
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
            faction = _faction_label(faction) or "független"     # a roll-call line without a group is an independent's
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
        rec["present_basis"] = "nev_szerint: igen+nem+tart (a leadott szavazatok, az API „Összes szavazat” mezője)"
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
        cast_n = rec["igen"] + (rec["nem"] or 0) + (rec["tartozkodott"] or 0)
        present_used = rec["present"]
        if present_used is not None and present_used < cast_n:
            # an old payload's header and roll call disagree (a 2002 vote: 347 cast, 346 in the roll call): the votes cast
            # were present by definition — take them as the base and say so
            present_used = cast_n
            rec["present_basis"] = (rec["present_basis"] or "") + " · a névsor kevesebb, mint a leadott szavazat: a leadott az alap"
        tally = Tally(yes=rec["igen"], no=rec["nem"] or 0, abstain=rec["tartozkodott"] or 0,
                      not_voting=(counts.get("nem_szavazott", 0) + counts.get("jelen_nem_szavazott", 0)),
                      present=present_used, seats=seats)
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
