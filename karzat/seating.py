"""The real seating plan of the Országgyűlés, reconstructed from the API's <ulohely> triples.

kepviselo.cgi gives every MP a seat as (szektor, sor, szék) — sector, row, seat. What it does
not give is geometry: how many sectors there are, how wide each is, which side of the Speaker
sector 1 sits on, or which way seat numbers run within a row. This module infers the *shape*
of the chamber from the triples themselves and states every assumption in the output:

  * sectors are wedges of a horseshoe (patkó) around the Speaker's platform, laid side by side
    from the Speaker's left to the Speaker's right;
  * each sector's angular width is proportional to the widest row it contains (plus an aisle);
  * rows are concentric arcs, row 1 nearest the platform;
  * seat numbers run left→right within a row as seen from the Speaker (assumption);
  * orientation follows parlament.hu's own description of the chamber — government benches
    to the Speaker's right, opposition to the left — so the sector order is flipped if the
    governing faction turns out to sit in the low-numbered sectors;
  * sector 0 / row 0 is the ministerial front bench (miniszteri patkó) in front of the
    platform — MPs who sit there as ministers — drawn as an inner arc across the middle, not
    as a wedge (observed 2026-08-18: 12 TISZA MPs, seat numbers up to 21).

The output is a set of seat coordinates plus a `geometry` record with the numbers the
inference used, so a reader can check them against a photograph of the chamber. Konzol's
algorithmic hemicycle stays available as the fallback for MPs without a seat.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Iterable

Seat = tuple[int, int, int]   # (sector, row, seat)


def seats_from_records(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{p_azon: {sector,row,seat,faction,name}} from normalised kepviselo records that carry a 'seat'."""
    out = {}
    for r in records:
        s = r.get("seat")
        if s and s.get("sector") is not None and s.get("row") is not None and s.get("seat") is not None:
            out[r["p_azon"]] = {"sector": s["sector"], "row": s["row"], "seat": s["seat"],
                                "faction": r.get("faction"), "name": r.get("name")}
    return out


