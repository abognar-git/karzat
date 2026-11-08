"""The whole test suite, the way the nightly runs it: across the cores where pytest-xdist is installed.

    python3 -m scripts.run_tests            # everything — about 20 minutes on eight cores instead of 38 on one
    python3 -m scripts.run_tests --quick    # without the browser audit and the whole-site link walk

`python3 -m unittest discover -s tests -t .` stays the documented path and needs nothing but Python; this is
the same tests, chosen by the same function the nightly uses (scripts/nightly.py test_commands), so a run by
hand proves exactly what a night's run proves and the two cannot drift apart. Without pytest-xdist it falls
back to that unittest command by itself.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true",
                    help="skip the browser audit and the whole-site link walk, as an open-cycle night does")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from scripts.nightly import test_commands
    t0 = time.time()
    for cmd in test_commands(quick=args.quick):
        print("$ python3 " + " ".join(cmd), flush=True)
        rc = subprocess.run([sys.executable] + cmd, cwd=ROOT).returncode
        if rc:
            print(f"failed after {(time.time() - t0) / 60:.1f} min", flush=True)
            return rc
    print(f"all green in {(time.time() - t0) / 60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
