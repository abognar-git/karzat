"""Measure the speech search against questions the record answers for itself.

    python3 -m scripts.bench_search                  # every cycle whose texts are derived
    python3 -m scripts.bench_search --cycle 42
    python3 -m scripts.bench_search --write          # record the figures for the page to quote

The hard part of judging a search is not the arithmetic, it is finding out what the right answer was. Buying a
labelled set is out of the question and hand-marking a few hundred results is a way of measuring your own
expectations. The record supplies both halves for nothing:

  **the question** — an iromány's `targy`, which is a short description of what a bill is about, written by the
  House and not by the members who then debated it.
  **the answer** — every speech filed against that bill. Not perfect: a speech on a bill can be about the
  procedure, or about the speaker before them, and the title's words sometimes appear verbatim in the speech,
  which flatters anything that matches words. Both are noise in the same direction for every candidate, which
  is what a comparison needs.

That gave 492 questions over cycle 42's 12,047 speeches, and the first thing it measured was this project's own
search finding the right speech in its first ten **7.7%** of the time. Not because the ranking was poor, but
because there was none: the page required every typed word to appear and then ordered by date. A long question
matched almost nothing, and what it matched came back by accident of when it was said.

  what shipped: every word required, newest first      recall@10   7.7%   MRR 0.245
  the same index, scored by BM25                       recall@10  40.6%   MRR 0.723

(Cycle 42, and the figures this file itself prints. An earlier draft said 43.8% and MRR 0.710, from a run with
a six-character stemmer that never shipped — and the page copied both. That is why `--write` exists: the page
now reads data/derived/search_bench.json instead of being told. Run it rather than believing it, including
these lines.)

Four other candidates were measured on the same bench before that one was built, because the obvious answer to
"the search only matches word stems" is a semantic index, and it was worth knowing whether that was true:

  LSA over the corpus itself, k=256                    recall@10  30.8%   MRR 0.441
  multilingual-e5-small, whole speech in passages      recall@10  39.2%   MRR 0.593
  e5 + word overlap, half and half                     recall@10  47.0%   MRR 0.685
  e5 distilled to one vector per term (no model)       recall@10   0.7%   MRR 0.015

Every one of them needs something shipped to the reader that BM25 does not. The best of them beats BM25 by
three points of recall and costs a hundred megabytes of model in the browser; the one that ships nothing
extra scores 0.7%, because these embeddings do not average over words that way. The cheap answer was the
right one, and it took the bench to see it — the expensive answer is the one I would have built.
"""

from __future__ import annotations

import argparse
import bisect
import collections
import gzip
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

K1, B = 1.5, 0.75          # the BM25 constants the page uses; changing one here changes nothing there


def fold(text: str) -> list[str]:
    """The page's own tokens: accents folded, lower-case, three characters or more."""
    f = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()
    return re.findall(r"[a-z0-9]{3,}", f)


def load_cycle(cycle: int) -> tuple[list[dict], list[tuple[str, str]]]:
    """The speeches of one cycle, and the questions the record asks about them."""
    import pyarrow.parquet as pq
    t = pq.read_table(ROOT / "site" / "adatok" / "szavazas_iromany.parquet",
                      columns=["ciklus", "iromany", "targy"]).to_pydict()
    titles: dict[str, str] = {}
    for c, i, tg in zip(t["ciklus"], t["iromany"], t["targy"]):
        if c == cycle and i and tg and len(tg) > 40:
            titles.setdefault(i, tg)

    tf = ROOT / "data" / "derived" / f"speech_texts_ckl{cycle}.json.gz"
    sf = ROOT / "data" / "derived" / f"speeches_ckl{cycle}.json.gz"
    if not tf.exists():
        tf = ROOT / "data" / "derived" / "speech_texts.json.gz"
        sf = ROOT / "data" / "derived" / "speeches.json.gz"
    if not tf.exists():
        return [], []
    texts = json.loads(gzip.decompress(tf.read_bytes())).get("texts") or {}
    sp = json.loads(gzip.decompress(sf.read_bytes()))
    rows = (sp.get("speeches") if isinstance(sp, dict) else sp) or []
    bill = {f"{r['ulnap']}-{r['seq']}": (r.get("iromany") or "").split(" · ")[0] for r in rows}

    docs = []
    for sid, r in texts.items():
        body = " ".join(r.get("paragraphs") or [])
        b = bill.get(sid) or ""
        if len(body) < 600 or not b:
            continue
        docs.append({"id": sid, "bill": b, "date": r.get("date") or "", "toks": fold(body)})
    per = collections.Counter(d["bill"] for d in docs)
    qs = [(b, titles[b]) for b in per if per[b] >= 3 and b in titles]
    return docs, qs


