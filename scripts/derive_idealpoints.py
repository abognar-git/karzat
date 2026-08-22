"""Where each member sits on the one axis the roll calls describe, cycle by cycle.

    python3 -m scripts.derive_idealpoints                # every cycle with a named record
    python3 -m scripts.derive_idealpoints --cycle 42
    python3 -m scripts.derive_idealpoints --report       # print the fit instead of writing it

The cohesion page already measures how tightly each group votes together and how often any two groups agree.
That second table is a ten-by-ten grid of numbers, and the question it cannot answer is the one a reader
actually has: is this grid really a line? If it is, every number in it follows from a single position per
member, and the grid is a harder way of reading the same fact.

So this fits the standard model of the field — a two-parameter logistic item-response model, the same
arithmetic that scores an exam candidate on a latent ability from which questions they answered. Each member
gets a position `x`, each vote a discrimination `a` and a threshold `b`, and the probability that member i
votes yes on vote j is σ(a_j · x_i + b_j). Nobody declares what the axis means; it is whatever best predicts
the record, and what it turns out to be is a finding rather than an input.

Three things this does not do, each of which would be easy to imply and wrong.

**It does not compare cycles.** The axis is estimated from one cycle's votes and its scale comes from the
prior, so +1.4 in one parliament and +1.4 in the next are not the same position — the two fits never saw a
vote in common. Comparing them needs bridging: shared members or shared items tying the scales together.
This does not attempt it, the figures are written per cycle, and the page shows them per cycle.

Its direction is a convention too, and one that bit. Orienting each cycle so the largest group sits on the
right flipped the axis between the 42nd and the 43rd, because the largest group changed party — the same
picture would have read as a realignment that never happened. It is anchored on a named group that sits in
every estimable cycle instead.

**It does not reach before 1998.** Cycle 34 has no roll call that names members and cycle 35 has one, so
there is nothing to estimate from. Those cycles get no positions and the page says why, pointing at the
coverage page rather than repeating it.

**It does not claim precision it has not measured.** Every position comes with a bootstrap interval over
resampled votes, because a member who voted forty times and one who voted four thousand are not known to the
same accuracy, and a chart that draws them as identical dots says they are.

What is reported alongside: the share of individual votes the fitted model predicts correctly, the same share
for the null model that always guesses the majority side of each vote, and what a fitted SECOND dimension adds
on top. All three are the same currency — votes called right — so a reader can put them side by side and check
the arithmetic. An earlier version reported the second dimension as a ratio of singular values instead, which
is a fine diagnostic and does not answer the question the page was asking with it.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ANCHOR_ORDER = ("Fidesz", "MSZP")   # the axis is drawn with the first of these on the right
MIN_VOTES_PER_MP = 20        # below this a position is guesswork; the member is listed as not estimated
MIN_MINORITY = 0.025         # a vote where under 2.5% dissent separates nobody and only adds noise
ITERATIONS = 60
BOOTSTRAPS = 40


def matrix(cycle: int):
    """The cycle's roll calls as (member, vote, yes) triples, with the two rosters that index them."""
    import numpy as np
    import pyarrow.parquet as pq

    t = pq.read_table(ROOT / "site" / "adatok" / "szavazatok.parquet",
                      columns=["ciklus", "szavazas_id", "kepviselo_id", "frakcio", "szavazat"]).to_pydict()
    rows = [(v, m, f, p) for c, v, m, f, p in
            zip(t["ciklus"], t["szavazas_id"], t["kepviselo_id"], t["frakcio"], t["szavazat"])
            if c == cycle and p in ("igen", "nem")]
    if not rows:
        return None

    # A vote nobody opposed measures nothing about anybody: it has no discrimination to estimate and it
    # drags the fit's apparent accuracy up, since guessing the majority is already right every time.
    by_vote = collections.defaultdict(lambda: [0, 0])
    for v, _, _, p in rows:
        by_vote[v][0 if p == "igen" else 1] += 1
    divided = {v for v, (y, n) in by_vote.items() if min(y, n) >= MIN_MINORITY * (y + n) and min(y, n) >= 2}
    rows = [r for r in rows if r[0] in divided]
    if not rows:
        return None

    per_mp = collections.Counter(m for _, m, _, _ in rows)
    mps = sorted(m for m, n in per_mp.items() if n >= MIN_VOTES_PER_MP)
    votes = sorted({v for v, _, _, _ in rows})
    mi = {m: i for i, m in enumerate(mps)}
    vi = {v: j for j, v in enumerate(votes)}
    faction = {}
    I, J, Y = [], [], []
    for v, m, f, p in rows:
        if m not in mi:
            continue
        I.append(mi[m]); J.append(vi[v]); Y.append(1.0 if p == "igen" else 0.0)
        if f:
            faction[m] = f
    return (np.asarray(I), np.asarray(J), np.asarray(Y, dtype=np.float64),
            mps, votes, faction, len(per_mp) - len(mps))


