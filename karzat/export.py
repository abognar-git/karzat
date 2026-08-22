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

from karzat.normalise import POSITION_LABEL   # one table, beside the codes it names



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


def dissents_csv(events: list[dict[str, Any]], cycle: int) -> str:
    """adatok/kulonvelemenyek.csv — one row per dissent (an MP's cast vote against their faction's plurality), newest first;
    the complete record behind the Atom feeds."""
    rows = []
    for e in events:
        v = e["vote"]
        for d in e["dissents"]:
            rows.append({"ts": v["ts"], "date": v["on_date"], "time": v.get("time"), "cycle": cycle, "p_azon": d.get("azon"), "name": d["name"],
                         "faction": d["faction"], "position": d["position"], "faction_plurality": d["plurality"],
                         "iromany": v.get("iromany"), "title": v.get("title"), "igen": v.get("igen"), "nem": v.get("nem"), "tartozkodott": v.get("tartozkodott"),
                         "result": v.get("result")})
    return _csv(rows, ["ts", "date", "time", "cycle", "p_azon", "name", "faction", "position", "faction_plurality", "iromany", "title", "igen", "nem", "tartozkodott", "result"])


def speeches_csv(rows: list[dict[str, Any]], cycle: int) -> str:
    """adatok/felszolalasok.csv — every line of the cycle's speech lists, in the days' order."""
    out = [{"cycle": cycle, "date": r.get("date"), "ulnap": r.get("ulnap"), "order": r.get("order"), "seq": r.get("seq"), "event": r.get("event"),
            "iromany": r.get("iromany"), "speaker": r.get("speaker_label"), "p_azon": r.get("azon"), "faction": r.get("faction"),
            "kind": r.get("kind"), "role": r.get("role"), "duration_s": r.get("duration_s"), "substantive": not r.get("technical")}
           for r in sorted(rows, key=lambda r: (r.get("date") or "", r.get("order") or 0))]
    return _csv(out, ["cycle", "date", "ulnap", "order", "seq", "event", "iromany", "speaker", "p_azon", "faction", "kind", "role", "duration_s", "substantive"])


