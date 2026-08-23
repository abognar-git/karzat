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
        if (inp["by_ts"].get(ts) or {}).get("kind") == "jelenlet":   # a quorum check is presence, not agreement
            continue
        tallies = faction_tallies_from_store(store, ts)
        if not tallies:
            continue
        pv = {}
        for f, t in tallies.items():
            r, ai = rice(t["igen"], t["nem"]), agreement_index(t["igen"], t["nem"], t["tartozkodott"])
            cast = t["igen"] + t["nem"] + t["tartozkodott"]
            if cast:
                pos, n = max(((p, t[p]) for p in CAST), key=lambda kv: kv[1])
                if sum(1 for p in CAST if t[p] == n) == 1:      # a tie is no plurality — and then nobody dissents
                    plur.setdefault(ts, {})[f] = pos
                    dissent = cast - n
                else:
                    dissent = 0
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
                    extra_present += 1                            # the base is the votes cast: everyone who would now cast one joins it
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
    from .majority import Rule, needed_for_base
    try:
        return needed_for_base(Rule(rule), base)
    except ValueError:
        return None


# -- bill journeys ------------------------------------------------------------------------------

import re as _re

_NUM = _re.compile(r"(\d+)")
_PREFIX = _re.compile(r"^([A-ZÁÉÍÓÖŐÚÜŰ]+)/(\d+)$")


def bill_key(iromany: str | None) -> tuple[int | None, str | None]:
    """'T/51' → (51, 'T'); '11152/8' (an amendment of bill 11152) → (11152, None); None → (None, None).
    Iromány numbers are one number space per cycle, so the number alone identifies the bill."""
    if not iromany:
        return None, None
    m = _PREFIX.match(iromany.strip())
    if m:
        return int(m.group(2)), m.group(1)
    m = _NUM.match(iromany.strip())
    return (int(m.group(1)), None) if m else (None, None)


def motion_is_own_stage(mo: dict[str, Any], prefix: str | None) -> bool:
    """Is this motion the bill itself (a stage of it: general debate, closing vote, egységes javaslat …) or one of its
    amendments? The API prints the bill's own number ('T/51') for some stages and the 'N/k' form for others (the closing
    vote on the egységes javaslat is '51/12' too), so the number's shape does not tell; the outcome text does:
    'önálló indítvány …' names the bill, 'módosító …' names an amendment."""
    outcome = (mo.get("outcome") or "").casefold()
    if "önálló" in outcome:
        return True
    if "módosító" in outcome:
        return False
    return prefix is not None