PRIOR_X = 1.0          # a standard normal over positions: the prior that makes a perfect loyalist finite
PRIOR_A = 4.0          # weaker, over the votes' discrimination and threshold


def fit(I, J, Y, n_mp: int, n_vote: int, iterations: int = ITERATIONS, seed: int = 0):
    """A one-dimensional 2PL fitted to its posterior mode, by alternating Newton steps on the members and
    then on the votes.

    Missing votes are absent from (I, J, Y) and so absent from the likelihood — which is the whole reason for
    fitting a model rather than decomposing a matrix. An absent vote is not a nought, and treating it as one
    pulls every member who was away towards the middle, where they look like moderates.

    The priors are not decoration. Without them the first fit put two Fidesz members of cycle 42 at +9.5 on a
    scale whose standard deviation is one, and they were the two most active members in the House: 2,063 votes
    each and not one of them against their own side. A member who is never on the losing side is *perfectly
    separable*, the likelihood has no maximum for them, and the estimate runs away until the iteration count
    stops it — at a number that looks like an extraordinary finding and is an artefact of arithmetic. A
    standard normal over positions bounds it: perfect loyalty comes out as a large finite position, which is
    what it is. This is the difference the word "bayesian" is doing in the name of the method."""
    import numpy as np
    rng = np.random.default_rng(seed)
    x = rng.normal(0, 1, n_mp)
    a = np.ones(n_vote)
    b = np.zeros(n_vote)

    def newton(num, den, lo=-1e3, hi=1e3):
        return np.clip(num / np.maximum(den, 1e-9), lo, hi)

    for _ in range(iterations):
        # members, given the votes
        eta = a[J] * x[I] + b[J]
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -30, 30)))
        g = np.bincount(I, weights=(Y - p) * a[J], minlength=n_mp) - x / PRIOR_X ** 2
        h = np.bincount(I, weights=p * (1 - p) * a[J] ** 2, minlength=n_mp) + 1.0 / PRIOR_X ** 2
        x = x + newton(g, h, -5, 5)
        x = x - x.mean()             # the prior fixes the scale; only the origin is still free

        # votes, given the members
        eta = a[J] * x[I] + b[J]
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -30, 30)))
        r = Y - p
        w = p * (1 - p)
        ga = np.bincount(J, weights=r * x[I], minlength=n_vote) - a / PRIOR_A ** 2
        gb = np.bincount(J, weights=r, minlength=n_vote) - b / PRIOR_A ** 2
        haa = np.bincount(J, weights=w * x[I] ** 2, minlength=n_vote) + 1.0 / PRIOR_A ** 2
        hab = np.bincount(J, weights=w * x[I], minlength=n_vote)
        hbb = np.bincount(J, weights=w, minlength=n_vote) + 1.0 / PRIOR_A ** 2
        det = haa * hbb - hab ** 2
        ok = np.abs(det) > 1e-9
        da = np.where(ok, (hbb * ga - hab * gb) / np.where(ok, det, 1), 0.0)
        db = np.where(ok, (haa * gb - hab * ga) / np.where(ok, det, 1), 0.0)
        a = np.clip(a + np.clip(da, -2, 2), -20, 20)
        b = np.clip(b + np.clip(db, -2, 2), -20, 20)
    return x, a, b