def weekly_csv(weeks: list[dict[str, Any]], digests: dict[str, dict[str, Any]], mps: dict[str, dict[str, Any]], cycle: int) -> str:
    """adatok/heti.csv — one row per MP per sitting week: the numbers of the weekly digest."""
    out = []
    for w in weeks:
        d = digests.get(w["key"]) or {}
        rc = (d.get("cycle") or {}).get("roll_calls", 0)
        for azon in sorted(mps):
            m = (d.get("mps") or {}).get(azon)
            out.append({"cycle": cycle, "week": w["key"], "from": w["from"], "to": w["to"], "sitting_days": w.get("ulnaps", len(w["days"])), "p_azon": azon,
                        "name": mps[azon]["name"], "faction": mps[azon].get("faction"), "roll_calls": rc,
                        "in_roll": (m or {}).get("in_roll", 0), "cast": (m or {}).get("cast", 0), "against": (m or {}).get("against", 0),
                        "speeches": len((m or {}).get("speeches") or [])})
    return _csv(out, ["cycle", "week", "from", "to", "sitting_days", "p_azon", "name", "faction", "roll_calls", "in_roll", "cast", "against", "speeches"])


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
            ("present", "jelenlévők = a leadott szavazatok: igen + nem + tartózkodott (a névsorból; ez az API „Összes szavazat” mezője is) — a „jelen, nem szavazott” nincs benne"),
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
        ("kulonvelemenyek.csv — minden különvélemény, egy sor egy képviselő egy szavazáson: a különvélemény-csatornák teljes tartalma, a függetlenek soraival együtt (őket a csatornák nem viszik)", [
            ("ts, date, time, cycle", "a szavazás azonosítója és ideje"),
            ("p_azon, name, faction", "a képviselő; a frakció, amit a névsor annál a szavazásnál ír"),
            ("position, faction_plurality", "a leadott szavazat és a frakció leadott szavazatainak többsége — eltérnek (ez a különvélemény)"),
            ("iromany, title, igen, nem, tartozkodott, result", "a szavazás első indítványa és összesítése"),
        ]),
        ("felszolalasok.csv — az ülésnapok felszólalás-listái, egy sor egy felszólalás-sor", [
            ("cycle, date, ulnap, order, seq", "ciklus, nap, ülésnap száma, sorszám a napon belül (minden napirendi ponton át), a lista saját sorszáma"),
            ("event, iromany", "a napirendi pont címe az API szerint; a címben talált iromány-szám"),
            ("speaker, p_azon, faction", "a felszólaló ahogy a lista írja („Név (Frakció)”), a feloldott azonosító (üres, ha nem képviselő: miniszter, köztársasági elnök), a nyomtatott frakció"),
            ("kind, role, duration_s", "a felszólalás fajtája („felszólalás”, „ülésvezetés”, „kérdés megválaszolva” …), a lista által írt szerep, hossz másodpercben"),
            ("substantive", "érdemi (igaz) vagy eljárási (hamis) — a fajta neve szerint, lásd a módszer oldalt"),
        ]),
        ("felszolalas/<ülésnap>-<sorszám>.json — egy felszólalás (a lapja mellett)", [
            ("id, date, ulnap, seq, speaker, p_azon, faction, kind, role, event, iromany, duration_s", "a napi lista sora: azonosító, nap, ülésnap, sorszám, felszólaló, feloldott azonosító, frakció, fajta, szerep, napirendi pont, iromány-szám(ok), hossz"),
            ("position, chars, paragraphs", "a szöveg-adat beosztása, a szöveg hossza karakterben, a bekezdések listája (üres, ha a szöveg nincs betöltve)"),
        ]),
        ("heti.csv — képviselőnként és ülésnapos hetenként a heti összefoglaló számai", [
            ("cycle, week, from, to, sitting_days", "ciklus, ISO-hét, a hét első és utolsó ülésnapja, ülésnapok száma"),
            ("p_azon, name, faction", "a képviselő; a ciklusbeli utolsó frakciója"),
            ("roll_calls, in_roll, cast, against, speeches", "a hét név szerinti szavazásai · ebből hány névsorában szerepelt · leadott szavazatai · a frakciója többségével szemben · érdemi felszólalásai"),
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
        ("beszedido/vitak.csv — évenként minden vita és minden iromány nélküli sáv", [
            ("cycle, year", "ciklus és naptári év; egy vita az évén belül értendő, ezért ami két évre nyúlik, két sorban van"),
            ("kind", "vita = a napirendi pont irományt nevez meg · sav = nem nevez meg (napirend előtti, napirend utáni, politikai vita)"),
            ("iromany", "a vita minden irományszáma „ · ”-tal elválasztva — összevont vitánál több; sávnál üres"),
            ("bills", "hány irományt fog össze ez a vita (1 = önálló, 2+ = összevont)"),
            ("title", "a napirendi pont szövege az irományszám után, a Ház saját címe; sávnál a napirendi pont neve"),
            ("seconds, speeches", "a vitára fordított érdemi felszólalási idő és a felszólalások száma"),
            ("share_of_year", "seconds osztva az év teljes érdemi idejével; egy éven belül a sorok kiadják az 1,0-t"),
            ("FIGYELEM", "ugyanaz az iromány több sorban is lehet, ha a Ház más társaságban vagy más évben vette elő — GROUP BY iromany előtt ezt érdemes tudni; az évösszeg viszont pontos, mert egy felszólalás sem szerepel kétszer"),
        ]),
        ("beszedido/bontas.csv — ugyanaz az idő háromfelé bontva, hosszú formában", [
            ("cycle, year, kind, item", "a vitak.csv sorára mutat: item = az ott lévő iromany (vitánál) vagy title (sávnál)"),
            ("dimension", "frakcio | fajta | szakasz"),
            ("value", "a bontás értéke: a frakció neve, a felszólalás fajtája az API szövegével, vagy a vita szakasza a napirendi pont szövegéből"),
            ("seconds", "ennyi idő esik erre az értékre a vitán belül"),
            ("miért hosszú és nem széles", "a három szókincs ciklusonként más — oszloponként egy frakció más oszlopkészletet adna minden ciklusra"),
        ]),
        ("beszedido/frakciok.csv — évenként és frakciónként a beszédidő és a Ház összetétele", [
            ("seconds", "a frakció érdemi felszólalási ideje abban az évben"),
            ("share_of_faction_time", "a frakciók egymás közti aránya — NEM az évé: a frakció nélküli felszólalók (nem képviselő miniszter, nemzetiségi szószóló, levezető elnök) nincsenek a nevezőben"),
            ("unattributed", "az év beszédidejének ez a hányada frakció nélküli — minden soron ugyanaz, hogy a fájl ne legyen enélkül olvasható"),
            ("share_of_house", "a frakció helye a Házban, IDŐARÁNYOSAN: minden felszólalás a saját hosszával számít bele annak a névsornak az összetételébe, ami aznap érvényes volt"),
            ("mean_seats", "ugyanez főben kifejezve, ezért tizedes — egy évközben megszűnt frakciónál jóval kisebb, mint a legnagyobb létszáma"),
            ("ratio", "share_of_faction_time / share_of_house. Az 1,00 nem küszöb: a vezérszónoki idő frakciónként jár, nem képviselőnként, ezért a nagy frakciók száma számtanilag lejjebb kerül"),
            ("mandátum", "ez nem mandátumszám — a W-API nem közöl ilyet; a névsor a jelen lévőket és a távollévőket is kiírja, tehát a Ház összetételét mutatja"),
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


def floor_debates_csv(ft: dict[str, Any], cycle: int) -> str:
    """One row per debate and per subject-less slot, so a year's floor time adds up in the file too.

    `kind` separates the two: a `vita` is an agenda item that named a bill, a `sav` is one that named none.
    Summing `seconds` over a year gives the year, which is the property the page depends on and the reason a
    joint debate is one row carrying every number it covered rather than several rows sharing it out."""
    rows = []
    for y in ft["years"]:
        for d in y["debates"]:
            rows.append({"cycle": cycle, "year": y["year"], "kind": "vita", "iromany": " · ".join(d["bills"]),
                         "bills": len(d["bills"]), "title": d["title"], "seconds": d["seconds"], "speeches": d["speeches"],
                         "share_of_year": d["seconds"] / y["seconds"] if y["seconds"] else None})
        for d in y["slots"]:
            rows.append({"cycle": cycle, "year": y["year"], "kind": "sav", "iromany": None, "bills": 0,
                         "title": d["event"], "seconds": d["seconds"], "speeches": d["speeches"],
                         "share_of_year": d["seconds"] / y["seconds"] if y["seconds"] else None})
    return _csv(rows, ["cycle", "year", "kind", "iromany", "bills", "title", "seconds", "speeches", "share_of_year"])


def floor_breakdown_csv(ft: dict[str, Any], cycle: int) -> str:
    """The same time cut three ways, long-form: which faction, what kind of speech, which stage of the debate.

    Long rather than wide because the three vocabularies are open-ended and cycle-specific — a column per
    faction would be a different set of columns for every cycle, and a reader joining two files would get
    silent nulls where a faction simply did not exist yet."""
    rows = []
    for y in ft["years"]:
        for scope, items, key in (("vita", y["debates"], lambda d: " · ".join(d["bills"])),
                                  ("sav", y["slots"], lambda d: d["event"])):
            for d in items:
                for dim, split in (("frakcio", d["factions"]), ("fajta", d["forms"]), ("szakasz", d.get("stages") or {})):
                    for k, secs in split.items():
                        rows.append({"cycle": cycle, "year": y["year"], "kind": scope, "item": key(d),
                                     "dimension": dim, "value": k, "seconds": secs})
    return _csv(rows, ["cycle", "year", "kind", "item", "dimension", "value", "seconds"])


def floor_factions_csv(ft: dict[str, Any], cycle: int) -> str:
    """Per year and faction: speaking time, the chamber it was spoken in, and the two divided.

    `share_of_house` is time-weighted, not an annual average: every speech counts with its own length
    towards the composition of the roll call in force that day, so a faction that dissolved in March is
    measured against the House it sat in rather than a year it did not finish. `mean_seats` is the same
    weighting expressed as a headcount, which is why it is a decimal. Neither is a mandate count — the API
    publishes none — and the columns are named for what they are so that nobody joins them to a seat table
    and calls the difference an error.

    `share_of_faction_time` has factions in its denominator, not the year: speeches by people in no faction
    (ministers who are not members, nationality spokespersons, the chair) are outside it. `unattributed`
    repeats the year's share of those on every row so the file cannot be read without it."""
    rows = []
    for y in ft["years"]:
        tot = sum(y["factions"].values()) or 1
        etot = sum(y["expected"].get(f, 0.0) for f in y["factions"]) or 1
        unattr = 1 - sum(y["factions"].values()) / (y["seconds"] or 1)
        for f, secs in y["factions"].items():
            e = y["expected"].get(f)
            rows.append({"cycle": cycle, "year": y["year"], "faction": f, "seconds": secs,
                         "share_of_faction_time": secs / tot, "mean_seats": round(y["seats"].get(f) or 0.0, 2),
                         "share_of_house": (e / etot) if e else None,
                         "ratio": ((secs / tot) / (e / etot)) if e else None,
                         "unattributed": round(unattr, 4)})
    return _csv(rows, ["cycle", "year", "faction", "seconds", "share_of_faction_time", "mean_seats",
                       "share_of_house", "ratio", "unattributed"])


def motion_records_csv(by_num: dict[int, dict[str, Any]], cycle: int, texts: dict[str, Any]) -> str:
    """One row per motion record — the registry's own view, which `iromanyok.csv` does not carry.

    A file of its own rather than columns added to `iromanyok.csv`: that one is written for every cycle and its
    column list is part of the published data dictionary, so widening it would give the eight archive cycles eight
    columns of blank. This one exists only where the registry answers, which is the current cycle.

    `spoken` counts the referenced speeches whose full text the site holds, so a reader can tell a motion whose
    debate is readable here from one where the record only points at it."""
    rows = []
    for n, r in sorted(by_num.items()):
        events = r.get("events") or []
        refs = [e["speech"].replace("/", "-") for e in events if e.get("speech")]
        prom = r.get("promulgation") or {}
        rows.append({"cycle": cycle, "number": n, "szam": r.get("szam"), "title": r.get("title"),
                     "type": r.get("type"), "main_type": r.get("main_type"), "nature": r.get("nature"),
                     "submitted_on": r.get("submitted_on"), "status": r.get("status"),
                     "submitters": "; ".join(r.get("submitters") or []), "submitter_kind": r.get("submitter_kind"),
                     "addressee": r.get("addressee"), "procedure_mode": r.get("procedure_mode"),
                     "promulgation_date": prom.get("date"), "law_ref": prom.get("law_ref"),
                     "committees": "; ".join(c.get("name") or "" for c in r.get("committees") or []),
                     "events": len(events), "speech_refs": len(refs),
                     "spoken": sum(1 for x in refs if x in texts),
                     "text_pdf_url": r.get("text_pdf_url"), "note": r.get("note")})
    return _csv(rows, ["cycle", "number", "szam", "title", "type", "main_type", "nature", "submitted_on", "status",
                       "submitters", "submitter_kind", "addressee", "procedure_mode", "promulgation_date", "law_ref",
                       "committees", "events", "speech_refs", "spoken", "text_pdf_url", "note"])


def monthly_csv(months: list[dict[str, Any]], cycle: int) -> str:
    rows = []
    for d in months:
        for f, x in sorted(d["factions"].items()):
            rows.append({"cycle": cycle, "month": d["month"], "votes": d["votes"], "decisions": d["decisions"], "qualified": d["qualified"], "roll_calls": d["roll_calls"],
                         "faction": f, "in_roll": x["in_roll"], "cast": x["cast"], "with": x["with"], "against": x["against"],
                         "participation": x["participation"], "dissent": x["dissent"], "ai": x.get("ai")})
    return _csv(rows, ["cycle", "month", "votes", "decisions", "qualified", "roll_calls", "faction", "in_roll", "cast", "with", "against", "participation", "dissent", "ai"])
