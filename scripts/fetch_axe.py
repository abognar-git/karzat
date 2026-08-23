#!/usr/bin/env python3
"""Vendor axe-core, the accessibility engine the audit runs, with its bytes pinned by hash.

    python3 -m scripts.fetch_axe            # fetch if missing, verify if present
    python3 -m scripts.fetch_axe --force    # refetch and re-lock

axe-core is Deque's rule engine — the same one behind Lighthouse's accessibility score and every browser
extension that claims to check a page. It is one self-contained file, MPL-2.0, and it runs inside the page.

Why vendored rather than installed. This repository has no `package.json` and no `node_modules`, and that is
a deliberate shape: the tooling is Python plus a bare `node` binary. Adding a dependency tree so that a build
gate can run would make the gate the largest thing in the project. One pinned file does the same work.

Why pinned. Whatever this file contains gets executed against every sampled page, and its verdict decides
whether a build passes. "The CDN served something different today" is exactly the event worth stopping for —
the same reasoning as `fetch_duckdb.py`, which vendors code served to readers. This one is never served: it
lives under `reference/`, is used at audit time only, and nothing in `site/` links to it.

The version is written out rather than tracked: an audit whose engine moves under it reports a different set
of violations from one run to the next, and a gate that changes its mind without anyone changing the site is
a gate nobody will trust for long. Upgrading is a deliberate act — bump AXE_VERSION, `--force`, and read what
moved.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AXE_VERSION = "4.13.0"
OUT = ROOT / "reference" / "axe"
LOCK = ROOT / "reference" / "axe.lock"
FILES = {
    "axe.min.js": f"https://cdn.jsdelivr.net/npm/axe-core@{AXE_VERSION}/axe.min.js",
    "LICENSE": f"https://cdn.jsdelivr.net/npm/axe-core@{AXE_VERSION}/LICENSE",
}


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "karzat/vendor"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="refetch even when present, and rewrite the lock")
    a = ap.parse_args(argv)

    want: dict[str, str] = {}
    if LOCK.exists():
        for line in LOCK.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                h, name = line.split(None, 1)
                want[name.strip()] = h

    OUT.mkdir(parents=True, exist_ok=True)
    got, changed = {}, []
    for name, url in FILES.items():
        p = OUT / name
        if p.exists() and not a.force:
            raw = p.read_bytes()
        else:
            print(f"  fetching {name} …", flush=True)
            raw = get(url)
            p.write_bytes(raw)
        h = hashlib.sha256(raw).hexdigest()
        got[name] = h
        if name in want and want[name] != h:
            changed.append(name)
        print(f"  {h[:16]}  {len(raw) / 1e6:6.2f} MB  {name}")

    if changed:
        print(f"\nHASH MISMATCH on {', '.join(changed)} — the CDN served something other than what is recorded.")
        print("This engine decides whether a build passes its accessibility gate. Nothing was accepted.")
        return 1
    if not want or a.force:
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        LOCK.write_text(f"# axe-core {AXE_VERSION} (MPL-2.0), the engine scripts/audit_a11y.py runs.\n"
                        f"# Fetched by scripts/fetch_axe.py. Build tooling: never served to a reader.\n"
                        + "".join(f"{h}  {n}\n" for n, h in sorted(got.items())), encoding="utf-8")
        print(f"\nwritten {LOCK}")
    else:
        print(f"\nboth files match {LOCK}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
