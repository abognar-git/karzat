"""Cohesion indices and the close-vote finder — counts over the roll-call store, with their formulas named.

Everything here is arithmetic on positions the API printed. Two cohesion measures, both standard:
  * Rice index (Rice 1925): |igen − nem| / (igen + nem) — abstentions ignored; 1 = unanimous on yes/no.
  * Agreement index (Hix–Noury–Roland 2005): (max − (Σ − max)/2) / Σ over the three cast options
    (igen, nem, tartózkodott); 1 = everyone cast the same, 0 = split evenly three ways.
The close-vote finder ranks decisions by their margin to the threshold the source names, lists the ones
a qualified threshold sank although more voted yes than no, and — a defined counterfactual, labelled as
such wherever shown — the ones whose outcome would flip if every MP in the roll call who cast nothing had
voted with their faction's plurality.
"""

from __future__ import annotations

from typing import Any

CAST = ("igen", "nem", "tartozkodott")


def rice(igen: int, nem: int) -> float | None:
    return abs(igen - nem) / (igen + nem) if (igen + nem) else None


def agreement_index(igen: int, nem: int, tart: int) -> float | None:
    total = igen + nem + tart
    if not total:
        return None
    mx = max(igen, nem, tart)
    return (mx - (total - mx) / 2) / total


def _expand(store: dict[str, Any], ts: str) -> list[tuple[str, str, str]]:
    codes = store["codes"]
    return [(key, store["factions"][fi], codes.get(code, code)) for key, fi, code in store["positions"].get(ts) or []]


def faction_tallies_from_store(store: dict[str, Any], ts: str) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for _, f, pos in _expand(store, ts):
        d = out.setdefault(f, {"igen": 0, "nem": 0, "tartozkodott": 0, "other": 0, "n": 0})
        d["n"] += 1
        if pos in CAST:
            d[pos] += 1
        else:
            d["other"] += 1
    return out


def cohesion(inp: dict[str, Any]) -> dict[str, Any]:
    """Per vote and per faction: Rice and AI; per faction over the cycle: means weighted by cast votes, dissent
    counts; the faction × faction plurality-agreement matrix; and per faction the MP × MP agreement matrix."""
    store, order, by_ts = inp["store"], inp["order"], inp["by_ts"]
    per_vote: dict[str, dict[str, dict[str, Any]]] = {}
    fac_acc: dict[str, dict[str, float]] = {}
    plur: dict[str, dict[str, str]] = {}
    mp_votes: dict[str, dict[str, str]] = {}          # faction -> azon -> {ts: pos}
    for ts in order:
        tallies = faction_tallies_from_store(store, ts)
        if not tallies:
            continue
        pv = {}
        for f, t in tallies.items():
            r, ai = rice(t["igen"], t["nem"]), agreement_index(t["igen"], t["nem"], t["tartozkodott"])
            cast = t["igen"] + t["nem"] + t["tartozkodott"]
            if cast:
                pos, n = max(((p, t[p]) for p in CAST), key=lambda kv: (kv[1], -CAST.index(kv[0])))
                plur.setdefault(ts, {})[f] = pos
                dissent = cast - n
            else:
                dissent = 0
            pv[f] = {"igen": t["igen"], "nem": t["nem"], "tartozkodott": t["tartozkodott"], "cast": cast, "rice": r, "ai": ai, "dissent": dissent}
            acc = fac_acc.setdefault(f, {"votes": 0, "cast": 0, "rice_w": 0.0, "rice_n": 0, "ai_w": 0.0, "ai_n": 0, "dissent": 0, "votes_with_dissent": 0, "unanimous": 0})
            if cast:
                acc["votes"] += 1
                acc["cast"] += cast
                if r is not None:
                    acc["rice_w"] += r * (t["igen"] + t["nem"]); acc["rice_n"] += t["igen"] + t["nem"]
                acc["ai_w"] += ai * cast; acc["ai_n"] += cast
                acc["dissent"] += dissent
                acc["votes_with_dissent"] += 1 if dissent else 0
                acc["unanimous"] += 1 if dissent == 0 else 0
        per_vote[ts] = pv
        for key, f, pos in _expand(store, ts):
            if pos in CAST and key in store["members"]:
                mp_votes.setdefault(f, {}).setdefault(key, {})[ts] = pos
    per_faction = {}
    for f, a in fac_acc.items():
        per_faction[f] = {"votes": a["votes"], "cast": a["cast"], "rice": (a["rice_w"] / a["rice_n"]) if a["rice_n"] else None,
                          "ai": (a["ai_w"] / a["ai_n"]) if a["ai_n"] else None, "dissenting_votes": a["dissent"],
                          "votes_with_dissent": a["votes_with_dissent"], "unanimous_votes": a["unanimous"]}
    # faction × faction: share of votes (where both cast) with the same plurality
    facs = sorted(per_faction, key=lambda f: -per_faction[f]["cast"])
    pair: dict[str, dict[str, dict[str, Any]]] = {}
    for i, a in enumerate(facs):
        for b in facs[i + 1:]:
            both = same = 0
            for ts, p in plur.items():
                if a in p and b in p:
                    both += 1
                    same += 1 if p[a] == p[b] else 0
            pair.setdefault(a, {})[b] = {"votes": both, "same": same, "share": (same / both) if both else None}
    # MP × MP within a faction: share of shared cast votes with the same position (only pairs with ≥ 20 shared votes)
    mp_pairs: dict[str, list[dict[str, Any]]] = {}
    for f, members in mp_votes.items():
        ids = sorted(members)
        rows = []
        for i, x in enumerate(ids):
            vx = members[x]
            for y in ids[i + 1:]:
                vy = members[y]
                shared = 0; same = 0
                if len(vx) < len(vy):
                    for ts, p in vx.items():
                        q = vy.get(ts)
                        if q is not None:
                            shared += 1; same += 1 if p == q else 0
                else:
                    for ts, q in vy.items():
                        p = vx.get(ts)
                        if p is not None:
                            shared += 1; same += 1 if p == q else 0
                if shared >= 20:
                    rows.append({"a": x, "b": y, "shared": shared, "same": same, "share": same / shared})
        mp_pairs[f] = rows
    return {"per_vote": per_vote, "per_faction": per_faction, "faction_pairs": pair, "mp_pairs": mp_pairs, "plurality": plur}