def accuracy(I, J, Y, x, a, b):
    """How many individual votes the model calls right, and how many the majority rule alone calls right."""
    import numpy as np
    p = 1.0 / (1.0 + np.exp(-np.clip(a[J] * x[I] + b[J], -30, 30)))
    model = float(((p >= 0.5) == (Y >= 0.5)).mean())
    n_vote = len(a)
    yes = np.bincount(J, weights=Y, minlength=n_vote)
    tot = np.bincount(J, minlength=n_vote)
    majority = (yes >= tot / 2).astype(float)
    null = float((majority[J] == Y).mean())
    return model, null


def second_dimension(I, J, Y, x, a, b, iterations: int = 40) -> float:
    """What a second axis would add, measured in the same currency as the first: votes called right.

    The first version of this reported a ratio of singular values from the residual matrix — a perfectly
    ordinary diagnostic, and not what the page then said about it. The page said "a second line would add
    0.2%", and a reader doing the arithmetic asked the obvious question: if the model gets 98.5% right, 1.5%
    is left, so where does 0.2% come from? Nowhere. It was a variance ratio wearing a classification's
    clothes, and the two are not comparable.

    So this fits an actual second dimension and returns the extra share of individual votes it calls right.
    A number the reader can check against the one beside it, which is the only kind worth printing."""
    import numpy as np
    n_mp, n_vote = len(x), len(a)
    base = a[J] * x[I] + b[J]                      # the first dimension, held fixed
    # Start where the structure is. Beginning with a2 = 0 makes the gradient on x2 identically zero, so
    # nothing moves and the answer comes back 0.0% for every cycle including the one that visibly has a
    # second cleavage — a fit that cannot fail to find nothing. The residual matrix's leading singular
    # vector is the obvious starting point: it is exactly where a second axis would be if there is one.
    p0 = 1.0 / (1.0 + np.exp(-np.clip(base, -30, 30)))
    R = np.zeros((n_mp, n_vote))
    R[I, J] = Y - p0
    u, sv, vt = np.linalg.svd(R, full_matrices=False)
    x2 = u[:, 0] * (sv[0] ** 0.5)
    x2 = x2 / (x2.std() or 1.0)
    a2 = vt[0] * (sv[0] ** 0.5)
    a2 = a2 / (np.abs(a2).max() or 1.0)
    for _ in range(iterations):
        p = 1.0 / (1.0 + np.exp(-np.clip(base + a2[J] * x2[I], -30, 30)))
        g = np.bincount(I, weights=(Y - p) * a2[J], minlength=n_mp) - x2 / PRIOR_X ** 2
        h = np.bincount(I, weights=p * (1 - p) * a2[J] ** 2, minlength=n_mp) + 1.0 / PRIOR_X ** 2
        x2 = x2 + np.clip(g / np.maximum(h, 1e-9), -2, 2)
        x2 = x2 - x2.mean()
        d = x2 @ x                                  # orthogonal to the first, or it just refits it
        x2 = x2 - d / max(float(x @ x), 1e-9) * x
        p = 1.0 / (1.0 + np.exp(-np.clip(base + a2[J] * x2[I], -30, 30)))
        ga = np.bincount(J, weights=(Y - p) * x2[I], minlength=n_vote) - a2 / PRIOR_A ** 2
        ha = np.bincount(J, weights=p * (1 - p) * x2[I] ** 2, minlength=n_vote) + 1.0 / PRIOR_A ** 2
        a2 = np.clip(a2 + np.clip(ga / np.maximum(ha, 1e-9), -2, 2), -20, 20)
    one = ((1.0 / (1.0 + np.exp(-np.clip(base, -30, 30))) >= 0.5) == (Y >= 0.5)).mean()
    two = ((1.0 / (1.0 + np.exp(-np.clip(base + a2[J] * x2[I], -30, 30))) >= 0.5) == (Y >= 0.5)).mean()
    return float(max(0.0, two - one))


def spread(I, J, Y, n_mp, n_vote, x, draws: int = BOOTSTRAPS, seed: int = 1):
    """A bootstrap interval per member, resampling votes rather than cells.

    A member with forty votes and one with four thousand are not known to the same accuracy, and a chart
    that draws them as identical dots claims they are."""
    import numpy as np
    rng = np.random.default_rng(seed)
    keep = np.zeros((draws, n_mp))
    for d in range(draws):
        pick = rng.integers(0, n_vote, n_vote)
        remap = -np.ones(n_vote, dtype=np.int64)
        remap[pick] = np.arange(n_vote)
        sel = remap[J] >= 0
        xd, _, _ = fit(I[sel], remap[J[sel]], Y[sel], n_mp, n_vote, iterations=25, seed=d)
        if np.corrcoef(xd, x)[0, 1] < 0:
            xd = -xd                                  # the sign is a convention; each draw picks its own
        keep[d] = xd
    return keep.std(axis=0)


