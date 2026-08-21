"""Vendor DuckDB-WASM onto our own origin, once, with every byte pinned by hash.

    python3 -m scripts.fetch_duckdb            # fetch if missing, verify if present
    python3 -m scripts.fetch_duckdb --force    # refetch and reprint the hashes

The SQL page runs the database in the reader's browser. It could load that from a public CDN in one line, and it
does not, for a reason that matters more here than the convenience: this site sets no cookie, runs no analytics
and calls no third party, so a reader's visit is known to nobody but the log this project keeps and prunes. One
`<script src="https://cdn…">` would quietly undo that for every visitor on every page load. So the runtime lives
on our own origin.

Vendoring somebody else's binary earns the two obligations below.

**Pinned.** Every file's SHA-256 is recorded here. A refetch that does not match is an error, not a warning:
this is executable code served to readers, and "the CDN gave me something different today" is exactly the event
worth stopping for.

**Not in the repository.** 35 MB of WebAssembly does not belong in a git history — `site/assets/duckdb/` is
ignored, and this script rebuilds it from the record below. A clone without it still builds; the SQL page checks
and says the runtime is not installed rather than half-working.

The ESM bundles come from jsDelivr's `+esm` transform, whose imports are absolute CDN paths. They are rewritten
to relative ones so that nothing on the page reaches off-origin at run time — which is the whole point, and is
checked by a test that greps the vendored files for a remaining `https://` import.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "assets" / "duckdb"
CDN = "https://cdn.jsdelivr.net"

# name on disk -> (url, sha256). The hashes are the record: a mismatch stops the script.
FILES = {
    "duckdb.mjs":         (f"{CDN}/npm/@duckdb/duckdb-wasm@1.29.0/+esm", None),
    "apache-arrow.mjs":   (f"{CDN}/npm/apache-arrow@17.0.0/+esm", None),
    "flatbuffers.mjs":    (f"{CDN}/npm/flatbuffers@24.3.25/+esm", None),
    "tslib.mjs":          (f"{CDN}/npm/tslib@2.6.3/+esm", None),
    "duckdb-eh.wasm":     (f"{CDN}/npm/@duckdb/duckdb-wasm@1.29.0/dist/duckdb-eh.wasm", None),
    "duckdb-eh.worker.js": (f"{CDN}/npm/@duckdb/duckdb-wasm@1.29.0/dist/duckdb-browser-eh.worker.js", None),
}
# jsDelivr's absolute import paths -> our own files, so nothing reaches off-origin once served
REWRITE = {
    "/npm/apache-arrow@17.0.0/+esm": "./apache-arrow.mjs",
    "/npm/flatbuffers@24.3.25/+esm": "./flatbuffers.mjs",
    "/npm/tslib@2.6.3/+esm": "./tslib.mjs",
}
LOCK = ROOT / "reference" / "duckdb-wasm.lock"


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "karzat/vendor"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    want = {}
    if LOCK.exists():
        for line in LOCK.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                h, name = line.split(None, 1)
                want[name.strip()] = h

    OUT.mkdir(parents=True, exist_ok=True)
    got, changed = {}, []
    for name, (url, _) in FILES.items():
        p = OUT / name
        if p.exists() and not a.force:
            raw = p.read_bytes()
        else:
            print(f"  fetching {name} …", flush=True)
            raw = get(url)
            if name.endswith(".mjs"):
                text = raw.decode("utf-8")
                for src, dst in REWRITE.items():
                    text = text.replace(f'"{src}"', f'"{dst}"')
                raw = text.encode("utf-8")
            p.write_bytes(raw)
        h = hashlib.sha256(raw).hexdigest()
        got[name] = h
        if name in want and want[name] != h:
            changed.append(name)
        print(f"  {h[:16]}  {len(raw) / 1e6:7.2f} MB  {name}")

    if changed:
        print(f"\nHASH MISMATCH on {', '.join(changed)} — the CDN served something other than what is recorded.")
        print("This is executable code served to readers. Nothing was accepted; inspect before re-locking.")
        return 1
    # Only a real import reaches off-origin. jsDelivr leaves an SRI notice in a comment, and duckdb.mjs carries
    # the CDN base URL as a constant for getJsDelivrBundles() — a function the page never calls, because it
    # passes our own paths. Grepping for any https:// would cry wolf on both.
    leftover = [n for n in got if n.endswith(".mjs")
                and re.search(rb'from\s*["\']https://', (OUT / n).read_bytes())]
    if leftover:
        print(f"\nOFF-ORIGIN IMPORT in {leftover} — the page would fetch code from a third party at run time.")
        return 1
    if not want or a.force:
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        LOCK.write_text("# DuckDB-WASM, vendored onto our own origin. Fetched by scripts/fetch_duckdb.py.\n"
                        + "".join(f"{h}  {n}\n" for n, h in sorted(got.items())), encoding="utf-8")
        print(f"\nwritten {LOCK}")
    else:
        print(f"\nall {len(got)} files match {LOCK}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
