"""Resolve every internal reference a built page makes against the files that actually exist.

One implementation, two callers: `tests/test_links.py` proves the whole site, and the intraday refresh in
`scripts/nightly.py` proves the subtree it just rewrote. A second copy of this walk is how a rule drifts —
the checker that runs nightly and the checker that runs in the gate have to be the same code, or the gate
stops meaning what it says.

Scoping is sound only because a refresh rewrites some pages and leaves the rest byte-identical: an untouched
page's links cannot newly break, so checking the rewritten prefixes checks everything that changed. The
target set is always the WHOLE tree, because a rewritten page may point anywhere in it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urldefrag

REF = re.compile(r'\b(?:href|src|poster)\s*=\s*"([^"]+)"')
EXTERNAL = ("http://", "https://", "mailto:", "data:", "#", "//", "javascript:")
TWIN_SUFFIXES = (".csv", ".json", ".xml", ".parquet", ".csv.gz")


def path_of(raw: str) -> str:
    """The file part of a reference: the fragment and the query both dropped.

    `urldefrag` takes off `#dir` and leaves `index.html?m=2010-05`, which is a file nobody has. The month strip
    on the Számok pages links to the cycle's vote list narrowed to a month, and this test read 481 of those as
    dead links — a checker that cannot parse a query string reports the site broken and is itself the bug.
    A static host ignores the query, so what has to exist is the path before it.
    """
    return urldefrag(raw)[0].split("?", 1)[0]


def file_set(site: Path) -> set[str]:
    """Every file in the tree, relative and slash-joined — resolving references with os.path.exists would be
    one stat call per reference, and there are 13.8 million of them. Case matters: the deploy target is
    case-sensitive even where this filesystem is not."""
    return {str(p.relative_to(site)) for p in site.rglob("*") if p.is_file()}


def documents(site: Path, under: list[str] | None = None) -> list[Path]:
    """The pages to read: the whole tree, or only those below the given prefixes."""
    if not under:
        return list(site.rglob("*.html"))
    out: list[Path] = []
    for pref in under:
        p = site / pref
        out.extend([p] if p.is_file() and p.suffix == ".html" else sorted(p.rglob("*.html")))
    return out


def check(site: Path, under: list[str] | None = None,
          files: set[str] | None = None) -> tuple[int, dict[str, tuple[int, str]], list[str]]:
    """(references checked, broken by kind, advertised data files that do not exist)."""
    have = files if files is not None else file_set(site)
    broken: dict[str, tuple[int, str]] = {}
    missing_twins: list[str] = []
    checked = 0
    for f in documents(site, under):
        here = f.parent.relative_to(site)
        for raw in REF.findall(f.read_text(encoding="utf-8", errors="replace")):
            url = path_of(raw)
            if not url or url.startswith(EXTERNAL):
                continue
            checked += 1
            target = url.lstrip("/") if url.startswith("/") else os.path.normpath(str(here / url))
            target = unquote(target).replace(os.sep, "/")
            if target.startswith(".."):
                key = "escapes the site root"
            elif target in have:
                continue
            else:
                key = target.split("/")[-1].rsplit(".", 1)[-1] or "no extension"
            n, ex = broken.get(key, (0, ""))
            broken[key] = (n + 1, ex or f"{f.relative_to(site)} → {raw}")
            if url.endswith(TWIN_SUFFIXES):
                missing_twins.append(f"{f.relative_to(site)} → {raw}")
    return checked, broken, missing_twins


def report(broken: dict[str, tuple[int, str]]) -> str:
    return "\n".join(f"  {k}: {n:,}× e.g. {ex}" for k, (n, ex) in sorted(broken.items()))