def analyse(seats: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Per-sector structure: rows, seats per row, occupancy and faction mix; plus duplicates."""
    per: dict[int, dict[str, Any]] = {}
    dup = Counter((s["sector"], s["row"], s["seat"]) for s in seats.values())
    for s in seats.values():
        sec = per.setdefault(s["sector"], {"rows": defaultdict(int), "count": 0, "factions": Counter(), "max_row": 0, "max_seat_by_row": defaultdict(int)})
        sec["count"] += 1
        sec["rows"][s["row"]] += 1
        sec["max_row"] = max(sec["max_row"], s["row"])
        sec["max_seat_by_row"][s["row"]] = max(sec["max_seat_by_row"][s["row"]], s["seat"])
        sec["factions"][s["faction"] or "?"] += 1
    sectors = {}
    for k in sorted(per):
        v = per[k]
        sectors[k] = {"count": v["count"], "rows": dict(sorted(v["rows"].items())), "max_row": v["max_row"],
                      "max_seat_by_row": dict(sorted(v["max_seat_by_row"].items())),
                      "widest_row": max(v["max_seat_by_row"].values()) if v["max_seat_by_row"] else 0,
                      "factions": dict(v["factions"].most_common())}
    return {"sectors": sectors, "n_seated": len(seats),
            "duplicates": [list(k) for k, n in dup.items() if n > 1]}


FRONT_BENCH = 0   # sector id of the ministerial bench


def infer_orientation(analysis: dict[str, Any], government: str, front_bench: int | None = FRONT_BENCH) -> dict[str, Any]:
    """Which way to lay the sectors out so the governing faction ends up on the Speaker's right.

    The front bench is excluded from the centroid (it is central by construction).
    Returns {'sector_order': [...left→right...], 'flipped': bool, 'gov_centroid': float, 'note': str}.
    """
    secs = analysis["sectors"]
    ids = sorted(i for i in secs if i != front_bench)
    if not ids:
        return {"sector_order": [], "flipped": False, "gov_centroid": None, "note": "no seats"}
    tot = sum(secs[i]["factions"].get(government, 0) for i in ids) or 1
    centroid = sum(i * secs[i]["factions"].get(government, 0) for i in ids) / tot
    mid = (ids[0] + ids[-1]) / 2
    flipped = centroid < mid           # government in low-numbered sectors → sector 1 must be on the right
    order = list(reversed(ids)) if flipped else ids
    note = (f"{government} centroid at sector {centroid:.2f} of {ids[0]}–{ids[-1]}; "
            f"{'reversed' if flipped else 'kept'} sector order so the government sits to the Speaker's right")
    return {"sector_order": order, "flipped": flipped, "gov_centroid": round(centroid, 2), "note": note}


def layout(seats: dict[str, dict[str, Any]], analysis: dict[str, Any], sector_order: list[int],
           r0: float = 70.0, row_gap: float = 17.0, aisle: float = 0.06, span: float = math.pi * 1.06,
           seat_numbers_left_to_right: bool = True, front_bench: int | None = FRONT_BENCH,
           front_bench_span: float = 1.9) -> dict[str, Any]:
    """Coordinates for every seat. Speaker's platform at the origin, seats above the x axis;
    Speaker's left = negative x. `span` slightly over π: a horseshoe, not a strict semicircle.
    The front bench (sector `front_bench`) is an inner arc of `front_bench_span` radians centred
    on the platform, at radius r0 − 1.4·row_gap."""
    secs = analysis["sectors"]
    sector_order = [i for i in sector_order if i != front_bench]
    widths = [max(1, secs[i]["widest_row"]) for i in sector_order]
    total_w = sum(widths)
    n_ais = len(sector_order) - 1
    usable = span - aisle * n_ais
    # angle from the Speaker's left (π + (span-π)/2) sweeping clockwise to the right
    start = math.pi / 2 + span / 2
    wedges = {}
    a = start
    for sec, w in zip(sector_order, widths):
        wa = usable * w / total_w
        wedges[sec] = (a, a - wa)          # (left edge angle, right edge angle) — angles decrease left→right
        a = a - wa - aisle
    coords = {}
    fb_r = r0 - 1.4 * row_gap
    fb_max = max((s["seat"] for s in seats.values() if s["sector"] == front_bench), default=1) if front_bench is not None else 1
    for azon, s in seats.items():
        sec, row, seat_no = s["sector"], s["row"], s["seat"]
        if front_bench is not None and sec == front_bench:
            frac = (seat_no - 0.5) / fb_max
            if not seat_numbers_left_to_right:
                frac = 1 - frac
            ang = (math.pi / 2 + front_bench_span / 2) - front_bench_span * frac
            coords[azon] = {"x": round(fb_r * math.cos(ang), 2), "y": round(-fb_r * math.sin(ang), 2),
                            "sector": sec, "row": row, "seat": seat_no, "front_bench": True}
            continue
        left, right = wedges[sec]
        k = secs[sec]["max_seat_by_row"].get(row, seat_no) or 1
        # evenly spread within the wedge, inset by half a slot so seats don't sit on the aisle
        frac = (seat_no - 0.5) / k if k else 0.5
        if not seat_numbers_left_to_right:
            frac = 1 - frac
        ang = left + (right - left) * frac
        r = r0 + (row - 1) * row_gap
        coords[azon] = {"x": round(r * math.cos(ang), 2), "y": round(-r * math.sin(ang), 2), "sector": sec, "row": row, "seat": seat_no}
    max_row = max((s["row"] for s in seats.values()), default=1)
    return {"coords": coords,
            "geometry": {"r0": r0, "row_gap": row_gap, "aisle_rad": aisle, "span_rad": round(span, 4),
                         "sector_order_left_to_right": sector_order, "sector_wedges_rad": {k: [round(v[0], 4), round(v[1], 4)] for k, v in wedges.items()},
                         "front_bench_sector": front_bench, "front_bench_radius": fb_r, "front_bench_span_rad": front_bench_span, "front_bench_max_seat": fb_max,
                         "max_row": max_row, "outer_radius": r0 + (max_row - 1) * row_gap,
                         "seat_numbers_left_to_right": seat_numbers_left_to_right,
                         "assumptions": ["sector width ∝ widest row (+ aisle)", "rows concentric, row 1 nearest the platform",
                                         "seat numbers left→right within a row (Speaker's view)",
                                         "orientation: government to the Speaker's right (parlament.hu description)",
                                         "sector 0 / row 0 = ministerial front bench, drawn as an inner arc"]}}