def bills(inp: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Every bill with at least one roll call: its own number/prefix/title/href where a vote named the bill itself,
    and every vote about it (the bill's own stages and its amendments) in order."""
    out: dict[int, dict[str, Any]] = {}
    for ts in inp["order"]:
        v = inp["by_ts"][ts]
        for mo in v.get("motions") or []:
            n, prefix = bill_key(mo.get("iromany"))
            if n is None:
                continue
            b = out.setdefault(n, {"number": n, "prefix": None, "title": None, "href": None, "votes": [], "amendment_votes": 0, "own_votes": 0})
            own = motion_is_own_stage(mo, prefix)
            if prefix:
                b["prefix"] = b["prefix"] or prefix
                b["href"] = b["href"] or mo.get("href")
            b["title"] = b["title"] or mo.get("title")
            if not b["votes"] or b["votes"][-1]["ts"] != ts:
                b["votes"].append({"ts": ts, "slug": v["slug"], "date": v["on_date"], "time": v.get("time"), "iromany": mo.get("iromany"), "outcome": mo.get("outcome"),
                                   "igen": v.get("igen"), "nem": v.get("nem"), "tartozkodott": v.get("tartozkodott"), "result": v.get("result_raw"),
                                   "rule": (v.get("majority") or {}).get("rule"), "mode": v.get("mode"), "kind": v.get("kind"), "own": own})
                if own:
                    b["own_votes"] += 1
                else:
                    b["amendment_votes"] += 1
            elif own and not b["votes"][-1]["own"]:
                b["votes"][-1]["own"] = True                     # one vote, several motions: the bill's own stage wins
                b["own_votes"] += 1; b["amendment_votes"] -= 1
    for b in out.values():
        b["first"] = b["votes"][0]["date"]; b["last"] = b["votes"][-1]["date"]
        b["final_outcome"] = b["votes"][-1]["outcome"]; b["final_result"] = b["votes"][-1]["result"]
        b["label"] = f'{b["prefix"]}/{b["number"]}' if b["prefix"] else str(b["number"])
    return out


# -- time series --------------------------------------------------------------------------------

def monthly(inp: dict[str, Any], co: dict[str, Any] | None = None) -> dict[str, Any]:
    """Per month of the cycle: votes, decisions, qualified-majority decisions, roll calls; per faction its members'
    participation (cast / in roll), dissent (against / cast) and cast-weighted agreement index."""
    store, order, by_ts = inp["store"], inp["order"], inp["by_ts"]
    per_mp = inp["alignment"]["per_mp"]
    months: dict[str, dict[str, Any]] = {}
    for ts in order:
        v = by_ts[ts]
        m = v["on_date"][:7]
        d = months.setdefault(m, {"month": m, "votes": 0, "decisions": 0, "qualified": 0, "roll_calls": 0, "days": set(), "factions": {}})
        d["votes"] += 1
        # The sitting days behind the month, because a month is not a unit of parliamentary activity and the
        # bar chart reads as though it were: December 2025 shows 182 votes over 4 sitting days and December
        # 2023 shows 181 over 3 — near-identical columns over 46 and 60 votes a day. The count is the House's
        # output; the count per sitting day is its pace, and the page can only offer both if this is here.
        if v.get("on_date"):
            d["days"].add(v["on_date"])
        if v.get("kind") == "dontes":
            d["decisions"] += 1
            if (v.get("majority") or {}).get("rule") not in (None, "egyszeru", "relativ"):
                d["qualified"] += 1
        if store["positions"].get(ts):
            d["roll_calls"] += 1
    # per faction per month from the alignment records (each MP's votes carry faction, position, align)
    for azon, rec in per_mp.items():
        for v in rec["votes"]:
            m = v["ts"][:4] + "-" + v["ts"][5:7]
            f = v.get("faction") or "—"
            fd = months.setdefault(m, {"month": m, "votes": 0, "decisions": 0, "qualified": 0, "roll_calls": 0, "days": set(), "factions": {}})["factions"].setdefault(f, {"in_roll": 0, "cast": 0, "with": 0, "against": 0})
            fd["in_roll"] += 1
            if v["position"] in CAST:
                fd["cast"] += 1
                if v.get("align") == "with":
                    fd["with"] += 1
                elif v.get("align") == "against":
                    fd["against"] += 1
    if co:
        for ts, per in co["per_vote"].items():
            m = ts[:4] + "-" + ts[5:7]
            for f, x in per.items():
                if m in months and f in months[m]["factions"] and x["cast"]:
                    fd = months[m]["factions"][f]
                    fd["ai_w"] = fd.get("ai_w", 0.0) + x["ai"] * x["cast"]; fd["ai_n"] = fd.get("ai_n", 0) + x["cast"]
    out = []
    for m in sorted(months):
        d = months[m]
        d["days"] = len(d["days"])
        d["per_day"] = (d["votes"] / d["days"]) if d["days"] else None
        for f, fd in d["factions"].items():
            fd["participation"] = fd["cast"] / fd["in_roll"] if fd["in_roll"] else None
            fd["dissent"] = fd["against"] / fd["cast"] if fd["cast"] else None
            fd["ai"] = (fd["ai_w"] / fd["ai_n"]) if fd.get("ai_n") else None
            fd.pop("ai_w", None); fd.pop("ai_n", None)
        out.append(d)
    return {"months": out}


# -- floor time ----------------------------------------------------------------------------------

def split_event(event: str | None, iromany: str | None) -> tuple[str | None, str | None]:
    """An agenda-item title into the stage it is and the matter it is about.

    Every agenda item that names a bill is written '<stage> <number> <title>' — 'Általános vita folytatása
    T/4181 Magyarország 2024. évi központi költségvetéséről'. What precedes the number is the stage, what
    follows is the subject, and both are the House's own words.

    One thing this does *not* prove, and the sentence that used to stand here implied it did: that every
    agenda item contains its bill number is true by construction, because `parse_felszolalasok` extracts
    `iromany` by regexing this same string. It would hold for garbled input too. What is external evidence
    is the shape of what comes out — across all eight cycles the stage side is a closed vocabulary of a few
    dozen phrases the House reuses, no extracted subject is empty, and no partition cuts a number in half
    (the character after the match is never another digit, so 'T/12' is never found inside 'T/123'). A
    subject may perfectly well *begin* with a digit — 'A/167 1,3 millióval csökkent a szegénységben élők
    száma' is the House's own question, and my first version of that check flagged it, which was measuring
    my expectations rather than the record. That distinction is what the tests encode.

    This matters because the stage is exactly what fragments a year. The 2024 budget appears under five
    different agenda items — the general debate begun, continued, continued and closed, the committee
    reports, the closing vote — and counted as five items it is five middling entries instead of the
    largest single thing the House did that year. Dropping the stage from the identity collapses them;
    keeping it as its own breakdown loses nothing.

    A joint debate names several bills ('T/8217 · T/8218'), and only the first number appears where the
    title begins, so the whole string after it is the subject of all of them together."""
    if not event:
        return None, None
    first = (iromany or "").split(" · ")[0].strip()
    if not first or first not in event:
        return None, event.strip() or None
    before, _, after = event.partition(first)
    return (before.strip() or None), (after.strip(" –—-:,") or None)


def floor_time(inp: dict[str, Any]) -> dict[str, Any]:
    """Where the cycle's substantive floor time went: per year, per debate, and in what form.

    The question "what does the House talk about" usually invites a topic model, and this project does not
    build one, because the labels would be mine. They do not need to be: the record already says what every
    speech is about. All of them carry an agenda item and four in five carry a bill number, and both are
    written by the House. Nothing here classifies anything — it adds up seconds and groups them by strings
    the source supplies.

    Three decisions worth naming, because each of them could have been made dishonestly:

      **The row is the debate, not the bill.** A joint debate — Finland's NATO accession beside Sweden's,
      the Fundamental Law amendment beside the act implementing it — is one item on the agenda and one
      block of time. Splitting it between its bills would invent a number, and charging it all to the
      first would understate the rest, so it stays one row wearing every number it carried. Cycle 42
      spends 8.6% of its floor time this way. The shares therefore sum to the year, which is the point.

      **The slots keep their own lines.** A fifth of the time sits under agenda items that name no bill,
      and 'Napirend előtti felszólalások' alone is a sixth of a year. That is not a subject, it is a
      container — spreading it over the subjects would quietly redistribute the largest single entry.

      **Faction size is measured, not looked up.** The API publishes no seat count, so the denominator
      here is the members a faction had in that year's roll calls, absentees included, which is the
      roster the record can actually show. It is close to the mandate count and it is not the same thing,
      so every page that prints the ratio says which it is.

    `forms` counts the kinds of speech, and those are not comparable between cycles: the pre-2014 lists say
    'expozé' and 'sürgősség indoklása' where the later ones say 'előterjesztő nyitóbeszéde', so a chart of
    one cycle's vocabulary against another's would be measuring the transcriber.

    One assumption underlies every hour on these pages and it is worth stating that it was tested rather
    than assumed. `duration_s` is the API's `videoido` — a video timecode, not a stopwatch at the microphone
    — so if it carried the walk to the podium, the chair's interruptions or clip padding, every figure here
    would be wrong in the same direction and no internal sum would reveal it, because they would all still
    tie. The transcript is an independent witness: over the 17,448 speeches that have both a length and a
    loaded text, characters against seconds correlate at r = 0.98 and the median rate is 910 characters a
    minute, which is ordinary Hungarian speech. Padding would show as short speeches yielding fewer
    characters per second than long ones; the record does the opposite (1,007 a minute under a minute,
    847 over ten), which is what pauses in a long speech look like. The clock measures speaking."""
    sp = (inp.get("speeches") or {}).get("speeches") or []
    rows = [r for r in sp if not r.get("technical") and r.get("duration_s") and (r.get("date") or "")]
    years: dict[str, dict[str, Any]] = {}
    stages_all: dict[str, int] = {}
    forms_all: dict[str, int] = {}

    def bucket(store: dict, key: Any, seed: dict) -> dict:
        d = store.get(key)
        if d is None:
            d = store[key] = dict(seed, seconds=0, speeches=0, factions={}, forms={})
        return d

    for r in rows:
        y = r["date"][:4]
        secs = r["duration_s"]
        yr = years.get(y)
        if yr is None:
            yr = years[y] = {"year": y, "seconds": 0, "speeches": 0, "days": set(), "debates": {}, "slots": {},
                             "forms": {}, "factions": {}, "expected": {}, "seats": {}, "weighted": 0}
        yr["seconds"] += secs
        yr["speeches"] += 1
        yr["days"].add(r.get("ulnap"))
        form = r.get("kind") or "—"
        fac = r.get("faction") or None
        yr["forms"][form] = yr["forms"].get(form, 0) + secs
        forms_all[form] = forms_all.get(form, 0) + secs
        if fac:
            yr["factions"][fac] = yr["factions"].get(fac, 0) + secs
        stage, subject = split_event(r.get("event"), r.get("iromany"))
        numbers = [x.strip() for x in (r.get("iromany") or "").split(" · ") if x.strip()]
        if numbers:
            # the debate's identity is its bills and its subject — the stage is deliberately not part of it
            d = bucket(yr["debates"], (" · ".join(numbers), subject or ""),
                       {"bills": numbers, "title": subject, "stages": {}})
            if stage:
                d["stages"][stage] = d["stages"].get(stage, 0) + secs
                stages_all[stage] = stages_all.get(stage, 0) + secs
        else:
            d = bucket(yr["slots"], subject or "—", {"event": subject or "—"})
        d["seconds"] += secs
        d["speeches"] += 1
        if fac:
            d["factions"][fac] = d["factions"].get(fac, 0) + secs
        d["forms"][form] = d["forms"].get(form, 0) + secs

    # THE DENOMINATOR, and it has to be time-resolved because the numerator is.
    #
    # Each speech carries its own faction label, so a faction that dissolved in March contributes no seconds
    # from April. Counting its members over the whole year and dividing charges it twelve months of size for
    # three months of speech. The MDF sat in cycle 38 until 16 March 2009 and appears in no roll call after
    # it — a year-long roster made it 0.51× where the House it actually spoke in makes it about 3.3×, and the
    # page colours anything under 1.00 differently, so the picture flipped too, not only the figure.
    #
    # A roll call lists the whole House, absentees included: 383–386 rows in a 386-seat chamber, 197–199 in a
    # 199-seat one. So each one is a snapshot of the composition on its date, and the honest expectation for
    # a faction is its share of the chamber *at the moments the speaking happened* — every speech weighted by
    # its own length against the roll call in force that day. Both sides of the ratio then answer the same
    # question about the same instants.
    #
    # This also disposes of a subtler error the union count carried: summing per-faction member sets
    # double-counts anyone who changed faction mid-year (403 against 392 real people in 2009), so the
    # denominator exceeded the House. A snapshot cannot, because it is the House.
    store = inp.get("store") or {}
    fac_names = store.get("factions") or []
    snaps: dict[str, dict[str, int]] = {}
    for ts, rows_ in (store.get("positions") or {}).items():
        if not rows_:
            continue
        counts: dict[str, int] = {}
        for _key, fi, _code in rows_:
            if 0 <= fi < len(fac_names):
                counts[fac_names[fi]] = counts.get(fac_names[fi], 0) + 1
        if counts:
            snaps[ts[:10].replace(".", "-")] = counts       # the day's last roll call speaks for the day
    snap_dates = sorted(snaps)
    if snap_dates:
        import bisect as _bisect
        for r in rows:
            i = _bisect.bisect_right(snap_dates, r["date"]) - 1
            counts = snaps[snap_dates[max(i, 0)]]           # before the first roll call: the earliest House
            house = sum(counts.values()) or 1
            yr = years[r["date"][:4]]
            secs = r["duration_s"]
            for f, n in counts.items():
                yr["expected"][f] = yr["expected"].get(f, 0.0) + secs * n / house
                yr["seats"][f] = yr["seats"].get(f, 0.0) + secs * n
            yr["weighted"] += secs

    out_years = []
    for y in sorted(years):
        yr = years[y]
        yr["days"] = len(yr["days"])
        w = yr["weighted"] or 1
        yr["seats"] = {f: n / w for f, n in sorted(yr["seats"].items(), key=lambda kv: -kv[1])}
        yr["house"] = sum(yr["seats"].values())
        yr["expected"] = {f: e / w for f, e in sorted(yr["expected"].items(), key=lambda kv: -kv[1])}
        yr["debates"] = sorted(yr["debates"].values(), key=lambda d: (-d["seconds"], d["bills"][0]))
        yr["slots"] = sorted(yr["slots"].values(), key=lambda d: (-d["seconds"], d["event"]))
        yr["attributed"] = sum(d["seconds"] for d in yr["debates"])
        for d in yr["debates"]:
            d["stages"] = dict(sorted(d["stages"].items(), key=lambda kv: -kv[1]))
        for d in yr["debates"] + yr["slots"]:
            d["factions"] = dict(sorted(d["factions"].items(), key=lambda kv: -kv[1]))
            d["forms"] = dict(sorted(d["forms"].items(), key=lambda kv: -kv[1]))
        for k in ("forms", "factions"):
            yr[k] = dict(sorted(yr[k].items(), key=lambda kv: -kv[1]))
        out_years.append(yr)
    return {"years": out_years, "seconds": sum(y["seconds"] for y in out_years),
            "speeches": sum(y["speeches"] for y in out_years),
            "stages": dict(sorted(stages_all.items(), key=lambda kv: -kv[1])),
            "forms": dict(sorted(forms_all.items(), key=lambda kv: -kv[1]))}