def index_of(docs: list[dict]) -> tuple[dict[str, list[int]], list[int]]:
    """token -> [doc, tf, …] and the document lengths: the shape the page's shards carry."""
    post: dict[str, list[int]] = {}
    for i, d in enumerate(docs):
        for tok, n in collections.Counter(d["toks"]).items():
            post.setdefault(tok, []).extend((i, min(n, 255)))
    return post, [len(d["toks"]) for d in docs]


def prefix_range(sorted_tokens: list[str], term: str) -> tuple[int, int]:
    return bisect.bisect_left(sorted_tokens, term), bisect.bisect_left(sorted_tokens, term + "￿")


def score_bm25(post, sorted_tokens, lens, avg, n_docs, terms) -> dict[int, float]:
    """Exactly what the page computes, in the same order, so a disagreement is a real one."""
    out: dict[int, float] = {}
    for term in terms:
        lo, hi = prefix_range(sorted_tokens, term)
        tfs: dict[int, int] = {}
        for tok in sorted_tokens[lo:hi]:
            arr = post[tok]
            for j in range(0, len(arr) - 1, 2):
                tfs[arr[j]] = tfs.get(arr[j], 0) + arr[j + 1]
        if not tfs:
            continue
        idf = math.log(1 + (n_docs - len(tfs) + 0.5) / (len(tfs) + 0.5))
        for d, f in tfs.items():
            dl = lens[d] or avg
            out[d] = out.get(d, 0.0) + idf * f * (K1 + 1) / (f + K1 * (1 - B + B * dl / avg))
    return out


def score_shipped(post, sorted_tokens, lens, avg, n_docs, terms, dates) -> dict[int, float]:
    """What the page did before: every term required, then newest first."""
    keep: set[int] | None = None
    for term in terms:
        lo, hi = prefix_range(sorted_tokens, term)
        here = set()
        for tok in sorted_tokens[lo:hi]:
            arr = post[tok]
            here.update(arr[j] for j in range(0, len(arr) - 1, 2))
        keep = here if keep is None else (keep & here)
        if not keep:
            return {}
    rank = {d: i for i, d in enumerate(sorted(range(len(lens)), key=lambda i: dates[i], reverse=True))}
    return {d: float(len(lens) - rank[d]) for d in (keep or ())}


def evaluate(docs, qs, scorer) -> tuple[float, float]:
    bills = [d["bill"] for d in docs]
    per = collections.Counter(bills)
    r10 = mrr = 0.0
    for bill, question in qs:
        s = scorer(fold(question))
        order = sorted(s, key=lambda d: -s[d])
        top = order[:10]
        r10 += sum(1 for d in top if bills[d] == bill) / min(10, per[bill])
        for rank, d in enumerate(order, 1):
            if bills[d] == bill:
                mrr += 1 / rank
                break
    return r10 / len(qs), mrr / len(qs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycle", type=int, action="append", help="repeatable; default every derived cycle")
    ap.add_argument("--min-questions", type=int, default=40)
    ap.add_argument("--write", action="store_true",
                    help="record the figures in data/derived/search_bench.json for the page to quote")
    a = ap.parse_args(argv)
    cycles = a.cycle or [43, 42, 41, 40]
    worst = 1.0
    results: dict = {"cycles": {}}
    for c in cycles:
        docs, qs = load_cycle(c)
        if len(qs) < a.min_questions:
            print(f"cycle {c}: {len(qs)} questions — too few to say anything, skipped")
            continue
        post, lens = index_of(docs)
        toks = sorted(post)
        avg = (sum(lens) / len(lens)) or 1.0
        dates = [d["date"] for d in docs]
        print(f"cycle {c}: {len(docs):,} speeches, {len(qs)} questions")
        was = evaluate(docs, qs, lambda t: score_shipped(post, toks, lens, avg, len(docs), t, dates))
        now = evaluate(docs, qs, lambda t: score_bm25(post, toks, lens, avg, len(docs), t))
        print(f"   every word required, newest first   recall@10 {was[0]:6.1%}   MRR {was[1]:6.3f}")
        print(f"   BM25 over the same index            recall@10 {now[0]:6.1%}   MRR {now[1]:6.3f}")
        worst = min(worst, now[0] - was[0])
        results["cycles"][str(c)] = {"speeches": len(docs), "questions": len(qs),
                                     "was": {"recall10": was[0], "mrr": was[1]},
                                     "now": {"recall10": now[0], "mrr": now[1]}}
    results["worst_gain_recall10"] = worst
    print(f"\nsmallest improvement in recall@10 across the cycles measured: {worst:+.1%}")
    if a.write:
        # The page quoted these from a run I did by hand, with a different tokeniser, and the figure drifted
        # by three points before anyone read it — inside a project that gates its README on exactly this.
        # A number a page states about itself is a number the page should not be told.
        out = ROOT / "data" / "derived" / "search_bench.json"
        out.write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"written {out}")
    return 0 if worst > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
