"""A receipt for every number the site publishes: which corpus, which code, which derived files.

    python3 -m scripts.derive_receipt
    python3 -m scripts.derive_receipt --verify     # do the files still hash to what the receipt says?

The site's discipline is that a number is generated rather than typed, and the README gate enforces it. But
"generated" only answers *how*, not *from what*: a reader looking at 79,829 votes has no way to tell which
payloads produced it, or whether the code that counted them is the code in front of them. Three things have to
be pinned together for a figure to be reproducible, and until now none of them were written down anywhere.

  **The corpus.** 146,709 cached payloads. Their content hashes are folded, in sorted order, into one root — a
  Merkle-style digest where adding, removing or altering any single payload moves the root. Content, not mtime:
  a re-fetch that returns identical bytes must not look like a change, and one that returns different bytes must.
  **The code.** The commit, and whether the working tree was clean when the build ran. A dirty tree is recorded
  as dirty rather than quietly attributed to the commit, because that is exactly the case where a reader trying
  to reproduce the number would fail and not know why.
  **The derived files.** Each one's own hash, so a receipt can be checked against the disk without re-deriving.

What this does not claim: that a given number came from a given payload. Tracking that would mean instrumenting
every read in every derivation, and the honest cheaper answer is the one here — this figure came from *this
corpus state* under *this code*. Both are checkable, which is more than most published statistics offer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RAW = ROOT / "data" / "raw"
DERIVED = ROOT / "data" / "derived"
OUT = DERIVED / "receipt.json"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 18), b""):
            h.update(chunk)
    return h.hexdigest()


def service_root(d: Path) -> tuple[str, int, int]:
    """(root, files, bytes) for one cached service — the root folds every payload's name and content hash in
    sorted order, so it moves for an addition, a removal or an edit alike."""
    h = hashlib.sha256()
    n = size = 0
    for p in sorted(d.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        h.update(p.relative_to(d).as_posix().encode())
        h.update(sha256_file(p).encode())
        n += 1
        size += p.stat().st_size
    return h.hexdigest(), n, size


def code_state() -> dict:
    def git(*a: str) -> str:
        try:
            return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:
            return ""
    dirty = [ln for ln in git("status", "--porcelain").splitlines() if ln and not ln.endswith(".json")]
    return {"commit": git("rev-parse", "HEAD"), "described": git("describe", "--always", "--dirty"),
            "clean": not dirty, "uncommitted_files": len(dirty)}


def build() -> dict:
    services = {}
    for d in sorted(p for p in RAW.iterdir() if p.is_dir() and not p.name.startswith(".")):
        root, n, size = service_root(d)
        services[d.name] = {"root": root, "payloads": n, "bytes": size}
    corpus = hashlib.sha256()
    for name, v in services.items():                       # one root over the roots, in name order
        corpus.update(name.encode())
        corpus.update(v["root"].encode())
    derived = {p.name: sha256_file(p) for p in sorted(DERIVED.iterdir())
               if p.is_file() and p.name != OUT.name}
    return {"built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "corpus_root": corpus.hexdigest(),
            "payloads": sum(v["payloads"] for v in services.values()),
            "bytes": sum(v["bytes"] for v in services.values()),
            "services": services, "code": code_state(), "derived": derived}


def verify() -> int:
    if not OUT.exists():
        print("no receipt yet — run: python3 -m scripts.derive_receipt")
        return 1
    r = json.loads(OUT.read_text(encoding="utf-8"))
    bad = []
    for name, want in r["derived"].items():
        p = DERIVED / name
        if not p.exists():
            bad.append(f"{name}: gone")
        elif sha256_file(p) != want:
            bad.append(f"{name}: changed since the receipt")
    now = build()
    print(f"receipt of {r['built_at']} · corpus {r['corpus_root'][:16]} · code {r['code']['described']}")
    print(f"  corpus now:  {now['corpus_root'][:16]}  {'same' if now['corpus_root'] == r['corpus_root'] else 'DIFFERENT'}")
    print(f"  derived files that no longer match: {len(bad)}")
    for b in bad[:10]:
        print("   ·", b)
    return 1 if bad or now["corpus_root"] != r["corpus_root"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args(argv)
    if a.verify:
        return verify()
    r = build()
    OUT.write_text(json.dumps(r, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"corpus {r['corpus_root'][:16]} · {r['payloads']:,} payloads, {r['bytes'] / 1e9:.2f} GB")
    for name, v in sorted(r["services"].items(), key=lambda kv: -kv[1]["payloads"]):
        print(f"   {v['payloads']:7,}  {name:16} {v['root'][:16]}")
    c = r["code"]
    state = "clean" if c["clean"] else f"{c['uncommitted_files']} uncommitted files"
    print(f"code {c['described']} · {state}")
    print(f"derived files hashed: {len(r['derived'])}")
    print("written", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
