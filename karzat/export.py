"""Export shapes: the same tables the pages show, as CSV and JSON, plus the per-cycle dump.

Everything here is derived from the builder's inputs (data/derived/*), never from the raw cache, so a
clean checkout produces the same files. CSV is UTF-8 with a BOM (Excel-friendly), comma-separated,
one header row; JSON is UTF-8, keys in Hungarian where the page uses Hungarian words and in the
schema's English where it mirrors schema.sql. The data dictionary (adatszotar()) is rendered on
adatok/index.html so a downloaded file never has to be reverse-engineered.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

POSITION_LABEL = {"igen": "igen", "nem": "nem", "tartozkodott": "tartózkodott", "jelen_nem_szavazott": "jelen, nem szavazott",
                  "nem_szavazott": "nem szavazott", "bejelentett_hianyzo": "előre bejelentett hiányzó", "igazoltan_tavol": "igazoltan távol"}


def _csv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in columns})
    return "﻿" + buf.getvalue()


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"


# -- one vote -----------------------------------------------------------------------------------

VOTE_COLUMNS = ["ts", "date", "time", "cycle", "mode", "kind", "secret", "igen", "nem", "tartozkodott", "osszes_szavazat", "present",
                "rule", "needed", "base", "margin", "result", "passed", "computed_agrees", "iromany", "title", "outcome", "submitters"]
POSITION_COLUMNS = ["ts", "p_azon", "name", "faction", "position", "position_label", "against_faction", "sector", "row", "seat"]


def vote_row(v: dict[str, Any], cycle: int) -> dict[str, Any]:
    m = v.get("majority") or {}
    mo = (v.get("motions") or [{}])[0] if v.get("motions") else {}
    return {"ts": v["ts"], "date": v["on_date"], "time": v.get("time"), "cycle": cycle, "mode": v.get("mode"), "kind": v.get("kind"),
            "secret": bool(v.get("secret")), "igen": v.get("igen"), "nem": v.get("nem"), "tartozkodott": v.get("tartozkodott"),
            "osszes_szavazat": v.get("osszes_szavazat"), "present": v.get("present"),
            "rule": m.get("rule"), "needed": m.get("needed"), "base": m.get("base"), "margin": m.get("margin"),
            "result": v.get("result_raw"), "passed": v.get("passed"), "computed_agrees": m.get("agrees_with_source"),
            "iromany": mo.get("iromany"), "title": mo.get("title"), "outcome": mo.get("outcome"),
            "submitters": "; ".join(mo.get("submitters") or []) if mo else None}


def vote_export(view: dict[str, Any], cycle: int, align: dict[str, str | None], seats: dict[str, dict] | None) -> tuple[str, str]:
    """(json, csv) for one vote: the vote row plus every roll-call position."""
    positions = []
    for p in view.get("positions") or []:
        xy = (seats or {}).get(p.get("mp_azon") or "") or {}
        positions.append({"ts": view["ts"], "p_azon": p.get("mp_azon"), "name": p["name"], "faction": p["faction"], "position": p["position"],
                          "position_label": POSITION_LABEL.get(p["position"], p["position"]),
                          "against_faction": (align.get(p.get("mp_azon") or "") == "against") if p.get("mp_azon") else None,
                          "sector": xy.get("sector"), "row": xy.get("row"), "seat": xy.get("seat")})
    row = vote_row(view, cycle)
    obj = {"vote": row, "motions": view.get("motions") or [], "faction_tallies": view.get("faction_tallies") or [],
           "position_counts": view.get("position_counts") or {}, "positions": positions}
    return _json(obj), _csv(positions, POSITION_COLUMNS)


# -- one MP -------------------------------------------------------------------------------------

MP_VOTE_COLUMNS = ["ts", "date", "time", "iromany", "title", "rule", "result", "position", "position_label", "faction", "faction_plurality", "align"]


def mp_export(mp: dict[str, Any], record: dict[str, Any], by_ts: dict[str, dict], cycle: int) -> tuple[str, str]:
    """(json, csv) for one MP in one cycle: the record and every roll call they appear in."""
    rows = []
    for v in record.get("votes") or []:
        vv = by_ts.get(v["ts"]) or {}
        mo = (vv.get("motions") or [{}])[0] if vv.get("motions") else {}
        rows.append({"ts": v["ts"], "date": vv.get("on_date"), "time": vv.get("time"), "iromany": mo.get("iromany"), "title": mo.get("title"),
                     "rule": ((vv.get("majority") or {}).get("rule")), "result": vv.get("result_raw"),
                     "position": v["position"], "position_label": POSITION_LABEL.get(v["position"], v["position"]),
                     "faction": v.get("faction"), "faction_plurality": v.get("faction_plurality"), "align": v.get("align")})
    obj = {"mp": {k: mp.get(k) for k in ("p_azon", "name", "faction", "faction_first", "current", "mandate_kind", "county", "constituency_no",
                                          "mandate_from", "mandate_to", "wikidata_qid", "parlament_url", "cycle")},
           "cycle": cycle, "summary": {k: record.get(k) for k in ("in_roll", "cast", "with", "against")}, "counts": record.get("counts") or {},
           "factions": mp.get("factions") or [], "elections": mp.get("elections") or [], "motion_stats": mp.get("motion_stats") or [],
           "motions": mp.get("motions") or [], "votes": rows}
    return _json(obj), _csv(rows, MP_VOTE_COLUMNS)


# -- the directory and the per-cycle dump -----------------------------------------------------------

def directory_export(votes: list[dict[str, Any]], cycle: int) -> tuple[str, str]:
    rows = [vote_row(v, cycle) for v in votes]
    return _json({"cycle": cycle, "votes": rows}), _csv(rows, VOTE_COLUMNS)


MP_COLUMNS = ["p_azon", "name", "faction", "faction_first", "mandate_kind", "county", "constituency_no", "mandate_from", "mandate_to",
              "wikidata_qid", "in_roll", "cast", "with", "against"]


def mps_export(mps: dict[str, dict], per_mp: dict[str, dict], cycle: int) -> tuple[str, str]:
    rows = []
    for azon, mp in sorted(mps.items(), key=lambda kv: kv[1]["name"]):
        r = per_mp.get(azon) or {}
        rows.append({**{k: mp.get(k) for k in MP_COLUMNS if k in mp}, "in_roll": r.get("in_roll", 0), "cast": r.get("cast", 0),
                     "with": r.get("with", 0), "against": r.get("against", 0)})
    return _json({"cycle": cycle, "mps": rows}), _csv(rows, MP_COLUMNS)


def positions_csv(store: dict[str, Any], order: list[str], align_by_vote: dict[str, dict[str, str | None]] | None = None) -> str:
    """Every roll-call position of the cycle, one row each (the compact store expanded)."""
    codes = store["codes"]
    out = io.StringIO()
    out.write("﻿")
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["ts", "p_azon", "name", "faction", "position", "align"])
    for ts in order:
        rows = store["positions"].get(ts) or []
        al = (align_by_vote or {}).get(ts) or {}
        for key, fi, code in rows:
            azon = key if key in store["members"] else ""
            name = store["members"][key]["name"] if azon else key
            w.writerow([ts, azon, name, store["factions"][fi], codes.get(code, code), al.get(azon) or ""])
    return out.getvalue()


MOTION_COLUMNS = ["p_azon", "mp_name", "szam", "kind", "number", "title", "status", "href", "co_submitters"]


def motions_export(mps: dict[str, dict], cycle: int) -> tuple[str, str]:
    rows = []
    for azon, mp in sorted(mps.items(), key=lambda kv: kv[1]["name"]):
        for m in mp.get("motions") or []:
            rows.append({"p_azon": azon, "mp_name": mp["name"], **{k: m.get(k) for k in ("szam", "kind", "number", "title", "status", "href", "co_submitters")}})
    return _json({"cycle": cycle, "motions": rows}), _csv(rows, MOTION_COLUMNS)


def adatszotar() -> list[tuple[str, list[tuple[str, str]]]]:
    """The data dictionary: file → (column, meaning). Rendered on adatok/index.html."""
    return [
        ("szavazasok.csv / szavazasok.json — a ciklus minden szavazása, egy sor egy szavazás", [
            ("ts", "az API időpontja, ÉÉÉÉ.HH.NN.ÓÓ:PP:MM — a szavazás azonosítója"),
            ("date, time", "ugyanaz két mezőben (ISO dátum, ÓÓ:PP)"),
            ("cycle", "ciklus száma (34 = 1990–94 … 43 = 2026–)"),
            ("mode", "„Szavazási mód” az API szerint — ez nevezi meg a többségi szabályt"),
            ("kind", "dontes | jelenlet (jelenlét megállapítása)"),
            ("secret", "titkos szavazás (nincs név szerinti lista)"),
            ("igen, nem, tartozkodott, osszes_szavazat", "az API összesítése"),
            ("present", "jelenlévők: igen + nem + tartózkodott + jelen, nem szavazott (névsorból), különben az „Összes szavazat”"),
            ("rule, needed, base, margin", "a szabály kódja (egyszeru, abszolut, ketharmad_jelenlevo, ketharmad_osszes, negyotod_jelenlevo, negyotod_osszes), a küszöb, az alap, igen − küszöb"),
            ("result, passed", "„Elfogadás” az API szerint; igaz/hamis ahol döntés"),
            ("computed_agrees", "a számított eredmény egyezik-e a forráséval"),
            ("iromany, title, outcome, submitters", "az első indítvány száma, címe, kimenetele, benyújtói"),
        ]),
        ("nevsorok.csv — minden név szerinti szavazat, egy sor egy képviselő egy szavazáson", [
            ("ts", "a szavazás azonosítója"),
            ("p_azon", "a képviselő azonosítója (= Wikidata P4966); üres, ha a név nem volt feloldható"),
            ("name, faction", "ahogy a névsor nyomtatja"),
            ("position, position_label", "igen | nem | tartozkodott | jelen_nem_szavazott | nem_szavazott | bejelentett_hianyzo | igazoltan_tavol — és ugyanez magyarul (a szavazás-oldal .csv-jében)"),
            ("align, against_faction", "with = a frakciója leadott szavazatainak többségével · against = ellene · üres = nem adott le szavazatot; a szavazás-oldal .csv-jében igaz/hamis"),
            ("sector, row, seat", "ülőhely a parlament.hu alaprajza szerint (csak a jelenlegi ciklusban)"),
            ("faction_tallies (a szavazás-oldal .json-jában)", "az API frakciósorai: igen, nem, tartozkodott, nem_szavazott (= nem szavazott + jelen, nem szavazott), igazoltan_tavol (= igazoltan távol + előre bejelentett hiányzó), osszesen"),
        ]),
        ("kepviselok.csv / kepviselok.json — a ciklus névsoraiban szereplő személyek", [
            ("p_azon, name, faction, faction_first", "azonosító, név, frakció az utolsó, illetve az első név szerinti szavazásán"),
            ("mandate_kind, county, constituency_no, mandate_from, mandate_to", "egyéni választókerület vagy lista; a mandátum kezdete és vége a képviselői adatlap szerint"),
            ("wikidata_qid", "Wikidata azonosító"),
            ("in_roll, cast, with, against", "hány névsorban szerepel · hányszor adott le szavazatot · frakciójával · ellene"),
            ("faction_plurality", "(a képviselő .csv-jében) a frakciója leadott szavazatainak többségi álláspontja az adott szavazáson"),
        ]),
        ("inditvanyok.csv / inditvanyok.json — a jelenlegi ciklus irományai benyújtó szerint (csak a 43. ciklusra)", [
            ("p_azon, mp_name", "a benyújtó"),
            ("szam, kind, number, title, status, href, co_submitters", "iromány száma, előtagja, sorszáma, címe, állapota, parlament.hu-link, további benyújtók száma"),
        ]),
        ("kohezio/ — frakciok.csv, frakcio_parok.csv, kepviselo_parok.csv, szavazasonkent.csv", [
            ("ai", "egyetértési index (Hix–Noury–Roland): (legnagyobb − (összes − legnagyobb)/2) / összes, az igen–nem–tartózkodott leadott szavazatokból"),
            ("rice", "Rice-index: |igen − nem| / (igen + nem)"),
            ("unanimous_votes, votes_with_dissent, dissenting_votes", "egyhangú szavazások · nem egyhangúak · a frakció többségétől eltérő leadott szavazatok összesen"),
            ("votes_both_cast, same_plurality, share", "frakciópáronként: hány szavazáson adott le mindkettő szavazatot · hányszor volt azonos a többségi álláspont · arány"),
            ("shared, same, share", "képviselőpáronként (frakción belül, ≥ 20 közös szavazás): közös leadott szavazások · azonos szavazat · arány"),
        ]),
        ("szamok/havonta.csv — a ciklus hónapról hónapra, frakciónként egy sor", [
            ("votes, decisions, qualified, roll_calls", "a hónap szavazásai · döntései · minősített többséget kívánó döntései · név szerinti listái"),
            ("in_roll, cast, with, against, participation, dissent, ai", "a frakció tagjaira: névsorban · leadott · frakcióval · ellene · leadott/névsor · ellene/leadott · egyetértési index (súlyozott havi átlag)"),
        ]),
        ("iromany/iromanyok.csv — minden iromány, amelyről név szerint szavaztak", [
            ("number, prefix, label", "az iromány száma (egy számtér ciklusonként), előtagja ha a névsor írta (T/, H/, I/…), a kettő együtt"),
            ("votes, own_votes, amendment_votes", "hány szavazás · az iromány saját szakaszairól · a módosítóiról"),
            ("first, last, final_outcome, final_result", "első és utolsó szavazás napja, az utolsó szavazás kimenetel-szövege és eredménye"),
        ]),
        ("szoros/dontesek.csv — minden döntés a küszöbéhez mérve", [
            ("margin", "igen − szükséges"),
            ("yes_majority, qualified", "több igen, mint nem · minősített (nem egyszerű) többséget kívánt"),
            ("hypo_igen, flips_if_all_voted", "számított feltevés: hány igen lenne, ha minden szavazatot le nem adó a frakciója többségével szavazott volna, és megfordulna-e a kimenetel"),
        ]),
        ("egy szavazás oldala mellett: <slug>.json és <slug>.csv", [
            ("json", "a szavazás sora + indítványok + frakciónkénti összesítés + a teljes névsor (ülőhellyel, ahol ismert)"),
            ("csv", "a névsor: ts, p_azon, name, faction, position, position_label, against_faction, sector, row, seat"),
        ]),
        ("egy képviselő oldala mellett: <p_azon>.json és <p_azon>.csv", [
            ("json", "a képviselő adatai, a ciklus összesítése, frakciótörténet, választások, indítványok, és minden szavazása"),
            ("csv", "minden szavazása: ts, date, time, iromany, title, rule, result, position, position_label, faction, faction_plurality, align"),
        ]),
    ]


# -- analytics ---------------------------------------------------------------------------------

def cohesion_factions_csv(co: dict[str, Any], cycle: int) -> str:
    rows = [{"cycle": cycle, "faction": f, **{k: v.get(k) for k in ("votes", "cast", "ai", "rice", "unanimous_votes", "votes_with_dissent", "dissenting_votes")}}
            for f, v in co["per_faction"].items()]
    return _csv(rows, ["cycle", "faction", "votes", "cast", "ai", "rice", "unanimous_votes", "votes_with_dissent", "dissenting_votes"])


def faction_pairs_csv(co: dict[str, Any], cycle: int) -> str:
    rows = [{"cycle": cycle, "faction_a": a, "faction_b": b, "votes_both_cast": x["votes"], "same_plurality": x["same"], "share": x["share"]}
            for a, d in co["faction_pairs"].items() for b, x in d.items()]
    return _csv(rows, ["cycle", "faction_a", "faction_b", "votes_both_cast", "same_plurality", "share"])


def mp_pairs_csv(co: dict[str, Any], mps: dict[str, dict], cycle: int) -> str:
    rows = [{"cycle": cycle, "faction": f, "a": r["a"], "a_name": (mps.get(r["a"]) or {}).get("name"), "b": r["b"], "b_name": (mps.get(r["b"]) or {}).get("name"),
             "shared": r["shared"], "same": r["same"], "share": r["share"]} for f, lst in co["mp_pairs"].items() for r in lst]
    return _csv(rows, ["cycle", "faction", "a", "a_name", "b", "b_name", "shared", "same", "share"])


def cohesion_votes_csv(co: dict[str, Any], cycle: int) -> str:
    rows = [{"cycle": cycle, "ts": ts, "faction": f, **{k: d.get(k) for k in ("igen", "nem", "tartozkodott", "cast", "rice", "ai", "dissent")}}
            for ts, per in co["per_vote"].items() for f, d in per.items()]
    return _csv(rows, ["cycle", "ts", "faction", "igen", "nem", "tartozkodott", "cast", "rice", "ai", "dissent"])


def decisions_csv(cl: dict[str, Any], cycle: int) -> str:
    cols = ["ts", "date", "time", "rule", "needed", "base", "igen", "nem", "tartozkodott", "margin", "passed", "yes_majority", "qualified", "hypo_igen", "flips_if_all_voted", "iromany", "title"]
    return _csv([{**r, "cycle": cycle} for r in cl["decisions"]], ["cycle"] + cols)


def bills_csv(bs: dict[int, dict[str, Any]], cycle: int) -> str:
    rows = [{"cycle": cycle, "number": n, "prefix": b["prefix"], "label": b["label"], "title": b["title"], "votes": len(b["votes"]),
             "own_votes": b["own_votes"], "amendment_votes": b["amendment_votes"], "first": b["first"], "last": b["last"],
             "final_outcome": b["final_outcome"], "final_result": b["final_result"], "href": b["href"]} for n, b in sorted(bs.items())]
    return _csv(rows, ["cycle", "number", "prefix", "label", "title", "votes", "own_votes", "amendment_votes", "first", "last", "final_outcome", "final_result", "href"])


def monthly_csv(months: list[dict[str, Any]], cycle: int) -> str:
    rows = []
    for d in months:
        for f, x in sorted(d["factions"].items()):
            rows.append({"cycle": cycle, "month": d["month"], "votes": d["votes"], "decisions": d["decisions"], "qualified": d["qualified"], "roll_calls": d["roll_calls"],
                         "faction": f, "in_roll": x["in_roll"], "cast": x["cast"], "with": x["with"], "against": x["against"],
                         "participation": x["participation"], "dissent": x["dissent"], "ai": x.get("ai")})
    return _csv(rows, ["cycle", "month", "votes", "decisions", "qualified", "roll_calls", "faction", "in_roll", "cast", "with", "against", "participation", "dissent", "ai"])
