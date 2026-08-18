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
           r0: float = 66.0, row_gap: float = 7.6, aisle: float = 0.07, span: float = math.pi * 1.06,
           seat_numbers_left_to_right: bool = True, front_bench: int | None = FRONT_BENCH,
           front_bench_span: float | None = None, front_bench_radius_rows: float = 2.1) -> dict[str, Any]:
    """Coordinates for every seat. Speaker's platform at the origin, seats above the x axis;
    Speaker's left = negative x. `span` slightly over π: a horseshoe, not a strict semicircle.

    Geometry (all estimated — the API gives sector / row / seat numbers, nothing metric):
    seats are the same width everywhere, so a sector's wedge is as wide as its tightest row
    needs (max over rows of seats ÷ radius) and the common seat pitch `w` (arc length) is what
    the span allows; rows are concentric, `row_gap` apart. The seat numbers a row skips are the
    room's empty seats (they are drawn faint), and rows nobody sits in are unknown. The front
    bench (sector `front_bench`) is the horseshoe's inner circle — parlament.hu: the ministers sit
    "a kormányzati patkó bársonyszékein", the Katolikus Lexikon: "a belső körben a mindenkori
    kormány miniszterei ülnek, középső részén az elnöki emelvény és a gyorsírók asztala" — so it is
    drawn as a semicircle centred on the platform at radius r0 − front_bench_radius_rows·row_gap,
    its 21 armchairs at whatever pitch the semicircle gives them (wider than an MP's seat, as
    velvet armchairs are); `front_bench_span` overrides the semicircle."""
    secs = analysis["sectors"]
    sector_order = [i for i in sector_order if i != front_bench]
    n_ais = len(sector_order) - 1
    usable = span - aisle * n_ais

    def radius(row: int) -> float:
        return r0 + (row - 1) * row_gap

    # angular need per sector: its tightest row (most seats per unit radius), in units of the seat pitch
    need = {}
    for sec in sector_order:
        rows = secs[sec]["max_seat_by_row"] or {1: 1}
        need[sec] = max(max(1, k) / radius(row) for row, k in rows.items())
    pitch = usable / sum(need.values())          # arc length of one seat, uniform across the room
    # angle from the Speaker's left (π + (span-π)/2) sweeping clockwise to the right
    start = math.pi / 2 + span / 2
    wedges = {}
    a = start
    for sec in sector_order:
        wa = need[sec] * pitch
        wedges[sec] = (a, a - wa)          # (left edge angle, right edge angle) — angles decrease left→right
        a = a - wa - aisle
    fb_r = r0 - front_bench_radius_rows * row_gap
    fb_max = max((s["seat"] for s in seats.values() if s["sector"] == front_bench), default=1) if front_bench is not None else 1
    fb_span = front_bench_span if front_bench_span is not None else math.pi

    def place(sec: int, row: int, seat_no: int) -> tuple[float, float, bool]:
        if front_bench is not None and sec == front_bench:
            frac = (seat_no - 0.5) / fb_max
            if not seat_numbers_left_to_right:
                frac = 1 - frac
            ang = (math.pi / 2 + fb_span / 2) - fb_span * frac
            return round(fb_r * math.cos(ang), 2), round(-fb_r * math.sin(ang), 2), True
        left, right = wedges[sec]
        k = secs[sec]["max_seat_by_row"].get(row, seat_no) or 1
        # centred in the wedge at the row's own pitch, so short inner rows are not stretched to the aisles
        row_span = min(right - left, -(k * pitch / radius(row)))          # angles decrease left→right (negative span)
        mid = (left + right) / 2
        frac = (seat_no - 0.5) / k if k else 0.5
        if not seat_numbers_left_to_right:
            frac = 1 - frac
        ang = mid - row_span / 2 + row_span * frac
        r = radius(row)
        return round(r * math.cos(ang), 2), round(-r * math.sin(ang), 2), False

    coords = {}
    for azon, s in seats.items():
        x, y, fb = place(s["sector"], s["row"], s["seat"])
        coords[azon] = {"x": x, "y": y, "sector": s["sector"], "row": s["row"], "seat": s["seat"], **({"front_bench": True} if fb else {})}
    # empty seats: numbers a row skips below its highest occupied seat (front bench: below fb_max)
    occupied = {(s["sector"], s["row"], s["seat"]) for s in seats.values()}
    empty = []
    rows_by_sector = {sec: secs[sec]["max_seat_by_row"] for sec in secs}
    for sec, rows in rows_by_sector.items():
        if front_bench is not None and sec == front_bench:
            for n in range(1, fb_max + 1):
                if (sec, 0, n) not in occupied and (sec, 1, n) not in occupied:
                    x, y, _ = place(sec, 0, n)
                    empty.append({"x": x, "y": y, "sector": sec, "row": 0, "seat": n, "front_bench": True})
            continue
        for row, k in rows.items():
            for n in range(1, (k or 0) + 1):
                if (sec, row, n) not in occupied:
                    x, y, _ = place(sec, row, n)
                    empty.append({"x": x, "y": y, "sector": sec, "row": row, "seat": n})
    max_row = max((s["row"] for s in seats.values()), default=1)
    return {"coords": coords, "empty_seats": empty,
            "geometry": {"r0": r0, "row_gap": row_gap, "aisle_rad": aisle, "span_rad": round(span, 4), "seat_pitch": round(pitch, 3),
                         "node_radius": round(0.42 * pitch, 2),
                         "sector_order_left_to_right": sector_order, "sector_wedges_rad": {k: [round(v[0], 4), round(v[1], 4)] for k, v in wedges.items()},
                         "front_bench_sector": front_bench, "front_bench_radius": fb_r, "front_bench_span_rad": round(fb_span, 4), "front_bench_max_seat": fb_max,
                         "max_row": max_row, "outer_radius": r0 + (max_row - 1) * row_gap,
                         "seat_numbers_left_to_right": seat_numbers_left_to_right,
                         "assumptions": ["seats are the same width everywhere: a sector's wedge is as wide as its tightest row needs, and every row sits at one common pitch (+ aisles)",
                                         "rows concentric, row 1 nearest the platform; row spacing is a drawing constant",
                                         "seat numbers left→right within a row (Speaker's view)",
                                         "the seat numbers a row skips are empty seats; rows nobody occupies are unknown and not drawn",
                                         "orientation: government to the Speaker's right (parlament.hu description)",
                                         "sector 0 / row 0 = the ministerial patkó: the horseshoe's inner circle, drawn as a semicircle round the platform"]}}