def close_votes(inp: dict[str, Any], plurality: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """Decisions ranked by |margin|; qualified thresholds that sank a yes-majority; absence-decisive outcomes."""
    store, order, by_ts = inp["store"], inp["order"], inp["by_ts"]
    rows = []
    for ts in order:
        v = by_ts[ts]
        m = v.get("majority") or {}
        if v.get("kind") != "dontes" or m.get("needed") is None or v.get("passed") is None:
            continue
        margin = m["margin"]
        # counterfactual: everyone in the roll call who cast nothing votes with their faction's plurality
        flip = None; hypo_igen = None
        exp = _expand(store, ts)
        if exp and plurality is not None and ts in plurality:
            plur = plurality[ts]
            extra_yes = extra_present = 0
            for _, f, pos in exp:
                if pos not in CAST and f in plur:
                    extra_present += 1
                    if plur[f] == "igen":
                        extra_yes += 1
            hypo_igen = v["igen"] + extra_yes
            base = m["base"]
            rule = m.get("rule") or ""
            if rule in ("egyszeru", "ketharmad_jelenlevo", "negyotod_jelenlevo", "relativ"):
                base = (v.get("present") or 0) + extra_present            # present-based thresholds grow with the presence
            needed_h = _needed(rule, base)
            passed_h = (hypo_igen >= needed_h) if needed_h is not None else None
            flip = (passed_h is not None and passed_h != bool(v["passed"]))
        rows.append({"ts": ts, "slug": v["slug"], "date": v["on_date"], "time": v.get("time"), "rule": m.get("rule"), "needed": m["needed"], "base": m["base"],
                     "igen": v["igen"], "nem": v["nem"], "tartozkodott": v["tartozkodott"], "margin": margin, "passed": bool(v["passed"]),
                     "yes_majority": (v["igen"] or 0) > (v["nem"] or 0), "qualified": (m.get("rule") or "") not in ("egyszeru", "relativ", ""),
                     "hypo_igen": hypo_igen, "flips_if_all_voted": flip,
                     "iromany": (v["motions"][0].get("iromany") if v.get("motions") else None), "title": (v["motions"][0].get("title") if v.get("motions") else None)})
    closest = sorted(rows, key=lambda r: (abs(r["margin"]), r["ts"]))
    sunk = [r for r in rows if r["qualified"] and not r["passed"] and r["yes_majority"]]
    flips = [r for r in rows if r["flips_if_all_voted"]]
    return {"decisions": rows, "closest": closest, "sunk_by_threshold": sunk, "absence_decisive": flips}


def _needed(rule: str, base: int) -> int | None:
    import math
    if rule == "egyszeru" or rule == "abszolut":
        return base // 2 + 1
    if rule in ("ketharmad_jelenlevo", "ketharmad_osszes"):
        return math.ceil(2 * base / 3)
    if rule in ("negyotod_jelenlevo", "negyotod_osszes"):
        return math.ceil(4 * base / 5)
    return None
