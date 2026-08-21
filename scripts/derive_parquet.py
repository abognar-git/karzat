"""The whole roll-call corpus as three Parquet files, for anyone who would rather write SQL than click.

    python3 -m scripts.derive_parquet
    python3 -m scripts.derive_parquet --out site/adatok

The site publishes a CSV per cycle per table, which is the right shape for a spreadsheet and the wrong one for a
question that spans 1990 to 2026: nine downloads and a join before you start. These three files are the same
data in one piece — 79,829 votes, 17.4 million cast positions, the people who cast them, and what they said
from the floor. Eight tables, not three; the argparse help and this sentence are the only places that number
is written down, and both are checked against the manifest by the tests.

Parquet rather than CSV for two reasons, and the second is the interesting one. It is columnar and dictionary
encoded, so the three columns that dominate the row count — member, faction, position — cost almost nothing:
seventeen million positions come out smaller than the CSVs they replace. And it is *seekable*: a reader can ask
one question over the whole corpus and pull only the row groups that answer it, over ordinary HTTP range
requests. That is what makes the browser page possible without a server, and it is why the row groups here are
sized deliberately rather than left to the default.

The column names are Hungarian, like the table names and like every value in them. An English schema on a
Hungarian site is a seam the reader has no reason to step over: somebody asking who voted against their own
party should not first have to learn that the word for it here is `faction`.

Every column is a fact the record carries. Nothing is inferred, nothing is scored, and the position column keeps
all seven values the House distinguishes rather than collapsing them into yes/no/abstain — the collapse is the
single most common way this data is misread, and a file that performs it silently would spread the mistake.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.build_site import CURRENT_CYCLE   # the unsuffixed derived files belong to this one

ROW_GROUP = 200_000          # ~1-3 MB compressed per group: small enough that a range request is worth making


def speech_rows(cycle: int) -> list:
    """The sitting days' speech lists for one cycle, or nothing where the record does not reach.

    The unsuffixed `speeches.json.gz` is the CURRENT cycle's file and nobody else's. The first version of this
    fell back to it whenever a per-cycle file was missing, which is true of cycles 34 and 35 — so the export
    gave the 1990-94 and 1994-98 parliaments 4,651 speeches each, dated 2026, spoken by members who were not
    yet born into that House. 9,302 rows of fiction, and nothing anywhere would have contradicted them: the
    file parsed, the counts looked plausible, the dates were only wrong if you looked.

    Where the record does not reach, the answer is no rows. A cycle with no speeches is a fact about the
    archive, and the coverage page says so."""
    import gzip
    import json as _json
    p = ROOT / "data" / "derived" / (f"speeches_ckl{cycle}.json.gz" if cycle != CURRENT_CYCLE
                                     else "speeches.json.gz")
    if not p.exists():
        return []
    d = _json.loads(gzip.decompress(p.read_bytes()))
    return (d.get("speeches") if isinstance(d, dict) else d) or []


def bill_rows() -> list:
    """Bills as the House records them: what was submitted, by whom, and what became of it.

    Only the current cycle's are fetched, and the table said so nowhere — a reader joining `iromanyok` to
    `szavazas_iromany` across the corpus would have got the current cycle's rows and silence about the rest.
    The `ciklus` column makes the scope a fact in the data rather than something you have to already know."""
    import json as _json
    p = ROOT / "data" / "derived" / "iromany_records.json"
    if not p.exists():
        return []
    recs = _json.loads(p.read_text(encoding="utf-8"))["records"]
    it = list(recs.values()) if isinstance(recs, dict) else recs
    return [{"ciklus": CURRENT_CYCLE, "iromany": r.get("szam"), "izon": r.get("izon"), "cim": r.get("title"),
             "tipus": r.get("type"), "fo_tipus": r.get("main_type"), "jelleg": r.get("nature"),
             "benyujtva": r.get("submitted_on"), "statusz": r.get("status"),
             "targyalas": r.get("procedure_mode"), "kihirdetes": r.get("promulgation"),
             "benyujto": ", ".join(r.get("submitters") or []) or None,
             "benyujto_fajta": r.get("submitter_kind"), "cimzett": r.get("addressee")} for r in it]


def switch_rows() -> list:
    """Faction switches. `bizonyitek` is this project's own verdict on whether the roll calls can testify to the
    date — most of the record cannot, and a table that omitted the column would imply they all can."""
    import json as _json
    p = ROOT / "data" / "derived" / "faction_switches.json"
    if not p.exists():
        return []
    return [{"kepviselo_id": r.get("azon"), "nev": r.get("name"), "ciklus_cimke": r.get("ciklus"),
             "honnan": r.get("from"), "hova": r.get("to"), "mikortol": r.get("since"),
             "valtas_napja": r.get("on"), "meddig": r.get("until"),
             "bizonyitek": r.get("verdict"), "csuszas_nap": r.get("lag_days")}
            for r in _json.loads(p.read_text(encoding="utf-8"))["items"]]


def committee_rows() -> list:
    """One row per person per committee, with the seat they hold in it."""
    import json as _json
    p = ROOT / "data" / "derived" / "committees.json"
    if not p.exists():
        return []
    recs = _json.loads(p.read_text(encoding="utf-8"))["records"]
    it = list(recs.values()) if isinstance(recs, dict) else recs
    rows = []
    for cm in it:
        for role, who in (("elnök", [cm.get("chair")] if cm.get("chair") else []),
                          ("alelnök", cm.get("vice_chairs") or []),
                          ("tag", cm.get("members") or [])):
            for m in who:
                rows.append({"bizottsag": cm.get("name"), "bizottsag_fajta": cm.get("type"),
                             "tisztseg": role, "kepviselo_id": m.get("azon"),
                             "nev": m.get("name"), "frakcio": m.get("faction")})
    return rows


def build(out: Path) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from scripts.build_site import load_inputs, available_cycles

    votes, pos, people, links, talk = [], [], [], [], []
    for c in available_cycles():
        inp = load_inputs(c)
        idx, st = inp["idx"], inp["store"]
        facs = st.get("factions") or []
        codes = st.get("codes") or {}
        for v in idx["votes"]:
            votes.append({
                "ciklus": c, "szavazas_id": v["ts"], "datum": v.get("on_date"), "ido": v.get("time"),
                "slug": v.get("slug"), "targy": (v.get("motions") or [{}])[0].get("title"),
                "iromany": (v.get("motions") or [{}])[0].get("iromany"),
                "iromany_db": len(v.get("motions") or []),
                "igen": v.get("igen"), "nem": v.get("nem"), "tartozkodott": v.get("tartozkodott"),
                "eredmeny": v.get("result_raw"), "fajta": v.get("kind"), "mod": v.get("mode"),
                "szabaly": (v.get("majority") or {}).get("rule"),
                "szukseges": (v.get("majority") or {}).get("needed"),
            })
            for k, m in enumerate(v.get("motions") or []):
                links.append({"ciklus": c, "szavazas_id": v["ts"], "sorszam": k + 1,
                              "iromany": m.get("iromany"), "targy": m.get("title")})
        day = {v["ts"]: v.get("on_date") for v in idx["votes"]}
        for ts, rows in (st.get("positions") or {}).items():
            d = day.get(ts)
            for r in rows:
                pos.append({"ciklus": c, "szavazas_id": ts, "datum": d, "kepviselo_id": r[0],
                            "frakcio": facs[r[1]] if r[1] is not None and r[1] < len(facs) else None,
                            "szavazat": codes.get(r[2], r[2])})
        for azon, m in (inp.get("mps") or {}).items():
            people.append({"ciklus": c, "kepviselo_id": azon, "nev": m.get("name"),
                           "frakcio": m.get("faction"), "mandatum": m.get("mandate_kind"),
                           "megye": m.get("county"), "oevk": m.get("constituency_no"),
                           "jelenlegi": bool(m.get("current"))})

        for r in speech_rows(c):
            talk.append({"ciklus": c, "ulesnap": r.get("ulnap"), "datum": r.get("date"),
                         "sorszam": r.get("seq"), "iromany": r.get("iromany"),
                         "kepviselo_id": r.get("azon"), "nev": r.get("name"), "frakcio": r.get("faction"),
                         "fajta": r.get("kind"), "szerep": r.get("role"), "kezdet": r.get("start"),
                         "hossz_mp": r.get("duration_s"), "ismetles": bool(r.get("reprint")),
                         "technikai": bool(r.get("technical"))})

    out.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, rows in (("szavazasok", votes), ("szavazatok", pos), ("kepviselok", people),
                       ("szavazas_iromany", links), ("felszolalasok", talk),
                       ("iromanyok", bill_rows()), ("frakciovaltasok", switch_rows()),
                       ("bizottsagi_tagsag", committee_rows())):
        if not rows:
            continue
        t = pa.Table.from_pylist(rows)
        p = out / f"{name}.parquet"
        pq.write_table(t, p, compression="zstd", compression_level=9,
                       row_group_size=ROW_GROUP, use_dictionary=True, write_statistics=True)
        written[name] = {"rows": t.num_rows, "bytes": p.stat().st_size, "columns": t.column_names}
        print(f"  {name:14} {t.num_rows:10,} rows  {p.stat().st_size / 1e6:8.1f} MB  {len(t.column_names)} columns")
    return {"built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "tables": written}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "site" / "adatok")
    a = ap.parse_args(argv)
    r = build(a.out)
    total = sum(v["bytes"] for v in r["tables"].values())
    print(f"  {'total':14} {sum(v['rows'] for v in r['tables'].values()):10,} rows  {total / 1e6:8.1f} MB")
    import json
    (ROOT / "data" / "derived" / "parquet.json").write_text(
        json.dumps(r, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
