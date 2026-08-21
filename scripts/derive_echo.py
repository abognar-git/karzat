"""Who said the same words as whom, and how long afterwards.

    python3 -m scripts.derive_echo                 # every cycle whose texts are derived
    python3 -m scripts.derive_echo --words 30
    python3 -m scripts.derive_echo --cycle 43 --report   # print the findings instead of writing

Two people using the same thirty words is not a coincidence, but it is not one thing either, and the whole
difficulty of this measurement is that the three things look identical to a matcher:

  **a template** — the committee's rapporteurs read the same report aloud, and its wording is the committee's,
  not theirs. Three people saying it is a rota, not a chorus.
  **a quotation** — the commonest reason two members share a passage is that one is reading the other back to
  them, usually within the same debate and usually to disagree.
  **a line** — the same sentences turning up in unrelated debates, days apart, in different mouths.

So this does not decide. It measures what can be measured — who said it first, who repeated it, how many days
later, whether they were in the same debate, whether they share a faction — and prints the passage so a reader
can see which of the three it is. The word "coordination" appears nowhere in the output, because the record
cannot support it.

What is deliberately excluded: the stenographer's stage directions in brackets, which are not the speaker's
words at all; a speaker repeating themselves; and the reprints, where one speech is filed under several agenda
items and would otherwise match itself.

Method: overlapping word n-grams, hashed, the duplicates found by sorting an array of 64-bit hashes rather than
by holding a dictionary of thirty-five million of them. Maximal runs are kept — where a long passage is shared,
the shorter windows inside it are dropped, so one echo is reported once at its full length.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DERIVED = ROOT / "data" / "derived"

WORD = re.compile(r"[a-záéíóöőúüű0-9]+")
STAGE = re.compile(r"\([^)]*\)")          # "(Taps a kormánypártok soraiban.)" — the stenographer, not the speaker


def normalise(paragraphs: list[str]) -> list[str]:
    return WORD.findall(STAGE.sub(" ", " ".join(paragraphs or []).lower()))


def hash_grams(words: list[str], k: int) -> list[int]:
    out = []
    for i in range(len(words) - k + 1):
        h = hashlib.blake2b(" ".join(words[i:i + k]).encode(), digest_size=8).digest()
        out.append(int.from_bytes(h, "big") & 0x7FFFFFFFFFFFFFFF)
    return out


def load_factions(cycle: int) -> dict:
    """speaker id -> faction, for the one column that separates a party line from a quotation."""
    f = DERIVED / ("mps.json" if cycle == 43 else f"mps_ckl{cycle}.json")
    if not f.exists():
        return {}
    return {k: (v.get("faction") or "") for k, v in json.loads(f.read_text(encoding="utf-8"))["mps"].items()}


def load_cycle(cycle: int) -> dict:
    f = DERIVED / ("speech_texts.json.gz" if cycle == 43 else f"speech_texts_ckl{cycle}.json.gz")
    return json.loads(gzip.decompress(f.read_bytes()))["texts"] if f.exists() else {}


def echoes(texts: dict, k: int, min_speakers: int = 2, factions: dict | None = None) -> list[dict]:
    # pass 1 — every n-gram hash into one array, sorted, so the repeated ones fall out without a dict of millions
    per_speech, all_h = {}, []
    for sid, t in texts.items():
        hs = hash_grams(normalise(t.get("paragraphs")), k)
        per_speech[sid] = hs
        all_h.extend(hs)
    if not all_h:
        return []
    arr = np.array(all_h, dtype=np.uint64)
    arr.sort()
    dup = set(arr[:-1][arr[1:] == arr[:-1]].tolist())

    # pass 2 — only the repeated hashes, with their position, so a run can be rebuilt into one passage
    where: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for sid, hs in per_speech.items():
        for i, h in enumerate(hs):
            if h in dup:
                where[h].append((sid, i))

    # a shared run of consecutive n-grams is one passage; keep the maximal run and drop the windows inside it
    covered: set[tuple[str, int]] = set()
    found = []
    for h, hits in where.items():
        speakers = {texts[s].get("speaker") for s, _ in hits}
        if len(speakers) < min_speakers:
            continue
        if any((s, i) in covered for s, i in hits):
            continue
        # extend while every participant still matches on the next n-gram
        length = k
        step = 0
        while True:
            nxt = {per_speech[s][i + step + 1] if i + step + 1 < len(per_speech[s]) else None for s, i in hits}
            if len(nxt) != 1 or None in nxt:
                break
            step += 1
            length += 1
        for s, i in hits:
            for j in range(i, i + step + 1):
                covered.add((s, j))
        first_sid, first_i = min(hits, key=lambda si: (texts[si[0]].get("date") or "", texts[si[0]].get("seq") or 0))
        words = normalise(texts[first_sid].get("paragraphs"))
        found.append({
            "words": length,
            "passage": " ".join(words[first_i:first_i + length]),
            "speeches": sorted(({"id": s, "speaker": texts[s].get("speaker"), "date": texts[s].get("date"),
                                 "kind": texts[s].get("kind"), "position": texts[s].get("position"),
                                 "faction": (factions or {}).get(texts[s].get("azon") or "", "")}
                                for s, _ in hits), key=lambda x: (x["date"] or "", x["id"])),
        })
    for f in found:
        days = sorted({x["date"] for x in f["speeches"] if x["date"]})
        f["speakers"] = len({x["speaker"] for x in f["speeches"]})
        f["days"] = len(days)
        f["first_day"], f["last_day"] = (days[0], days[-1]) if days else (None, None)
        f["lag_days"] = _lag(f["first_day"], f["last_day"])
        f["same_day"] = f["days"] == 1
        # a passage every speaker delivers in the same kind of speech is the institution's wording, not theirs
        f["same_kind"] = len({x["kind"] for x in f["speeches"]}) == 1
        facs = {x["faction"] for x in f["speeches"] if x["faction"]}
        f["factions"] = sorted(facs)
        # one faction and different days is a line being carried; two factions is far more often one member
        # reading the other back to them, which is why the two are counted apart and never added together
        f["one_faction"] = len(facs) == 1 and len(f["speeches"]) > 1
    return sorted(found, key=lambda f: (-f["speakers"], -f["words"]))


def _lag(a: str | None, b: str | None) -> int | None:
    from datetime import date
    if not a or not b:
        return None
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--words", type=int, default=30, help="shortest passage counted as an echo (default 30)")
    ap.add_argument("--cycle", type=int, action="append")
    ap.add_argument("--report", action="store_true", help="print, write nothing")
    a = ap.parse_args(argv)
    cycles = a.cycle or [c for c in (43, 42, 41, 40)
                         if (DERIVED / ("speech_texts.json.gz" if c == 43 else f"speech_texts_ckl{c}.json.gz")).exists()]

    out = {"generated_at": None, "words": a.words, "cycles": {}}
    for c in cycles:
        texts = load_cycle(c)
        if not texts:
            continue
        fs = echoes(texts, a.words, factions=load_factions(c))
        across = [f for f in fs if not f["same_day"]]
        out["cycles"][str(c)] = {
            "speeches": len(texts), "echoes": len(fs),
            "same_day": len(fs) - len(across), "later_day": len(across),
            "one_kind_of_speech": sum(1 for f in fs if f["same_kind"]),
            "one_faction_later_day": sum(1 for f in fs if f["one_faction"] and not f["same_day"]),
            "across_factions": sum(1 for f in fs if len(f["factions"]) > 1),
            "items": fs[:400],
        }
        print(f"cycle {c}: {len(texts):,} speeches · {len(fs)} passages of {a.words}+ words in two or more mouths "
              f"({len(fs) - len(across)} inside one sitting day, {len(across)} days apart)")
        if a.report:
            for f in fs[:10]:
                who = ", ".join(dict.fromkeys(x["speaker"] for x in f["speeches"]))
                tag = "egy ülésnapon" if f["same_day"] else f'{f["lag_days"]} nap alatt'
                kind = " · ugyanaz a felszólalásfajta" if f["same_kind"] else ""
                fac = (" · " + " / ".join(f["factions"])) if f["factions"] else ""
                print(f'  {f["words"]:3d} szó · {f["speakers"]} beszélő · {tag}{kind}{fac}\n    {who}\n    „{f["passage"][:150]}…"')
    if not a.report:
        from datetime import datetime, timezone
        out["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        p = DERIVED / "echo.json"
        # --cycle rewrites one cycle, so the others are carried over rather than dropped: the nightly only has new
        # text for the running cycle, and a run that quietly emptied the archive cycles would be the worse bug
        if p.exists() and a.cycle:
            prev = json.loads(p.read_text(encoding="utf-8")).get("cycles") or {}
            out["cycles"] = {**prev, **out["cycles"]}
            out["cycles"] = {k: out["cycles"][k] for k in sorted(out["cycles"], key=int, reverse=True)}
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("written", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
