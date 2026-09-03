"""Derive the footer's facts from the corpus → data/derived/tenyek.json.

    python3 -m scripts.derive_facts

Every fact is computed here, never typed, and carries the page that proves it. Two rules keep them honest:

  * **Scope.** A fact says which cycles it covers. The vote listing's 400-row cap truncated 54 months
    (reference/parlament/listakorlat.json), so any fact that ranks or counts votes is computed only over months the
    cap never touched — otherwise a "record" would be a record of what survived the cut, not of what happened.
    Facts about people, committees, speeches or a single named vote do not depend on that listing at all.
  * **No judgement.** Numbers and records only: who, how many, when. Never why, never whether it was good.

Deterministic: reads the SQLite built by `karzat load`, no clock, no network; the file is committed so the site
builds from it offline.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "karzat.sqlite"
OUT = ROOT / "data" / "derived" / "tenyek.json"
CAP = ROOT / "reference" / "parlament" / "listakorlat.json"
CYCLE_DIR = {43: "ckl43/"}


def hu(n: int | float) -> str:
    return f"{n:,.0f}".replace(",", " ")


def hu_date(iso: str) -> str:
    months = ["január", "február", "március", "április", "május", "június", "július",
              "augusztus", "szeptember", "október", "november", "december"]
    y, m, d = iso.split("-")
    return f"{y}. {months[int(m) - 1]} {int(d)}."


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args(argv)
    # A file is not a database. A stray zero-byte data/karzat.sqlite — one `sqlite3 <path>` with no
    # statement leaves one behind — passed this check for a fortnight and turned a clear "no database"
    # message into `OperationalError: no such table: vote` from inside a query, which reads like a schema
    # bug rather than a missing input. Ask the file whether it holds the table this script needs.
    if not DB.exists() or DB.stat().st_size == 0:
        print("no database — run: python3 -m karzat load --since 1990-01-01", file=sys.stderr)
        return 1
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vote'").fetchone():
        print(f"{DB} has no `vote` table — run: python3 -m karzat load --since 1990-01-01", file=sys.stderr)
        return 1
    db.row_factory = sqlite3.Row
    capped = set(json.loads(CAP.read_text(encoding="utf-8")).get("months_originally_truncated") or []) if CAP.exists() else set()
    # months the listing cap never touched: only these may carry a ranking or a total
    safe = "substr(v.on_date,1,7) NOT IN (%s)" % (",".join("?" * len(capped)) or "''")
    args = tuple(sorted(capped))
    facts: list[dict] = []

    def add(hu_text: str, scope: str, href: str = "", **nums):
        facts.append({"hu": hu_text, "scope": scope, "href": href, "numbers": nums})

    def q(sql, params=()):
        return db.execute(sql, params).fetchall()

    # 1 — a chamber that voted as one: every member, one position
    for r in q(f"""SELECT v.ts, v.slug, v.ckl, v.on_date, v.igen, v.nem, v.osszes_szavazat,
                          (SELECT m.title FROM vote_motion m WHERE m.vote_ts = v.ts LIMIT 1) title
                   FROM vote v WHERE v.kind='dontes' AND v.secret=0 AND {safe}
                     AND v.osszes_szavazat >= 190 AND (v.igen = v.osszes_szavazat OR v.nem = v.osszes_szavazat)
                   ORDER BY v.osszes_szavazat DESC LIMIT 1""", args):
        how = "igennel" if r["igen"] == r["osszes_szavazat"] else "nemmel"
        add(f'{hu_date(r["on_date"])}: mind a {hu(r["osszes_szavazat"])} szavazó képviselő {how} szavazott — a Ház egyhangúlag döntött.',
            f'{r["ckl"]}. ciklus', f'ckl{r["ckl"]}/szavazas/{r["slug"]}.html', votes=r["osszes_szavazat"])

    # 2 — the narrowest decision
    # margin >= 0 matters: a handful of votes the record calls Elfogadva compute to a negative margin (the site's own
    # "disagreements" count). Those are a discrepancy to show on their own page, never "the narrowest passage".
    for r in q(f"""SELECT v.slug, v.ckl, v.on_date, v.igen, v.nem, v.needed, v.margin,
                          (SELECT m.iromany FROM vote_motion m WHERE m.vote_ts=v.ts LIMIT 1) iromany
                   FROM vote v WHERE v.kind='dontes' AND v.passed=1 AND v.margin >= 0 AND {safe}
                   ORDER BY v.margin ASC, v.on_date DESC LIMIT 1""", args):
        if r["margin"] == 0:
            text = (f'{hu_date(r["on_date"])}: pontosan a szükséges {hu(r["needed"])} igen gyűlt össze — '
                    f'egyetlen szavazattal sem több.')
        else:
            gap = "egyetlen szavazaton" if r["margin"] == 1 else f'{hu(r["margin"])} szavazaton'
            text = (f'{hu_date(r["on_date"])}: {hu(r["igen"])} igen a szükséges {hu(r["needed"])} ellenében — '
                    f'{gap} múlt, hogy a javaslat átment.')
        add(text, f'{r["ckl"]}. ciklus', f'ckl{r["ckl"]}/szavazas/{r["slug"]}.html', margin=r["margin"])

    # 3 — more yes than no, and still rejected: the qualified threshold at work
    rows = q(f"""SELECT COUNT(*) n FROM vote v WHERE v.kind='dontes' AND v.passed=0 AND v.igen > v.nem
                 AND v.majority_rule IS NOT NULL AND v.majority_rule <> 'egyszeru' AND {safe}""", args)
    if rows and rows[0]["n"]:
        add(f'{hu(rows[0]["n"])} olyan döntés volt, ahol több igen szavazat esett, mint nem — a javaslat mégis elbukott, '
            f'mert minősített többség kellett volna hozzá.', "a nem csonkolt hónapok", "modszer/index.html#tobbseg", n=rows[0]["n"])

    # 4 — the day the House voted most
    for r in q(f"""SELECT v.on_date, v.ckl, COUNT(*) n FROM vote v WHERE {safe}
                   GROUP BY v.on_date ORDER BY n DESC LIMIT 1""", args):
        add(f'{hu_date(r["on_date"])}: {hu(r["n"])} szavazás egyetlen ülésnapon.',
            f'{r["ckl"]}. ciklus', f'ckl{r["ckl"]}/index.html', n=r["n"])

    # 5 — the longest service: most cycles held
    for r in q("""SELECT m.azon, m.name, COUNT(DISTINCT d.ckl) c FROM mp m JOIN mp_mandate d ON d.mp_azon=m.azon
                  GROUP BY m.azon ORDER BY c DESC, m.name LIMIT 1"""):
        add(f'{r["name"]} {hu(r["c"])} ciklusban volt országgyűlési képviselő — a legtöbben az 1990 óta megválasztottak közül.',
            "1990–2026", f'szemely/{r["azon"]}.html', cycles=r["c"])

    # 6 — the most roll calls a single member has answered
    for r in q(f"""SELECT p.mp_azon azon, MAX(m.name) name, COUNT(*) n FROM vote_position p
                   JOIN vote v ON v.ts=p.vote_ts JOIN mp m ON m.azon=p.mp_azon
                   WHERE p.position IN ('igen','nem','tartozkodott') AND {safe}
                   GROUP BY p.mp_azon ORDER BY n DESC LIMIT 1""", args):
        add(f'{r["name"]} {hu(r["n"])} név szerinti szavazáson adta le a voksát — több, mint bárki más.',
            "a nem csonkolt hónapok, 1998 óta", f'szemely/{r["azon"]}.html', n=r["n"])

    # 7 — the longest speech the record keeps, in characters
    for r in q("""SELECT t.ckl, t.nap ulnap, t.seq, t.speaker, t.chars, s.on_date date FROM speech_text t
                  LEFT JOIN speech s ON s.ckl=t.ckl AND s.nap=t.nap AND s.seq=t.seq
                  ORDER BY t.chars DESC LIMIT 1"""):
        when = f', {hu_date(r["date"])}' if r["date"] else "."          # hu_date already ends in a full stop
        add(f'A leghosszabb betöltött felszólalás {hu(r["chars"])} karakter: {r["speaker"]}{when}',
            f'{r["ckl"]}. ciklus (a szövegek 2022 óta)', f'ckl{r["ckl"]}/felszolalas/{r["ulnap"]}-{r["seq"]}.html', chars=r["chars"])

    # 8 — how much of the record is the chair speaking
    for r in q("""SELECT COUNT(*) tot, SUM(kind='ülésvezetés') ulv FROM speech"""):
        if r["tot"]:
            add(f'A jegyzőkönyv {hu(r["tot"])} felszólalás-sorából {hu(r["ulv"])} az ülésvezetés — a Házban elhangzottak '
                f'{r["ulv"] * 100 // r["tot"]} százaléka az ülés vezetése.', "1998–2026", "ckl43/felszolalas/index.html", n=r["ulv"])

    # 9 — the committee that draws the most members
    # subcommittee is '' rather than NULL for a seat on the committee itself — an IS NULL test silently matches nothing
    for r in q("""SELECT committee, COUNT(DISTINCT mp_azon) n FROM mp_committee
                  WHERE to_date IS NULL AND (subcommittee IS NULL OR subcommittee = '')
                  GROUP BY committee ORDER BY n DESC LIMIT 1"""):
        add(f'A legnépesebb bizottság most a {r["committee"]}: {hu(r["n"])} tag.', "43. ciklus", "ckl43/bizottsag/index.html", n=r["n"])

    # 10 — the vote that split the House's own sides most: dissenters against their faction's plurality
    for r in q(f"""SELECT v.slug, v.ckl, v.on_date, COUNT(*) n FROM vote_position p
                   JOIN vote v ON v.ts=p.vote_ts
                   JOIN vote_faction_majority fm ON fm.vote_ts=p.vote_ts AND fm.faction_id=p.faction_id
                   WHERE p.position IN ('igen','nem','tartozkodott') AND p.position <> fm.majority_position AND {safe}
                   GROUP BY v.ts ORDER BY n DESC LIMIT 1""", args):
        add(f'{hu_date(r["on_date"])}: {hu(r["n"])} képviselő szavazott a saját frakciója többségétől eltérően — '
            f'ennél többen egyetlen név szerinti szavazáson sem.',
            f'{r["ckl"]}. ciklus', f'ckl{r["ckl"]}/szavazas/{r["slug"]}.html', n=r["n"])

    # 11 — the House at night
    for r in q(f"""SELECT COUNT(*) n FROM vote v WHERE CAST(substr(v.ts,12,2) AS INTEGER) >= 22 AND {safe}""", args):
        if r["n"]:
            add(f'{hu(r["n"])} szavazás esett este tíz óra utánra.', "a nem csonkolt hónapok", "modszer/index.html", n=r["n"])

    # 12 — the bill the House returned to most often
    for r in q(f"""SELECT m.iromany, MAX(v.ckl) ckl, COUNT(*) n FROM vote_motion m JOIN vote v ON v.ts=m.vote_ts
                   WHERE m.iromany IS NOT NULL AND {safe} GROUP BY m.iromany ORDER BY n DESC LIMIT 1""", args):
        num = "".join(ch for ch in (r["iromany"] or "").split("/")[-1] if ch.isdigit())
        add(f'A {r["iromany"]} jelű irományról {hu(r["n"])} alkalommal szavazott a Ház — több szavazás egyetlen irományról sem szólt.',
            f'{r["ckl"]}. ciklus', f'ckl{r["ckl"]}/iromany/{num}.html' if num else "", n=r["n"])

    db.close()
    out = {"count": len(facts), "note": "Minden állítás a korpuszból számolt; a rangsorok csak azokra a hónapokra "
                                        "vonatkoznak, amelyeket a lista 400-as korlátja nem csonkolt.",
           "facts": facts}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"written {OUT}: {len(facts)} facts")
    for f in facts:
        print(f"  · {f['hu']}  [{f['scope']}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
