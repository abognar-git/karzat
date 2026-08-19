"""Derive one cycle's speech texts from the cached felszolalas.cgi payloads.

    python3 -m scripts.derive_speech_texts --cycle 43     → data/derived/speech_texts.json.gz
    python3 -m scripts.derive_speech_texts --cycle 42     → data/derived/speech_texts_ckl42.json.gz

One entry per fetched speech, keyed '<ülésnap>-<sorszám>': the speaker as the payload prints it, the p_azon from the
speaker's href (the payload's own identification — no name resolution needed here), the position (beosztás), the kind,
the length, and the text as plain paragraphs (the payload's HTML paragraphs, whitespace normalised, markup dropped).
The texts are the official record's own words; nothing is summarised.

Deterministic: the stamp is the newest speech day, gzip with mtime=0. Texts not yet fetched are simply absent — the site
prints "a szöveg nincs betöltve" for those rows.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karzat.normalise import parse_felszolalas  # noqa: E402

RAW = ROOT / "data" / "raw"
DERIVED = ROOT / "data" / "derived"
_NAME = re.compile(r"^ckl(\d+)_nap(\d+)_f(\d+)$")


def write_gz(path: Path, obj) -> None:
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
    with gzip.GzipFile(filename="", mode="wb", fileobj=path.open("wb"), mtime=0) as gz:
        gz.write(text.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycle", type=int, required=True)
    args = ap.parse_args(argv)
    cycle = args.cycle
    files = sorted((RAW / "felszolalas").glob(f"ckl{cycle}_nap*_f*.xml"))
    texts: dict[str, dict] = {}
    bad = 0
    for f in files:
        m = _NAME.match(f.stem)
        if not m:
            continue
        try:
            d = parse_felszolalas(f.read_bytes())
        except Exception as e:                       # one broken payload must not take the derive down
            bad += 1
            print(f"skip {f.name}: {e}", file=sys.stderr)
            continue
        uln, seq = int(m.group(2)), int(m.group(3))
        if not d["paragraphs"]:
            continue                                     # no text (not transcribed yet): not a text
        texts[f"{uln}-{seq}"] = {"ulnap": uln, "seq": seq, "date": d["date"], "speaker": d["speaker"], "azon": d["azon"],
                                 "position": d["position"], "kind": d["kind"], "duration_s": d["duration_s"], "chars": d["chars"],
                                 "paragraphs": d["paragraphs"]}
    if not texts:
        print(f"no cached speech texts for cycle {cycle} — run: python3 -m karzat sync-speech-texts --ckl {cycle}", file=sys.stderr)
        return 1
    out = {"cycle": cycle, "as_of": max((t["date"] for t in texts.values() if t["date"]), default=None), "count": len(texts),
           "chars": sum(t["chars"] for t in texts.values()), "with_azon": sum(1 for t in texts.values() if t["azon"]), "texts": texts}
    sfx = "" if cycle == 43 else f"_ckl{cycle}"
    path = DERIVED / f"speech_texts{sfx}.json.gz"
    write_gz(path, out)
    print(f"written {path}: {len(texts)} texts, {out['chars']:,} characters, {out['with_azon']} with an MP id from the href"
          + (f"; {bad} payloads skipped" if bad else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
