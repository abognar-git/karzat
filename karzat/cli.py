"""Command line for karzat.

    python -m karzat dry-run                      # print the request URLs, no network
    python -m karzat probe                        # one MP-list call: encoding, root tag, counts
    python -m karzat sync-votes --from 2026-09-01 --to 2026-09-30 [--no-details]
    python -m karzat sync-mps                     # kepviselok + kepviselo for every p_azon
    python -m karzat freshness [--ckl 43] [--fetch]   # the sentence the site is allowed to show
    python -m karzat inspect data/raw/szavazas/2026-09-15T09-37-07.xml   # cached XML -> JSON
    python -m karzat fingerprint tests/fixtures/*.xml # digest table for tests/test_golden.py
    python -m karzat load [--db data/karzat.sqlite] [--since 2026-05-01]   # rebuild SQLite from the cache
    python -m karzat stats [--db data/karzat.sqlite]  # counts and sanity queries

The token comes from PARLAMENT_API_TOKEN (environment or a local .env file).
Successful sync runs record data/sync_state.json; `freshness` reads it and writes
data/freshness.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from .api import SERVICES, TOKEN_ENV, ApiError, WebApi, build_url, mask_token
from .freshness import BUDAPEST, assess, now_budapest, sitting_days_from_ulesnap
from .xmlutil import (
    as_list,
    declared_encoding,
    fmt_date,
    parse_ts,
    parse_xml,
    slug_to_ts,
    to_dict,
    ts_to_slug,
)


def _data_dir(cache: Path) -> Path:
    """data/raw -> data ; anything else -> its parent."""
    return cache.parent if cache.name == "raw" else cache


def _write_sync_state(cache: Path, cmd: str, api: WebApi, **extra) -> Path:
    """Record a successful sync run. Only called when the run completed without a fatal error."""
    d = _data_dir(cache)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "sync_state.json"
    state = {"last_sync_at": now_budapest().isoformat(), "cmd": cmd,
             "live_calls": api.live_calls, "cache_hits": api.cache_hits, **extra}
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _read_sync_state(cache: Path) -> dict | None:
    path = _data_dir(cache) / "sync_state.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env loader (KEY=VALUE lines, no quoting rules) so python-dotenv is optional."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _iso(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


# -- commands ---------------------------------------------------------------------------

def cmd_dry_run(_: argparse.Namespace) -> int:
    today = date.today()
    examples = [
        ("kepviselok", {}),
        ("kepviselo", {"p_azon": "z004"}),
        ("szavazasok", {"p_datum_tol": fmt_date(today - timedelta(days=30)), "p_datum_ig": fmt_date(today)}),
        ("szavazas", {"p_szavdatum": "2026.09.15.09:37:07"}),
        ("iromanyok", {"p_all": "F"}),
        ("iromany", {"p_izon": "12345"}),
        ("bizottsagok", {"p_aktual": "i"}),
        ("ulesnap", {"p_ckl": 43}),
        ("felszolalasok", {"p_ckl": 43, "p_nap": 1}),
    ]
    print("Services known to this client:", ", ".join(SERVICES))
    for svc, params in examples:
        print(mask_token(build_url(svc, params, token="TOKEN")))
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    api = WebApi(cache_dir=args.cache)
    try:
        raw = api.fetch("kepviselok", refresh=args.refresh)
    except ApiError as e:
        print(f"probe failed: {e}", file=sys.stderr)
        return 2
    root = parse_xml(raw)
    print(f"bytes            {len(raw)}")
    print(f"declared enc     {declared_encoding(raw)}")
    print(f"root tag         <{root.tag}> attrs={dict(root.attrib)}")
    kids = list(root)
    tags = sorted({k.tag for k in kids})
    print(f"children         {len(kids)} (tags: {', '.join(tags)})")
    if kids:
        first = kids[0]
        print(f"first child      <{first.tag}> attrs={dict(first.attrib)}")
        print("first child dump " + json.dumps(to_dict(first), ensure_ascii=False)[:600])
    print(f"cache            {api.cache_path('kepviselok', {})}")
    print(f"live calls={api.live_calls} cache hits={api.cache_hits}")
    return 0


def _vote_elements(root):
    """Yield <szavazas idopont=...> elements wherever they sit in the list payload."""
    for el in root.iter():
        if el.tag == "szavazas" and el.get("idopont"):
            yield el


def cmd_sync_votes(args: argparse.Namespace) -> int:
    api = WebApi(cache_dir=args.cache)
    d_from, d_to = args.date_from, args.date_to
    if d_to < d_from:
        print("--to must not be before --from", file=sys.stderr)
        return 2
    total_listed = 0
    total_detail = 0
    # The manual gives no maximum window; walk month-sized windows to keep payloads sane.
    cur = d_from
    while cur <= d_to:
        nxt = min(d_to, date(cur.year + (cur.month // 12), cur.month % 12 + 1, 1) - timedelta(days=1))
        try:
            raw = api.fetch("szavazasok", p_datum_tol=fmt_date(cur), p_datum_ig=fmt_date(nxt))
        except ApiError as e:
            print(f"list {cur}..{nxt}: {e}", file=sys.stderr)
            return 2
        votes = list(_vote_elements(parse_xml(raw)))
        total_listed += len(votes)
        print(f"{cur}..{nxt}: {len(votes)} votes listed")
        if not args.no_details:
            for el in votes:
                idopont = el.get("idopont")
                try:
                    parse_ts(idopont)
                except ValueError:
                    print(f"  skip: unexpected idopont {idopont!r}", file=sys.stderr)
                    continue
                try:
                    api.fetch("szavazas", p_szavdatum=idopont)
                    total_detail += 1
                except ApiError as e:
                    print(f"  detail {idopont}: {e}", file=sys.stderr)
        cur = nxt + timedelta(days=1)
    print(f"done: {total_listed} listed, {total_detail} details cached; "
          f"live calls={api.live_calls} cache hits={api.cache_hits}")
    state = _write_sync_state(args.cache, "sync-votes", api, date_from=d_from.isoformat(),
                              date_to=d_to.isoformat(), listed=total_listed, details=total_detail)
    print(f"sync state -> {state}")
    return 0


def _mp_ids(root) -> list[str]:
    ids: list[str] = []
    for el in root.iter():
        for key in ("p_azon", "azon", "id"):
            if el.tag.startswith("kepviselo") and el.get(key):
                ids.append(el.get(key))
                break
    return ids


def cmd_sync_mps(args: argparse.Namespace) -> int:
    api = WebApi(cache_dir=args.cache)
    try:
        root = parse_xml(api.fetch("kepviselok"))
    except ApiError as e:
        print(f"kepviselok: {e}", file=sys.stderr)
        return 2
    ids = _mp_ids(root)
    print(f"{len(ids)} MP ids found in kepviselok")
    if not ids:
        print("could not locate p_azon attributes — inspect the cached XML and adjust _mp_ids()",
              file=sys.stderr)
        return 3
    ok = 0
    for azon in ids:
        try:
            api.fetch("kepviselo", p_azon=azon)
            ok += 1
        except ApiError as e:
            print(f"  {azon}: {e}", file=sys.stderr)
    print(f"done: {ok}/{len(ids)} MP records cached; live calls={api.live_calls} cache hits={api.cache_hits}")
    state = _write_sync_state(args.cache, "sync-mps", api, mps=len(ids), ok=ok)
    print(f"sync state -> {state}")
    return 0


def _newest_cached_vote(cache: Path) -> datetime | None:
    """Max vote timestamp among cached szavazas/*.xml files (names are ts slugs)."""
    folder = cache / "szavazas"
    if not folder.exists():
        return None
    best: datetime | None = None
    for p in folder.glob("*.xml"):
        try:
            dt = parse_ts(slug_to_ts(p.stem)).replace(tzinfo=BUDAPEST)
        except ValueError:
            continue
        if best is None or dt > best:
            best = dt
    return best


def cmd_freshness(args: argparse.Namespace) -> int:
    api = WebApi(cache_dir=args.cache)
    sitting_days = None
    try:
        if args.fetch:
            raw = api.fetch("ulesnap", refresh=True, p_ckl=args.ckl)
        else:
            path = api.cache_path("ulesnap", {"p_ckl": args.ckl})
            raw = path.read_bytes() if path.exists() else None
        if raw is not None:
            sitting_days = sitting_days_from_ulesnap(to_dict(parse_xml(raw)))
            if not sitting_days:
                print("ulesnap payload held no recognisable dates — adjust sitting_days_from_ulesnap()",
                      file=sys.stderr)
                sitting_days = None
    except ApiError as e:
        print(f"ulesnap: {e}", file=sys.stderr)
    state = _read_sync_state(args.cache)
    last_sync = datetime.fromisoformat(state["last_sync_at"]) if state and state.get("last_sync_at") else None
    fr = assess(sitting_days=sitting_days, newest_vote_at=_newest_cached_vote(args.cache),
                last_sync_at=last_sync, now=now_budapest())
    out = _data_dir(args.cache) / "freshness.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fr.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"status   {fr.status}")
    print(f"EN       {fr.sentence_en}")
    print(f"HU       {fr.sentence_hu}")
    print(f"written  {out}")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    raw = Path(args.file).read_bytes()
    root = parse_xml(raw)
    print(json.dumps({root.tag: to_dict(root)}, ensure_ascii=False, indent=2))
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    from .load import DEFAULT_DB, build_from_cache
    from pathlib import Path as _P
    snap = _P(__file__).resolve().parent.parent / "reference" / "wikidata" / "members_ckl43.json"
    stats = build_from_cache(args.cache, args.db or DEFAULT_DB, wikidata_snapshot=snap, since=args.since)
    for k, v in stats.items():
        print(f"{k:24} {v}")
    print(f"written {args.db or DEFAULT_DB}")
    return 1 if stats.get("fk_problems") else 0


def cmd_stats(args: argparse.Namespace) -> int:
    from .load import DEFAULT_DB, summary
    db = args.db or DEFAULT_DB
    if not Path(db).exists():
        print(f"{db} not found — run `python3 -m karzat load` first", file=sys.stderr)
        return 2
    st = summary(Path(db))
    for k, v in st.items():
        print(f"{k:28} {v}")
    if args.json:
        out = _data_dir(args.cache) / "derived" / "db_summary.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        st_json = {k: (v if not isinstance(v, list) else [list(r) for r in v]) for k, v in st.items()}
        try:
            st_json["db"] = str(Path(db).resolve().relative_to(Path.cwd()))   # a committed file must not name this machine's home
        except ValueError:
            st_json["db"] = Path(db).name
        out.write_text(json.dumps(st_json, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"written {out}")
    return 0


def cmd_fingerprint(args: argparse.Namespace) -> int:
    from .fingerprint import table
    paths = sorted(Path(f) for f in args.files)
    missing = [p for p in paths if not p.exists()]
    if missing:
        print(f"no such file(s): {', '.join(map(str, missing))}", file=sys.stderr)
        return 2
    print(table(paths))
    return 0


# -- entry point ------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser(prog="karzat", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cache", type=Path, default=Path("data/raw"), help="raw XML cache directory")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("dry-run", help="print request URLs without touching the network").set_defaults(fn=cmd_dry_run)

    sp = sub.add_parser("probe", help="fetch the MP list once and describe the payload")
    sp.add_argument("--refresh", action="store_true", help="bypass the cache")
    sp.set_defaults(fn=cmd_probe)

    sv = sub.add_parser("sync-votes", help="cache vote lists and vote details for a date range")
    sv.add_argument("--from", dest="date_from", type=_iso, required=True, help="YYYY-MM-DD")
    sv.add_argument("--to", dest="date_to", type=_iso, default=date.today(), help="YYYY-MM-DD (default today)")
    sv.add_argument("--no-details", action="store_true", help="only the lists, no per-vote calls")
    sv.set_defaults(fn=cmd_sync_votes)

    sm = sub.add_parser("sync-mps", help="cache the MP list and every MP record")
    sm.set_defaults(fn=cmd_sync_mps)

    sf = sub.add_parser("freshness", help="compute the freshness sentence from cache + sync state")
    sf.add_argument("--ckl", type=int, default=43, help="parliamentary cycle for ulesnap (default 43)")
    sf.add_argument("--fetch", action="store_true", help="refresh ulesnap.cgi from the API (needs token)")
    sf.set_defaults(fn=cmd_freshness)

    si = sub.add_parser("inspect", help="print a cached XML file as JSON")
    si.add_argument("file")
    si.set_defaults(fn=cmd_inspect)

    sg = sub.add_parser("fingerprint", help="digest table for tests/test_golden.py")
    sg.add_argument("files", nargs="+")
    sg.set_defaults(fn=cmd_fingerprint)

    sl = sub.add_parser("load", help="rebuild the SQLite database from the cache")
    sl.add_argument("--db", type=Path, default=None, help="output path (default data/karzat.sqlite)")
    sl.add_argument("--since", default=None, help="ignore vote lists/details before this ISO date")
    sl.set_defaults(fn=cmd_load)

    ss = sub.add_parser("stats", help="counts and sanity queries over the database")
    ss.add_argument("--db", type=Path, default=None)
    ss.add_argument("--json", action="store_true", help="also write data/derived/db_summary.json (committed; the README is gated on it)")
    ss.set_defaults(fn=cmd_stats)

    args = p.parse_args(argv)
    needs_token = args.cmd in ("probe", "sync-votes", "sync-mps") or (args.cmd == "freshness" and args.fetch)
    if needs_token and not os.environ.get(TOKEN_ENV):
        print(f"{TOKEN_ENV} is not set — copy .env.example to .env once the token arrives "
              f"(dry-run and inspect work without it)", file=sys.stderr)
        return 2
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