def run(cycle: int) -> dict | None:
    import numpy as np
    m = matrix(cycle)
    if m is None:
        return None
    I, J, Y, mps, votes, faction, dropped = m
    n_mp, n_vote = len(mps), len(votes)
    if n_mp < 20 or n_vote < 20:
        return None
    x, a, b = fit(I, J, Y, n_mp, n_vote)

    # The sign is arbitrary; orient it so the largest group sits on the right, and record which group that
    # was. Without this the axis flips between cycles for no reason and a reader reads a realignment into it.
    # Orient on a named group that sits in every estimable cycle, not on the largest one: the largest group
    # changed party between 42 and 43, so "largest to the right" silently flipped the axis's meaning between
    # two adjacent parliaments. The direction is a convention either way; a convention that moves is a trap.
    present = {faction.get(m2) for m2 in mps}
    anchor = next((f for f in ANCHOR_ORDER if f in present), None)
    if anchor is None:
        big = collections.Counter(faction.get(m2) for m2 in mps if faction.get(m2)).most_common(1)
        anchor = big[0][0] if big else None
    if anchor:
        idx = [i for i, m2 in enumerate(mps) if faction.get(m2) == anchor]
        if idx and float(np.mean(x[idx])) < 0:
            x, a = -x, -a

    model, null = accuracy(I, J, Y, x, a, b)
    sd = spread(I, J, Y, n_mp, n_vote, x)
    per_f = collections.defaultdict(list)
    for i, m2 in enumerate(mps):
        per_f[faction.get(m2) or "—"].append(float(x[i]))
    return {
        "cycle": cycle,
        "members": n_mp, "votes": n_vote, "cells": int(len(Y)),
        "not_estimated": int(dropped),
        "anchor": anchor,
        "correct": model, "null": null,
        "apre": (model - null) / (1 - null) if null < 1 else 0.0,
        "second_dimension": second_dimension(I, J, Y, x, a, b),
        "positions": {m2: {"x": round(float(x[i]), 4), "sd": round(float(sd[i]), 4),
                           "votes": int((I == i).sum()), "faction": faction.get(m2)}
                      for i, m2 in enumerate(mps)},
        "factions": {f: {"n": len(v), "mean": round(float(np.mean(v)), 4),
                         "sd": round(float(np.std(v)), 4)}
                     for f, v in sorted(per_f.items(), key=lambda kv: float(np.mean(kv[1])))},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycle", type=int, action="append")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    from scripts.build_site import available_cycles
    cycles = a.cycle or available_cycles()
    out: dict = {"derived_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "cycles": {}}
    for c in sorted(cycles, reverse=True):
        r = run(c)
        if r is None:
            print(f"  cycle {c}: no named roll call to estimate from")
            continue
        out["cycles"][str(c)] = r
        print(f"  cycle {c}: {r['members']} members, {r['votes']:,} divided votes — "
              f"model {r['correct']:.1%} vs majority {r['null']:.1%} (APRE {r['apre']:.1%}), "
              f"second dimension {r['second_dimension']:.1%}")
        if a.report:
            for f, v in r["factions"].items():
                print(f"      {f:24} n={v['n']:>3}  {v['mean']:+6.2f} ± {v['sd']:.2f}")
    if not a.report:
        # A closed parliament's votes will not change again, and refitting eight of them takes a quarter of an
        # hour. The nightly asks for the open cycle only and this keeps the rest, so the file is complete
        # without the work being repeated on data that is finished.
        p = ROOT / "data" / "derived" / "idealpoints.json"
        prev = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"cycles": {}}
        merged = dict(prev.get("cycles") or {})
        merged.update(out["cycles"])
        out["cycles"] = dict(sorted(merged.items(), key=lambda kv: -int(kv[0])))
        p.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        print(f"written {p}  ({p.stat().st_size / 1e6:.1f} MB, {len(out['cycles'])} cycles)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
