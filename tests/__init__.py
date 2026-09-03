"""Shared helpers for the test suite.

`node_bin()` is here because the nightly runs under launchd, whose PATH is `/usr/bin:/bin:/usr/sbin:/sbin`
and nothing else. `shutil.which("node")` therefore answers None every night on a machine that has node
installed, and thirty-six tests skipped themselves — including the one that parses the shipped JavaScript
bundle, whose node-less fallback then reported a false failure on a correct bundle. A gate that is weaker
when nobody is watching is not a gate, so the lookup goes where a person would look rather than trusting
the environment it was launched in.
"""

from __future__ import annotations

import functools
import shutil
from pathlib import Path


@functools.lru_cache(maxsize=1)
def node_bin() -> str | None:
    """The node executable, or None when the machine genuinely has none."""
    found = shutil.which("node")
    if found:
        return found
    fixed = [Path("/opt/homebrew/bin/node"), Path("/usr/local/bin/node"), Path("/usr/bin/node")]
    # nvm keeps one directory per version; take the highest that actually runs
    nvm = Path.home() / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        fixed += sorted((d / "bin" / "node" for d in nvm.iterdir() if d.is_dir()), reverse=True)
    for p in fixed:
        if p.is_file() and p.stat().st_mode & 0o111:
            return str(p)
    return None
