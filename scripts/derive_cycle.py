#!/usr/bin/env python3
"""Derive one parliamentary cycle's inputs from the cache — the two derive scripts with the right window.

    python3 -m scripts.derive_cycle 41          # → data/derived/{votes_index,votes_positions}_ckl41.json.gz,
                                                #   first_light_ckl41.json, hero_vote_ckl41.json, mps_ckl41.json
    python3 -m scripts.derive_cycle 34 35 …     # several

Windows come from karzat.normalise.CYCLE_STARTS (constitutive sittings); the current cycle (43) is derived
by the two scripts' defaults and is not handled here.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karzat.normalise import CYCLE_STARTS  # noqa: E402


def window(cycle: int) -> tuple[str, str]:
    start = CYCLE_STARTS[cycle]
    nxt = CYCLE_STARTS.get(cycle + 1)
    end = (date.fromisoformat(nxt) - timedelta(days=1)).isoformat() if nxt else date.today().isoformat()
    return start, end


def main(argv: list[str] | None = None) -> int:
    cycles = [int(a) for a in (argv if argv is not None else sys.argv[1:])]
    if not cycles:
        print(__doc__)
        return 2
    for c in cycles:
        a, b = window(c)
        print(f"== cycle {c}: {a} .. {b}")
        r1 = subprocess.run([sys.executable, "-m", "scripts.derive_first_light", "--since", a, "--until", b, "--suffix", f"ckl{c}", "--gzip", "--hero", "auto"], cwd=ROOT)
        r2 = subprocess.run([sys.executable, "-m", "scripts.derive_mps", "--cycle", str(c)], cwd=ROOT)
        if r1.returncode or r2.returncode:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
