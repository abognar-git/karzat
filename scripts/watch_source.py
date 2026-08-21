"""Ask the source whether it still says what it said — a few records a night, for years.

    python3 -m scripts.watch_source --calls 120       # one night's budget
    python3 -m scripts.watch_source --report          # what has changed so far, no calls

The whole project rests on one API, and nothing watches it. A record can be amended after the fact with no
notice: parliament.hu publishes no changelog, the payloads carry no version, and until now the cache simply
overwrote the old bytes on a refresh, so a rewrite upstream would have been absorbed in silence and a published
number would have moved with nothing anywhere to explain it. `WebApi.fetch` keeps the superseded payload now, so
every refresh is a check; this is what makes refreshes happen on purpose.

**It only asks about records that should never change.** A committee's membership, a running cycle's motion and
the vote listing all change legitimately and constantly — re-reading them proves nothing. The detail of a vote
held in 2019, though, is a closed historical fact: who voted how, what the result was. If that moves, it is
news, and nobody else is looking.

**It is deliberately slow.** 120 calls a night against 79,829 cached vote details is a two-year sweep, and that
is the right trade: this is a background integrity check on somebody else's server, not a crawl. Records are
taken in rotation, least-recently-checked first, so the coverage grows evenly rather than re-asking the same
thousand. The politeness floor and the circuit breaker are the client's own.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RAW = ROOT / "data" / "raw"
STATE = ROOT / "data" / "watch_state.json"
CHANGES = RAW / ".superseded" / "changes.jsonl"


def load_state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"checked": {}}


def changes() -> list[dict]:
    if not CHANGES.exists():
        return []
    out = []
    for line in CHANGES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def report() -> int:
    ch = changes()
    st = load_state()
    print(f"records the source has rewritten since watching began: {len(ch)}")
    by = {}
    for c in ch:
        by.setdefault(c["service"], []).append(c)
    for svc, cs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(cs):5d}  {svc}")
        for c in cs[:5]:
            print(f"           {c['seen']}  {c['key']}  {c['was']['bytes']}→{c['now']['bytes']} bytes")
    print(f"\nvote details checked at least once: {len(st.get('checked', {})):,} of "
          f"{len(list((RAW / 'szavazas').glob('*.xml'))):,}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--calls", type=int, default=120, help="how many records to re-ask (default 120)")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    if a.report:
        return report()

    from karzat.api import WebApi, ApiError
    from karzat.cli import load_dotenv
    load_dotenv()
    api = WebApi()
    st = load_state()
    checked = st.setdefault("checked", {})

    # least-recently-checked first, then never-checked, so the sweep is even rather than random re-asking
    files = sorted((RAW / "szavazas").glob("*.xml"), key=lambda p: checked.get(p.stem, ""))
    if not files:
        print("nothing cached to re-ask")
        return 0
    pick = files[:a.calls]
    before = len(changes())
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    asked = failed = 0
    for p in pick:
        # the cache key is the parameter the record was fetched with; the timestamp *is* the vote's identity
        try:
            api.fetch("szavazas", refresh=True, p_szavdatum=_param_from_key(p.stem))
            checked[p.stem] = stamp
            asked += 1
        except ApiError as e:
            failed += 1
            if failed >= 5:
                print(f"stopping after {failed} failures: {e}")
                break
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    found = len(changes()) - before
    print(f"re-asked {asked} vote records ({failed} failed) · the source had rewritten {found}")
    if found:
        for c in changes()[-found:]:
            print(f"   CHANGED  {c['key']}  {c['was']['bytes']}→{c['now']['bytes']} bytes")
    print(f"coverage: {len(checked):,} of {len(files):,} vote details asked at least once")
    return 0


def _param_from_key(stem: str) -> str:
    """'2019-06-04T13-12-50' → '2019.06.04.13:12:50', the form the service takes."""
    d, _, t = stem.partition("T")
    return f"{d.replace('-', '.')}.{t.replace('-', ':')}" if t else stem


if __name__ == "__main__":
    sys.exit(main())
