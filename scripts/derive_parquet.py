"""The whole roll-call corpus as three Parquet files, for anyone who would rather write SQL than click.

    python3 -m scripts.derive_parquet
    python3 -m scripts.derive_parquet --out site/adatok

The site publishes a CSV per cycle per table, which is the right shape for a spreadsheet and the wrong one for a
question that spans 1990 to 2026: nine downloads and a join before you start. These three files are the same
data in one piece — 79,829 votes, 17.4 million cast positions, and the people who cast them.

Parquet rather than CSV for two reasons, and the second is the interesting one. It is columnar and dictionary
encoded, so the three columns that dominate the row count — member, faction, position — cost almost nothing:
seventeen million positions come out smaller than the CSVs they replace. And it is *seekable*: a reader can ask
one question over the whole corpus and pull only the row groups that answer it, over ordinary HTTP range
requests. That is what makes the browser page possible without a server, and it is why the row groups here are
sized deliberately rather than left to the default.

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

ROW_GROUP = 200_000          # ~1-3 MB compressed per group: small enough that a range request is worth making


def build(out: Path) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from scripts.build_site import load_inputs, available_cycles

    votes, pos, people = [], [], []
    for c in available_cycles():
        inp = load_inputs(c)
        idx, st = inp["idx"], inp["store"]
        facs = st.get("factions") or []
        codes = st.get("codes") or {}
        for v in idx["votes"]:
            votes.append({
                "cycle": c, "ts": v["ts"], "date": v.get("on_date"), "time": v.get("time"),
                "slug": v.get("slug"), "subject": (v.get("motions") or [{}])[0].get("title"),
                "motion": (v.get("motions") or [{}])[0].get("iromany"),
                "igen": v.get("igen"), "nem": v.get("nem"), "tartozkodott": v.get("tartozkodott"),
                "result": v.get("result_raw"), "kind": v.get("kind"), "mode": v.get("mode"),
                "rule": (v.get("majority") or {}).get("rule"), "needed": (v.get("majority") or {}).get("needed"),
            })
        day = {v["ts"]: v.get("on_date") for v in idx["votes"]}
        for ts, rows in (st.get("positions") or {}).items():
            d = day.get(ts)
            for r in rows:
                pos.append({"cycle": c, "ts": ts, "date": d, "mp": r[0],
                            "faction": facs[r[1]] if r[1] is not None and r[1] < len(facs) else None,
                            "position": codes.get(r[2], r[2])})
        for azon, m in (inp.get("mps") or {}).items():
            people.append({"cycle": c, "mp": azon, "name": m.get("name"), "faction": m.get("faction"),
                           "mandate_kind": m.get("mandate_kind"), "county": m.get("county"),
                           "constituency_no": m.get("constituency_no"), "current": bool(m.get("current"))})

    out.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, rows in (("szavazasok", votes), ("szavazatok", pos), ("kepviselok", people)):
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
