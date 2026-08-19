#!/usr/bin/env python3
"""Build the site — site/index.html and one page per vote under site/szavazas/ — from committed inputs.

    python3 -m scripts.build_site               # index + every vote page
    python3 -m scripts.build_site --index-only  # just the index
    python3 -m scripts.build_site --check       # exit 1 if the committed index or site/assets/* differ from a fresh build

Inputs (all committed, all produced by scripts/derive_* from the cache):
  data/derived/votes_index.json      every listed vote: motion, majority verdict, tallies, sitting days
  data/derived/votes_positions.json  every roll call, compact (azon, faction, position code)
  data/derived/seating.json          the reconstructed chamber (coordinates per p_azon)
  data/derived/first_light.json      the headline counts
  data/derived/hero_vote.json        kept for the README gate; the hero itself is rendered like any vote page
  data/derived/mps.json              every person in the roll calls: mandate, seat, faction history, elections
  config/factions.yml                faction colours and left→right order

Outputs: site/index.html and site/assets/karzat.css + karzat.js (committed, checked), site/szavazas/<slug>.html
× votes and site/kepviselo/<p_azon>.html × MPs + site/kepviselo/index.html (generated, git-ignored). The look is
Konzol's console language (see reference/konzol/): dark ground, dot grid, corner-bracketed panels, mono labels,
a terminal footer whose log lines are real values, and a boot sequence that reduced-motion users never see.

Deterministic: no clock is read (the freshness sentence is computed at the derive timestamp
stored in the inputs). site/index.html is committed and guarded by --check + tests; the vote
pages are generated (git-ignored) and reproducible from the committed inputs.
"""

from __future__ import annotations

import argparse
import html
from collections import Counter
import json
import os
import math
import re
import sys
from datetime import date as _date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karzat.freshness import BUDAPEST, assess  # noqa: E402
from karzat.load import cycle_of_date  # noqa: E402
from karzat.normalise import CYCLE_LABELS as CYCLE_LABELS_  # noqa: E402
from karzat.majority import PAYLOAD_RULES, RULES, Rule  # noqa: E402
from karzat import export as ex  # noqa: E402
from karzat import analytics as an  # noqa: E402
from karzat import feeds as fd  # noqa: E402

DERIVED = ROOT / "data" / "derived"
SEATING = DERIVED / "seating.json"
SITE_DIR = ROOT / "site"
SITE = SITE_DIR / "index.html"
ASSETS_DIR = SITE_DIR / "assets"
CORNERS = '<span class="kz-corner tl"></span><span class="kz-corner tr"></span><span class="kz-corner bl"></span><span class="kz-corner br"></span>'
FACTIONS = ROOT / "config" / "factions.yml"

POSITION_ORDER = ["igen", "nem", "tartozkodott", "jelen_nem_szavazott", "nem_szavazott", "bejelentett_hianyzo", "igazoltan_tavol"]
POSITION_LABEL = {"igen": "igen", "nem": "nem", "tartozkodott": "tartózkodott", "jelen_nem_szavazott": "jelen, nem szavazott",
                  "nem_szavazott": "nem szavazott", "bejelentett_hianyzo": "előre bejelentett hiányzó", "igazoltan_tavol": "igazoltan távol"}
HU_MONTHS = ['január', 'február', 'március', 'április', 'május', 'június', 'július', 'augusztus', 'szeptember', 'október', 'november', 'december']


# -- inputs -----------------------------------------------------------------------------------

def factions(cycle: int = 43) -> list[dict]:
    """Minimal reader for config/factions.yml (id, name, colour, sort_order) — no YAML dependency.

    `factions:` is the current cycle (43); `factions_ckl<N>:` blocks describe earlier cycles.
    """
    out, cur, block = [], None, None
    want = "factions" if cycle == 43 else f"factions_ckl{cycle}"
    for line in FACTIONS.read_text(encoding="utf-8").splitlines():
        top = re.match(r"^([A-Za-z_0-9]+):\s*(?:#.*)?$", line)
        if top:
            block = top.group(1)
            cur = None
            continue
        if block != want:
            continue
        m = re.match(r"\s*-\s*id:\s*\"?([^\"#]+?)\"?\s*(?:#.*)?$", line)
        if m:
            cur = {"id": m.group(1).strip()}
            out.append(cur)
            continue
        if cur is None:
            continue
        for key, pat in (("name", r'\s*name:\s*"?([^"#]+?)"?\s*(?:#.*)?$'),
                         ("colour", r'\s*colour:\s*"?(#[0-9A-Fa-f]{3,8})"?'),
                         ("sort_order", r'\s*sort_order:\s*(\d+)')):
            m = re.match(pat, line)
            if m:
                cur[key] = int(m.group(1)) if key == "sort_order" else m.group(1).strip()
    return sorted([f for f in out if "sort_order" in f], key=lambda f: f["sort_order"])


CURRENT_CYCLE = 43
CYCLE_DIR = {43: ""}                     # earlier cycles live one level down: site/ckl<N>/
CYCLE_SPAN = {43: "2026-05-09 –", 42: "2022-05-02 – 2026-05-08", 41: "2018-05-08 – 2022-05-01", 40: "2014-05-06 – 2018-05-07",
              39: "2010-05-14 – 2014-05-05", 38: "2006-05-16 – 2010-05-13", 37: "2002-05-15 – 2006-05-15", 36: "1998-06-18 – 2002-05-14",
              35: "1994-06-28 – 1998-06-17", 34: "1990-05-02 – 1994-06-27"}


def load_json(path: Path):
    """path.json, or path.json.gz next to it (a whole cycle's index and store are committed gzipped)."""
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    gz = path.with_suffix(path.suffix + ".gz")
    if gz.exists():
        import gzip
        with gzip.open(gz, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    raise FileNotFoundError(path)


def available_cycles() -> list[int]:
    """Cycles with derived inputs: the current one plus every votes_index_ckl<N>.json(.gz), newest first."""
    out = {CURRENT_CYCLE}
    for p in DERIVED.glob("votes_index_ckl*.json*"):
        out.add(int(re.search(r"ckl(\d+)", p.name).group(1)))
    return sorted(out, reverse=True)


def cycle_dir(cycle: int) -> str:
    return CYCLE_DIR.get(cycle, f"ckl{cycle}/")


_TOTALS: dict = {}


_HU_FOLD = str.maketrans({"á": "a", "é": "e", "í": "i", "ó": "o", "ö": "o~", "ő": "o~", "ú": "u", "ü": "u~", "ű": "u~"})


def name_key(name: str | None) -> str:
    """Sort key for a person's name: no honorific (Dr., ifj. …), Hungarian order — á with a, ö/ő after every o,
    ü/ű after every u (a rough collation; the digraphs are left alone)."""
    from karzat.normalise import strip_honorific
    return strip_honorific(name or "").casefold().translate(_HU_FOLD)


def cut(text: str, n: int) -> str:
    """Shorten to about n characters at a word boundary, with an ellipsis; short strings pass through untouched."""
    t = (text or "").strip()
    if len(t) <= n:
        return t
    head = t[:n]
    if " " in head[n // 2:]:
        head = head[:head.rfind(" ")]
    return head.rstrip(" ,;:–-") + "…"


def ext_url(u: str | None) -> str:
    """An outbound link, http(s) only: a bare host gets https://, anything with another scheme is dropped."""
    u = (u or "").strip()
    if not u:
        return ""
    if re.match(r"^https?://", u, re.I):
        return u
    if re.match(r"^[a-z][a-z0-9+.-]*:", u, re.I):
        return ""
    return "https://" + u


def hu_dec(x, places: int = 2) -> str:
    """A decimal with the Hungarian comma: 0.87 → '0,87'."""
    return "—" if x is None else f"{x:.{places}f}".replace(".", ",")


def hu_num(n) -> str:
    """31 402 — thousands with a non-breaking space, as Hungarian prints them."""
    return f"{n:,}".replace(",", "\u00a0") if isinstance(n, int) else str(n)


def site_totals() -> dict:
    """What the whole site holds, computed once from every cycle's derived inputs: cycles, the span of dates,
    votes, roll calls, disagreements, and the union of the rosters. Numbers here are only ever what is loaded."""
    if _TOTALS:
        return _TOTALS
    cycles = available_cycles()
    votes = rolls = disagreements = 0
    starts, ends, people = [], [], set()
    for c in cycles:
        sfx = "" if c == CURRENT_CYCLE else f"_ckl{c}"
        fl = load_json(DERIVED / f"first_light{sfx}.json")
        idx = load_json(DERIVED / f"votes_index{sfx}.json")
        votes += fl["votes"]
        rolls += idx.get("details_cached", 0) - fl.get("secret_ballots", 0)
        disagreements += len(idx.get("disagreements") or [])
        starts.append(fl["window"]["from"]); ends.append(fl["window"]["to"])
        mp_path = DERIVED / f"mps{sfx}.json"
        if mp_path.exists():
            people |= set(load_json(mp_path)["mps"])
    _TOTALS.update({"cycles": cycles, "from": min(starts), "to": max(ends), "votes": votes, "roll_calls": rolls,
                    "disagreements": disagreements, "people": len(people)})
    return _TOTALS


def load_inputs(cycle: int = CURRENT_CYCLE) -> dict:
    sfx = "" if cycle == CURRENT_CYCLE else f"_ckl{cycle}"
    idx = load_json(DERIVED / f"votes_index{sfx}.json")
    store = load_json(DERIVED / f"votes_positions{sfx}.json")
    fl = load_json(DERIVED / f"first_light{sfx}.json")
    plan = json.loads(SEATING.read_text(encoding="utf-8")) if (cycle == CURRENT_CYCLE and SEATING.exists()) else None   # seats are present-tense in the API
    mps_path = DERIVED / f"mps{sfx}.json"
    mps = load_json(mps_path)["mps"] if mps_path.exists() else {}
    facs = factions(cycle)
    sp_path = DERIVED / f"speeches{sfx}.json"
    speeches = load_json(sp_path) if (sp_path.exists() or sp_path.with_suffix(".json.gz").exists()) else None   # the cycle's speech lists, when synced
    tx_path = DERIVED / f"speech_texts{sfx}.json"
    texts = load_json(tx_path) if (tx_path.exists() or tx_path.with_suffix(".json.gz").exists()) else None      # the fetched texts, when any
    inp = {"idx": idx, "store": store, "fl": fl, "plan": plan, "facs": facs, "mps": mps, "speeches": speeches, "texts": texts,
           "by_ts": {v["ts"]: v for v in idx["votes"]}, "order": [v["ts"] for v in idx["votes"]]}
    inp["alignment"] = compute_alignment(inp)
    inp["cycle"] = cycle
    inp["closed"] = cycle != CURRENT_CYCLE
    inp["base_depth"] = 0 if cycle == CURRENT_CYCLE else 1
    inp["cycles"] = available_cycles()
    # the same person in the other cycles' rosters, for cross-links between an MP's pages
    inp["also_in"] = {}
    for c in inp["cycles"]:
        if c == cycle:
            continue
        other = DERIVED / ("mps.json" if c == CURRENT_CYCLE else f"mps_ckl{c}.json")
        if other.exists():
            inp["also_in"][c] = set(load_json(other)["mps"])
    return inp


CAST = ("igen", "nem", "tartozkodott")


def compute_alignment(inp: dict) -> dict:
    """One pass over every roll call: per vote the faction pluralities, per MP the record.

    Definitions (also printed on the pages): an MP voted *with* their faction when their cast
    position (igen / nem / tartózkodott) equals the plurality of the faction's cast positions in
    that vote; *against* when it differs; positions that are not a cast vote (jelen, nem szavazott;
    nem szavazott; előre bejelentett hiányzó) are participation, not alignment. A quorum check
    (jelenlét megállapítása) counts as participation only: nobody is with or against a faction there.
    """
    st = inp["store"]
    codes = st["codes"]
    per_mp: dict[str, dict] = {}
    plur_by_vote: dict[str, dict[str, tuple[str, int, int]]] = {}
    for ts in inp["order"]:
        rows = st["positions"].get(ts) or []
        if not rows:
            continue
        v = inp["by_ts"][ts]
        by_f: dict[str, dict[str, int]] = {}
        expanded = []
        for key, fi, code in rows:
            f = st["factions"][fi]
            pos = codes.get(code, code)
            expanded.append((key, f, pos))
            if pos in CAST:
                by_f.setdefault(f, {}).setdefault(pos, 0)
                by_f[f][pos] += 1
        plur = {}
        if v.get("kind") != "jelenlet":                        # a quorum check is presence, not a position: no with/against
            for f, c in by_f.items():
                pos, n = max(c.items(), key=lambda kv: kv[1])
                if sum(1 for v in c.values() if v == n) > 1:
                    continue                                   # a tie is no plurality: nobody is with or against it
                plur[f] = (pos, n, sum(c.values()))
        plur_by_vote[ts] = plur
        for key, f, pos in expanded:
            rec = per_mp.setdefault(key, {"votes": [], "counts": {}, "with": 0, "against": 0, "cast": 0, "in_roll": 0})
            rec["in_roll"] += 1
            rec["counts"][pos] = rec["counts"].get(pos, 0) + 1
            fp = plur.get(f)
            align = None
            if pos in CAST:
                rec["cast"] += 1
                if fp and fp[2] > 0:
                    align = "with" if pos == fp[0] else "against"
                    rec[align] += 1
            rec["votes"].append({"ts": ts, "faction": f, "position": pos, "faction_plurality": fp[0] if fp else None,
                                 "align": align, "kind": v["kind"], "secret": v.get("secret")})
    return {"per_mp": per_mp, "plurality_by_vote": plur_by_vote}


def vote_view(inp: dict, ts: str) -> dict:
    """The row from votes_index + its expanded roll call — the one shape every renderer reads."""
    v = dict(inp["by_ts"][ts])
    st = inp["store"]
    rows = st["positions"].get(ts) or []
    codes = st["codes"]
    positions = []
    for key, fi, code in rows:
        azon = key if key in st["members"] else None
        name = st["members"][key]["name"] if azon else key
        positions.append({"mp_azon": azon, "name": name, "faction": st["factions"][fi], "position": codes.get(code, code)})
    v["positions"] = positions
    v["roll_call_available"] = bool(positions)
    return v


# -- seat chart -------------------------------------------------------------------------------

def hemicycle_layout(n: int, rows: int = 7, r0: float = 78.0, gap: float = 17.0) -> list[dict]:
    """Fallback layout: concentric semicircular rows, seats per row ∝ radius, ordered left→right then inner→outer."""
    radii = [r0 + i * gap for i in range(rows)]
    total = sum(radii)
    per = [round(n * r / total) for r in radii]
    s, i = sum(per), rows - 1
    while s < n:
        per[i] += 1; s += 1
    while s > n:
        if per[i] > 1:
            per[i] -= 1; s -= 1
        else:
            i -= 1
    seats = []
    for row, (r, k) in enumerate(zip(radii, per)):
        for j in range(k):
            a = math.pi - (j / (k - 1) if k > 1 else 0.5) * math.pi
            seats.append({"cx": round(r * math.cos(a), 2), "cy": round(-r * math.sin(a), 2), "angle": a, "row": row})
    seats.sort(key=lambda p: (-p["angle"], p["row"]))
    return seats[:n]


def _node(cx: float, cy: float, pos: str, c: str, k: float = 1.0) -> str:
    """One MP as a glyph; `k` scales the 5.2-unit design radius to the layout's seat pitch."""
    R, r, d, sw, sw1 = 5.2 * k, 4.6 * k, 2.2 * k, 1.6 * k, 1.0 * k
    f = lambda v: f"{v:.2f}"
    if pos == "igen":
        return f'<circle cx="{cx}" cy="{cy}" r="{f(R)}" fill="{c}"/>'
    if pos == "nem":
        return f'<circle cx="{cx}" cy="{cy}" r="{f(r)}" fill="none" stroke="{c}" stroke-width="{f(sw)}"/>'
    if pos == "tartozkodott":
        return (f'<circle cx="{cx}" cy="{cy}" r="{f(r)}" fill="none" stroke="{c}" stroke-width="{f(sw)}"/>'
                f'<path d="M {f(cx-r)} {cy} A {f(r)} {f(r)} 0 0 1 {f(cx+r)} {cy} Z" fill="{c}"/>')
    if pos == "jelen_nem_szavazott":
        return f'<circle cx="{cx}" cy="{cy}" r="{f(r)}" fill="none" stroke="{c}" stroke-width="{f(sw)}" stroke-dasharray="{f(2.4*k)} {f(2*k)}"/>'
    if pos == "nem_szavazott":
        return f'<circle cx="{cx}" cy="{cy}" r="{f(r)}" fill="{c}" fill-opacity="0.35" stroke="{c}" stroke-opacity="0.6" stroke-width="{f(sw1)}"/>'
    if pos == "igazoltan_tavol":   # excused absence (cycle 42 onwards): the small dot inside a faint ring
        return (f'<circle cx="{cx}" cy="{cy}" r="{f(r)}" fill="none" stroke="{c}" stroke-opacity="0.5" stroke-width="{f(sw1)}"/>'
                f'<circle cx="{cx}" cy="{cy}" r="{f(d)}" fill="{c}" fill-opacity="0.6"/>')
    return f'<circle cx="{cx}" cy="{cy}" r="{f(d)}" fill="{c}" fill-opacity="0.6"/>'


def _annulus(ri: float, ro: float, a0: float, a1: float, cls: str) -> str:
    """An annular wedge between radii ri < ro and angles a0 → a1 (angles decrease left→right)."""
    return (f'<path d="M {ri*math.cos(a0):.1f} {-ri*math.sin(a0):.1f} L {ro*math.cos(a0):.1f} {-ro*math.sin(a0):.1f} '
            f'A {ro:.1f} {ro:.1f} 0 0 1 {ro*math.cos(a1):.1f} {-ro*math.sin(a1):.1f} L {ri*math.cos(a1):.1f} {-ri*math.sin(a1):.1f} '
            f'A {ri:.1f} {ri:.1f} 0 0 0 {ri*math.cos(a0):.1f} {-ri*math.sin(a0):.1f} Z" class="{cls}"/>')


def room_floor(geo: dict) -> str:
    """The room under the seats: each sector's floor with its row guides, and the front-bench band."""
    if "sector_wedges_rad" not in geo:
        return ""
    gap, r0, r_out = geo["row_gap"], geo["r0"], geo["outer_radius"]
    out = []
    for sec, (a0, a1) in geo["sector_wedges_rad"].items():
        out.append(_annulus(r0 - gap * 0.55, r_out + gap * 0.55, a0, a1, "floor"))
        for row in range(1, geo["max_row"] + 1):
            rr = r0 + (row - 1) * gap
            out.append(f'<path d="M {rr*math.cos(a0):.1f} {-rr*math.sin(a0):.1f} A {rr:.1f} {rr:.1f} 0 0 1 {rr*math.cos(a1):.1f} {-rr*math.sin(a1):.1f}" class="rowline"/>')
    fb, fs = geo["front_bench_radius"], geo["front_bench_span_rad"]
    a0, a1 = math.pi / 2 + fs / 2, math.pi / 2 - fs / 2
    out.append(_annulus(fb - gap * 0.5, fb + gap * 0.5, a0, a1, "floor"))
    out.append(f'<path d="M {fb*math.cos(a0):.1f} {-fb*math.sin(a0):.1f} A {fb} {fb} 0 0 1 {fb*math.cos(a1):.1f} {-fb*math.sin(a1):.1f}" class="rowline"/>')
    return "".join(out)


PODIUM = '<path d="M -11 0 A 11 11 0 0 1 11 0 L 11 3 L -11 3 Z" class="rostrum"/><path d="M -6 -3 A 6 6 0 0 1 6 -3 L 6 0 L -6 0 Z" class="rostrum hi"/>'


def _seat_group(pos: str, faction: str, azon: str | None, align: str | None, title: str, node: str, ring_r: float, k: float, hit_r: float | None = None) -> str:
    """One interactive seat: an invisible hit circle the size of the whole seat cell (so hovering near the dot
    is hovering the dot — Konzol's .kz-hit), data hooks for the inspector and filters, and a white ring when
    the MP voted against their faction. The hover glow is the hit circle's own fill, not a CSS filter."""
    cx, cy = node_cx(node), node_cy(node)
    ring = f'<circle cx="{cx}" cy="{cy}" r="{ring_r:.2f}" class="against"/>' if align == "against" else ""
    hit = f'<circle cx="{cx}" cy="{cy}" r="{(hit_r if hit_r else 5.2 * k * 1.7):.2f}" class="hit"/>'
    attrs = f'class="seat" data-pos="{pos}" data-f="{html.escape(faction or "")}"' + (f' data-az="{html.escape(azon)}"' if azon else "") + (f' data-al="{align}"' if align else "") + ' tabindex="-1"'
    return f'<g {attrs}><title>{title}</title>{hit}{ring}{node}</g>'


def node_cx(node: str) -> str:
    return re.search(r'cx="([^"]+)"', node).group(1)


def node_cy(node: str) -> str:
    return re.search(r'cy="([^"]+)"', node).group(1)


def seat_svg_real(view: dict, facs: list[dict], plan: dict, align: dict | None = None) -> tuple[str, dict]:
    """The reconstructed chamber: every voting MP at their (sector,row,seat). Returns (svg, info)."""
    colour = {f["id"]: f["colour"] for f in facs}
    coords = plan["coords"]; geo = plan["geometry"]
    k = geo.get("node_radius", 5.2) / 5.2                       # glyphs sized to the plan's seat pitch, never overlapping
    align = align or {}
    ring_r = 5.2 * k * 1.55
    hit_r = geo.get("seat_pitch", 5.2 * k * 2) * 0.55            # the whole seat cell answers to the pointer
    parts, unplaced = [], []
    for m in view["positions"]:
        azon = m.get("mp_azon")
        c = colour.get(m["faction"] or "", "#8a8a8a")
        pos = m["position"]
        title = html.escape(f'{m["name"]} ({m["faction"]}) — {POSITION_LABEL.get(pos, pos)}')
        if azon and azon in coords:
            xy = coords[azon]
            place = "miniszteri pad" if xy.get("front_bench") else str(xy["sector"]) + ". szektor"
            where = " · " + place + ", " + str(xy["row"]) + ". sor, " + str(xy["seat"]) + ". szék"
            parts.append(_seat_group(pos, m["faction"], azon, align.get(azon), title + html.escape(where), _node(xy["x"], xy["y"], pos, c, k), ring_r, k, hit_r))
        else:
            unplaced.append(m)
    outlines = plan.get("seat_outlines")
    if outlines:
        # parlament.hu's own floor plan: every seat's outline is the floor; empty seats stay outlines
        occupied = {(c["sector"], c["row"], c["seat"]) for c in coords.values()}
        empties = "".join(
            f'<polygon points="{o["points"]}" class="seatshape{" occ" if (o["sector"], o["row"], o["seat"]) in occupied else ""}">'
            + ("" if (o["sector"], o["row"], o["seat"]) in occupied else f'<title>üres hely · {"miniszteri pad" if o["sector"] == geo.get("front_bench_sector") else str(o["sector"]) + ". szektor, " + str(o["row"]) + ". sor"}, {o["seat"]}. szék</title>')
            + '</polygon>' for o in outlines)
        W = max(abs(geo["x_min"]), abs(geo["x_max"])) + 22
        R = -geo["y_min"] + 10
        labels = []
        for sec, (ax, ay) in geo["sector_label_anchors"].items():
            if int(sec) == geo.get("front_bench_sector"):
                continue
            labels.append(f'<text x="{ax}" y="{ay}" class="seclabel" text-anchor="middle" dominant-baseline="middle">{sec}</text>')
        floor = []
        fx, fy = geo["sector_label_anchors"].get(str(geo.get("front_bench_sector")), (0, -40))
        arc = f'<text x="{fx}" y="{fy}" class="seclabel s" text-anchor="middle">miniszteri pad</text>'
    else:
        # the estimated geometry: numbers a row skips are the empty seats, drawn faint
        empties = "".join(f'<circle cx="{e["x"]}" cy="{e["y"]}" r="{4.6*k:.2f}" class="seat-empty"><title>üres hely · '
                          + ("miniszteri pad" if e.get("front_bench") else f'{e["sector"]}. szektor, {e["row"]}. sor') + f', {e["seat"]}. szék</title></circle>'
                          for e in plan.get("empty_seats") or [])
        R = geo["outer_radius"] + 12
        W = R + 20                                                    # half-width of the drawing
        labels = []
        for sec, (a0, a1) in geo["sector_wedges_rad"].items():
            mid = (a0 + a1) / 2
            labels.append(f'<text x="{R*math.cos(mid):.1f}" y="{-R*math.sin(mid):.1f}" class="seclabel" text-anchor="middle" dominant-baseline="middle">{sec}</text>')
        floor = [room_floor(geo)]
        arc = '<text x="0" y="-13" class="seclabel s" text-anchor="middle">miniszteri pad</text>'
    # MPs the plan cannot place: a small labelled tray, bottom left, outside the room
    tray = ""
    if unplaced:
        x0, y0 = -W + 8, 14
        tray = f'<text x="{x0:.1f}" y="{y0 - 4:.1f}" class="seclabel s">ülőhely nélkül</text>'
        for i, m in enumerate(unplaced):
            c = colour.get(m["faction"] or "", "#8a8a8a")
            why = "azonosítatlan név" if not m.get("mp_azon") else "nincs ülőhely a képviselői adatlapon (a mandátum azóta megszűnt)"
            tray += _seat_group(m["position"], m["faction"], m.get("mp_azon"), align.get(m.get("mp_azon") or ""),
                                f'{html.escape(m["name"])} ({html.escape(m["faction"] or "")}) — {POSITION_LABEL.get(m["position"], m["position"])} · {why}',
                                _node(x0 + 3 + i * 6, y0 + 3, m["position"], c, k), ring_r, k, hit_r)
    svg = (f'<svg viewBox="{-W:.0f} -{R+8:.0f} {2*W:.0f} {R+30:.0f}" role="img" aria-label="Az ülésterem rekonstruált ülésrendje: {len(view["positions"])} képviselő, frakció és szavazat szerint">'
           + "".join(floor) + arc + PODIUM + '<text x="0" y="9" class="seclabel s" text-anchor="middle">elnöki emelvény</text>'
           + "".join(labels) + empties + "".join(parts) + tray + '</svg>')
    return svg, {"placed": len(view["positions"]) - len(unplaced), "unplaced": len(unplaced), "seated_total": plan["seated"], "empty": len(plan.get("empty_seats") or []),
                 "seats_in_plan": len(outlines) if outlines else None}


def seat_svg_fallback(view: dict, facs: list[dict], align: dict | None = None) -> str:
    order = {f["id"]: f["sort_order"] for f in facs}
    colour = {f["id"]: f["colour"] for f in facs}
    pos_rank = {p: i for i, p in enumerate(POSITION_ORDER)}
    members = sorted(view["positions"], key=lambda p: (order.get(p["faction"] or "", 999), pos_rank.get(p["position"], 9), name_key(p["name"])))
    seats = hemicycle_layout(len(members))
    align = align or {}
    # glyphs sized to the layout's own pitch: 199 seats sit comfortably at the design size, 386 do not
    dmin = 12.0
    for i, a in enumerate(seats):
        for b in seats[max(0, i - 3):i]:
            d = math.hypot(a["cx"] - b["cx"], a["cy"] - b["cy"])
            if 0 < d < dmin:
                dmin = d
    k = min(1.0, dmin / 12.4)
    parts = []
    for seat, m in zip(seats, members):
        c = colour.get(m["faction"] or "", "#8a8a8a")
        title = html.escape(f'{m["name"]} ({m["faction"]}) — {POSITION_LABEL.get(m["position"], m["position"])}')
        parts.append(_seat_group(m["position"], m["faction"], m.get("mp_azon"), align.get(m.get("mp_azon") or ""), title,
                                 _node(seat["cx"], seat["cy"], m["position"], c, k), 5.2 * k * 1.55, k, dmin * 0.55))
    r0, gap, rows = 78.0, 17.0, 7
    ri, ro = r0 - gap * 0.5, r0 + (rows - 1) * gap + gap * 0.5
    floor = (f'<path d="M {-ri:.1f} 0 L {-ro:.1f} 0 A {ro:.1f} {ro:.1f} 0 0 1 {ro:.1f} 0 L {ri:.1f} 0 A {ri:.1f} {ri:.1f} 0 0 0 {-ri:.1f} 0 Z" class="floor"/>'
             + "".join(f'<path d="M {-(r0 + i*gap):.1f} 0 A {r0 + i*gap:.1f} {r0 + i*gap:.1f} 0 0 1 {r0 + i*gap:.1f} 0" class="rowline"/>' for i in range(rows)))
    podium = '<path d="M -14 0 A 14 14 0 0 1 14 0 L 14 4 L -14 4 Z" class="rostrum"/><path d="M -8 -4 A 8 8 0 0 1 8 -4 L 8 0 L -8 0 Z" class="rostrum hi"/>'
    return ('<svg viewBox="-200 -200 400 214" role="img" aria-label="Ülésrend-diagram">'
            + floor + podium + "".join(parts) + '</svg>')


# -- helpers ----------------------------------------------------------------------------------

def hu_date(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{y}. {HU_MONTHS[int(m)-1]} {int(d)}."


def rule_short(rule: str | None) -> str:
    return {"egyszeru": "egyszerű", "abszolut": "abszolút (összes ½)", "ketharmad_jelenlevo": "⅔ jelenlévők",
            "ketharmad_osszes": "⅔ összes", "negyotod_jelenlevo": "⅘ jelenlévők", "negyotod_osszes": "⅘ összes",
            "relativ": "relatív"}.get(rule or "", "—")


def esc(s) -> str:
    return html.escape("" if s is None else str(s))


def result_badge(v: dict) -> str:
    if v.get("passed") is True:
        return '<span class="badge ok">Elfogadva</span>'
    if v.get("passed") is False:
        return '<span class="badge no">Elutasítva</span>'
    return f'<span class="badge mid">{esc(v.get("result_raw"))}</span>'


# -- CSS / JS ---------------------------------------------------------------------------------

CSS = """
:root{--bg:#050505;--panel:rgba(10,10,10,.9);--panel-deep:rgba(5,5,5,.95);--topbar:rgba(5,5,5,.9);--terminal:#000;
--border:#27272a;--border-hi:#3f3f46;--corner:#777;--text:#e4e4e7;--dim:#a1a1aa;--dim2:#8a8a93;--dim3:#7a7a83;--dim4:#71717a;--line2:#18181b;--grid:rgba(255,255,255,.05);
--ok:#34d399;--okbg:rgba(16,185,129,.1);--okbd:rgba(16,185,129,.4);--bad:#f87171;--badbg:rgba(239,68,68,.1);--badbd:rgba(239,68,68,.4);--white:#fff;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;--mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%;color-scheme:dark}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 var(--sans);-webkit-font-smoothing:antialiased;font-weight:300}
a{color:inherit;text-decoration:none}a:hover{color:var(--white)}
::-webkit-scrollbar{width:4px;height:4px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:#3f3f46;border-radius:4px}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.label{font-family:var(--mono);font-size:10px;letter-spacing:.3em;text-transform:uppercase;color:var(--dim2)}
[id]{scroll-margin-top:64px}
/* top bar */
.kz-topbar{position:sticky;top:0;z-index:20;height:48px;border-bottom:1px solid var(--border);background:var(--topbar);backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:space-between;padding:0 16px;font-family:var(--mono);font-size:10px;letter-spacing:.2em;text-transform:uppercase}
.kz-topbar .l{display:flex;align-items:center;gap:14px;min-width:0;flex:1 1 auto}.kz-topbar .r{display:flex;align-items:center;gap:14px;flex:0 0 auto;white-space:nowrap}
.kz-topbar .brand{display:flex;align-items:center;gap:10px;color:var(--white);font-weight:500;letter-spacing:.35em;flex-shrink:0}
.kz-topbar .brand i{width:8px;height:8px;background:var(--white);display:inline-block;transition:box-shadow .2s}.kz-topbar .brand:hover i{box-shadow:0 0 8px rgba(255,255,255,.8)}
.kz-topbar .sep{width:1px;height:16px;background:var(--border)}
.kz-topbar nav{display:flex;align-items:center;gap:8px;min-width:0;color:var(--dim2);white-space:nowrap}.kz-topbar nav a,.kz-topbar nav .cur{min-width:0;overflow:hidden;text-overflow:ellipsis}.kz-topbar nav a:hover{color:var(--white)}.kz-topbar nav .cur{color:var(--white)}.sl{color:var(--border-hi)}
.kz-topbar .kv{color:var(--dim2);white-space:nowrap}.kz-topbar .kv b{color:#d4d4d8;font-weight:400;margin-left:6px}.kz-topbar .cyc a{margin-left:2px;padding:6px 4px;display:inline-block}.kz-topbar .cyc a:hover{color:var(--white)}
.kz-topbar .dot{width:6px;height:6px;border-radius:50%;background:#52525b;display:inline-block;margin-right:6px}
@media(max-width:900px){.kz-topbar nav[aria-label="Útvonal"] a,.kz-topbar nav[aria-label="Útvonal"] .sl{display:none}}@media(max-width:760px){.kz-topbar .hide-sm{display:none}}@media(max-width:600px){.kz-topbar .hide-xs{display:none}.kz-topbar .cyc a:not(.near),.kz-topbar .cyc a:not(.near)+.sl,.kz-topbar .cyc .sl:has(+a:not(.near)){display:none}.kz-topbar .kv.sync{display:none}.kz-topbar .r{flex:0 1 auto;min-width:0}.kz-topbar nav .cur{min-width:6ch}}
/* main */
.kz-main{padding:20px 16px 16px;background-image:radial-gradient(var(--grid) 1px,transparent 1px);background-size:24px 24px;background-position:50%}
.wrap{max-width:1400px;margin:0 auto}
.crumbs{font-family:var(--mono);font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:var(--dim2);margin:0 0 10px}.crumbs a:hover{color:var(--white)}
a:focus-visible,button:focus-visible,input:focus-visible,[tabindex]:focus-visible{outline:1px solid var(--dim);outline-offset:2px}
.hero-h{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 18px;margin:0 0 6px}
h1{margin:0;font-size:38px;font-weight:300;letter-spacing:-.03em;color:var(--white);line-height:1.05}
.lede{margin:6px 0 0;color:var(--dim);max-width:78ch;font-size:13px}
/* panels */
.panel{position:relative;background:var(--panel);border:1px solid var(--border);padding:16px 18px;margin-top:16px}
.panel.deep{background:var(--panel-deep)}
.kz-corner{position:absolute;width:16px;height:16px;border:0 solid var(--corner);pointer-events:none}
.kz-corner.tl{top:-1px;left:-1px;border-top-width:1px;border-left-width:1px}.kz-corner.tr{top:-1px;right:-1px;border-top-width:1px;border-right-width:1px}
.kz-corner.bl{bottom:-1px;left:-1px;border-bottom-width:1px;border-left-width:1px}.kz-corner.br{bottom:-1px;right:-1px;border-bottom-width:1px;border-right-width:1px}
.panel > h2{margin:0 0 10px;font-family:var(--mono);font-size:10px;letter-spacing:.3em;text-transform:uppercase;color:var(--dim2);font-weight:400;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.panel > h2 .tag{color:var(--dim2);letter-spacing:.15em}
.grid{display:grid;grid-template-columns:1.25fr .9fr;gap:16px}.grid.one{grid-template-columns:1fr}.grid .panel{margin-top:16px;min-width:0}
@media(max-width:900px){.grid{grid-template-columns:1fr}}
.fresh{margin-top:14px;padding:10px 14px;border:1px solid var(--border);border-left:2px solid var(--dim2);background:var(--panel-deep);font-family:var(--mono);font-size:11px;color:var(--dim);letter-spacing:.02em;position:relative}
.fresh .en{color:var(--dim2);display:block;margin-top:2px}
.fresh:before{content:">";color:var(--dim3);margin-right:8px}
/* text */
.hero-title{font-size:22px;font-weight:300;letter-spacing:-.01em;line-height:1.3;margin:2px 0 8px;color:var(--white)}
.hero-title a{color:var(--white);border-bottom:1px solid var(--dim3)}.hero-title a:hover{border-bottom-color:var(--white)}
.hero-meta{color:var(--dim2);font-size:12px;font-family:var(--mono);letter-spacing:.02em}
.hero-meta.prose{font-family:var(--sans);font-size:12.5px;color:var(--dim);letter-spacing:0}
.speech{font-family:var(--sans);font-size:15px;line-height:1.55;color:var(--text);max-width:72ch}.speech p{margin:0 0 .9em}.speech mark,.snip mark{background:rgba(255,255,255,.18);color:var(--white);padding:0 2px}
.snip{font-family:var(--sans);font-size:12.5px;color:var(--dim);display:block;margin-top:3px;letter-spacing:0;text-transform:none}
tr.grp td{background:rgba(255,255,255,.03);color:var(--dim);font-family:var(--sans);font-size:12.5px;padding-top:10px}tr.dim td{color:var(--dim3)}tr.dim a{color:var(--dim2)}
/* chart */
.chart svg{width:100%;height:auto;display:block;margin-top:6px}
.seat circle,.seat path{transition:opacity .15s,filter .2s;cursor:crosshair}
.chart svg .seat{transition:opacity .15s,filter .15s;outline:none;cursor:pointer}
.chart svg .seat[data-pos="nem_szavazott"],.chart svg .seat[data-pos="bejelentett_hianyzo"],.chart svg .seat[data-pos="igazoltan_tavol"],.chart svg .seat[data-pos="jelen_nem_szavazott"]{opacity:.55}
.chart svg .seat.dim{opacity:.12}
.chart svg .seat.hl,.chart svg .seat:hover,.chart svg .seat:focus-visible{opacity:1}
.chart svg .hit{fill:#fff;fill-opacity:0;stroke:none;transition:fill-opacity .12s}
.chart svg .seat.hl .hit,.chart svg .seat:hover .hit,.chart svg .seat:focus-visible .hit{fill-opacity:.22}
.chart svg .seat:focus-visible .hit{stroke:#fff;stroke-width:1.2;stroke-opacity:.9}
.chart svg .against{fill:none;stroke:#fff;stroke-width:.55;stroke-opacity:.9}
.chart.pinned svg .seat:not(.hl){opacity:.35}
tbody tr.hl td{background:rgba(255,255,255,.07)}
/* seat inspector */
.inspector{margin-top:10px;min-height:66px;border:1px solid var(--border);background:var(--panel-deep);padding:9px 12px;font-family:var(--mono);font-size:11px;letter-spacing:.02em;color:var(--dim);position:relative}
.inspector .insp-hint{color:var(--dim2);font-size:10px;letter-spacing:.08em;padding-top:14px}
.inspector .row1{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 14px}
.inspector .name{font-family:var(--sans);font-size:15px;font-weight:400;color:var(--white)}.inspector .name a{color:var(--white);border-bottom:1px solid var(--dim3)}.inspector .name a:hover{border-bottom-color:var(--white)}
.inspector .meta{color:var(--dim2);font-size:10px;letter-spacing:.06em}
.inspector .row2{display:flex;flex-wrap:wrap;align-items:center;gap:8px 18px;margin-top:6px}
.inspector .rec{color:var(--dim)}.inspector .rec b{color:var(--white);font-weight:400}
.inspector .streak{display:inline-flex;gap:2px;align-items:center}.inspector .streak i{width:7px;height:10px;display:inline-block;background:var(--line2);border:1px solid var(--border)}
.inspector .streak i.w{background:var(--c);border-color:var(--c)}.inspector .streak i.a{background:#fff;border-color:#fff}.inspector .streak i.c{background:var(--dim3);border-color:var(--dim3)}.inspector .streak i.n{background:transparent}.inspector .streak i.x{visibility:hidden}
.inspector .streak i.now{outline:1px solid var(--white);outline-offset:1px}
.inspector .lbl{font-size:9px;letter-spacing:.2em;text-transform:uppercase;color:var(--dim2);margin-right:4px}
.inspector .pin{position:absolute;top:6px;right:8px;font-size:9px;letter-spacing:.2em;text-transform:uppercase;color:var(--dim3)}
.inspector .pin button{border:1px solid var(--border);background:transparent;color:var(--dim2);font-family:var(--mono);font-size:9px;letter-spacing:.15em;text-transform:uppercase;padding:2px 6px;cursor:pointer;margin-left:6px}
.inspector .pin button:hover{color:var(--white);border-color:var(--border-hi)}
@media(max-width:600px){.inspector .row1 .meta{flex-basis:100%}}
.rostrum{fill:var(--border)}.rostrum.hi{fill:var(--border-hi)}.floor{fill:rgba(255,255,255,.028)}.rowline{fill:none;stroke:rgba(255,255,255,.07);stroke-width:.5}
.seatshape{fill:rgba(255,255,255,.035);stroke:rgba(255,255,255,.12);stroke-width:.35}.seatshape.occ{fill:rgba(255,255,255,.06)}
.ts .axis{stroke:var(--border-hi);stroke-width:1}.ts .axl{font-size:9px;fill:var(--dim2);font-family:var(--mono)}.tswrap{overflow-x:auto}.tswrap svg{min-width:480px;display:block}
.seclabel{font-size:5px;fill:var(--dim2);font-family:var(--mono);letter-spacing:.1em}.seclabel.s{font-size:3.4px;letter-spacing:.15em;fill:var(--dim3)}.seat-empty{fill:none;stroke:var(--border-hi);stroke-width:.6}.aisle{stroke:var(--line2);stroke-width:.8}.fbarc{fill:none;stroke:var(--border-hi);stroke-width:.8;stroke-dasharray:2 2}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:8px;font-family:var(--mono);font-size:9px;letter-spacing:.15em;text-transform:uppercase;color:var(--dim2)}
.legend .f{display:inline-flex;align-items:center;gap:6px}.legend i{width:9px;height:9px;border-radius:50%;display:inline-block}.legend svg{width:12px;height:12px;flex:none;color:var(--dim)}
/* numbers */
.tally{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--border);border:1px solid var(--border);margin:8px 0 12px}
.tally div{background:var(--panel-deep);padding:10px 12px}
.tally b{display:block;font-size:44px;font-weight:300;letter-spacing:-.03em;line-height:1;color:var(--white);font-family:var(--sans);font-variant-numeric:tabular-nums}
.tally span{display:block;margin-top:6px;font-family:var(--mono);font-size:9px;color:var(--dim2);text-transform:uppercase;letter-spacing:.2em}
.tally{grid-template-columns:repeat(3,minmax(0,1fr))}.tally.six{grid-template-columns:repeat(3,minmax(0,1fr))}.tally.six b{font-size:30px}.tally.seven{grid-template-columns:repeat(4,minmax(0,1fr))}.tally.seven b{font-size:30px}
.verdict{display:grid;grid-template-columns:auto 1fr;gap:5px 16px;font-size:12px;margin-top:8px;font-family:var(--mono)}
.verdict dt{color:var(--dim2);text-transform:uppercase;letter-spacing:.15em;font-size:10px;padding-top:2px}.verdict dd{margin:0;color:var(--text)}
.verdict dd .hero-meta{display:block;color:var(--dim2);font-size:10px}
.badge{display:inline-block;padding:2px 8px;border:1px solid var(--border);font-family:var(--mono);font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:var(--dim)}
.badge.hi{border-color:var(--dim3);color:var(--white)}.badge.ok{border-color:var(--okbd);background:var(--okbg);color:var(--ok)}.badge.no{border-color:var(--badbd);background:var(--badbg);color:var(--bad)}.badge.mid{color:var(--dim2);background:rgba(24,24,27,.6)}
.bar{height:6px;background:var(--line2);position:relative;margin:8px 0 4px}
.bar i{position:absolute;left:0;top:0;bottom:0;background:var(--white);opacity:.9}
.bar em{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--dim)}
.fbars{margin-top:12px;font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase}.fbars .row.hdr{color:var(--dim2);font-size:9px;margin-bottom:2px}.fbars .row{display:grid;grid-template-columns:78px 1fr 106px;gap:8px;align-items:center;margin:5px 0;color:var(--dim)}.fbars .row.hdr span{white-space:nowrap}
.fbars .stack{display:flex;height:6px;overflow:hidden;background:var(--line2)}.fbars .stack i{display:block;height:100%}
.fbars .row .sub{display:block;color:var(--dim2);font-size:9px;letter-spacing:.06em;text-transform:none;line-height:1.3}.fbars .row.hdr .sub{white-space:normal}
.counts{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));margin-top:16px;border-top:1px solid var(--border);border-left:1px solid var(--border)}
@media(max-width:1100px){.counts{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:560px){.counts{grid-template-columns:repeat(2,minmax(0,1fr))}}
.counts .c{background:var(--panel-deep);padding:14px 16px;position:relative;border-right:1px solid var(--border);border-bottom:1px solid var(--border)}
.counts b{display:block;font-size:34px;font-weight:300;letter-spacing:-.03em;color:var(--white);font-family:var(--sans);font-variant-numeric:tabular-nums;line-height:1}
.counts span{display:block;font-family:var(--mono);font-size:9px;color:var(--dim2);text-transform:uppercase;letter-spacing:.2em;margin-top:8px}
.counts span.sub{color:var(--dim2);text-transform:none;letter-spacing:.05em;margin-top:3px}
/* directory / tables */
.filters{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 10px;align-items:center}
.filters button{border:1px solid var(--border);background:transparent;color:var(--dim2);padding:6px 10px;min-height:26px;font-family:var(--mono);font-size:10px;letter-spacing:.15em;text-transform:uppercase;cursor:pointer;transition:color .15s,border-color .15s}
.filters button:hover{color:var(--white);border-color:var(--border-hi)}.filters button.on{border-color:var(--dim3);color:var(--white);background:rgba(24,24,27,.6)}
.filters input{border:1px solid var(--dim3);background:rgba(0,0,0,.4);color:var(--text);padding:5px 9px;font-family:var(--mono);font-size:11px;min-width:220px}
.filters input::placeholder{color:var(--dim2)}
.filters .n{color:var(--dim2);font-family:var(--mono);font-size:10px;letter-spacing:.15em;margin-left:auto}
.tablewrap{overflow-x:auto;border:1px solid var(--border);background:rgba(0,0,0,.35)}tr[hidden]{display:none}
.cite pre{margin:0;white-space:pre-wrap;word-break:break-word;font-size:11px;color:var(--dim);background:rgba(0,0,0,.35);border:1px solid var(--border);padding:8px 10px;flex:1 1 auto}
.cite .cite-row{display:flex;gap:8px;align-items:flex-start;margin-top:6px}.cite details summary{cursor:pointer;color:var(--dim2);font-size:10px;letter-spacing:.2em;text-transform:uppercase;margin-top:8px}
.cite .copy{border:1px solid var(--border);background:transparent;color:var(--dim2);font-family:var(--mono);font-size:10px;letter-spacing:.15em;text-transform:uppercase;padding:4px 8px;cursor:pointer;white-space:nowrap}
.cite .copy:hover{color:var(--white);border-color:var(--border-hi)}.cite .copy.done{color:var(--ok);border-color:var(--okbd)}
.pgr{display:flex;flex-wrap:wrap;align-items:center;gap:4px;margin-top:8px;font-family:var(--mono);font-size:10px;letter-spacing:.1em}
.pgr button{border:1px solid var(--border);background:transparent;color:var(--dim2);min-width:28px;min-height:26px;padding:4px 8px;font-family:var(--mono);font-size:10px;letter-spacing:.1em;cursor:pointer}
.pgr button:hover{color:var(--white);border-color:var(--border-hi)}.pgr button.on{border-color:var(--dim3);color:var(--white);background:rgba(24,24,27,.6)}.pgr button:disabled{opacity:.35;cursor:default}
.pgr .gap{color:var(--dim3);padding:0 2px}.pgr .all{margin-left:8px;text-transform:uppercase}
table{border-collapse:collapse;width:100%;font-size:12px}
th,td{padding:7px 10px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line2)}
th{font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.2em;color:var(--dim2);font-weight:400;background:rgba(5,5,5,.95)}
th.sortable{cursor:pointer}th.sortable:hover{color:var(--white)}th.sortable .sortbtn{all:unset;cursor:pointer;font:inherit;color:inherit;letter-spacing:inherit;text-transform:inherit}th.sortable .sortbtn:focus-visible{outline:1px solid var(--dim);outline-offset:2px}th.sortable[aria-sort="ascending"]:after{content:" ↑"}th.sortable[aria-sort="descending"]:after{content:" ↓"}th.num{text-align:right}
tr:last-child td{border-bottom:0}tbody tr:hover td{background:rgba(24,24,27,.5)}
td.num{text-align:right;white-space:nowrap;font-family:var(--mono)}td.ts{white-space:nowrap;color:var(--dim2);font-family:var(--mono);font-size:11px}td.ts a:hover{color:var(--white)}
td .sub{color:var(--dim2);font-size:11px;display:block;font-family:var(--mono);letter-spacing:.02em}
td a{color:#d4d4d8;text-decoration:underline;text-decoration-color:var(--dim3);text-underline-offset:2px}td a:hover{color:var(--white);text-decoration-color:var(--white)}
.pos{display:inline-flex;align-items:center;gap:6px}.pos svg{width:12px;height:12px;flex:none}
.d{width:9px;height:9px;border-radius:50%;background:var(--c);display:inline-block;flex:none}
.g{width:12px;height:12px;border-radius:50%;display:inline-block;box-sizing:border-box;flex:none}
.g-igen{background:var(--c)}.g-nem{border:1.6px solid var(--c)}
.g-tartozkodott{border:1.6px solid var(--c);background:linear-gradient(to bottom,var(--c) 50%,transparent 50%)}
.g-jelen_nem_szavazott{border:1.6px dashed var(--c)}
.g-nem_szavazott{border:1px solid var(--c);background:var(--c);opacity:.5}
.g-bejelentett_hianyzo{background:var(--c);opacity:.6;transform:scale(.5)}
.g-igazoltan_tavol{border:1px solid var(--c);opacity:.7;background:radial-gradient(circle,var(--c) 0 34%,transparent 36%)}
.timeline{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:8px;font-family:var(--mono);font-size:10px;letter-spacing:.05em}@media(max-width:560px){.timeline{grid-template-columns:repeat(2,minmax(0,1fr))}}.timeline div{border-left:1px solid var(--dim3);padding-left:8px;color:var(--dim)}.timeline b{display:block;color:var(--white);font-weight:400;letter-spacing:.15em;text-transform:uppercase;margin-bottom:2px}
.pager{display:flex;justify-content:space-between;gap:12px;margin:16px 0 0;font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim2)}.pager a{max-width:46%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;border:1px solid var(--border);padding:6px 10px}.pager a:hover{color:var(--white);border-color:var(--border-hi)}
/* terminal footer */
.kz-terminal{margin:0 auto 40px;width:calc(100% - 32px);max-width:1400px;background:var(--terminal);border:1px solid var(--border);padding:14px 18px;font-family:var(--mono);font-size:10px;letter-spacing:.05em;color:var(--dim2);position:relative}
.kz-terminal .scan{position:absolute;inset:0;overflow:hidden;pointer-events:none}
.kz-terminal .log{color:var(--dim2);line-height:1.7;position:relative}.kz-terminal .log span{display:block}
.kz-terminal .cursor{display:inline-block;width:6px;height:11px;background:var(--dim2);vertical-align:-2px;margin-left:2px;animation:kz-blink 1s step-end infinite}
.kz-terminal .method{margin-top:14px;padding-top:12px;border-top:1px solid var(--line2);font-family:var(--sans);font-size:11.5px;color:var(--dim);letter-spacing:0;line-height:1.6;max-width:110ch;position:relative}
.kz-terminal .method h3{font-family:var(--mono);font-size:9px;letter-spacing:.3em;text-transform:uppercase;color:var(--dim2);margin:8px 0 4px;font-weight:400}
@keyframes kz-blink{0%,100%{opacity:1}50%{opacity:0}}
.kz-terminal .scan:after{content:"";position:absolute;top:-10%;left:0;width:100%;height:2px;background:rgba(255,255,255,.08);animation:kz-scan 6s linear infinite}
@keyframes kz-scan{0%{top:-10%}100%{top:110%}}
@media (prefers-reduced-motion:reduce){.kz-terminal .scan:after,.kz-terminal .cursor{animation:none}}
@media print{:root{--bg:#fff;--panel:#fff;--deep:#fff;--text:#111;--white:#000;--dim:#333;--dim2:#444;--dim3:#555;--border:#bbb;--border-hi:#999;--line2:#eee;--grid:transparent}
body{background:#fff;color:#111}.kz-topbar,.pgr,.filters button,.copy,.kz-corner,.scan,.insp-hint{display:none!important}.kz-topbar+*{margin-top:0}
tr[hidden]{display:table-row!important}[style*="opacity:0"]{opacity:1!important}.panel,.kz-terminal{break-inside:avoid;border-color:#bbb}a{color:#000;text-decoration:underline}.chart svg .seat{opacity:1!important}}
"""

JS_PAGER = """
(function(){
  // Every table with data-page-size gets a pager: 25 rows at a time, prev / next / page numbers / 'mind'.
  // Filters mark excluded rows with data-x and call table.__pager.render(true); sorters re-append rows and
  // call render(false). Without JS the whole table is in the page.
  function paginate(table){
    var per0 = parseInt(table.getAttribute('data-page-size') || '25', 10), per = per0, page = 1;
    var tbody = table.tBodies[0]; if (!tbody) return;
    var counter = table.getAttribute('data-counter') ? document.getElementById(table.getAttribute('data-counter')) : null;
    var wrap = table.closest('.tablewrap') || table;
    var nav = document.createElement('nav'); nav.className = 'pgr'; nav.setAttribute('aria-label', 'Lapozás');
    wrap.parentNode.insertBefore(nav, wrap.nextSibling);
    function render(reset){
      if (reset) page = 1;
      var rows = Array.prototype.slice.call(tbody.rows).filter(function(r){ return !r.classList.contains('empty') && !(r.cells.length === 1 && r.cells[0].colSpan > 1); }), vis = rows.filter(function(r){ return !r.hasAttribute('data-x'); });
      var total = vis.length, pages = Math.max(1, Math.ceil(total / per));
      if (page > pages) page = pages;
      rows.forEach(function(r){ r.hidden = true; });
      var from = (page - 1) * per, to = Math.min(total, from + per);
      vis.slice(from, to).forEach(function(r){ r.hidden = false; });
      if (counter) counter.textContent = (total ? (from + 1) + '–' + to : '0') + ' / ' + total + (total !== rows.length ? ' (' + rows.length + ')' : '');
      if (pages <= 1 && per === per0 && total <= per0) { nav.innerHTML = ''; nav.hidden = true; return; }
      nav.hidden = false;
      var nums = [], seen = {};
      [1, 2, page - 2, page - 1, page, page + 1, page + 2, pages - 1, pages].forEach(function(n){ if (n >= 1 && n <= pages && !seen[n]) { seen[n] = 1; nums.push(n); } });
      nums.sort(function(a, b){ return a - b; });
      var html = '<button type="button" data-pg="prev"' + (page <= 1 ? ' disabled' : '') + ' aria-label="Előző oldal">‹</button>', last = 0;
      nums.forEach(function(n){ if (n - last > 1) html += '<span class="gap">…</span>'; html += '<button type="button" data-pg="' + n + '"' + (n === page ? ' aria-current="page" class="on"' : '') + '>' + n + '</button>'; last = n; });
      html += '<button type="button" data-pg="next"' + (page >= pages ? ' disabled' : '') + ' aria-label="Következő oldal">›</button>';
      html += '<button type="button" data-pg="all" class="all' + (per > per0 ? ' on' : '') + '">' + (per > per0 ? per0 + ' / oldal' : 'mind') + '</button>';
      nav.innerHTML = html;
    }
    var empty = null;
    function emptyRow(total){
      // an emptied table keeps its header and says so, instead of ending in a blank
      if (!total) { if (!empty) { empty = document.createElement('tr'); empty.className = 'empty'; var td = document.createElement('td'); td.colSpan = (table.tHead && table.tHead.rows[0] ? table.tHead.rows[0].cells.length : 1); td.textContent = 'Nincs találat.'; empty.appendChild(td); } if (empty.parentNode !== tbody) tbody.appendChild(empty); }
      else if (empty && empty.parentNode) empty.parentNode.removeChild(empty);
    }
    var render0 = render;
    render = function(reset){ render0(reset); var placeholder = Array.prototype.some.call(tbody.rows, function(r){ return r.cells.length === 1 && r.cells[0].colSpan > 1 && !r.classList.contains('empty'); }); emptyRow(placeholder ? 1 : tbody.querySelectorAll('tr:not([data-x]):not(.empty)').length); };
    nav.addEventListener('click', function(e){
      var b = e.target.closest('button[data-pg]'); if (!b) return;
      var v = b.getAttribute('data-pg'), had = document.activeElement === b;
      if (v === 'prev') page--; else if (v === 'next') page++;
      else if (v === 'all') { per = per > per0 ? per0 : 100000; page = 1; }
      else page = parseInt(v, 10);
      render(false);
      if (had) { var again = nav.querySelector('button[data-pg="' + v + '"]:not([disabled])') || nav.querySelector('button[data-pg="' + (v === 'prev' ? 'next' : v === 'next' ? 'prev' : 'all') + '"]') || nav.querySelector('button.on'); if (again) again.focus(); }
      var top = wrap.getBoundingClientRect().top; if (top < 60) window.scrollTo({top: window.pageYOffset + top - 64, behavior: 'auto'});
    });
    table.__pager = {render: render};
    render(false);
  }
  document.querySelectorAll('table[data-page-size]').forEach(paginate);
  window.__karzatRerender = function(table, reset){ if (table && table.__pager) table.__pager.render(reset); else if (table) { var rows = Array.prototype.slice.call(table.tBodies[0].rows); rows.forEach(function(r){ if (!r.classList.contains('grp')) r.hidden = r.hasAttribute('data-x'); }); } };
})();
"""

JS_INDEX = """
(function(){
  var tbody = document.getElementById('rows'), n = document.getElementById('n'); if (!tbody) return;
  var rows = Array.prototype.slice.call(tbody.rows), rule = 'all', result = 'all', year = 'all', q = '';
  var hay = rows.map(function(r){ var more = r.querySelector('.more'); return ((r.textContent || '') + ' ' + (more ? more.getAttribute('title') : '')).toLowerCase(); });
  function press(sel, on){ document.querySelectorAll(sel).forEach(function(x){ var isOn = x === on; x.classList.toggle('on', isOn); x.setAttribute('aria-pressed', isOn ? 'true' : 'false'); }); }
  var table = tbody.closest('table');
  function render(){
    var k = 0;
    rows.forEach(function(r, i){
      var ok = (rule === 'all' || r.getAttribute('data-rule') === rule) && (result === 'all' || r.getAttribute('data-result') === result) && (year === 'all' || r.getAttribute('data-y') === year) && (!q || hay[i].indexOf(q) >= 0);
      if (ok) { r.removeAttribute('data-x'); k++; } else r.setAttribute('data-x', '');
    });
    if (window.__karzatRerender) window.__karzatRerender(table, true); else n.textContent = k + ' / ' + rows.length;
  }
  document.querySelectorAll('button[data-rule]').forEach(function(b){ b.addEventListener('click', function(){ rule = b.getAttribute('data-rule'); press('button[data-rule]', b); render(); }); });
  document.querySelectorAll('button[data-result]').forEach(function(b){ b.addEventListener('click', function(){ result = b.getAttribute('data-result'); press('button[data-result]', b); render(); }); });
  document.querySelectorAll('button[data-year]').forEach(function(b){ b.addEventListener('click', function(){ year = b.getAttribute('data-year'); press('button[data-year]', b); render(); }); });
  document.getElementById('q').addEventListener('input', function(e){ q = e.target.value.trim().toLowerCase(); render(); });
  render();
})();
"""

JS_VOTE = """
(function(){
  var table = document.getElementById('roll'); if (!table) return;
  var tbody = table.tBodies[0], rows = Array.prototype.slice.call(tbody.rows), dir = {};
  var fac = 'all', pos = 'all';
  function press(sel, on){ document.querySelectorAll(sel).forEach(function(x){ var isOn = x === on; x.classList.toggle('on', isOn); x.setAttribute('aria-pressed', isOn ? 'true' : 'false'); }); }
  function apply(){
    var k = 0;
    rows.forEach(function(r){ var ok = (fac === 'all' || r.getAttribute('data-f') === fac) && (pos === 'all' || r.getAttribute('data-p') === pos) && (!table.__textMatch || table.__textMatch(r)); if (ok) { r.removeAttribute('data-x'); k++; } else r.setAttribute('data-x', ''); });
    if (window.__karzatRerender) window.__karzatRerender(table, true); else document.getElementById('rn').textContent = k + ' / ' + rows.length;
    if (window.__karzatDimSeats) window.__karzatDimSeats(fac, pos);
  }
  table.__reapply = apply;
  document.querySelectorAll('button[data-fac]').forEach(function(b){ b.addEventListener('click', function(){ fac = b.getAttribute('data-fac'); press('button[data-fac]', b); apply(); }); });
  document.querySelectorAll('button[data-posf]').forEach(function(b){ b.addEventListener('click', function(){ pos = b.getAttribute('data-posf'); press('button[data-posf]', b); apply(); }); });
  var heads = table.querySelectorAll('th.sortable');
  heads.forEach(function(th, i){
    th.setAttribute('aria-sort', 'none');
    var btn = document.createElement('button'); btn.type = 'button'; btn.className = 'sortbtn';
    while (th.firstChild) btn.appendChild(th.firstChild);
    th.appendChild(btn);
    function sort(){
      var d = dir[i] = -(dir[i] || -1);
      var key = th.getAttribute('data-key');
      function val(r){ return key === 'text' ? (r.cells[0].textContent || '').trim() : (r.getAttribute('data-' + key) || ''); }
      rows.sort(function(a, b){ var x = val(a), y = val(b); var nx = parseFloat(x), ny = parseFloat(y); if (!isNaN(nx) && !isNaN(ny)) return (nx - ny) * d; return x.localeCompare(y, 'hu') * d; });
      rows.forEach(function(r){ tbody.appendChild(r); });
      heads.forEach(function(h){ h.setAttribute('aria-sort', h === th ? (d > 0 ? 'ascending' : 'descending') : 'none'); });
      if (window.__karzatRerender) window.__karzatRerender(table, false);
    }
    btn.addEventListener('click', sort);
  });
  apply();
})();
"""

JS_INSPECT = """
(function(){
  var chart = document.querySelector('.chart'), box = document.getElementById('insp'), src = document.getElementById('insp-data');
  if (!chart || !box || !src) return;
  var data; try { data = JSON.parse(src.textContent); } catch (e) { return; }
  var svg = chart.querySelector('svg'); if (!svg) return;
  var hint = box.innerHTML, pinned = null, colours = {};
  document.querySelectorAll('.legend .f[data-f] i').forEach(function(i){ colours[i.parentNode.getAttribute('data-f')] = i.style.background; });
  svg.querySelectorAll('.seat[data-az]').forEach(function(g){ if (g.hasAttribute('data-f') && !colours[g.getAttribute('data-f')]) { var c = g.querySelector('[fill]:not([fill="none"])'); if (c) colours[g.getAttribute('data-f')] = c.getAttribute('fill'); } });
  // keyboard: the chamber is one tab stop; ← → ↑ ↓ / Home / End walk the seats, Enter pins
  var seats = Array.prototype.slice.call(svg.querySelectorAll('.seat[data-az]'));
  seats.forEach(function(g, i){ g.setAttribute('tabindex', i === 0 ? '0' : '-1'); });
  function focusSeat(i){ if (!seats.length) return; i = (i + seats.length) % seats.length; seats.forEach(function(g, k){ g.setAttribute('tabindex', k === i ? '0' : '-1'); }); seats[i].focus(); }
  svg.addEventListener('keydown', function(e){
    var g = e.target.closest('.seat[data-az]'); if (!g) return; var i = seats.indexOf(g); if (i < 0) return;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); focusSeat(i + 1); }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { e.preventDefault(); focusSeat(i - 1); }
    else if (e.key === 'Home') { e.preventDefault(); focusSeat(0); }
    else if (e.key === 'End') { e.preventDefault(); focusSeat(seats.length - 1); }
  });
  var POS = {igen:'igen', nem:'nem', tartozkodott:'tartózkodott', jelen_nem_szavazott:'jelen, nem szavazott', nem_szavazott:'nem szavazott', bejelentett_hianyzo:'előre bejelentett hiányzó', igazoltan_tavol:'igazoltan távol'};
  var root = (document.querySelector('a.brand') || {}).getAttribute ? document.querySelector('a.brand').getAttribute('href').replace(/index\.html$/, '') : '';
  var mpBase = (svg.closest('body').querySelector('.pager') ? '../kepviselo/' : 'kepviselo/');
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function render(az){
    var d = data[az]; if (!d) return;
    var name = d[0], fac = d[1], mandate = d[2], seat = d[3], pos = d[4], cast = d[5], inroll = d[6], w = d[7], a = d[8], streak = d[9];
    var c = colours[fac] || '#8a8a8a';
    var part = inroll ? Math.round(100 * cast / inroll) : 0, agree = (w + a) ? Math.round(100 * w / (w + a)) : null;
    var sq = '';
    for (var i = 0; i < streak.length; i++) { var ch = streak[i]; sq += '<i class="' + (ch === '.' ? 'x' : ch) + (i === streak.length - 1 ? ' now' : '') + '" style="--c:' + c + '"></i>'; }
    box.innerHTML = '<div class="row1"><span class="name"><a href="' + mpBase + esc(az) + '.html">' + esc(name) + '</a></span>' +
      '<span class="meta"><i class="d" style="--c:' + c + '"></i> ' + esc(fac) + ' · ' + esc(mandate) + (seat ? ' · ' + esc(seat) : '') + '</span>' +
      '<span class="badge' + (pos === 'igen' ? ' ok' : pos === 'nem' ? ' no' : ' mid') + '">' + esc(POS[pos] || pos) + '</span></div>' +
      '<div class="row2"><span class="rec"><span class="lbl">a ciklusban</span>leadott <b>' + cast + '</b> / ' + inroll + ' (' + part + '%) · frakciójával <b>' + w + '</b> · ellene <b>' + a + '</b>' + (agree !== null ? ' · egyezés ' + agree + '%' : '') + '</span>' +
      '<span class="streak" title="az utolsó ' + streak.length + ' név szerinti szavazás eddig a szavazásig: frakciójával (szín) · ellene (fehér) · nem adott le (üres)"><span class="lbl">utolsó ' + streak.length + '</span>' + sq + '</span></div>' +
      (pinned ? '<span class="pin">rögzítve<button type="button" data-unpin>Esc</button></span>' : '');
  }
  function mark(az, on){
    svg.querySelectorAll('.seat[data-az="' + az + '"]').forEach(function(g){ g.classList.toggle('hl', on); });
    document.querySelectorAll('tr[data-az="' + az + '"]').forEach(function(r){ r.classList.toggle('hl', on); });
  }
  function show(az){ if (pinned && pinned !== az) return; render(az); }
  function reset(){ if (pinned) return; box.innerHTML = hint; }
  svg.addEventListener('mouseover', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) return; mark(g.getAttribute('data-az'), true); show(g.getAttribute('data-az')); });
  svg.addEventListener('mouseout', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) return; if (pinned !== g.getAttribute('data-az')) mark(g.getAttribute('data-az'), false); reset(); });
  svg.addEventListener('focusin', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) return; mark(g.getAttribute('data-az'), true); show(g.getAttribute('data-az')); });
  svg.addEventListener('focusout', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) return; if (pinned !== g.getAttribute('data-az')) mark(g.getAttribute('data-az'), false); reset(); });
  function pin(az){ if (pinned) mark(pinned, false); pinned = az; chart.classList.add('pinned'); mark(az, true); render(az); }
  function unpin(){ if (pinned) mark(pinned, false); pinned = null; chart.classList.remove('pinned'); box.innerHTML = hint; }
  svg.addEventListener('click', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) { unpin(); return; } var az = g.getAttribute('data-az'); if (pinned === az) unpin(); else pin(az); });
  svg.addEventListener('keydown', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) return; if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); var az = g.getAttribute('data-az'); if (pinned === az) unpin(); else pin(az); } });
  box.addEventListener('click', function(e){ if (e.target.closest('[data-unpin]')) unpin(); });
  document.addEventListener('keydown', function(e){ if (e.key === 'Escape') unpin(); });
  // the roll-call table talks back: hovering a row lights its seat
  document.querySelectorAll('tr[data-az]').forEach(function(r){
    r.addEventListener('mouseenter', function(){ mark(r.getAttribute('data-az'), true); show(r.getAttribute('data-az')); });
    r.addEventListener('mouseleave', function(){ if (pinned !== r.getAttribute('data-az')) mark(r.getAttribute('data-az'), false); reset(); });
  });
  // filters dim the seats they exclude
  window.__karzatDimSeats = function(fac, pos){
    svg.querySelectorAll('.seat').forEach(function(g){ var ok = (fac === 'all' || g.getAttribute('data-f') === fac) && (pos === 'all' || g.getAttribute('data-pos') === pos); g.classList.toggle('dim', !ok); });
  };
})();
"""

JS_TEXTFILTER = """
(function(){
  // <input data-filter-table="id"> narrows a table by text (accent-insensitive); the button filters of the same
  // table consult table.__textMatch, so both narrow together.
  function fold(s){ return String(s || '').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); }
  document.querySelectorAll('input[data-filter-table]').forEach(function(inp){
    var table = document.getElementById(inp.getAttribute('data-filter-table')); if (!table || !table.tBodies[0]) return;
    var all = Array.prototype.slice.call(table.tBodies[0].rows), rows = [], groups = [], cur = null, curTxt = '';
    all.forEach(function(r){ if (r.classList.contains('grp')) { cur = r; curTxt = fold(r.textContent); groups.push(r); r.__rows = []; return; } r.__hay = fold(r.textContent) + ' ' + curTxt; rows.push(r); if (cur) cur.__rows.push(r); });
    table.__textq = '';
    table.__textMatch = function(r){ return !table.__textq || (r.__hay || fold(r.textContent)).indexOf(table.__textq) >= 0; };
    table.__rowf = null;                                       // an attribute filter set by button[data-rowf] (below), if any
    function apply(){
      if (table.__reapply) { table.__reapply(); return; }
      rows.forEach(function(r){ var ok = table.__textMatch(r) && (!table.__rowf || r.getAttribute(table.__rowf[0]) === table.__rowf[1]); if (ok) r.removeAttribute('data-x'); else r.setAttribute('data-x', ''); });
      groups.forEach(function(g){ var any = g.__rows.some(function(r){ return !r.hasAttribute('data-x'); }); g.hidden = !any; });
      if (window.__karzatRerender) window.__karzatRerender(table, true);
    }
    table.__applyFilters = apply;
    inp.addEventListener('input', function(){ table.__textq = fold(inp.value).trim(); apply(); });
  });
  // <button data-rowf="attr:value" data-table="id"> (or data-rowf="all") beside a text box: keeps rows whose attribute equals the value
  document.querySelectorAll('button[data-rowf]').forEach(function(b){
    var table = document.getElementById(b.getAttribute('data-table')); if (!table) return;
    b.addEventListener('click', function(){
      var v = b.getAttribute('data-rowf');
      table.__rowf = (v === 'all') ? null : v.split(':');
      document.querySelectorAll('button[data-rowf][data-table="' + table.id + '"]').forEach(function(x){ var on = x === b; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on ? 'true' : 'false'); });
      if (table.__applyFilters) table.__applyFilters();
    });
  });
})();
"""

JS_MP = """
(function(){
  var t = document.getElementById('mine'); if (!t) return;
  var rows = Array.prototype.slice.call(t.tBodies[0].rows), n = document.getElementById('rn'), al = 'all', yr = 'all';
  function apply(){ var k = 0; rows.forEach(function(r){ var ok = (al === 'all' || r.getAttribute('data-al') === al) && (yr === 'all' || r.getAttribute('data-y') === yr) && (!t.__textMatch || t.__textMatch(r)); if (ok) { r.removeAttribute('data-x'); k++; } else r.setAttribute('data-x', ''); }); if (window.__karzatRerender) window.__karzatRerender(t, true); else n.textContent = k + ' / ' + rows.length; }
  t.__reapply = apply;
  function press(sel, on){ document.querySelectorAll(sel).forEach(function(x){ var isOn = x === on; x.classList.toggle('on', isOn); x.setAttribute('aria-pressed', isOn ? 'true' : 'false'); }); }
  document.querySelectorAll('button[data-alf]').forEach(function(b){ b.addEventListener('click', function(){ al = b.getAttribute('data-alf'); press('button[data-alf]', b); apply(); }); });
  document.querySelectorAll('button[data-yf]').forEach(function(b){ b.addEventListener('click', function(){ yr = b.getAttribute('data-yf'); press('button[data-yf]', b); apply(); }); });
  apply();
})();
"""

JS_CITE = """
(function(){
  // The citation names the page by its site-root path when no deployed origin was configured at build time;
  // in a browser the origin is known, so print the address the reader is actually looking at.
  var sc = document.querySelector('script[src$="assets/karzat.js"]'), root = sc ? sc.getAttribute('src').replace(/assets\/karzat\.js$/, '') : '';
  document.querySelectorAll('.cite[data-path]').forEach(function(sec){
    var p = sec.getAttribute('data-path'); if (!p || /^[a-z]+:/i.test(p)) return;
    var abs; try { abs = new URL(root + p, location.href).href; } catch (e) { return; }
    sec.querySelectorAll('pre').forEach(function(pre){ pre.textContent = pre.textContent.split(p).join(abs); });
  });
  document.querySelectorAll('.cite [data-copy]').forEach(function(b){
    b.addEventListener('click', function(){
      var pre = b.parentNode.querySelector('pre'); if (!pre) return;
      var done = function(){ b.classList.add('done'); b.textContent = 'másolva'; setTimeout(function(){ b.classList.remove('done'); b.textContent = 'másolás'; }, 1500); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(pre.textContent).then(done, function(){});
      else { var r = document.createRange(); r.selectNodeContents(pre); var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r); try { document.execCommand('copy'); done(); } catch (e) {} }
    });
  });
})();
"""

JS_SPEECHSEARCH = """
(function(){
  // felszolalas/kereses.html: terms are AND-ed; each term matches tokens by prefix (3+ letters, accents folded); the index
  // is sharded by the token's first two letters (idx/xx.json), the results table lists the speeches newest first and
  // fetches the visible pages' texts for a snippet.
  var q = document.getElementById('spq'), body = document.getElementById('spres'), n = document.getElementById('spn'), table = body && body.closest('table');
  if (!q || !body) return;
  function fold(s){ return String(s || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase(); }
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  var meta = null, metaFailed = false, metaWaiters = [], shards = {}, texts = {}, seq = 0;
  function get(url, cb){ var x = new XMLHttpRequest(); x.open('GET', url); x.onload = function(){ if (x.status >= 400) return cb(null); try { cb(JSON.parse(x.responseText)); } catch (e) { cb(null); } }; x.onerror = function(){ cb(null); }; x.send(); }
  function shard2(key, cb){ if (shards[key] !== undefined) return cb(shards[key]); get('idx/' + key + '.json', function(d){ shards[key] = d || {}; cb(shards[key]); }); }
  function shard(term, cb){ shard2(term.slice(0, 2), function(sh){ if (sh && sh.__split) shard2(term.slice(0, 3), cb); else cb(sh); }); }   // a big two-letter shard is split by the third letter
  function withMeta(cb){ if (meta) return cb(); if (metaFailed) return cb(); metaWaiters.push(cb); if (metaWaiters.length > 1) return; get('meta.json', function(d){ if (d) meta = d; else metaFailed = true; var w = metaWaiters; metaWaiters = []; w.forEach(function(f){ f(); }); }); }
  var urlTimer = null;
  function syncUrl(){ clearTimeout(urlTimer); urlTimer = setTimeout(function(){ if (!history.replaceState) return; var v = q.value.trim(); history.replaceState(null, '', v ? '?q=' + encodeURIComponent(v) : location.pathname); }, 300); }
  function search(){
    var my = ++seq;
    var terms = (fold(q.value).match(/[a-z0-9]{3,}/g) || []).filter(function(t, i, a){ return a.indexOf(t) === i; });
    if (!terms.length) { body.innerHTML = '<tr><td colspan="4" class="hero-meta">Kezdj el gépelni (legalább három betű).</td></tr>'; n.textContent = ''; if (window.__karzatRerender && table) window.__karzatRerender(table, true); return; }
    var need = terms.length + 1, sets = [];
    function step(){ if (--need > 0 || my !== seq) return; render(); }
    withMeta(step);
    terms.forEach(function(t, i){
      shard(t, function(sh){
        var ids = {};
        for (var k in sh) if (k !== '__split' && k.indexOf(t) === 0) { var arr = sh[k]; for (var j = 0; j < arr.length; j++) ids[arr[j]] = 1; }
        sets[i] = ids; step();
      });
    });
    function render(){
      if (!meta) { body.innerHTML = '<tr><td colspan="3" class="hero-meta">A kereső listája (meta.json) nem tölthető be.</td></tr>'; n.textContent = ''; return; }
      var hits = [];
      for (var i = 0; i < meta.length; i++) { var ok = true; for (var s = 0; s < sets.length; s++) { if (!sets[s][i]) { ok = false; break; } } if (ok) hits.push(i); }
      hits.reverse();                                                       // ids are chronological: newest first
      body.innerHTML = hits.map(function(i){ var m = meta[i]; var who = m[3] ? '<a href="../kepviselo/' + esc(m[3]) + '.html">' + esc(m[2]) + '</a>' : esc(m[2]);
        return '<tr data-i="' + i + '"><td class="ts mono"><a href="' + esc(m[0]) + '.html">' + esc(m[1]) + '</a></td><td>' + who + '<span class="sub">' + esc(m[4] || '') + (m[6] ? ' · ' + esc(m[6]) : '') + '</span></td><td>' + esc(m[5] || '') + '<span class="snip"></span></td></tr>'; }).join('')
        || '<tr><td colspan="3" class="hero-meta">Nincs találat.</td></tr>';
      n.textContent = hits.length + ' találat';
      if (window.__karzatRerender && table) window.__karzatRerender(table, true);
      snippets(terms);
    }
  }
  function snippets(terms){
    var rows = Array.prototype.slice.call(body.rows).filter(function(r){ return !r.hidden && r.hasAttribute('data-i'); });
    rows.forEach(function(r){
      var i = parseInt(r.getAttribute('data-i'), 10), m = meta[i], cell = r.querySelector('.snip'); if (!cell || cell.getAttribute('data-done')) return;
      cell.setAttribute('data-done', '1');
      var fill = function(d){ if (!d || !d.paragraphs) { cell.textContent = ''; return; }
        // matches are found on the folded text at word starts (the index rule) and painted on the original by the same
        // offsets — folding a precomposed letter keeps the length, so the offsets line up
        var txt = d.paragraphs.join(' '), f = fold(txt);
        if (f.length !== txt.length) f = txt.toLowerCase();
        var spans = [], first = -1;
        terms.forEach(function(t){ var re = new RegExp('(^|[^a-z0-9])' + t.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'), 'g'), m; while ((m = re.exec(f))) { var st = m.index + m[1].length, en = st + t.length; while (en < f.length && /[a-z0-9]/.test(f[en])) en++; spans.push([st, en]); if (first < 0 || st < first) first = st; if (spans.length > 40) break; } });
        if (first < 0) { cell.textContent = txt.slice(0, 160) + (txt.length > 160 ? '…' : ''); return; }
        var a = Math.max(0, first - 90), b = Math.min(txt.length, first + 130), out = '', pos = a;
        spans.sort(function(x, y){ return x[0] - y[0]; }).forEach(function(sp){ if (sp[1] <= a || sp[0] >= b || sp[0] < pos) return; out += esc(txt.slice(pos, sp[0])) + '<mark>' + esc(txt.slice(sp[0], Math.min(sp[1], b))) + '</mark>'; pos = Math.min(sp[1], b); });
        out += esc(txt.slice(pos, b));
        cell.innerHTML = (a > 0 ? '…' : '') + out + (b < txt.length ? '…' : ''); };
      if (texts[m[0]]) fill(texts[m[0]]); else get(m[0] + '.json', function(d){ texts[m[0]] = d; fill(d); });
    });
  }
  q.addEventListener('input', function(){ syncUrl(); search(); });
  if (table) table.addEventListener('click', function(e){ if (e.target.closest && e.target.closest('button')) setTimeout(function(){ snippets((fold(q.value).match(/[a-z0-9]{3,}/g) || [])); }, 50); });
  document.addEventListener('click', function(e){ var b = e.target.closest && e.target.closest('nav.pgr button'); if (b && table && b.closest('nav.pgr') && b.closest('nav.pgr').previousElementSibling && b.closest('nav.pgr').previousElementSibling.contains(table)) setTimeout(function(){ snippets((fold(q.value).match(/[a-z0-9]{3,}/g) || [])); }, 50); });
  if (location.search) { var m = /[?&]q=([^&]+)/.exec(location.search); if (m) { q.value = decodeURIComponent(m[1].replace(/\\+/g, ' ')); search(); } }
})();
"""

JS_SEARCH = """
(function(){
  var q = document.getElementById('sq'), out = document.getElementById('sres'), n = document.getElementById('sn'); if (!q || !out) return;
  var items = null, loading = false, kind = 'all', cyc = 'all';
  function fold(s){ return String(s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, ''); }
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  var failed = false;
  function load(cb){ if (items) return cb(); if (loading) return; loading = true; var x = new XMLHttpRequest(); x.open('GET', 'index.json'); x.onload = function(){ try { items = JSON.parse(x.responseText); } catch (e) { items = []; failed = true; } items.forEach(function(it){ it.f = fold(it.t) + ' ' + fold(it.s); it.ft = fold(it.t); }); cb(); }; x.onerror = function(){ items = []; failed = true; cb(); }; x.send(); }
  var urlTimer = null;
  function syncUrl(){ clearTimeout(urlTimer); urlTimer = setTimeout(function(){ if (!history.replaceState) return; var v = q.value.trim(); history.replaceState(null, '', v ? '?q=' + encodeURIComponent(v) : location.pathname); }, 300); }
  var KIND = {iromany: 'iromány', kepviselo: 'képviselő', szemely: 'pályakép'};
  function render(){
    var terms = fold(q.value).split(/\s+/).filter(Boolean);
    if (!terms.length) { out.innerHTML = '<tr><td colspan="3" class="hero-meta">Kezdj el gépelni.</td></tr>'; n.textContent = ''; return; }
    if (failed) { out.innerHTML = '<tr><td colspan="3" class="hero-meta">A keresőindex (index.json) nem tölthető be — a listák külön oldalakon: irományok, képviselők, személyek.</td></tr>'; n.textContent = ''; return; }
    var hits = [];
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      if (kind !== 'all' && it.k !== kind) continue;
      if (cyc !== 'all' && String(it.c) !== cyc && it.k !== 'szemely') continue;
      var ok = true, score = 0;
      for (var j = 0; j < terms.length; j++) { var t = terms[j]; if (it.f.indexOf(t) < 0) { ok = false; break; } if (it.ft === t) score += 3; else if (it.ft.indexOf(t) === 0) score += 2; else if (it.ft.indexOf(t) >= 0) score += 1; }
      if (ok) hits.push([score, it]);
    }
    hits.sort(function(a, b){ return b[0] - a[0] || (b[1].d || '').localeCompare(a[1].d || ''); });
    var shown = hits.slice(0, 200);
    out.innerHTML = shown.map(function(h){ var it = h[1]; return '<tr><td><a href="../' + esc(it.u) + '">' + esc(it.t) + '</a><span class="sub">' + esc(it.s) + '</span></td><td class="mono">' + esc(KIND[it.k] || it.k) + (it.n ? ' · ' + it.n + ' szavazás' : '') + '</td><td class="mono">' + (it.c ? it.c + '.' : '—') + '</td></tr>'; }).join('') || '<tr><td colspan="3" class="hero-meta">Nincs találat.</td></tr>';
    n.textContent = hits.length + ' találat' + (hits.length > 200 ? ' (az első 200)' : '');
  }
  q.addEventListener('input', function(){ syncUrl(); load(render); });
  document.querySelectorAll('button[data-sk]').forEach(function(b){ b.addEventListener('click', function(){ kind = b.getAttribute('data-sk'); document.querySelectorAll('button[data-sk]').forEach(function(x){ var on = x === b; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on ? 'true' : 'false'); }); load(render); }); });
  document.querySelectorAll('button[data-sc]').forEach(function(b){ b.addEventListener('click', function(){ cyc = b.getAttribute('data-sc'); document.querySelectorAll('button[data-sc]').forEach(function(x){ var on = x === b; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on ? 'true' : 'false'); }); load(render); }); });
  if (location.search) { var m = /[?&]q=([^&]+)/.exec(location.search); if (m) { q.value = decodeURIComponent(m[1].replace(/\+/g, ' ')); load(render); } }
})();
"""

JS_BOOT = """
(function(){
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  if (document.hidden) return;                       // a background tab gets the finished page, not a stalled boot
  var CH = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  function rnd(){ return CH[Math.floor(Math.random()*CH.length)]; }
  function ease(t){ return 1 - Math.pow(1 - t, 3); }
  // panels: fade and settle in
  var panels = Array.prototype.slice.call(document.querySelectorAll('.panel, .counts .c, .fresh, .kz-terminal'));
  panels.forEach(function(p, i){ p.style.opacity = '0'; p.style.transform = 'scale(.985)'; p.style.transition = 'opacity .35s ease-out, transform .45s cubic-bezier(.2,.7,.2,1)'; setTimeout(function(){ p.style.opacity = ''; p.style.transform = ''; }, 60 + i * 35); });
  // seats: pop in, inner rows first
  var seats = Array.prototype.slice.call(document.querySelectorAll('.chart svg .seat'));
  if (seats.length && seats.length < 600) {
    seats.forEach(function(g){ g.style.transition = 'none'; g.style.opacity = '0'; g.style.transform = 'scale(.3)'; g.style.transformBox = 'fill-box'; g.style.transformOrigin = 'center'; });
    seats.forEach(function(g, i){ setTimeout(function(){ g.style.transition = 'opacity .28s ease-out, transform .38s cubic-bezier(.2,.8,.2,1.2)'; g.style.opacity = ''; g.style.transform = ''; setTimeout(function(){ g.style.transition = ''; g.style.transformBox = ''; g.style.transformOrigin = ''; }, 450); }, 250 + i * 4); });
  }
  // labels (mono, uppercase only): scramble in — digits and punctuation stay put
  document.querySelectorAll('[data-kz-text]').forEach(function(el, i){
    var txt = el.textContent, n = txt.length; if (!n || n > 120) return;
    var t0 = null, dur = 320 + Math.min(n, 40) * 8, delay = 200 + i * 40;
    function step(ts){ if (!t0) t0 = ts; var k = ease(Math.min(1, (ts - t0) / dur)) * n, out = ''; for (var j = 0; j < n; j++){ var c = txt[j]; out += (j < k || /[\\s\\d.,:;–—\\-\\/()·|%]/.test(c)) ? c : rnd(); } el.textContent = out; if (k < n) requestAnimationFrame(step); else el.textContent = txt; }
    setTimeout(function(){ requestAnimationFrame(step); }, delay);
  });
  // numbers: count up to the printed value, formatted exactly as printed
  document.querySelectorAll('[data-kz-number]').forEach(function(el, i){
    var text = el.textContent, m = /^([^\\d]*)(\\d[\\d\\s.,]*)(.*)$/.exec(text); if (!m) return;
    var target = parseFloat(m[2].replace(/[\\s,]/g, '')); if (isNaN(target)) return;
    var prefix = m[1], suffix = m[3], t0 = null, dur = 900, delay = 300 + i * 30, isInt = Math.round(target) === target;
    function fmt(v){ return prefix + (isInt ? String(Math.round(v)) : v.toFixed(1)) + suffix; }
    function step(ts){ if (!t0) { t0 = ts; } var p = ease(Math.min(1, (ts - t0) / dur)); el.textContent = p < 1 ? fmt(target * p) : text; if (p < 1) requestAnimationFrame(step); }
    setTimeout(function(){ requestAnimationFrame(function(ts){ el.textContent = fmt(0); step(ts); }); }, delay);
  });
  // terminal: type the log lines, cursor at the end of the last one
  var log = document.querySelector('.kz-terminal .log');
  if (log) {
    var spans = Array.prototype.slice.call(log.querySelectorAll('span')), lines = spans.map(function(s){ return s.textContent; });
    spans.forEach(function(s){ s.textContent = ''; });
    var cur = document.createElement('i'); cur.className = 'cursor';
    var li = 0, ci = 0;
    function tick(){ if (li >= lines.length){ if (spans.length) spans[spans.length - 1].appendChild(cur); return; } var line = lines[li]; ci++; spans[li].textContent = line.slice(0, ci); if (ci >= line.length){ li++; ci = 0; setTimeout(tick, 120); } else setTimeout(tick, 9); }
    setTimeout(tick, 500);
  }
})();
"""


# -- shared fragments -------------------------------------------------------------------------

def page_head(title: str, description: str, depth: int = 0, feeds: list[tuple[str, str]] = ()) -> str:
    """`feeds`: (href, title) pairs announced for feed-reader autodiscovery."""
    rel = "../" * depth
    alt = "".join(f'\n<link rel="alternate" type="application/atom+xml" href="{esc(h)}" title="{esc(t)}">' for h, t in feeds)
    return f"""<!doctype html>
<html lang="hu">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="stylesheet" href="{rel}assets/karzat.css">{alt}
</head>
<body>"""


def topbar(inp: dict, crumbs: list[tuple[str, str | None]], depth: int = 0) -> str:
    """Konzol-style top bar: brand square, breadcrumb, cycle and the sync time — no 'live' badge."""
    rel = "../" * depth
    depth = depth + inp["base_depth"]
    rel = "../" * depth
    sync_txt = sync_stamp(inp)
    parts = []
    if inp["closed"]:
        crumbs = [(f'{inp["cycle"]}. ciklus', f'{rel}{cycle_dir(inp["cycle"])}index.html' if crumbs else None)] + list(crumbs)
    for i, (name, href) in enumerate(crumbs):
        if i:
            parts.append('<span class="sl">/</span>')
        parts.append(f'<a href="{esc(href)}">{esc(name)}</a>' if href else f'<span class="cur" aria-current="page">{esc(name)}</span>')
    cur_i = inp["cycles"].index(inp["cycle"]) if inp["cycle"] in inp["cycles"] else 0
    def cyc_link(i: int, c: int) -> str:
        if c == inp["cycle"]:
            return f'<b aria-current="true">{c}</b>'
        near = ' class="near"' if abs(i - cur_i) == 1 else ""
        return f'<a href="{rel}{cycle_dir(c)}index.html"{near}>{c}</a>'
    switch = ' <span class="sl">·</span> '.join(cyc_link(i, c) for i, c in enumerate(inp["cycles"]))
    return (f'<header class="kz-topbar"><div class="l"><a class="brand" href="{rel}{cycle_dir(inp["cycle"])}index.html"><i></i>karzat</a>'
            + (f'<span class="sep"></span><nav aria-label="Útvonal">{"".join(parts)}</nav>' if crumbs else "")
            + f'</div><div class="r"><nav class="kv cyc" aria-label="Ciklus"><span class="hide-xs">Ciklus </span>{switch}</nav><span class="sep hide-sm"></span>'
            f'<span class="kv sync"><span class="dot"></span><span class="hide-xs">Szinkron</span><b>{esc(sync_txt)}</b></span></div></header>\n<main class="kz-main"><div class="wrap">')


SITE_URL = os.environ.get("KARZAT_SITE_URL", "").rstrip("/")     # the deployed origin, when there is one; else paths are cited


def cite_html(inp: dict, path: str, title: str, key: str, json_href: str | None = None, csv_href: str | None = None) -> str:
    """A 'Hivatkozás' box: plain-text and BibTeX citations for this page, with a copy button, and the data twins."""
    url = f"{SITE_URL}/{path}" if SITE_URL else path
    stamp = sync_stamp(inp)
    year = stamp[:4] if stamp != "—" else "s. a."
    plain = f"karzat: {title}{'' if title[-1:] in '.?!' else '.'} Az Országgyűlés Web API-jának adataiból, frissítve {stamp}. {url}"
    bib = ("@misc{karzat-" + key + ",\n  title = {" + title.replace("{", "").replace("}", "") + "},\n  howpublished = {\\url{" + url + "}},\n"
           "  note = {karzat — az Országgyűlés szavazásai; az Országgyűlés Web API-jának adataiból, frissítve " + stamp + "},\n  year = {" + year + "}\n}")
    links = " · ".join(x for x in [f'<a href="{esc(json_href)}">JSON</a>' if json_href else "", f'<a href="{esc(csv_href)}">CSV</a>' if csv_href else ""] if x)
    return (f'<section class="panel cite" data-path="{esc(url)}">{CORNERS}<h2><span data-kz-text>Hivatkozás</span><span class="tag">{links}</span></h2>'
            f'<div class="cite-row"><pre class="mono">{esc(plain)}</pre><button type="button" class="copy" data-copy>másolás</button></div>'
            f'<details><summary class="mono">BibTeX</summary><div class="cite-row"><pre class="mono">{esc(bib)}</pre><button type="button" class="copy" data-copy>másolás</button></div></details></section>')


def sync_stamp(inp: dict) -> str:
    """The last sync as a Budapest timestamp, or an honest dash: the derive time is not a sync."""
    ls = inp["idx"].get("last_sync_at")
    return datetime.fromisoformat(ls).astimezone(BUDAPEST).strftime("%Y-%m-%d %H:%M") if ls else "—"


def page_tail(inp: dict, depth: int = 0, extra_script: str = "") -> str:
    rel = "../" * (depth + inp["base_depth"])
    inp["_depth"] = depth
    return f'</div></main>\n{terminal_html(inp)}{extra_script}<script src="{rel}assets/karzat.js"></script>\n</body>\n</html>\n'


def legend_html(view: dict, facs: list[dict], n_against: int = 0) -> str:
    fac_by_id = {f["id"]: f for f in facs}
    legend_f = "".join(
        f'<span class="f" data-f="{esc(t["faction"])}"><i style="background:{fac_by_id.get(t["faction"], {"colour": "#8a8a8a"})["colour"]}"></i>{esc(t["faction"])} <span class="mono">{esc(t["osszesen"])}</span></span>'
        for t in view.get("faction_tallies") or [])
    legend_p = ('<span class="f"><svg viewBox="-7 -7 14 14" aria-hidden="true" focusable="false"><circle r="5" fill="currentColor"/></svg>igen</span>'
                '<span class="f"><svg viewBox="-7 -7 14 14" aria-hidden="true" focusable="false"><circle r="4.6" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>nem</span>'
                '<span class="f"><svg viewBox="-7 -7 14 14" aria-hidden="true" focusable="false"><circle r="4.6" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M-4.6 0 A4.6 4.6 0 0 1 4.6 0Z" fill="currentColor"/></svg>tartózkodott</span>'
                '<span class="f"><svg viewBox="-7 -7 14 14" aria-hidden="true" focusable="false"><circle r="4.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.4 2"/></svg>jelen, nem szavazott</span>'
                '<span class="f"><svg viewBox="-7 -7 14 14" aria-hidden="true" focusable="false"><circle r="4.6" fill="currentColor" fill-opacity=".35" stroke="currentColor" stroke-opacity=".6"/></svg>nem szavazott</span>'
                '<span class="f"><svg viewBox="-7 -7 14 14" aria-hidden="true" focusable="false"><circle r="2.2" fill="currentColor" fill-opacity=".6"/></svg>előre bejelentett hiányzó</span>'
                + ('<span class="f"><svg viewBox="-7 -7 14 14" aria-hidden="true" focusable="false"><circle r="4.6" fill="none" stroke="currentColor" stroke-opacity=".5"/><circle r="2.2" fill="currentColor" fill-opacity=".6"/></svg>igazoltan távol</span>'
                   if (view.get("position_counts") or {}).get("igazoltan_tavol") else "")
                + (f'<span class="f"><svg viewBox="-7 -7 14 14" aria-hidden="true" focusable="false"><circle r="6.2" fill="none" stroke="#fff" stroke-width="1"/><circle r="3.6" fill="currentColor"/></svg>frakciója többsége ellen <span class="mono">{n_against}</span></span>'
                   if n_against else ""))
    return f'<div class="legend">{legend_f}</div><div class="legend">{legend_p}</div>'


STREAK_LEN = 20


def inspector_data(view: dict, inp: dict) -> tuple[dict, dict]:
    """Per MP in this roll call: what the seat inspector shows without any network — name, faction, mandate,
    seat, this vote's position, the cycle record (cast / in roll / with / against) and the last 20 roll calls
    up to and including this one as a streak string (w = with faction, a = against, c = cast without a
    faction plurality, n = did not cast, . = not in that roll call). Also returns {azon: align} for this vote."""
    per_mp = inp["alignment"]["per_mp"]
    al = inp["alignment"]
    if "by_mp_ts" not in al:                                   # index each MP's votes by ts once per cycle, not once per page
        al["by_mp_ts"] = {azon: {v["ts"]: v for v in rec["votes"]} for azon, rec in per_mp.items()}
        al["roll_order"] = [t for t in inp["order"] if inp["store"]["positions"].get(t)]
        al["roll_pos"] = {t: i for i, t in enumerate(al["roll_order"])}
    ts = view["ts"]
    here = al["roll_pos"].get(ts)
    # the roll calls of the cycle up to and including this one, newest last
    window = al["roll_order"][max(0, here + 1 - STREAK_LEN):here + 1] if here is not None else []
    data, align_here = {}, {}
    for pnt in view["positions"]:
        azon = pnt.get("mp_azon")
        if not azon:
            continue
        rec = per_mp.get(azon) or {"votes": [], "cast": 0, "in_roll": 0, "with": 0, "against": 0}
        by_ts = al["by_mp_ts"].get(azon) or {}
        streak = ""
        for t in window:
            v = by_ts.get(t)
            if v is None:
                streak += "."
            elif v["align"] == "with":
                streak += "w"
            elif v["align"] == "against":
                streak += "a"
            elif v["position"] in CAST:
                streak += "c"
            else:
                streak += "n"
        mp = inp["mps"].get(azon) or {}
        align_here[azon] = (by_ts.get(ts) or {}).get("align")
        data[azon] = [pnt["name"], pnt["faction"] or "", mandate_text(mp) if mp else "—",
                      seat_text(mp) if (mp and inp["plan"]) else None, pnt["position"],
                      rec["cast"], rec["in_roll"], rec["with"], rec["against"], streak]
    return data, align_here


def chart_block(view: dict, inp: dict) -> str:
    plan, facs = inp["plan"], inp["facs"]
    if not view["roll_call_available"]:
        return ('<div class="hero-meta" style="margin:14px 0">Titkos szavazás — nincs név szerinti lista.</div>'
                if view.get("secret") else '<div class="hero-meta" style="margin:14px 0">Nincs név szerinti lista.</div>')
    _, data, align_here = vote_bundle(inp, view["ts"])
    n_against = sum(1 for a in align_here.values() if a == "against")
    if plan:
        svg, info = seat_svg_real(view, facs, plan, align_here)
        geo = plan["geometry"]
        note = ((f'Az Országház alaprajza, mindenki a saját helyén. Az emelvény felől nézve balra az ellenzék, jobbra a kormányoldal. {info.get("empty", 0)} üres hely.')
                if info.get("seats_in_plan") else
                (f'Ülésrend a képviselői adatlapokból, az emelvény felől nézve: balra az ellenzék, jobbra a kormányoldal. Halvány karika: üres hely ({info.get("empty", 0)}).'))
    else:
        svg = seat_svg_fallback(view, facs, align_here)
        note = "Frakciónként, azon belül szavazat szerint rendezve. Ülésrend csak a jelenlegi ciklusból ismert."
    if n_against:
        note += f' Fehér gyűrű: {n_against} képviselő a frakciója többsége ellen szavazott.'
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/").replace("<!--", "<\\u0021--")
    inspector = ('<div class="inspector" id="insp" aria-live="polite">'
                 f'<div class="insp-hint">{"Egy ülőhelyre" if plan else "Egy jelre"} mutatva vagy koppintva a képviselő; kattintás rögzíti, <span class="mono">Esc</span> vagy a padlóra kattintás elengedi.</div></div>'
                 f'<script type="application/json" id="insp-data">{payload}</script>')
    return f'<div class="chart">{svg}</div>{inspector}{legend_html(view, facs, n_against)}<div class="hero-meta" style="margin-top:8px">{note}</div>'


def verdict_block(view: dict, inp: dict) -> str:
    facs = inp["facs"]
    fac_by_id = {f["id"]: f for f in facs}
    m = view.get("majority") or {}
    rule_info = RULES[Rule(m["rule"])] if m else None
    counts = view.get("position_counts") or {}
    tally = (f'<div class="tally"><div><b class="mono" data-kz-number="{view["igen"]}">{view["igen"]}</b><span>igen</span></div><div><b class="mono" data-kz-number="{view["nem"]}">{view["nem"]}</b><span>nem</span></div>'
             f'<div><b class="mono" data-kz-number="{view["tartozkodott"]}">{view["tartozkodott"]}</b><span>tartózkodott</span></div></div>')
    if view["kind"] == "jelenlet":
        return tally + f'<div class="hero-meta">Jelenlét megállapítása — nem döntés. Jelen: {esc(view.get("present"))}.</div>'
    if not m:
        return tally + '<div class="hero-meta">Nincs számított küszöb.</div>'
    share = round(100 * view["igen"] / m["base"], 1) if m.get("base") else 0
    need_pct = round(100 * m["needed"] / m["base"], 1) if m.get("base") else 50
    verdict_ok = m.get("agrees_with_source")
    fb = []
    for t in view.get("faction_tallies") or []:
        if all(t.get(k) is None for k in ("igen", "nem", "tartozkodott")):
            continue                                   # secret ballot: the API gives no per-position faction counts
        f = fac_by_id.get(t["faction"], {"colour": "#8a8a8a"})
        n = t["osszesen"] or 1
        seg = lambda k, op: f'<i style="width:{100*(t[k] or 0)/n:.1f}%;background:{f["colour"]};opacity:{op}" title="{esc(t["faction"])} {POSITION_LABEL.get(k,k)}: {t[k]}"></i>'
        rest = n - (t["igen"] or 0) - (t["tartozkodott"] or 0) - (t["nem"] or 0)
        ai = an.agreement_index(t["igen"] or 0, t["nem"] or 0, t["tartozkodott"] or 0)
        fb.append(f'<div class="row"><span>{esc(t["faction"])}</span><span class="stack">{seg("igen",1)}{seg("tartozkodott",.6)}{seg("nem",.35)}'
                  f'<i style="width:{100*rest/n:.1f}%;background:transparent" title="{esc(t["faction"])} nem szavazott / hiányzó: {rest}"></i></span>'
                  f'<span class="mono" style="text-align:right">{t["igen"]} – {t["nem"]} – {t["tartozkodott"]}<span class="sub" title="egyetértési index (Hix–Noury–Roland): 1 = egyhangú">{hu_dec(ai) if ai is not None else ""}</span></span></div>')
    base_word = "képviselő" if rule_info and rule_info.base == "seats" else "jelenlévő"
    base_note = ""
    return (tally +
            f'<div class="bar"><i style="width:{share}%"></i><em style="left:{need_pct}%"></em></div>'
            f'<div class="hero-meta mono">{view["igen"]} igen · szükséges {m.get("needed")} / {m.get("base")} · különbség {("+" if (m.get("margin") or 0) >= 0 else "") + str(m.get("margin"))}</div>'
            '<dl class="verdict">'
            f'<dt>Szavazási mód</dt><dd>{esc(view["mode"])}</dd>'
            f'<dt>Szabály</dt><dd>{esc(rule_info.label_hu if rule_info else "—")}</dd>'
            f'<dt>Alap</dt><dd class="mono">{m.get("base")} {base_word}{base_note}</dd>'
            f'<dt>Eredmény</dt><dd>{result_badge(view)} <span class="hero-meta">számítás {"egyezik" if verdict_ok else ("eltér" if verdict_ok is False else "—")}</span></dd>'
            + (f'<dt>Nem szavazott</dt><dd class="mono">{counts.get("nem_szavazott", 0)} · jelen, nem szavazott {counts.get("jelen_nem_szavazott", 0)} · előre bejelentett hiányzó {counts.get("bejelentett_hianyzo", 0)}' + (f' · igazoltan távol {counts["igazoltan_tavol"]}' if counts.get("igazoltan_tavol") else "") + '</dd>' if view["roll_call_available"] else '')
            + '</dl>'
            + (f'<div class="fbars"><div class="row hdr"><span></span><span></span><span class="mono" style="text-align:right">igen – nem – tart.<span class="sub">egyetértési index · 1 = egyhangú</span></span></div>{"".join(fb)}</div>' if fb else ''))


def needed_tag(view: dict) -> str:
    m = view.get("majority") or {}
    if view["kind"] == "jelenlet":
        return "jelenlét"
    return f'szükséges {m["needed"]} / {m["base"]}' if m.get("needed") and m.get("base") else "—"


def motion_title_html(view: dict, up: str = "../") -> tuple[str, str]:
    """`up` = path from the page to the cycle root ('' on the directory, '../' on a vote page)."""
    mo = view["motions"][0] if view.get("motions") else {}
    if not mo:
        title = "Jelenlét megállapítása" if view["kind"] == "jelenlet" else (view.get("remark") or "Szavazás")
        return f'<div class="hero-title">{esc(title)}</div>', ""
    n, _ = an.bill_key(mo.get("iromany"))
    link = (f'<a href="{up}iromany/{n}.html" title="az iromány szavazásai">{esc(mo.get("iromany"))}</a> ' if n is not None else esc(mo.get("iromany", "")) + " ")
    meta = f'{esc(mo.get("outcome", ""))} · benyújtó: {esc(", ".join(mo.get("submitters") or []))}' if mo.get("submitters") else esc(mo.get("outcome", ""))
    if mo.get("href"):
        meta += f' · <a href="{esc(mo["href"])}" target="_blank" rel="noopener">parlament.hu ↗</a>'
    return f'<div class="hero-title">{link}{esc(mo.get("title", ""))}</div>', f'<div class="hero-meta">{meta}</div>'


def _glyph(pos: str, c: str) -> str:
    """The position glyph as a CSS-drawn element (thousands per page; the legend keeps the SVG originals)."""
    return f'<i class="g g-{esc(pos)}" style="--c:{c}" aria-hidden="true"></i>'


def roll_call_table(view: dict, inp: dict) -> str:
    plan, facs = inp["plan"], inp["facs"]
    if not view["roll_call_available"]:
        return ""
    colour = {f["id"]: f["colour"] for f in facs}
    coords = (plan or {}).get("coords", {})
    order = {f["id"]: f["sort_order"] for f in facs}
    pos_rank = {p: i for i, p in enumerate(POSITION_ORDER)}
    rows = sorted(view["positions"], key=lambda p: (order.get(p["faction"] or "", 999), pos_rank.get(p["position"], 9), name_key(p["name"])))
    trs = []
    for p in rows:
        xy = coords.get(p.get("mp_azon") or "")
        if xy:
            place = "miniszteri pad" if xy.get("front_bench") else f'{xy["sector"]}. szektor'
            seat_full = f'{place}, {xy["row"]}. sor, {xy["seat"]}. szék'
            seatkey = f'{xy["sector"]:02d}{xy["row"]:02d}{xy["seat"]:02d}'
        else:
            seat_full, seatkey = "—", "999999"
        c = colour.get(p["faction"] or "", "#8a8a8a")
        name_html = f'<a href="../kepviselo/{esc(p["mp_azon"])}.html">{esc(p["name"])}</a>' if p.get("mp_azon") else esc(p["name"])
        trs.append(f'<tr data-f="{esc(p["faction"])}" data-p="{esc(p["position"])}" data-posrank="{pos_rank.get(p["position"], 9)}" data-name="{esc(name_key(p["name"]))}"' + (f' data-seat="{seatkey}"' if plan else "") + (f' data-az="{esc(p["mp_azon"])}"' if p.get("mp_azon") else "") + '>'
                   f'<td>{name_html}</td><td><span class="pos"><i class="d" style="--c:{c}"></i>{esc(p["faction"])}</span></td>'
                   f'<td><span class="pos">{_glyph(p["position"], c)}{esc(POSITION_LABEL.get(p["position"], p["position"]))}</span></td>' + (f'<td class="mono">{esc(seat_full)}</td>' if plan else '') + '</tr>')
    fac_buttons = '<button type="button" data-fac="all" class="on" aria-pressed="true">minden frakció</button>' + "".join(f'<button type="button" data-fac="{esc(t["faction"])}" aria-pressed="false">{esc(t["faction"])}</button>' for t in view.get("faction_tallies") or [])
    pos_buttons = '<button type="button" data-posf="all" class="on" aria-pressed="true">minden szavazat</button>' + "".join(f'<button type="button" data-posf="{p}" aria-pressed="false">{esc(POSITION_LABEL[p])}</button>' for p in POSITION_ORDER if (view.get("position_counts") or {}).get(p))
    dl = f' · <a href="{esc(view["slug"])}.csv">CSV</a> · <a href="{esc(view["slug"])}.json">JSON</a>'
    return (f'<section class="panel deep">{CORNERS}<h2><span data-kz-text>Név szerinti lista</span><span class="tag">{len(rows)} képviselő{dl}</span></h2>'
            f'<div class="filters" role="group" aria-label="Szűrés frakció szerint">{fac_buttons}</div><div class="filters" role="group" aria-label="Szűrés szavazat szerint">{pos_buttons}<input type="search" data-filter-table="roll" placeholder="név" aria-label="Szűrés névre" style="min-width:140px"><span class="n" id="rn" aria-live="polite"></span></div>'
            '<div class="tablewrap"><table id="roll" data-page-size="25" data-counter="rn"><thead><tr><th scope="col" class="sortable" data-key="name">Képviselő</th><th scope="col" class="sortable" data-key="f">Frakció</th>'
            '<th scope="col" class="sortable" data-key="posrank">Szavazat</th>' + ('<th scope="col" class="sortable" data-key="seat">Ülőhely</th>' if plan else '') + '</tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table></div>'
            '</section>')


def rel_root_for_footer(inp: dict) -> str:
    """The footer is emitted at every depth; the caller sets inp['_depth'] before rendering (build functions do)."""
    return "../" * (inp.get("_depth", 0) + inp["base_depth"])


def terminal_html(inp: dict, lines: list[str] | None = None) -> str:
    """The footer as a terminal: a boot log of true values, then the method notes."""
    idx, fl, plan = inp["idx"], inp["fl"], inp["plan"]
    tot = site_totals()
    n_cyc = len(tot["cycles"])
    roster_n = fl["mps"]["total"] if not inp["closed"] else len(inp["mps"])
    days = fl["sitting_days"]["count"]
    check = ("a számított eredmény minden szavazásnál egyezik a jegyzőkönyvivel" if tot["disagreements"] == 0
             else f"a számított eredmény {hu_num(tot['disagreements'])} szavazásnál eltér a jegyzőkönyvitől — jelölve")
    seats_line = (f"ülésrend: az Országház alaprajza, {hu_num(len(plan['seat_outlines']))} hely — csak a jelenlegi ciklusból ismert"
                  if plan and plan.get("seat_outlines") else "ülésrend: csak a jelenlegi ciklusból ismert")
    default_lines = [
        f"{n_cyc} ciklus · {hu_date(tot['from'])} – {hu_date(tot['to'])} · {hu_num(tot['votes'])} szavazás · {hu_num(tot['roll_calls'])} név szerinti lista · {hu_num(tot['people'])} képviselő",
        f"ezen az oldalon: {inp['cycle']}. ciklus · {hu_num(fl['votes'])} szavazás · {hu_num(days) if days else '—'} ülésnap · {hu_num(roster_n)} képviselő",
        check,
        seats_line,
        (f"frissítve {hu_date(sync_stamp(inp)[:10])} {sync_stamp(inp)[11:]}" if sync_stamp(inp) != "—" else "frissítve —"),
    ]
    log = "".join(f"<span>&gt; {esc(l)}</span>" for l in (lines or default_lines))
    return f"""<footer class="kz-terminal"><div class="scan"></div>{CORNERS}
  <div class="log">{log}</div>
  <div class="method">
    <h3>Honnan</h3>
    Az Országgyűlés nyilvános Web API-jából, szavazásonként és név szerint; a képviselőket a Wikidata azonosítói kötik össze a ciklusokon át. A szükséges többség az, amit az Országgyűlés maga jelöl meg minden szavazásnál — az oldal csak utánaszámol. Jelenlévőnek azt vesszük, aki igennel, nemmel, tartózkodással szavazott, vagy jelen volt és nem szavazott.
    <h3>Mit nem</h3>
    Az oldal számol, nem ítél: ki hogyan szavazott, hányszor, a frakciójával vagy ellene. Hogy miért, azt nem tudja. A színek az oldal jelölései, nem a pártokéi. Minden számítás leírása: <a href="{rel_root_for_footer(inp)}modszer/index.html">módszer</a>.
  </div>
</footer>"""


# -- pages ------------------------------------------------------------------------------------

def freshness_html(inp: dict) -> str:
    idx = inp["idx"]
    derived_at = datetime.fromisoformat(idx["derived_at"]).astimezone(BUDAPEST)
    last_sync = datetime.fromisoformat(idx["last_sync_at"]).astimezone(BUDAPEST) if idx.get("last_sync_at") else None
    newest = max((v["ts"] for v in idx["votes"]), default=None)
    newest_dt = datetime.strptime(newest, "%Y.%m.%d.%H:%M:%S").replace(tzinfo=BUDAPEST) if newest else None
    fr = assess(sitting_days=[_date.fromisoformat(d["date"]) for d in idx["sitting_days"] if d.get("date")],
                newest_vote_at=newest_dt, last_sync_at=last_sync, now=derived_at, absolute_sync=True)
    return f'<div class="fresh">{CORNERS}<span class="hu">{esc(fr.sentence_hu)}</span><span class="en" lang="en">{esc(fr.sentence_en)}</span></div>'


def roster_summary(inp: dict) -> dict:
    """Who sat in this cycle: the current list for the current cycle; the roll-call roster (mps_ckl<N>) for a closed one."""
    if not inp["closed"]:
        return {"total": inp["fl"]["mps"]["total"], "by_faction": inp["fl"]["mps"]["by_faction"], "note": "a jelenlegi névsor"}
    order = {f["id"]: f["sort_order"] for f in inp["facs"]}
    by_f: dict[str, int] = {}
    for m in inp["mps"].values():
        by_f[m.get("faction") or "—"] = by_f.get(m.get("faction") or "—", 0) + 1
    by_f = dict(sorted(by_f.items(), key=lambda kv: (order.get(kv[0], 999), kv[0])))
    return {"total": len(inp["mps"]), "by_faction": by_f, "note": "a ciklus névsoraiban szereplők, a pótlásokkal, utolsó frakciójuk szerint"}


def directory_row(v: dict) -> str:
    """One row of the vote directory, rendered at build time (the JS only hides/shows rows)."""
    m = v.get("majority") or None
    rule = m["rule"] if m else ("jelenlet" if v["kind"] == "jelenlet" else "none")
    mo = v["motions"][0] if v.get("motions") else None
    if mo:
        full = mo.get("title") or ""
        bn, _ = an.bill_key(mo.get("iromany"))
        subj = ((f'<a href="iromany/{bn}.html">{esc(mo.get("iromany") or "")}</a>' if bn is not None else esc(mo.get("iromany") or ""))
                + " " + esc(full[:110]) + (f'<span class="more" title="{esc(full)}">…</span>' if len(full) > 110 else "")
                + f'<span class="sub">{esc(mo.get("outcome") or "")}</span>')
    else:
        subj = "<em>Jelenlét megállapítás</em>" if v["kind"] == "jelenlet" else esc(v.get("remark") or "—")
    need = f'{esc(rule_short(m["rule"]))} <span class="sub mono">{m["needed"]} / {m["base"]}</span>' if m else "—"
    res = result_badge(v)
    margin = ("+" if m["margin"] >= 0 else "") + str(m["margin"]) if m and m.get("margin") is not None else ""
    flag = ' <span class="badge hi" title="a számított és a forrás szerinti eredmény eltér">?</span>' if m and m.get("agrees_with_source") is False else ""
    return (f'<tr data-rule="{esc(rule)}" data-result="{esc(v["result_raw"])}" data-y="{esc(v["on_date"][:4])}">'
            f'<td class="ts mono"><a href="szavazas/{esc(v["slug"])}.html">{esc(v["on_date"])} {esc(v["time"])}</a></td><td>{subj}</td><td>{need}</td>'
            f'<td class="num mono">{v["igen"]} – {v["nem"]} – {v["tartozkodott"]}</td><td class="num mono">{margin}</td><td>{res}{flag}</td></tr>')


def build_index(inp: dict, hero_ts: str) -> str:
    idx, fl = inp["idx"], inp["fl"]
    hero, _, _ = vote_bundle(inp, hero_ts)
    title_html, meta_html = motion_title_html(hero, up="")
    roster = roster_summary(inp)
    sessions = fl["sitting_days"]["by_session"]
    dated = [d for d in idx["sitting_days"] if d.get("date")]                # date-sorted by the derive
    sessions_txt = (" · ".join(f'{k}: {v}' for k, v in sessions.items()) if len(sessions) <= 4
                    else f'{len(sessions)} ülésszak, {dated[0]["session"]} … {dated[-1]["session"]}' if dated else f'{len(sessions)} ülésszak')
    no_ulesnap = fl["sitting_days"]["count"] == 0
    counts_cards = [
        (fl["votes"], "szavazás", f'{hu_date(fl["window"]["from"])} – {hu_date(fl["window"]["to"])}'),
        (fl["sitting_days"]["count"] if not no_ulesnap else "—", "ülésnap", sessions_txt if not no_ulesnap else "ülésnap-lista az API-ban csak 1998-tól"),
        (fl["qualified_majority_votes"], "minősített többségű szavazás", f'{fl["decisions"]} döntésből'),
        (fl["kiveteles_votes"], "kivételes eljárás elrendelése", f'{fl["surgos_votes"]} sürgős tárgyalás'),
        (fl["hazszabalytol_elteres_votes"], "Házszabálytól eltérés", f'{fl["interpellation_answers"]} interpellációs válasz elfogadva'),
        (roster["total"], "képviselő", " · ".join(f'{k}: {v}' for k, v in roster["by_faction"].items())),
    ]
    counts_html = "".join(f'<div class="c">{CORNERS}<b class="mono" data-kz-number="{c}">{hu_num(c)}</b><span data-kz-text>{esc(l)}</span><span class="sub">{esc(s)}</span></div>' for c, l, s in counts_cards)
    if inp["closed"]:
        counts_html += f'<div class="c" style="grid-column:1/-1;padding:8px 16px"><span class="sub">{esc(roster["note"])}</span></div>'
    mode_rows = "".join(f'<tr><td>{esc(k)}</td><td class="num mono">{v}</td></tr>' for k, v in fl["by_mode"].items())
    rules_present = sorted({(v["majority"] or {}).get("rule") for v in idx["votes"] if v.get("majority")}, key=lambda r: list(Rule).index(Rule(r)) if r else 99)
    rule_buttons = '<button type="button" data-rule="all" class="on" aria-pressed="true">mind</button>' + "".join(f'<button type="button" data-rule="{r}" aria-pressed="false">{esc(rule_short(r))}</button>' for r in rules_present) + '<button type="button" data-rule="jelenlet" aria-pressed="false">jelenlét</button>'
    result_buttons = '<button type="button" data-result="all" class="on" aria-pressed="true">minden eredmény</button><button type="button" data-result="Elfogadva" aria-pressed="false">elfogadva</button><button type="button" data-result="Elutasítva" aria-pressed="false">elutasítva</button>'
    years = sorted({v["on_date"][:4] for v in idx["votes"]}, reverse=True)
    year_buttons = ('<div class="filters" role="group" aria-label="Szűrés év szerint"><button type="button" data-year="all" class="on" aria-pressed="true">minden év</button>'
                    + "".join(f'<button type="button" data-year="{y}" aria-pressed="false">{y}</button>' for y in years) + '</div>') if len(years) > 1 else ""
    dir_rows = "".join(directory_row(row) for row in reversed(idx["votes"]))          # newest first, complete without JS
    hero_date = hu_date(hero["on_date"])
    if inp["closed"]:
        result_rows = "".join(f'<tr><td>{esc(k)}</td><td class="num mono">{v}</td></tr>' for k, v in fl["by_result"].items())
        month_rows = sorted(fl["by_month"].items())
        top = max((v for _, v in month_rows), default=1)
        months = "".join(f'<div class="row"><span>{esc(m)}</span><span class="stack"><i style="width:{100*v/top:.1f}%;background:var(--dim)"></i></span><span class="mono" style="text-align:right">{v}</span></div>' for m, v in month_rows)
        second_panel = (f'<section class="panel">{CORNERS}<h2><span data-kz-text>Eredmények és a ciklus ritmusa</span><span class="tag">{fl["decisions"]} döntés</span></h2>'
                        f'<div class="tablewrap" style="border:0"><table><thead><tr><th scope="col">Eredmény</th><th scope="col" class="num">db</th></tr></thead><tbody>{result_rows}</tbody></table></div>'
                        f'<div class="hero-meta" style="margin-top:10px"><span class="lbl">szavazás havonta</span></div><div class="fbars" style="max-height:260px;overflow:auto;margin-top:4px">{months}</div></section>')
    else:
        second_panel = (f'<section class="panel">{CORNERS}<h2><span data-kz-text>Egy törvény útja</span><span class="tag">T/71</span></h2>'
                        '<div class="hero-title" style="font-size:16px">A Nemzetközi Büntetőbíróság Statútumából való kilépés visszavonása — 2026. évi XV. törvény</div>'
                        '<div class="timeline"><div><b>máj. 25.</b>benyújtva (kormány)</div><div><b>máj. 26.</b>kivételes eljárás elrendelve</div><div><b>máj. 27.</b>zárószavazás</div><div><b>máj. 28.</b>kihirdetve, Magyar Közlöny 60</div></div>'
                        '<div class="hero-meta prose" style="margin-top:8px">3 nap a benyújtástól a kihirdetésig.</div></section>')
    cyc = f'{inp["cycle"]}. ciklus ({CYCLE_SPAN.get(inp["cycle"], "")})'
    closed_line = (f'<div class="fresh">{CORNERS}<span class="hu">Lezárt ciklus, {esc(CYCLE_SPAN.get(inp["cycle"], ""))}: mind a {hu_num(fl["votes"])} szavazás itt van.</span>'
                   f'<span class="en" lang="en">A closed term, {esc(CYCLE_SPAN.get(inp["cycle"], ""))}: all {hu_num(fl["votes"])} votes are here.</span></div>') if inp["closed"] else freshness_html(inp)
    return page_head(("karzat — az Országgyűlés szavazásai, ülőhelyenként" if not inp["closed"] else f'karzat — {inp["cycle"]}. ciklus, az Országgyűlés szavazásai'),
                     f"Az Országgyűlés szavazásai a {inp['cycle']}. ciklusban: minden szavazás a saját szükséges többségével, egy szavazás {'ülőhelyenként' if not inp['closed'] else 'név szerint, frakciónként rendezve'} kirajzolva. Forrás: parlament.hu Web API.", inp["base_depth"],
                     feeds=[("feed/kulonvelemeny.xml", f'karzat · különvélemények · {inp["cycle"]}. ciklus'), ("feed/szavazasok.xml", f'karzat · szavazások · {inp["cycle"]}. ciklus'), ("feed/heti.xml", f'karzat · a hét számokban · {inp["cycle"]}. ciklus')]) + topbar(inp, [], 0) + f"""
<div class="hero-h"><h1>karzat</h1><small class="label" data-kz-text>{esc(cyc) + " — " if inp["closed"] else ""}az Országgyűlés szavazásai, {"ülőhelyenként" if not inp["closed"] else "név szerint"} — a szükséges többséggel együtt</small></div>
<nav class="crumbs" aria-label="Szakaszok"><a href="#dir">szavazások</a> <span class="sl">/</span> <a href="kepviselo/index.html">képviselők</a> <span class="sl">/</span> <a href="{"../" * inp["base_depth"]}szemely/index.html">személyek</a> <span class="sl">/</span> <a href="iromany/index.html">irományok</a> <span class="sl">/</span> <a href="szamok/index.html">számok</a> <span class="sl">/</span> <a href="kohezio/index.html">kohézió</a> <span class="sl">/</span> <a href="szoros/index.html">szoros szavazások</a> <span class="sl">/</span> <a href="felszolalas/index.html">felszólalások</a> <span class="sl">/</span> <a href="kepviselom/index.html">képviselőm</a> <span class="sl">/</span> <a href="feed/index.html">értesítések</a> <span class="sl">/</span> <a href="adatok/index.html">adatok</a> <span class="sl">/</span> <a href="{"../" * inp["base_depth"]}modszer/index.html">módszer</a> <span class="sl">/</span> <a href="{"../" * inp["base_depth"]}kereses/index.html">keresés</a> <span class="sl">/</span> ciklusok: {" · ".join(f'<b>{c}</b>' if c == inp["cycle"] else f'<a href="{"../" * inp["base_depth"]}{cycle_dir(c)}index.html" title="{esc(CYCLE_SPAN.get(c, ""))}">{c}</a>' for c in inp["cycles"])} <span class="sl">·</span> {esc(CYCLE_SPAN.get(inp["cycle"], ""))}</nav>
<p class="lede">{hu_num(fl["votes"])} szavazás a {inp["cycle"]}. ciklus{" első " + str(fl["sitting_days"]["count"]) + " ülésnapjáról" if not inp["closed"] else "ból"}, mindegyik a saját oldalán: ki hogyan szavazott, és mennyi kellett hozzá.</p>
{closed_line}
<section class="grid">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>{"Egy szavazás, ülőhelyenként" if not inp["closed"] else "Egy szavazás, frakciónként"}</span><span class="tag"><a href="szavazas/{esc(hero["slug"])}.html">{esc(hero_date)} {esc(hero["ts"][11:16])} ↗</a></span></h2>
    {title_html}{meta_html}
    {chart_block(hero, inp)}
  </section>
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Eredmény és a szükséges többség</span><span class="tag">{esc(needed_tag(hero))}</span></h2>
    {verdict_block(hero, inp)}
  </section>
</section>
<section class="counts">{counts_html}</section>
<section class="grid">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Szavazási módok</span></h2>
    <div class="tablewrap" style="border:0"><table><thead><tr><th>Szavazási mód</th><th class="num">db</th></tr></thead><tbody>{mode_rows}</tbody></table></div>
  </section>
  {second_panel}
</section>
<section class="panel deep" id="dir">{CORNERS}
  <h2><span data-kz-text>Szavazások</span><span class="tag">{hu_num(fl["votes"])} tétel · a legfrissebb elöl · <a href="adatok/szavazasok.csv">CSV</a> · <a href="adatok/szavazasok.json">JSON</a> · <a href="adatok/index.html">minden adat</a></span></h2>
  {year_buttons}
  <div class="filters" role="group" aria-label="Szűrés szükséges többség szerint">{rule_buttons}</div>
  <div class="filters" role="group" aria-label="Szűrés eredmény szerint">{result_buttons}<input id="q" type="search" placeholder="keresés --tárgy" aria-label="Keresés a tárgyban"><span class="n" id="n" aria-live="polite"></span></div>
  <div class="tablewrap"><table data-page-size="25" data-counter="n"><thead><tr><th scope="col">Időpont</th><th scope="col">Tárgy</th><th scope="col">Szükséges többség</th><th scope="col" class="num">igen – nem – tart.</th><th scope="col" class="num">különbség</th><th scope="col">Eredmény</th></tr></thead><tbody id="rows">{dir_rows}</tbody></table></div>
</section>
""" + page_tail(inp, 0)


def vote_bundle(inp: dict, ts: str) -> tuple[dict, dict, dict]:
    """(view, inspector data, align) for one vote — computed once, shared by the page and its exports."""
    cache = inp.setdefault("_bundle", {})
    if ts not in cache:
        view = vote_view(inp, ts)
        data, align_here = inspector_data(view, inp)
        cache.clear()                                        # one vote at a time is all a build needs
        cache[ts] = (view, data, align_here)
    return cache[ts]


def vote_exports(inp: dict, ts: str) -> tuple[str, str]:
    """(json, csv) for one vote, from the same view the page renders."""
    view, _, align_here = vote_bundle(inp, ts)
    return ex.vote_export(view, inp["cycle"], align_here, (inp["plan"] or {}).get("coords") if inp["plan"] else None)


def build_vote_page(inp: dict, ts: str) -> str:
    order = inp["order"]
    i = order.index(ts)
    prev_ts = order[i - 1] if i > 0 else None
    next_ts = order[i + 1] if i + 1 < len(order) else None
    view, _, _ = vote_bundle(inp, ts)
    title_html, meta_html = motion_title_html(view)
    mo = view["motions"][0] if view.get("motions") else {}
    label = f'{mo.get("iromany") or ""} {cut(mo.get("title") or "", 90)}'.strip() if mo else ("Jelenlét megállapítása" if view["kind"] == "jelenlet" else "Szavazás")
    # minute precision, unless another vote of the cycle fell in the same minute (then the seconds tell them apart)
    mc = inp.get("_minute_counts")
    if mc is None:
        mc = inp["_minute_counts"] = Counter(t[:16] for t in inp["order"])
    when = f'{hu_date(view["on_date"])} {view["ts"][11:16]}{":" + view["ts"][17:19] if mc[view["ts"][:16]] > 1 else ""}'

    def pager_link(t, arrow_left):
        if not t:
            return "<span></span>"
        vv = inp["by_ts"][t]
        m0 = vv["motions"][0] if vv["motions"] else {}
        txt = f'{vv["on_date"]} {vv["time"]} · {(m0.get("iromany") or "")} {cut(m0.get("title") or vv.get("remark") or "", 60)}'
        return f'<a href="{esc(vv["slug"])}.html">{"← " if arrow_left else ""}{esc(txt)}{"" if arrow_left else " →"}</a>'

    return page_head(f'{when} — {label}{" · " + str(inp["cycle"]) + ". ciklus" if inp["closed"] else ""} · karzat', f'Az Országgyűlés {when} szavazása {"ülőhelyenként" if inp["plan"] else "név szerint"}, a szükséges többséggel: {label}', 1 + inp["base_depth"]) + \
        topbar(inp, [("szavazások", "../index.html#dir"), (f'{view["on_date"]} {view["ts"][11:16]}', None)], 1) + f"""
<div class="hero-h"><h1>{esc(when)}</h1><small class="label" data-kz-text>{esc(view["mode"])}</small></div>
<section class="grid">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>A szavazás</span><span class="tag">{esc(view["ts"])}</span></h2>
    {title_html}{meta_html}
    {chart_block(view, inp)}
  </section>
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Eredmény és a szükséges többség</span><span class="tag">{esc(needed_tag(view))} · <a href="{"../" * (1 + inp["base_depth"])}modszer/index.html#tobbseg">módszer</a></span></h2>
    {verdict_block(view, inp)}
  </section>
</section>
{roll_call_table(view, inp)}
{cite_html(inp, f'{cycle_dir(inp["cycle"])}szavazas/{view["slug"]}.html', f'{when} — {label}', f'{inp["cycle"]}-{view["slug"]}', f'{view["slug"]}.json', f'{view["slug"]}.csv' if view["roll_call_available"] else None)}
<nav class="pager" aria-label="Előző és következő szavazás">{pager_link(prev_ts, True)}{pager_link(next_ts, False)}</nav>
""" + page_tail(inp, 1)


# -- MP pages ---------------------------------------------------------------------------------

def chamber_mini_svg(inp: dict, azon: str) -> str:
    """The whole chamber in faint dots with this MP's seat highlighted."""
    plan = inp["plan"]
    if not plan or azon not in plan["coords"]:
        return ""
    colour = {f["id"]: f["colour"] for f in inp["facs"]}
    geo = plan["geometry"]
    R = (geo["outer_radius"] + 14) if "outer_radius" in geo else (-geo["y_min"] + 10)
    parts = []
    k = geo.get("node_radius", 5.2) / 5.2
    for a, xy in plan["coords"].items():
        if a == azon:
            continue
        parts.append(f'<circle cx="{xy["x"]}" cy="{xy["y"]}" r="{3.6*k:.2f}" fill="#a1a1aa" fill-opacity=".16"/>')
    outlines = plan.get("seat_outlines")
    if outlines:
        parts = [f'<polygon points="{o["points"]}" class="seatshape"/>' for o in outlines] + parts
        R = -geo["y_min"] + 10
    else:
        for e in plan.get("empty_seats") or []:
            parts.append(f'<circle cx="{e["x"]}" cy="{e["y"]}" r="{3.6*k:.2f}" class="seat-empty"/>')
    me = plan["coords"][azon]
    c = colour.get(inp["mps"].get(azon, {}).get("faction") or "", "#8a8a8a")
    parts.append(f'<circle cx="{me["x"]}" cy="{me["y"]}" r="{9*k:.2f}" fill="{c}" fill-opacity=".25"/><circle cx="{me["x"]}" cy="{me["y"]}" r="{5*k:.2f}" fill="{c}"/>')
    W = (max(abs(geo["x_min"]), abs(geo["x_max"])) + 22) if outlines else R + 20
    return (f'<svg viewBox="{-W:.0f} -{R+8:.0f} {2*W:.0f} {R+24:.0f}" role="img" aria-label="Ülőhely az ülésteremben">'
            + room_floor(geo) + PODIUM + "".join(parts) + '</svg>')


def mandate_text(mp: dict) -> str:
    if mp.get("mandate_kind") == "egyeni":
        return f'egyéni választókerület — {mp.get("county")} {mp.get("constituency_no")}. OEVK'
    if mp.get("mandate_kind") == "lista":
        return (mp.get("list_label") or "országos lista").lower() if mp.get("list_label") else "országos lista"
    return "—"


def seat_text(mp: dict) -> str:
    s = mp.get("seat") or {}
    if s.get("sector") is None:
        return "nincs ülőhely-adat"
    if s["sector"] == 0:
        return f'miniszteri pad, {s["seat"]}. szék'
    return f'{s["sector"]}. szektor, {s["row"]}. sor, {s["seat"]}. szék'


def mp_exports(inp: dict, azon: str) -> tuple[str, str]:
    mp = inp["mps"][azon]
    rec = inp["alignment"]["per_mp"].get(azon) or {"votes": [], "counts": {}, "with": 0, "against": 0, "cast": 0, "in_roll": 0}
    return ex.mp_export(mp, rec, inp["by_ts"], inp["cycle"])


def build_mp_page(inp: dict, azon: str) -> str:
    mp = inp["mps"][azon]
    # an MP has a dissent channel when a faction plurality exists to differ from: not for a person who ended the
    # cycle as an independent — unless they dissented from a faction earlier in the cycle
    has_channel = mp.get("faction") != fd.INDEPENDENT or any(d["azon"] == azon and d["faction"] != fd.INDEPENDENT for e in dissent_events(inp) for d in e["dissents"])
    al = inp["alignment"]["per_mp"].get(azon) or {"votes": [], "counts": {}, "with": 0, "against": 0, "cast": 0, "in_roll": 0}
    colour = {f["id"]: f["colour"] for f in inp["facs"]}
    c = colour.get(mp.get("faction") or "", "#8a8a8a")
    counts = al["counts"]
    in_roll = al["in_roll"] or 0
    cast = al["cast"]
    part = f"{100*cast/in_roll:.0f}%" if in_roll else "—"
    aligned_total = al["with"] + al["against"]
    with_pct = f"{100*al['with']/aligned_total:.0f}%" if aligned_total else "—"
    devs = [v for v in al["votes"] if v["align"] == "against"]

    def vote_row(v):
        vv = inp["by_ts"][v["ts"]]
        mo = vv["motions"][0] if vv["motions"] else {}
        subj = f'{esc(mo.get("iromany") or "")} {esc(cut(mo.get("title") or vv.get("remark") or ("Jelenlét megállapítása" if vv["kind"] == "jelenlet" else ""), 80))}'
        pos_html = f'<span class="pos">{_glyph(v["position"], c)}{esc(POSITION_LABEL.get(v["position"], v["position"]))}</span>'
        fp = POSITION_LABEL.get(v["faction_plurality"] or "", "—") if v["faction_plurality"] else "—"
        flag = {"with": '<span class="badge">frakcióval</span>', "against": '<span class="badge hi">frakció ellen</span>'}.get(v["align"] or "", '<span class="badge mid">nem szavazott</span>' if v["position"] not in CAST else "")
        return (f'<tr data-al="{esc(v["align"] or "none")}" data-p="{esc(v["position"])}" data-y="{esc(vv["on_date"][:4])}"><td class="ts mono"><a href="../szavazas/{esc(vv["slug"])}.html">{esc(vv["on_date"])} {esc(vv["time"])}</a></td>'
                f'<td>{subj}<span class="sub">{esc(rule_short((vv.get("majority") or {}).get("rule")))} · {esc(vv["result_raw"])}</span></td><td>{pos_html}</td><td>{esc(fp)}</td><td>{flag}</td></tr>')
    dev_rows = "".join(vote_row(v) for v in reversed(devs))
    all_rows = "".join(vote_row(v) for v in reversed(al["votes"]))
    mp_years = sorted({inp["by_ts"][v["ts"]]["on_date"][:4] for v in al["votes"]}, reverse=True)
    mp_year_buttons = ('<div class="filters" role="group" aria-label="Szűrés év szerint"><button type="button" data-yf="all" class="on" aria-pressed="true">minden év</button>'
                       + "".join(f'<button type="button" data-yf="{y}" aria-pressed="false">{y}</button>' for y in mp_years) + '</div>') if len(mp_years) > 1 else ""
    fac_hist = "".join(f'<tr><td class="mono">{esc(f["ciklus"])}</td><td>{esc(f["faction"])}</td><td class="mono">{esc(f["from"] or "")} – {esc(f["to"] or "")}</td></tr>' for f in mp.get("factions") or [])
    elections = "".join(f'<tr><td class="mono">{esc(e["ciklus"])}</td><td>{esc(e["constituency"] or "—")}</td><td class="mono">{esc(e["elected_on"] or "")}</td><td class="mono">{esc(e["mandate_from"] or "")} – {esc(e["mandate_to"] or "")}</td></tr>' for e in mp.get("elections") or [])
    motions = "".join(f'<tr><td class="mono">{esc(m["ciklus"])}</td><td class="num mono">{esc(m["onallo"])}</td><td class="num mono">{esc(m["nem_onallo"])}</td></tr>' for m in mp.get("motion_stats") or [])
    items = mp.get("motions") or []
    kinds = {}
    for it in items:
        kinds[it.get("kind") or "?"] = kinds.get(it.get("kind") or "?", 0) + 1
    kind_txt = " · ".join(f'{esc(k)}/ {n}' for k, n in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0])))
    stat_now = next((x["onallo"] for x in mp.get("motion_stats") or [] if x.get("ciklus") == "2026-"), None)
    def item_row(it: dict) -> str:
        num = f'<a href="{esc(it["href"])}" target="_blank" rel="noopener">{esc(it["szam"])}</a>' if it.get("href") else esc(it["szam"])
        co = f'<span class="sub">+{it["co_submitters"]} további benyújtó</span>' if it.get("co_submitters") else ""
        badge = "badge mid" if it.get("status") == "lezárt" else "badge"
        return f'<tr><td class="ts mono">{num}</td><td>{esc(it["title"] or "")}{co}</td><td><span class="{badge}">{esc(it.get("status") or "—")}</span></td></tr>'
    item_rows = "".join(item_row(it) for it in items)
    if inp["closed"]:
        motions_note = "A tételek csak a jelenlegi ciklusból érhetők el."
        items_html = ""
    else:
        motions_note = ("" if stat_now is None or stat_now == len(items) else
                        f'Eltérés: az adatlap {stat_now} önálló indítványt számol, a lista {len(items)} tételt talál.')
        items_html = (f'<div class="hero-meta" style="margin:10px 0 4px"><span class="lbl">a jelenlegi ciklusban</span> {len(items)} iromány' + (f' · {kind_txt}' if kind_txt else '') + '</div>'
                      f'<div class="tablewrap"><table data-page-size="25"><thead><tr><th scope="col">Szám</th><th scope="col">Cím</th><th scope="col">Állapot</th></tr></thead><tbody>'
                      + (item_rows or '<tr><td colspan="3">Ebben a ciklusban nincs benyújtott irománya.</td></tr>') + '</tbody></table></div>') if not inp["closed"] else ""
    rel_root = "../" * (1 + inp["base_depth"])
    other_links = " · ".join([f'<a href="{rel_root}{cycle_dir(c)}kepviselo/{esc(azon)}.html">{c}. ciklus ↗</a>' for c, members in inp["also_in"].items() if azon in members]
                             + [f'<a href="{rel_root}szemely/{esc(azon)}.html">pályakép ↗</a>'])
    if inp["closed"]:
        ended = mp.get("mandate_to") or mp.get("wikidata_end")            # the record's own row for the cycle first
        former_note = (f'<div class="hero-meta">A {inp["cycle"]}. ciklus lezárult · '
                       + ("ma is képviselő" if mp.get("current") else (f"mandátum vége: {hu_date(ended)}" if ended else "mandátuma megszűnt"))
                       + (f' · ugyanez a személy: {other_links}' if other_links else "") + '</div>')
    else:
        former_note = ("" if mp.get("current") else f'<div class="hero-meta">{("Mandátum vége: " + hu_date(mp["wikidata_end"])) if mp.get("wikidata_end") else "Mandátuma megszűnt."}</div>') \
            + (f'<div class="hero-meta">Ugyanez a személy: {other_links}</div>' if other_links else "")
    links = " · ".join(x for x in [
        f'<a href="{esc(mp["parlament_url"])}" target="_blank" rel="noopener">parlament.hu adatlap ↗</a>',
        f'<a href="{esc(mp["photo_url"])}" target="_blank" rel="noopener">fénykép (parlament.hu) ↗</a>' if mp.get("photo_url") else "",
        f'<a href="https://www.wikidata.org/wiki/{esc(mp["wikidata_qid"])}" target="_blank" rel="noopener">Wikidata {esc(mp["wikidata_qid"])} ↗</a>' if mp.get("wikidata_qid") else "",
        f'<a href="{esc(ext_url(mp["website"]))}" target="_blank" rel="noopener">{esc(re.sub(r"^https?://", "", mp["website"]))} ↗</a>' if mp.get("website") and "." in mp["website"] and ext_url(mp["website"]) else "",
    ] if x)
    shown = [p for p in POSITION_ORDER if p != "igazoltan_tavol" or counts.get(p)]     # the seventh state only when it occurs
    pos_cells = "".join(f'<div><b class="mono" data-kz-number="{counts.get(p, 0)}">{hu_num(counts.get(p, 0))}</b><span>{esc(POSITION_LABEL[p])}</span></div>' for p in shown)
    tally_cls = "tally seven" if len(shown) == 7 else "tally six"
    seat_panel = (f"""  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Ülőhely az ülésteremben</span><span class="tag">{esc(seat_text(mp))}</span></h2>
    <div class="chart">{chamber_mini_svg(inp, azon) or '<div class="hero-meta">Nincs ülőhely-adat.</div>'}</div>
    <div class="hero-meta">az emelvény felől nézve</div>
  </section>""" if not inp["closed"] else "")
    seat_label = f' · {esc(seat_text(mp))}' if not inp["closed"] else ""
    # -- the digest: sitting weeks, speeches, committees
    weeks = sitting_weeks(inp)
    dg = week_digests(inp)
    m_from, m_to = mp.get("mandate_from") or "", mp.get("mandate_to")
    def in_mandate(w):
        return not (w["to"] < m_from or (m_to and w["from"] > m_to))
    weeks = [w for w in weeks if in_mandate(w)]
    wrows = []
    for w in weeks:
        d = dg.get(w["key"]) or {}
        m = (d.get("mps") or {}).get(azon)
        rc = (d.get("cycle") or {}).get("roll_calls", 0)
        known = speeches_cover(inp, w)
        if m and not m["in_roll"] and mp.get("faction") == fd.INDEPENDENT:
            m = dict(m, independent=True)                       # no roll call that week: independence from the person's faction
        against = "—" if (m or {}).get("independent") else str((m or {}).get("against", 0))
        wrows.append(f'<tr><td class="ts mono">{esc(w["label"])}</td><td class="num mono">{w["ulnaps"]}</td><td class="num mono">{(m or {}).get("in_roll", 0)} / {rc}</td>'
                     f'<td class="num mono">{(m or {}).get("cast", 0)}</td><td class="num mono">{against}</td><td class="num mono">{len((m or {}).get("speeches") or []) if known else "—"}</td></tr>')
    last_w = weeks[0] if weeks else None
    last_m = ((dg.get(last_w["key"]) or {}).get("mps") or {}).get(azon) if last_w else None
    last_rc = ((dg.get(last_w["key"]) or {}).get("cycle") or {}).get("roll_calls", 0) if last_w else 0
    if last_m and not last_m["in_roll"] and mp.get("faction") == fd.INDEPENDENT:
        last_m = dict(last_m, independent=True)
    last_known = speeches_cover(inp, last_w) if last_w else False
    digest_now = (f'<div class="hero-meta"><b>{esc(last_w["label"])}</b> · {last_w["ulnaps"]} ülésnap · {esc(digest_line(last_m, last_rc, last_known))}</div>' if last_w else "")
    if last_m and last_m["speeches"]:
        digest_now += '<ul class="prose" style="margin:6px 0 0 18px;padding:0">' + "".join(
            f'<li>{esc(r["date"])} · {esc(cut(r["event"] or "—", 110))} — {esc(r["kind"] or "")}{" · " + hu_mmss(r["duration_s"]) if r.get("duration_s") else ""}</li>'
            for r in sorted(last_m["speeches"], key=lambda r: (r["date"], r["order"]))) + "</ul>"
    sp_rows = speeches_by_mp(inp).get(azon, [])
    sp_sub = [r for r in sp_rows if not r["technical"]]
    rec_stat = next((st for st in (mp.get("speech_stats") or []) if st.get("ciklus") == CYCLE_LABELS_.get(inp["cycle"])), None)
    sp_note = ""
    if inp["speeches"] is None:
        sp_note = "A ciklus felszólalás-listái nincsenek betöltve."
    elif rec_stat and rec_stat.get("speeches") is not None and rec_stat["speeches"] != len(sp_sub):
        sp_note = f'Az adatlap erre a ciklusra {hu_num(rec_stat["speeches"])} felszólalást és {hu_num(rec_stat["technical"] or 0)} eljárási sort számol; a napi listák szerint itt {hu_num(len(sp_sub))} érdemi és {hu_num(len(sp_rows) - len(sp_sub))} eljárási sor van.'
    sp_trs = "".join(f'<tr><td class="ts mono"><a href="../felszolalas/{speech_id(r)}.html">{esc(r["date"] or "")}</a></td><td>{esc(cut(r["event"] or "—", 120))}{(" <span class=" + chr(34) + "sub" + chr(34) + ">" + esc(r["iromany"]) + "</span>") if r.get("iromany") else ""}</td>'
                     f'<td>{esc(r["kind"] or "")}</td><td class="mono">{esc(r["role"] or "")}</td><td class="num mono">{hu_mmss(r["duration_s"]) if r.get("duration_s") else "—"}</td></tr>' for r in sp_sub)
    coms = committees_in_cycle(mp, inp["cycle"])
    com_rows = "".join(f'<tr><td>{esc(c["committee"] or "")}{(" <span class=" + chr(34) + "sub" + chr(34) + ">" + esc(c["subcommittee"]) + "</span>") if c.get("subcommittee") else ""}</td><td>{esc(c.get("role") or "")}</td>'
                       f'<td class="ts mono">{esc(c.get("from") or "")}{" – " + esc(c["to"]) if c.get("to") else " –"}</td></tr>' for c in coms)
    return page_head(f'{mp["name"]} ({mp.get("faction") or "—"}){" · " + str(inp["cycle"]) + ". ciklus" if inp["closed"] else ""} · karzat',
                     f'{mp["name"]} — {mp.get("faction") or ""}, {mandate_text(mp)}: szavazási részvétel és a frakcióval való egyezés a {inp["cycle"]}. ciklusban.', 1 + inp["base_depth"],
                     feeds=([(f"{azon}.xml", f'karzat · {mp["name"]} — különvéleményei · {inp["cycle"]}. ciklus')] if has_channel else []) + [(f"{azon}-heti.xml", f'karzat · {mp["name"]} — heti összefoglaló · {inp["cycle"]}. ciklus')]) + \
        topbar(inp, [("képviselők", "index.html"), (mp["name"], None)], 1) + f"""
<div class="hero-h"><h1>{esc(mp["name"])}</h1><small class="label"><span class="pos"><i class="d" style="--c:{c}"></i>{esc(mp.get("faction") or "—")}</span> · {esc(mandate_text(mp))}{seat_label}</small></div>
<p class="hero-meta">{links}</p>
{former_note}
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Hétről hétre</span><span class="tag">{"a legutóbbi ülésnapos hét" if not inp["closed"] else "a ciklus utolsó ülésnapos hete"} · <a href="{esc(azon)}-heti.xml" type="application/atom+xml" title="heti összefoglaló Atom-csatornaként">Atom</a> · <a href="../adatok/heti.csv">CSV, minden hét</a></span></h2>
  {digest_now or '<div class="hero-meta">—</div>'}
  <div class="tablewrap" style="margin-top:10px"><table data-page-size="25" data-counter="wn"><thead><tr><th scope="col">Hét</th><th scope="col" class="num">Ülésnap</th><th scope="col" class="num">Névsorban</th><th scope="col" class="num">Leadott</th><th scope="col" class="num">Frakciója ellen</th><th scope="col" class="num">Felszólalás</th></tr></thead><tbody>{"".join(wrows) or '<tr><td colspan="6">—</td></tr>'}</tbody></table></div>
  <div class="filters"><span class="n" id="wn" aria-live="polite"></span></div>
  <div class="hero-meta prose" style="margin-top:8px">Hét: naptári hét, csak az ülésnapjai számítanak; csak a mandátum idejére eső hetek. Névsorban: hány név szerinti szavazás névsorában szerepelt a hét összes ilyen szavazásából; leadott: igen, nem vagy tartózkodott; frakciója ellen: a frakciója többségétől eltérő leadott szavazat (döntetlennél nincs többség; függetlennek nincs frakciója: —); felszólalás: érdemi felszólalás a napi listák szerint (ülésvezetés, bejelentés nélkül; — ahol a lista nincs betöltve).</div>
</section>
<section class="{"grid" if not inp["closed"] else "grid one"}">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Szavazási részvétel a {inp["cycle"]}. ciklusban</span><span class="tag">{hu_num(in_roll)} név szerinti szavazás</span></h2>
    <div class="{tally_cls}">{pos_cells}</div>
    <dl class="verdict">
      <dt>Leadott szavazat</dt><dd class="mono">{cast} / {in_roll} ({part})</dd>
      <dt>Frakciójával</dt><dd class="mono">{al["with"]} · ellene {al["against"]} · egyezés {with_pct}</dd>
    </dl>
    <div class="hero-meta prose" style="margin-top:8px">„Frakciójával”: a frakciója leadott szavazatainak többségével egyezően.</div>
  </section>
{seat_panel}
</section>
<section class="grid">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Frakciótagság és mandátum</span></h2>
    <div class="tablewrap" style="border:0"><table><thead><tr><th>Ciklus</th><th>Frakció</th><th>Időszak</th></tr></thead><tbody>{fac_hist or '<tr><td colspan="3">—</td></tr>'}</tbody></table></div>
    <div class="tablewrap" style="border:0;margin-top:10px"><table><thead><tr><th>Ciklus</th><th>Választókerület / lista</th><th>Választás</th><th>Mandátum</th></tr></thead><tbody>{elections or '<tr><td colspan="4">—</td></tr>'}</tbody></table></div>
  </section>
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Benyújtott indítványok</span></h2>
    <div class="tablewrap" style="border:0"><table><thead><tr><th>Ciklus</th><th class="num">önálló</th><th class="num">nem önálló</th></tr></thead><tbody>{motions or '<tr><td colspan="3">—</td></tr>'}</tbody></table></div>
    {items_html}
    {f'<div class="hero-meta prose" style="margin-top:8px">{motions_note}</div>' if motions_note else ''}
  </section>
</section>
<section class="grid">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Bizottságok</span><span class="tag">{len(coms)} tagság a ciklusban</span></h2>
    <div class="tablewrap" style="border:0"><table><thead><tr><th scope="col">Bizottság</th><th scope="col">Tisztség</th><th scope="col">Időszak</th></tr></thead><tbody>{com_rows or '<tr><td colspan="3">—</td></tr>'}</tbody></table></div>
  </section>
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Felszólalásai</span><span class="tag">{hu_num(len(sp_sub))} érdemi · {hu_num(len(sp_rows) - len(sp_sub))} eljárási sor</span></h2>
    <div class="hero-meta prose">Érdemi: a tárgyhoz szólás, kérdés, interpelláció és válaszaik, napirend előtti és utáni, ügyrendi; eljárási: ülésvezetés, bejelentés, eredmény kihirdetése.{(" " + esc(sp_note)) if sp_note else ""}</div>
  </section>
</section>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Érdemi felszólalásai</span><span class="tag">{hu_num(len(sp_sub))} tétel · a legfrissebb elöl · <a href="../felszolalas/index.html">a ciklus felszólalásai</a></span></h2>
  <div class="filters"><input type="search" data-filter-table="speeches" placeholder="tárgy, fajta" aria-label="Szűrés tárgyra vagy fajtára" style="min-width:160px"><span class="n" id="sn" aria-live="polite"></span></div>
  <div class="tablewrap"><table id="speeches" data-page-size="25" data-counter="sn"><thead><tr><th scope="col">Nap</th><th scope="col">Napirendi pont</th><th scope="col">Fajta</th><th scope="col">Szerep</th><th scope="col" class="num">Hossz</th></tr></thead><tbody>{sp_trs or ('<tr><td colspan="5">A felszólalás-listák nincsenek betöltve.</td></tr>' if inp["speeches"] is None else '<tr><td colspan="5">Ebben a ciklusban nem volt.</td></tr>')}</tbody></table></div>
</section>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Szavazatok a frakció többségével szemben</span><span class="tag">{len(devs)}{f' · <a href="{esc(azon)}.xml" type="application/atom+xml" title="a képviselő különvéleményei Atom-csatornaként">Atom</a>' if has_channel else " · függetlenként nincs frakciótöbbség — csatorna nincs"}</span></h2>
  <div class="tablewrap"><table data-page-size="25"><thead><tr><th>Időpont</th><th>Tárgy</th><th>Szavazata</th><th>Frakció többsége</th><th></th></tr></thead><tbody>{dev_rows or '<tr><td colspan="5">Nincs ilyen szavazás.</td></tr>'}</tbody></table></div>
</section>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Minden szavazása</span><span class="tag">{hu_num(in_roll)} tétel · a legfrissebb elöl · <a href="{esc(azon)}.csv">CSV</a> · <a href="{esc(azon)}.json">JSON</a></span></h2>
  {mp_year_buttons}
  <div class="filters" role="group" aria-label="Szűrés egyezés szerint"><button type="button" data-alf="all" class="on" aria-pressed="true">mind</button><button type="button" data-alf="with" aria-pressed="false">frakcióval</button><button type="button" data-alf="against" aria-pressed="false">frakció ellen</button><button type="button" data-alf="none" aria-pressed="false">nem adott le szavazatot</button><input type="search" data-filter-table="mine" placeholder="tárgy, szám" aria-label="Szűrés tárgyra" style="min-width:160px"><span class="n" id="rn" aria-live="polite"></span></div>
  <div class="tablewrap"><table id="mine" data-page-size="25" data-counter="rn"><thead><tr><th>Időpont</th><th>Tárgy</th><th>Szavazata</th><th>Frakció többsége</th><th></th></tr></thead><tbody>{all_rows}</tbody></table></div>
</section>
{cite_html(inp, f'{cycle_dir(inp["cycle"])}kepviselo/{azon}.html', f'{mp["name"]} ({mp.get("faction") or "—"}), {inp["cycle"]}. ciklus', f'{inp["cycle"]}-{azon}', f'{azon}.json', f'{azon}.csv')}
""" + page_tail(inp, 1)


def build_mp_index(inp: dict) -> str:
    facs = inp["facs"]
    colour = {f["id"]: f["colour"] for f in facs}
    order = {f["id"]: f["sort_order"] for f in facs}
    mps = sorted(inp["mps"].values(), key=lambda m: (not m["current"], order.get(m.get("faction") or "", 999), name_key(m["name"])))
    trs = []
    for mp in mps:
        al = inp["alignment"]["per_mp"].get(mp["p_azon"]) or {"with": 0, "against": 0, "cast": 0, "in_roll": 0}
        c = colour.get(mp.get("faction") or "", "#8a8a8a")
        part = f'{100*al["cast"]/al["in_roll"]:.0f}%' if al["in_roll"] else "—"
        former_badge = ((' <span class="badge mid">ma is képviselő</span>' if mp["current"] else "") if inp["closed"]
                        else ("" if mp["current"] else ' <span class="badge mid">volt képviselő</span>'))
        part_key = f'{al["cast"]/al["in_roll"]:.4f}' if al["in_roll"] else "-1"
        trs.append(f'<tr data-f="{esc(mp.get("faction") or "")}" data-name="{esc(name_key(mp["name"]))}" data-part="{part_key}" data-cast="{al["cast"]}" data-against="{al["against"]}" data-cur="{1 if mp["current"] else 0}">'
                   f'<td><a href="{esc(mp["p_azon"])}.html">{esc(mp["name"])}</a>{former_badge}</td>'
                   f'<td><span class="pos"><i class="d" style="--c:{c}"></i>{esc(mp.get("faction") or "—")}</span></td>'
                   f'<td>{esc(mandate_text(mp))}</td>' + ('' if inp["closed"] else f'<td class="mono">{esc(seat_text(mp))}</td>') +
                   f'<td class="num mono">{al["cast"]} / {al["in_roll"]}</td><td class="num mono">{part}</td><td class="num mono">{al["against"]}</td></tr>')
    fac_buttons = '<button type="button" data-fac="all" class="on" aria-pressed="true">minden frakció</button>' + "".join(f'<button type="button" data-fac="{esc(f["id"])}" aria-pressed="false">{esc(f["id"])}</button>' for f in facs if any(m.get("faction") == f["id"] for m in mps))
    n_cur = sum(1 for m in mps if m["current"]); n_former = len(mps) - n_cur
    return page_head(f'Képviselők{" · " + str(inp["cycle"]) + ". ciklus" if inp["closed"] else ""} · karzat', f"Az Országgyűlés képviselői a {inp['cycle']}. ciklusban: mandátum, {'ülőhely, ' if not inp['closed'] else ''}szavazási részvétel és a frakcióval való egyezés.", 1 + inp["base_depth"]) + \
        topbar(inp, [("képviselők", None)], 1) + f"""
<div class="hero-h"><h1>Képviselők</h1><small class="label" data-kz-text>{(f"{n_cur} jelenlegi és {n_former} volt képviselő a névsorokból") if not inp["closed"] else (f"{len(mps)} képviselő a {inp['cycle']}. ciklus névsoraiból, közülük {n_cur} ma is képviselő")}</small></div>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Névsor</span><span class="tag">a fejlécre kattintva rendezhető · <a href="../adatok/kepviselok.csv">CSV</a> · <a href="../adatok/kepviselok.json">JSON</a></span></h2>
  <div class="filters" role="group" aria-label="Szűrés frakció szerint">{fac_buttons}<input type="search" data-filter-table="roll" placeholder="név" aria-label="Szűrés névre" style="min-width:140px"><span class="n" id="rn" aria-live="polite"></span></div>
  <div class="tablewrap"><table id="roll" data-page-size="25" data-counter="rn"><thead><tr><th scope="col" class="sortable" data-key="name">Képviselő</th><th scope="col" class="sortable" data-key="f">Frakció</th><th scope="col">Mandátum</th>{'' if inp["closed"] else '<th scope="col">Ülőhely</th>'}<th scope="col" class="sortable num" data-key="cast">Leadott / névsorban</th><th scope="col" class="sortable num" data-key="part">Részvétel</th><th scope="col" class="sortable num" data-key="against">Frakció ellen</th></tr></thead><tbody>{"".join(trs)}</tbody></table></div>
</section>
""" + page_tail(inp, 1)


# -- main -------------------------------------------------------------------------------------

HERO_TS = "2026.06.15.17:20:04"                                     # cycle 43: the 16th Alaptörvény amendment
HERO_BY_CYCLE = {43: HERO_TS, 42: "2025.04.14.17:20:48"}                # cycle 42: the 15th, final vote 140–21–0


def pick_hero(inp: dict) -> str:
    """The hero vote of a cycle without a hand-picked one: the cycle's last decision passed with two thirds of
    all MPs (a constitutional-majority moment); failing that, the last roll call; failing that, the last vote."""
    if inp["cycle"] in HERO_BY_CYCLE and HERO_BY_CYCLE[inp["cycle"]] in inp["by_ts"]:
        return HERO_BY_CYCLE[inp["cycle"]]
    for ts in reversed(inp["order"]):
        v = inp["by_ts"][ts]
        m = v.get("majority") or {}
        if m.get("rule") == "ketharmad_osszes" and v.get("passed") and inp["store"]["positions"].get(ts):
            return ts
    for ts in reversed(inp["order"]):
        if inp["store"]["positions"].get(ts):
            return ts
    return inp["order"][-1]


def build() -> str:
    """The index page (the name tests and --check use)."""
    return build_index(load_inputs(), HERO_TS)


def build_assets() -> dict[str, str]:
    """The shared stylesheet and script, generated like the pages (committed, checked, never hand-edited)."""
    return {"karzat.css": CSS.strip() + "\n", "karzat.js": (JS_PAGER + "\n" + JS_TEXTFILTER + "\n" + JS_SPEECHSEARCH + "\n" + JS_INDEX + "\n" + JS_INSPECT + "\n" + JS_VOTE + "\n" + JS_MP + "\n" + JS_CITE + "\n" + JS_SEARCH + "\n" + JS_BOOT).strip() + "\n"}


def _pct(x, digits=0) -> str:
    return "—" if x is None else f"{100 * x:.{digits}f}%".replace(".", ",")


def _f2(x) -> str:
    return hu_dec(x, 2)


def build_cohesion_page(inp: dict, co: dict) -> str:
    """kohezio/index.html — per faction Rice and AI over the cycle, the faction × faction agreement matrix, and per
    faction the least-agreeing MP pairs; formulas named on the page; CSVs beside it."""
    pf = co["per_faction"]
    facs = sorted(pf, key=lambda f: -pf[f]["cast"])
    colour = {f["id"]: f["colour"] for f in inp["facs"]}
    rows = "".join(
        f'<tr><td><span class="pos"><i class="d" style="--c:{colour.get(f, "#8a8a8a")}"></i>{esc(f)}</span></td>'
        f'<td class="num mono">{hu_num(v["votes"])}</td><td class="num mono">{hu_num(v["cast"])}</td><td class="num mono">{_f2(v["ai"])}</td><td class="num mono">{_f2(v["rice"])}</td>'
        f'<td class="num mono">{hu_num(v["unanimous_votes"])}<span class="sub">{_pct(v["unanimous_votes"] / v["votes"] if v["votes"] else None)}</span></td>'
        f'<td class="num mono">{hu_num(v["votes_with_dissent"])}</td><td class="num mono">{hu_num(v["dissenting_votes"])}</td></tr>'
        for f, v in ((f, co["per_faction"][f]) for f in facs))
    # matrix
    head = "".join(f'<th scope="col" class="num">{esc(b)}</th>' for b in facs)
    mrows = []
    for a in facs:
        cells = []
        for b in facs:
            if a == b:
                cells.append('<td class="num mono" style="color:var(--dim3)">·</td>')
            else:
                x = (co["faction_pairs"].get(a, {}).get(b) or co["faction_pairs"].get(b, {}).get(a))
                sh = x["share"] if x else None
                bg = f'background:rgba(255,255,255,{0.03 + 0.25 * sh:.2f})' if sh is not None else ""
                cells.append(f'<td class="num mono" style="{bg}" title="{x["same"] if x else 0} / {x["votes"] if x else 0} szavazás">{_pct(sh)}</td>')
        mrows.append(f'<tr><th scope="row">{esc(a)}</th>{"".join(cells)}</tr>')
    # least agreeing pairs per faction (top 8), plus most against
    pair_blocks = []
    names = {a: m["name"] for a, m in inp["mps"].items()}
    for f in facs:
        pairs = sorted(co["mp_pairs"].get(f) or [], key=lambda r: r["share"])[:8]
        if not pairs:
            continue
        prs = "".join(f'<tr><td><a href="../kepviselo/{esc(r["a"])}.html">{esc(names.get(r["a"], r["a"]))}</a> – <a href="../kepviselo/{esc(r["b"])}.html">{esc(names.get(r["b"], r["b"]))}</a></td>'
                      f'<td class="num mono">{r["same"]} / {r["shared"]}</td><td class="num mono">{_pct(r["share"])}</td></tr>' for r in pairs)
        pair_blocks.append(f'<h3 class="mono" style="font-size:10px;letter-spacing:.2em;color:var(--dim2);margin:12px 0 4px">{esc(f)} — a legkevésbé egyező párok</h3>'
                           f'<div class="tablewrap" style="border:0"><table><thead><tr><th scope="col">Pár</th><th scope="col" class="num">Egyező / közös</th><th scope="col" class="num">Egyezés</th></tr></thead><tbody>{prs}</tbody></table></div>')
    return page_head(f'Kohézió · {inp["cycle"]}. ciklus · karzat', f'Frakciók összetartása a {inp["cycle"]}. ciklusban: Rice-index, egyetértési index, frakciók közti egyezés, képviselőpárok.', 1 + inp["base_depth"]) + \
        topbar(inp, [("kohézió", None)], 1) + f"""
<div class="hero-h"><h1>Kohézió</h1><small class="label" data-kz-text>{inp["cycle"]}. ciklus · frakciók összetartása a név szerinti szavazásokon</small></div>
<p class="lede">Két szokásos mérőszám, mindkettő csak számolás: a Rice-index az igen–nem egyöntetűség (|igen − nem| / (igen + nem)), az egyetértési index a három leadott opcióé ((legnagyobb − (összes − legnagyobb)/2) / összes; Hix–Noury–Roland). 1 = mindenki ugyanúgy szavazott.</p>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Frakciónként</span><span class="tag">leadott szavazatokkal súlyozott átlagok · <a href="frakciok.csv">CSV</a> · szavazásonként: <a href="szavazasonkent.csv">CSV</a> · különvélemények csatornaként: <a href="../feed/index.html">értesítések</a></span></h2>
  <div class="tablewrap"><table><thead><tr><th scope="col">Frakció</th><th scope="col" class="num">Szavazás</th><th scope="col" class="num">Leadott</th><th scope="col" class="num">Egyetértési index</th><th scope="col" class="num">Rice</th><th scope="col" class="num">Egyhangú</th><th scope="col" class="num">Nem egyhangú</th><th scope="col" class="num">Kisebbségi szavazatok</th></tr></thead><tbody>{rows}</tbody></table></div>
  <div class="hero-meta prose" style="margin-top:8px">„Egyhangú”: minden leadott szavazat azonos; „kisebbségi szavazatok”: a frakció többségétől eltérő leadott szavazatok (döntetlennél nincs többség, így ellene sincs — a képviselőoldalak „frakció ellen” összege). Jelenlét-megállapítás nem számít.{" A „független” sor nem frakció — egymástól független képviselők összesítése, a száma nem összetartás." if "független" in pf else ""}</div>
</section>
<section class="panel">{CORNERS}
  <h2><span data-kz-text>Frakciók egymással</span><span class="tag">a többségi álláspontok egyezése · <a href="frakcio_parok.csv">CSV</a></span></h2>
  <div class="tablewrap" style="border:0"><table><thead><tr><th scope="col"></th>{head}</tr></thead><tbody>{"".join(mrows)}</tbody></table></div>
  <div class="hero-meta prose" style="margin-top:8px">Azoknak a szavazásoknak a hányada, ahol a két frakció leadott szavazatainak többsége ugyanaz volt — csak ott számolva, ahol mindkettő adott le szavazatot.</div>
</section>
<section class="panel">{CORNERS}
  <h2><span data-kz-text>Képviselőpárok</span><span class="tag">frakción belül · legalább 20 közös szavazás · <a href="kepviselo_parok.csv">CSV</a></span></h2>
  {"".join(pair_blocks)}
</section>
{cite_html(inp, f'{cycle_dir(inp["cycle"])}kohezio/index.html', f'Kohézió — {inp["cycle"]}. ciklus', f'{inp["cycle"]}-kohezio')}
""" + page_tail(inp, 1)


def build_close_page(inp: dict, cl: dict) -> str:
    """szoros/index.html — the closest decisions, qualified thresholds that sank a yes-majority, and (a labelled
    counterfactual) outcomes that would flip if every non-caster had voted with their faction's plurality."""
    def row(r, extra=""):
        m = ("+" if r["margin"] >= 0 else "") + str(r["margin"])
        subj = f'{esc(r["iromany"] or "")} {esc(cut(r["title"] or "", 90))}'
        badge = '<span class="badge ok">Elfogadva</span>' if r["passed"] else '<span class="badge no">Elutasítva</span>'
        return (f'<tr><td class="ts mono"><a href="../szavazas/{esc(r["slug"])}.html">{esc(r["date"])} {esc(r["time"] or "")}</a></td><td>{subj}</td>'
                f'<td class="mono">{esc(rule_short(r["rule"]))}<span class="sub">{r["needed"]} / {r["base"]}</span></td>'
                f'<td class="num mono">{r["igen"]} – {r["nem"]} – {r["tartozkodott"]}</td><td class="num mono">{m}</td><td>{badge}</td>{extra}</tr>')
    closest = "".join(row(r) for r in cl["closest"][:100])
    sunk = "".join(row(r) for r in cl["sunk_by_threshold"])
    flips = "".join(row(r, f'<td class="num mono">{r["hypo_igen"]}</td>') for r in cl["absence_decisive"])
    n = len(cl["decisions"])
    return page_head(f'Szoros szavazások · {inp["cycle"]}. ciklus · karzat', f'A {inp["cycle"]}. ciklus legszorosabb döntései, a minősített küszöbön elbukott igen-többségek, és a hiányzókon múló kimenetelek.', 1 + inp["base_depth"]) + \
        topbar(inp, [("szoros", None)], 1) + f"""
<div class="hero-h"><h1>Szoros szavazások</h1><small class="label" data-kz-text>{inp["cycle"]}. ciklus · {hu_num(n)} döntés a küszöbéhez mérve</small></div>
<p class="lede">Minden döntésnél az igenek és a szükséges többség különbsége (a „különbség” oszlop): mennyivel ment át, vagy mennyi hiányzott.</p>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>A legszorosabbak</span><span class="tag">a 100 legkisebb különbség · <a href="dontesek.csv">CSV, mind a {hu_num(n)}</a></span></h2>
  <div class="tablewrap"><table data-page-size="25"><thead><tr><th scope="col">Időpont</th><th scope="col">Tárgy</th><th scope="col">Szükséges</th><th scope="col" class="num">igen – nem – tart.</th><th scope="col" class="num">Különbség</th><th scope="col">Eredmény</th></tr></thead><tbody>{closest or '<tr><td colspan="6">—</td></tr>'}</tbody></table></div>
</section>
<section class="panel">{CORNERS}
  <h2><span data-kz-text>Több igen, mint nem — mégis elutasítva</span><span class="tag">{len(cl["sunk_by_threshold"])} döntés</span></h2>
  <div class="tablewrap"><table data-page-size="25"><thead><tr><th scope="col">Időpont</th><th scope="col">Tárgy</th><th scope="col">Szükséges</th><th scope="col" class="num">igen – nem – tart.</th><th scope="col" class="num">Különbség</th><th scope="col">Eredmény</th></tr></thead><tbody>{sunk or '<tr><td colspan="6">Ebben a ciklusban nem volt ilyen.</td></tr>'}</tbody></table></div>
  <div class="hero-meta prose" style="margin-top:8px">Minősített többséget kívánó döntések, ahol az igenek meghaladták a nemeket, de a küszöböt nem érték el.</div>
</section>
<section class="panel">{CORNERS}
  <h2><span data-kz-text>Ahol a hiányzók döntöttek</span><span class="tag">{len(cl["absence_decisive"])} döntés · feltevés, nem tény</span></h2>
  <div class="tablewrap"><table data-page-size="25"><thead><tr><th scope="col">Időpont</th><th scope="col">Tárgy</th><th scope="col">Szükséges</th><th scope="col" class="num">igen – nem – tart.</th><th scope="col" class="num">Különbség</th><th scope="col">Eredmény</th><th scope="col" class="num">Igen, ha mindenki szavaz</th></tr></thead><tbody>{flips or '<tr><td colspan="7">Ebben a ciklusban nem volt ilyen.</td></tr>'}</tbody></table></div>
  <div class="hero-meta prose" style="margin-top:8px">Számított feltevés: ha a névsorban szereplő, de szavazatot le nem adó képviselők mind a frakciójuk többségével szavaztak volna, más lett volna-e a kimenetel. Jelenléthez kötött küszöbnél a jelenlét is nő velük.</div>
</section>
{cite_html(inp, f'{cycle_dir(inp["cycle"])}szoros/index.html', f'Szoros szavazások — {inp["cycle"]}. ciklus', f'{inp["cycle"]}-szoros')}
""" + page_tail(inp, 1)


def build_bill_page(inp: dict, b: dict) -> str:
    """iromany/<n>.html — one bill's roll calls in order: the bill's own stages and its amendments, each with rule,
    counts and result; the parlament.hu record link when the list carries it (current cycle only)."""
    rows = []
    for v in b["votes"]:
        badge = result_badge({"result_raw": v["result"], "kind": v["kind"]})
        rows.append(f'<tr{" class=own" if v.get("own") else ""}><td class="ts mono"><a href="../szavazas/{esc(v["slug"])}.html">{esc(v["date"])} {esc(v["time"] or "")}</a></td>'
                    f'<td class="mono">{esc(v["iromany"] or "")}</td><td>{esc(v["outcome"] or "")}</td>'
                    f'<td class="mono">{esc(rule_short(v["rule"]))}</td><td class="num mono">{v["igen"]} – {v["nem"]} – {v["tartozkodott"]}</td><td>{badge}</td></tr>')
    label = b["label"]
    title = b["title"] or "—"
    ext = f' · <a href="{esc(b["href"])}" target="_blank" rel="noopener">parlament.hu ↗</a>' if b.get("href") else ""
    span = f'{hu_date(b["first"])} – {hu_date(b["last"])}' if b["first"] != b["last"] else hu_date(b["first"])
    return page_head(f'{label} — {cut(title, 80)}{" · " + str(inp["cycle"]) + ". ciklus" if inp["closed"] else ""} · karzat', f'{label} {title}: a {inp["cycle"]}. ciklusban tartott név szerinti szavazásai sorban, az indítvány szakaszaival és módosítóival.', 1 + inp["base_depth"]) + \
        topbar(inp, [("irományok", "index.html"), (label, None)], 1) + f"""
<div class="hero-h"><h1>{esc(label)}</h1><small class="label" data-kz-text>{len(b["votes"])} szavazás · {esc(span)}</small></div>
<div class="hero-title">{esc(title)}</div>
<p class="hero-meta">{"a javaslatról magáról: " + str(b["own_votes"]) if b["own_votes"] else "csak módosítókról szavaztak név szerint"}{" · módosítókról: " + str(b["amendment_votes"]) if b["amendment_votes"] else ""}{ext}</p>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Az iromány útja a szavazásokon</span><span class="tag">utolsó: {esc(b["final_outcome"] or "")}</span></h2>
  <div class="tablewrap"><table data-page-size="50"><thead><tr><th scope="col">Időpont</th><th scope="col">Szám</th><th scope="col">Szakasz</th><th scope="col">Szabály</th><th scope="col" class="num">igen – nem – tart.</th><th scope="col">Eredmény</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>
  <div class="hero-meta prose" style="margin-top:8px">A „szakasz” az API kimenetel-szövege minden szavazásnál; a szám az iromány saját száma vagy egy módosítóé (szám/pont).</div>
</section>
{cite_html(inp, f'{cycle_dir(inp["cycle"])}iromany/{b["number"]}.html', f'{label} — {cut(title, 80)}', f'{inp["cycle"]}-iromany-{b["number"]}')}
""" + page_tail(inp, 1)


def build_bill_index(inp: dict, bs: dict) -> str:
    trs = []
    for n, b in sorted(bs.items(), key=lambda kv: -kv[0]):
        badge = result_badge({"result_raw": b["final_result"], "kind": b["votes"][-1]["kind"]})
        trs.append(f'<tr data-p="{esc(b["prefix"] or "")}" data-num="{n}" data-nv="{len(b["votes"])}"><td class="mono"><a href="{n}.html">{esc(b["label"])}</a></td><td>{esc(cut(b["title"] or "—", 110))}</td>'
                   f'<td class="num mono">{len(b["votes"])}</td><td class="ts mono">{esc(b["first"])}{(" – " + esc(b["last"])) if b["last"] != b["first"] else ""}</td>'
                   f'<td>{esc(cut(b["final_outcome"] or "", 60))}</td><td>{badge}</td></tr>')
    prefixes = sorted({b["prefix"] for b in bs.values() if b["prefix"]})
    pbuttons = '<button type="button" data-posf="all" class="on" aria-pressed="true">mind</button>' + "".join(f'<button type="button" data-posf="{p}" aria-pressed="false">{p}/</button>' for p in prefixes)
    return page_head(f'Irományok · {inp["cycle"]}. ciklus · karzat', f'A {inp["cycle"]}. ciklus irományai, amelyekről név szerint szavaztak: szavazásaik száma, első és utolsó szavazás, utolsó kimenetel.', 1 + inp["base_depth"]) + \
        topbar(inp, [("irományok", None)], 1) + f"""
<div class="hero-h"><h1>Irományok</h1><small class="label" data-kz-text>{inp["cycle"]}. ciklus · {hu_num(len(bs))} iromány, amelyről név szerint szavaztak</small></div>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Irományok</span><span class="tag">a legfrissebb szám elöl · <a href="iromanyok.csv">CSV</a></span></h2>
  <div class="filters" role="group" aria-label="Szűrés előtag szerint">{pbuttons}<input type="search" data-filter-table="roll" placeholder="szám, cím" aria-label="Szűrés számra vagy címre" style="min-width:160px"><span class="n" id="rn" aria-live="polite"></span></div>
  <div class="tablewrap"><table id="roll" data-page-size="25" data-counter="rn"><thead><tr><th scope="col" class="sortable" data-key="num">Szám</th><th scope="col">Cím</th><th scope="col" class="sortable num" data-key="nv">Szavazás</th><th scope="col">Időszak</th><th scope="col">Utolsó szakasz</th><th scope="col">Eredmény</th></tr></thead><tbody>{"".join(trs)}</tbody></table></div>
  <div class="hero-meta prose" style="margin-top:8px">Az előtag (T/ törvényjavaslat, H/ határozati javaslat, I/ interpelláció, K/ kérdés…) csak ott ismert, ahol a névsor az iromány saját számát írta; a csak módosítókról ismert irományok szám nélküli előtaggal szerepelnek.</div>
</section>
""" + page_tail(inp, 1)


def svg_series(labels: list[str], series: list[tuple[str, str, list]], kind: str = "bars", ymax: float | None = None, fmt=lambda v: f"{v}", w: int = 640, h: int = 140, name: str = "") -> str:
    """A small chart: bars (first series) or lines (all series), mono axis labels, hover titles per point. Values may be None."""
    n = max(1, len(labels))
    pad_l, pad_b, pad_t = 34, 22, 8
    iw, ih = w - pad_l - 6, h - pad_b - pad_t
    vals = [v for _, _, vs in series for v in vs if v is not None]
    top = ymax if ymax is not None else (max(vals) if vals else 1) or 1
    def x(i): return pad_l + iw * (i + 0.5) / n
    def y(v): return pad_t + ih * (1 - (v / top if top else 0))
    parts = [f'<line x1="{pad_l}" y1="{pad_t + ih:.1f}" x2="{w - 6}" y2="{pad_t + ih:.1f}" class="axis"/>',
             f'<text x="{pad_l - 6}" y="{pad_t + 4}" class="axl" text-anchor="end">{esc(fmt(top))}</text><text x="{pad_l - 6}" y="{pad_t + ih:.1f}" class="axl" text-anchor="end">0</text>']
    step = max(1, n // 8)
    for i, lab in enumerate(labels):
        if i % step == 0 or i == n - 1:
            parts.append(f'<text x="{x(i):.1f}" y="{h - 6}" class="axl" text-anchor="middle">{esc(lab)}</text>')
    if kind == "bars":
        name, colour, vs = series[0]
        bw = max(2.0, iw / n * 0.6)
        for i, v in enumerate(vs):
            if v is None: continue
            parts.append(f'<rect x="{x(i) - bw / 2:.1f}" y="{y(v):.1f}" width="{bw:.1f}" height="{pad_t + ih - y(v):.1f}" fill="{colour}"><title>{esc(labels[i])}: {esc(fmt(v))}</title></rect>')
    else:
        for name, colour, vs in series:
            pts = [(x(i), y(v)) for i, v in enumerate(vs) if v is not None]
            if len(pts) > 1:
                parts.append(f'<polyline points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in pts)}" fill="none" stroke="{colour}" stroke-width="1.4"/>')
            for i, v in enumerate(vs):
                if v is None: continue
                parts.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="2.2" fill="{colour}"><title>{esc(name)} · {esc(labels[i])}: {esc(fmt(v))}</title></circle>')
    # the accessible name says what the picture is and where the same numbers are in text (the table below)
    label = f'{name}: {", ".join(n for n, _, _ in series)} — {labels[0]} … {labels[-1]}, {n} hónap; a számok a táblázatban' if labels else name
    return f'<div class="tswrap"><svg viewBox="0 0 {w} {h}" class="ts" role="img" aria-label="{esc(label)}">{"".join(parts)}</svg></div>'


def build_numbers_page(inp: dict, ms: list[dict]) -> str:
    """szamok/index.html — the cycle month by month: votes and the qualified share, participation, dissent and cohesion per faction."""
    labels = [d["month"][2:] for d in ms]                     # 26-05
    colour = {f["id"]: f["colour"] for f in inp["facs"]}
    facs = [f["id"] for f in inp["facs"] if any(f["id"] in d["factions"] for d in ms)]
    pct = lambda v: f"{100 * v:.0f}%"
    ser = lambda key: [(f, colour.get(f, "#8a8a8a"), [((d["factions"].get(f) or {}).get(key)) for d in ms]) for f in facs]
    charts = [
        ("Szavazások havonta", svg_series(labels, [("szavazás", "#a1a1aa", [d["votes"] for d in ms])], "bars"), "minden szavazás, jelenlét-megállapítással együtt"),
        ("Minősített többségű döntések aránya", svg_series(labels, [("minősített", "#a1a1aa", [(d["qualified"] / d["decisions"]) if d["decisions"] else None for d in ms])], "lines", 1.0, pct), "a hónap döntéseiből mennyi kívánt kétharmadot, négyötödöt vagy abszolút többséget"),
        ("Részvétel frakciónként", svg_series(labels, ser("participation"), "lines", 1.0, pct), "leadott szavazat / névsorban szereplés, a frakció tagjaira összesítve"),
        ("Frakció elleni szavazatok aránya", svg_series(labels, ser("dissent"), "lines", None, lambda v: f"{100 * v:.1f}%".replace(".", ",")), "a frakció többségétől eltérő leadott szavazatok aránya"),
        ("Egyetértési index frakciónként", svg_series(labels, ser("ai"), "lines", 1.0, lambda v: hu_dec(v)), "leadott szavazatokkal súlyozott havi átlag"),
    ]
    def named(t, svg):
        return svg.replace('aria-label=":', 'aria-label="' + esc(t) + ':', 1)
    blocks = "".join(f'<section class="panel">{CORNERS}<h2><span data-kz-text>{esc(t)}</span></h2><div class="chart">{named(t, svg)}</div><div class="hero-meta" style="margin-top:6px">{esc(note)}</div></section>' for t, svg, note in charts)
    legend = "".join(f'<span class="f"><i style="background:{colour.get(f, "#8a8a8a")}"></i>{esc(f)}</span>' for f in facs)
    head = "".join(f'<th scope="col" class="num">{esc(f)}<span class="sub">részvétel · ellene</span></th>' for f in facs)
    def cell(x):
        part = pct(x["participation"]) if x and x["participation"] is not None else "—"
        dis = f'{100 * x["dissent"]:.1f}%'.replace(".", ",") if x and x["dissent"] is not None else "—"
        return f'<td class="num mono">{part}<span class="sub">{dis}</span></td>'
    trs = []
    for d in ms:
        cells = "".join(cell(d["factions"].get(f)) for f in facs)
        trs.append(f'<tr><td class="mono">{esc(d["month"])}</td><td class="num mono">{d["votes"]}</td><td class="num mono">{d["decisions"]}</td><td class="num mono">{d["qualified"]}</td>{cells}</tr>')
    return page_head(f'Számok · {inp["cycle"]}. ciklus · karzat', f'A {inp["cycle"]}. ciklus hónapról hónapra: szavazások, minősített többség, részvétel, frakció elleni szavazatok, egyetértési index.', 1 + inp["base_depth"]) + \
        topbar(inp, [("számok", None)], 1) + f"""
<div class="hero-h"><h1>Számok</h1><small class="label" data-kz-text>{inp["cycle"]}. ciklus · hónapról hónapra</small></div>
<div class="legend">{legend}</div>
{blocks}
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>A táblázat</span><span class="tag"><a href="havonta.csv">CSV</a></span></h2>
  <div class="tablewrap"><table data-page-size="50"><thead><tr><th scope="col">Hónap</th><th scope="col" class="num">Szavazás</th><th scope="col" class="num">Döntés</th><th scope="col" class="num">Minősített</th>{head}</tr></thead><tbody>{"".join(trs)}</tbody></table></div>
</section>
{cite_html(inp, f'{cycle_dir(inp["cycle"])}szamok/index.html', f'Számok — {inp["cycle"]}. ciklus', f'{inp["cycle"]}-szamok')}
""" + page_tail(inp, 1)


def hu_size(n: int) -> str:
    """A file size the way a Hungarian download page prints it: 312 kB, 1,2 MB, 30,7 MB."""
    if n < 1000 * 1000:
        return f"{max(1, round(n / 1000))} kB"
    return f"{n / 1e6:.1f} MB".replace(".", ",")


def build_data_page(inp: dict, out_dir: Path | None = None) -> str:
    """adatok/index.html — every export of the cycle (with sizes, when the files are already on disk) and the data dictionary."""
    files = [("szavazasok.csv", "szavazasok.json", "minden szavazás, egy sor egy szavazás"),
             ("nevsorok.csv", None, "minden név szerinti szavazat, egy sor egy képviselő egy szavazáson"),
             ("kulonvelemenyek.csv", None, "minden különvélemény — a frakció többségével szemben leadott szavazatok; a különvélemény-csatornák teljes tartalma (a függetlenek soraival, akiket a csatornák nem visznek)"),
             ("felszolalasok.csv", None, "az ülésnapok felszólalás-listái soronként: napirendi pont, felszólaló, fajta, szerep, hossz, érdemi/eljárási"),
             ("heti.csv", None, "képviselőnként és ülésnapos hetenként: névsorban, leadott, frakciója ellen, érdemi felszólalás — a heti összefoglalók számai"),
             ("kepviselok.csv", "kepviselok.json", "a ciklus névsoraiban szereplő személyek, összesítéssel"),
             ] + ([("inditvanyok.csv", "inditvanyok.json", "a jelenlegi ciklus irományai benyújtó szerint")] if not inp["closed"] else []) + [
             ("../iromany/iromanyok.csv", None, "minden iromány, amelyről név szerint szavaztak"),
             ("../kohezio/frakciok.csv", None, "frakciónként: Rice-index, egyetértési index, kisebbségi szavazatok"),
             ("../kohezio/szavazasonkent.csv", None, "szavazásonként és frakciónként a két index"),
             ("../kohezio/frakcio_parok.csv", None, "frakciópárok többségi álláspontjának egyezése"),
             ("../kohezio/kepviselo_parok.csv", None, "képviselőpárok egyezése frakción belül"),
             ("../szoros/dontesek.csv", None, "minden döntés a küszöbéhez mérve, a hiányzó-feltevéssel"),
             ("../szamok/havonta.csv", None, "a ciklus hónapról hónapra")]
    def size(rel):
        if out_dir is None:
            return ""
        f = (out_dir / "adatok" / rel).resolve()
        return f' <span class="sub" style="display:inline">({hu_size(f.stat().st_size)})</span>' if f.exists() else ""
    rows = "".join(f'<tr><td class="mono"><a href="{c}">{c.split("/")[-1] if c.startswith("../") else c}</a>{size(c)}{(" · <a href=" + chr(34) + j + chr(34) + ">" + j + "</a>" + size(j)) if j else ""}</td><td>{esc(d)}{(" · " + c.split("/")[1] + "/") if c.startswith("../") else ""}</td></tr>' for c, j, d in files)
    dic = "".join(f'<h3 class="mono">{esc(title)}</h3><div class="tablewrap" style="border:0"><table><tbody>' + "".join(f'<tr><td class="mono" style="white-space:nowrap">{esc(col)}</td><td>{esc(mean)}</td></tr>' for col, mean in cols) + '</tbody></table></div>'
                  for title, cols in ex.adatszotar())
    return page_head(f'Adatok · {inp["cycle"]}. ciklus · karzat', f'Letölthető táblák és adatszótár a {inp["cycle"]}. ciklus szavazásaihoz.', 1 + inp["base_depth"]) + \
        topbar(inp, [("adatok", None)], 1) + f"""
<div class="hero-h"><h1>Adatok</h1><small class="label" data-kz-text>{inp["cycle"]}. ciklus · letölthető táblák és adatszótár</small></div>
<p class="lede">Ugyanazok a táblák, amiket az oldalak mutatnak, CSV-ben (UTF-8, vesszővel) és JSON-ban. Egy szavazás és egy képviselő oldala mellett a saját <span class="mono">.csv</span> és <span class="mono">.json</span> párja is ott van.</p>
<section class="panel">{CORNERS}
  <h2><span data-kz-text>Letöltés</span><span class="tag">{inp["cycle"]}. ciklus</span></h2>
  <div class="tablewrap" style="border:0"><table><thead><tr><th scope="col">Fájl</th><th scope="col">Tartalom</th></tr></thead><tbody>{rows}</tbody></table></div>
</section>
<section class="panel">{CORNERS}
  <h2><span data-kz-text>Adatszótár</span></h2>
  {dic}
</section>
""" + page_tail(inp, 1)


# -- feeds ------------------------------------------------------------------------------------

def feed_vote(inp: dict, ts: str) -> dict:
    """The few fields a feed entry prints about a vote — the same view the pages use."""
    v = inp["by_ts"][ts]
    mo = v["motions"][0] if v.get("motions") else {}
    m = v.get("majority") or {}
    return {"ts": ts, "slug": v["slug"], "on_date": v["on_date"], "time": v.get("time"), "iromany": mo.get("iromany"), "title": mo.get("title"),
            "igen": v.get("igen"), "nem": v.get("nem"), "tartozkodott": v.get("tartozkodott"), "result": v.get("result_raw"),
            "rule_label": rule_short(m.get("rule")) if m.get("rule") else None, "needed": m.get("needed"), "base": m.get("base"), "margin": m.get("margin"),
            "kind": v.get("kind")}


def dissent_events(inp: dict) -> list[dict]:
    """Every roll call with at least one dissent, newest first: {vote, dissents}, dissenters ordered by faction then
    name — from the same alignment record the MP pages, the cohesion page and the CSVs print (tie = no plurality,
    quorum checks excluded)."""
    if "_dissent_events" in inp:
        return inp["_dissent_events"]
    st = inp["store"]
    order = {f["id"]: i for i, f in enumerate(inp["facs"])}
    by_ts: dict[str, list[dict]] = {}
    for key, rec in inp["alignment"]["per_mp"].items():
        azon = key if key in st["members"] else None
        name = st["members"][key]["name"] if azon else key
        for v in rec["votes"]:
            if v["align"] == "against":
                by_ts.setdefault(v["ts"], []).append({"azon": azon, "name": name, "faction": v["faction"], "position": v["position"], "plurality": v["faction_plurality"]})
    events = []
    for ts in reversed(inp["order"]):
        if ts in by_ts:
            events.append({"vote": feed_vote(inp, ts), "dissents": sorted(by_ts[ts], key=lambda d: (order.get(d["faction"], 999), name_key(d["name"])))})
    inp["_dissent_events"] = events
    return events


def channel_events(events: list[dict]) -> list[dict]:
    """The events as the channels carry them: without the independents — "független" is not a faction, so a vote
    against the other independents' plurality is not a dissent from a faction (the MP pages keep that number, labelled)."""
    out = []
    for e in events:
        ds = [d for d in e["dissents"] if d["faction"] != fd.INDEPENDENT]
        if ds:
            out.append({"vote": e["vote"], "dissents": ds})
    return out


def feed_updated(inp: dict) -> str:
    """The feed's <updated>: the sync stamp; failing that, the newest vote's time. Never the wall clock."""
    newest = max(inp["order"]) if inp["order"] else "2026.01.01.00:00:00"
    return fd.rfc3339_iso(inp["idx"].get("last_sync_at"), fd.rfc3339(newest))


def build_feed_page(inp: dict, events: list[dict], fac_counts: dict[str, int], n_independent: int = 0) -> str:
    """feed/index.html — Értesítések: what the channels are, how to use one, and every channel of the cycle."""
    cyc = inp["cycle"]
    closed = inp["closed"]
    n_votes = len(events); n_diss = sum(len(e["dissents"]) for e in events)
    fac_rows = "".join(f'<tr><td><span class="pos"><i class="d" style="--c:{colour}"></i>{esc(f)}</span></td><td class="num mono">{hu_num(n)}</td>'
                       f'<td class="mono"><a href="frakcio-{fd.slug(f)}.xml">frakcio-{fd.slug(f)}.xml</a></td></tr>'
                       for f, n, colour in ((f, fac_counts[f], next((x["colour"] for x in inp["facs"] if x["id"] == f), "#8a8a8a")) for f in fac_counts))
    latest = "".join(f'<tr><td class="ts mono"><a href="../szavazas/{esc(e["vote"]["slug"])}.html">{esc(e["vote"]["on_date"])} {esc(e["vote"]["time"] or "")}</a></td>'
                     f'<td>{esc(cut(" ".join(x for x in [e["vote"].get("iromany") or "", e["vote"].get("title") or ""] if x) or "Szavazás", 90))}</td>'
                     f'<td class="num mono">{len(e["dissents"])}</td><td>{esc(", ".join(sorted({d["faction"] for d in e["dissents"]})))}</td></tr>'
                     for e in events[:25])
    label = f'{cyc}. ciklus · Atom-csatornák · ' + ("lezárt ciklus, új tétel nem érkezik" if closed else "a szinkronnal frissülnek")
    lede = ("Ugyanazok a számok, mint az oldalakon, csatornaként: egy hírolvasó (RSS/Atom) a címre iratkozik fel, és minden frissítés után megkapja az újat. Nincs szerver, nincs fiók — a fájlok az oldal részei."
            if not closed else
            "Ugyanazok a számok, mint az oldalakon, csatornaként. Lezárt ciklus: a csatornák nem változnak — a ciklus utolsó napjainak tételeit adják, a teljes lista a CSV; böngészni valók, nem értesítésre.")
    how = ('<div class="hero-meta prose" style="margin-top:8px">Használat: a csatorna címét (a jobb gombbal másolva) a hírolvasóba kell beilleszteni — Feedly, NetNewsWire, Thunderbird, Miniflux mind ismeri az Atomot. '
           f'Egy csatornában az utolsó {fd.MIN_ENTRIES} tétel van, de legalább az utolsó {fd.RECENT_DAYS} szavazási nap mindegyike; a teljes lista a CSV. '
           'A tételek ideje a szavazásé, a csatorna frissítési ideje a szinkroné.' + (" A címlap csatornái mindig a folyó ciklust adják; a lezárt ciklusoké a ciklus saját könyvtárában marad." if not closed else "") + '</div>')
    indep = (f'<div class="hero-meta prose" style="margin-top:8px">A független képviselők nincsenek a csatornákban: nem frakció, így frakciótöbbség sincs, amitől eltérnének — a képviselőoldalak a többi függetlenhez mért számukat mutatják, feliratozva; a CSV-ben ott vannak ({hu_num(n_independent)} sor, „független” frakcióval).</div>'
             if n_independent else "")
    return page_head(f'Értesítések · {cyc}. ciklus · karzat', f'Atom-csatornák a {cyc}. ciklusról: különvélemények ciklusra, frakcióra és képviselőre, minden szavazás, és a heti összefoglalók képviselőnként és a ciklusra' + (" — a szinkronnal frissülnek." if not closed else " — lezárt ciklus, teljes lista."), 1 + inp["base_depth"],
                     feeds=[("kulonvelemeny.xml", f"karzat · különvélemények · {cyc}. ciklus"), ("szavazasok.xml", f"karzat · szavazások · {cyc}. ciklus"), ("heti.xml", f"karzat · a hét számokban · {cyc}. ciklus"), ("felszolalasok.xml", f"karzat · felszólalások szövege · {cyc}. ciklus")]) + \
        topbar(inp, [("értesítések", None)], 1) + f"""
<div class="hero-h"><h1>Értesítések</h1><small class="label" data-kz-text>{esc(label)}</small></div>
<p class="lede">{lede}</p>
<section class="panel">{CORNERS}
  <h2><span data-kz-text>Különvélemények</span><span class="tag">{hu_num(n_votes)} szavazás · {hu_num(n_diss)} különvélemény · <a href="../adatok/kulonvelemenyek.csv">CSV, mind</a></span></h2>
  <div class="tablewrap" style="border:0"><table><thead><tr><th scope="col">Kör</th><th scope="col">Mi van benne</th><th scope="col">Fájl</th></tr></thead><tbody>
    <tr><td>a ciklus</td><td>minden szavazás, ahol legalább egy képviselő a frakciója többségével szemben szavazott — szavazásonként egy tétel, a különvéleményekkel</td><td class="mono"><a href="kulonvelemeny.xml">kulonvelemeny.xml</a></td></tr>
    <tr><td>egy frakció</td><td>ugyanez, csak az adott frakció tagjainak különvéleményeivel</td><td class="mono">frakcio-…xml (lent)</td></tr>
    <tr><td>egy képviselő</td><td>a képviselő saját különvéleményei — a címe a képviselő oldalán is ott van</td><td class="mono"><a href="../kepviselo/index.html">kepviselo/&lt;azonosító&gt;.xml</a></td></tr>
  </tbody></table></div>
  <h2 style="margin-top:14px"><span data-kz-text>Heti összefoglalók</span><span class="tag">ülésnapos hetenként · <a href="../adatok/heti.csv">CSV, mind</a></span></h2>
  <div class="tablewrap" style="border:0"><table><thead><tr><th scope="col">Kör</th><th scope="col">Mi van benne</th><th scope="col">Fájl</th></tr></thead><tbody>
    <tr><td>egy képviselő</td><td>hetente: névsorban / leadott / frakciója ellen / érdemi felszólalásai (napirendi pont, fajta, hossz) — a címe a képviselő oldalán</td><td class="mono"><a href="../kepviselom/index.html">kepviselo/&lt;azonosító&gt;-heti.xml</a></td></tr>
    <tr><td>a ciklus</td><td>a hét számokban: szavazások, döntések, név szerinti szavazások, különvélemények, érdemi felszólalások, felszólalók</td><td class="mono"><a href="heti.xml">heti.xml</a></td></tr>
  </tbody></table></div>
  <h2 style="margin-top:14px"><span data-kz-text>Felszólalások szövege</span><span class="tag">kulcsszóra figyelni · <a href="../felszolalas/kereses.html">keresés a szövegekben</a></span></h2>
  <div class="tablewrap" style="border:0"><table><thead><tr><th scope="col">Kör</th><th scope="col">Mi van benne</th><th scope="col">Fájl</th></tr></thead><tbody>
    <tr><td>a ciklus</td><td>az érdemi felszólalások teljes jegyzőkönyvi szövege, a legfrissebb elöl (az utolsó napok tételei)</td><td class="mono"><a href="felszolalasok.xml">felszolalasok.xml</a></td></tr>
  </tbody></table></div>
  <div class="hero-meta prose" style="margin-top:8px">Ebben a csatornában az utolsó 200 érdemi felszólalás van, teljes szöveggel; a tétel ideje a nap és a napi sorrend (az API nem ad órát); szöveges CSV nincs, a lapok és a JSON-ok igen. Kulcsszóra így lehet figyelni: a hírolvasó szűrője vagy szabálya a tétel <em>tartalmára</em> — a címben csak nap, felszólaló és fajta áll (Miniflux: keep rule a tartalomra, Inoreader Pro: szabály, Thunderbird: üzenetszűrő a törzsre) — a szöveg teljes, ezért a szűrő mindent lát. Az oldal nem sorol témába és nem foglal össze: a találat a jegyzőkönyv szava. Bizottsági ülések anyaga nincs az API-ban.</div>
  <div class="hero-meta prose" style="margin-top:8px">{esc(fd.CAVEATS)}</div>{indep}{how}
</section>
<section class="panel">{CORNERS}
  <h2><span data-kz-text>Frakciónként</span><span class="tag">különvélemények száma a ciklusban</span></h2>
  <div class="tablewrap" style="border:0"><table><thead><tr><th scope="col">Frakció</th><th scope="col" class="num">Különvélemény</th><th scope="col">Fájl</th></tr></thead><tbody>{fac_rows or '<tr><td colspan="3">—</td></tr>'}</tbody></table></div>
</section>
<section class="panel">{CORNERS}
  <h2><span data-kz-text>Minden szavazás</span><span class="tag">a legfrissebb elöl · <a href="../adatok/szavazasok.csv">CSV, mind</a></span></h2>
  <div class="tablewrap" style="border:0"><table><thead><tr><th scope="col">Kör</th><th scope="col">Mi van benne</th><th scope="col">Fájl</th></tr></thead><tbody>
    <tr><td>a ciklus</td><td>a ciklus minden szavazása: tárgy, arányok, szükséges többség, eredmény; a titkos szavazás és a jelenlét megállapítása jelölve</td><td class="mono"><a href="szavazasok.xml">szavazasok.xml</a></td></tr>
  </tbody></table></div>
</section>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>A legutóbbi különvélemények</span><span class="tag">a csatorna első {min(25, n_votes)} tétele</span></h2>
  <div class="tablewrap"><table><thead><tr><th scope="col">Időpont</th><th scope="col">Tárgy</th><th scope="col" class="num">Különvélemény</th><th scope="col">Frakciók</th></tr></thead><tbody>{latest or '<tr><td colspan="4">Ebben a ciklusban nem volt.</td></tr>'}</tbody></table></div>
</section>
""" + page_tail(inp, 1)


# -- the MP digest: speeches, committees, sitting weeks -------------------------------------------

def hu_mmss(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    h, rem = divmod(int(seconds), 3600)
    m, s_ = divmod(rem, 60)
    return f"{h}:{m:02d}:{s_:02d}" if h else f"{m}:{s_:02d}"


def cycle_window(cycle: int) -> tuple[str, str | None]:
    """[first day, last day] of a cycle by CYCLE_STARTS; the current cycle is open-ended."""
    from karzat.normalise import CYCLE_STARTS
    start = CYCLE_STARTS[cycle]
    nxt = CYCLE_STARTS.get(cycle + 1)
    end = (_date.fromisoformat(nxt) - __import__("datetime").timedelta(days=1)).isoformat() if nxt else None
    return start, end


def committees_in_cycle(mp: dict, cycle: int) -> list[dict]:
    """The record's committee memberships that overlap the cycle, current ones first, then by start."""
    start, end = cycle_window(cycle)
    out = []
    for c in mp.get("committees") or []:
        fr, to = c.get("from") or "", c.get("to")
        if end and fr > end:
            continue
        if to and to < start:
            continue
        out.append(c)
    return sorted(out, key=lambda c: (c.get("to") is not None, c.get("to") or "", c.get("from") or ""), reverse=False)


def speeches_by_mp(inp: dict) -> dict[str, list[dict]]:
    """azon → the MP's speech rows, newest first (memoised)."""
    if "_sp_by_mp" in inp:
        return inp["_sp_by_mp"]
    out: dict[str, list[dict]] = {}
    for r in (inp["speeches"] or {}).get("speeches") or []:
        if r.get("azon"):
            out.setdefault(r["azon"], []).append(r)
    for rows in out.values():
        rows.sort(key=lambda r: (r["date"] or "", r["order"]), reverse=True)
    inp["_sp_by_mp"] = out
    return out


def sitting_weeks(inp: dict) -> list[dict]:
    """The cycle's sitting days grouped into calendar weeks (Monday–Sunday), newest first:
    {key: '2026-W33', from, to (the first and last sitting day of the week), days: [...], label}."""
    if "_weeks" in inp:
        return inp["_weeks"]
    numbered = [d for d in inp["idx"].get("sitting_days") or [] if d.get("date")]
    days = sorted({d["date"] for d in numbered})
    if not days:                                                                # no ulesnap for the cycle: weeks from the votes' dates
        days = sorted({inp["by_ts"][t]["on_date"] for t in inp["order"]})
    weeks: dict[str, list[str]] = {}
    for d in days:
        y, w, _ = _date.fromisoformat(d).isocalendar()
        weeks.setdefault(f"{y}-W{w:02d}", []).append(d)
    # "ülésnap" is the API's numbered sitting: two sittings can share a date (a rendes and a rendkívüli), so the count
    # per week is by number, not by distinct date
    per_week_n: dict[str, int] = {}
    for d in numbered:
        y, w, _ = _date.fromisoformat(d["date"]).isocalendar()
        per_week_n[f"{y}-W{w:02d}"] = per_week_n.get(f"{y}-W{w:02d}", 0) + 1
    out = []
    for key in sorted(weeks, reverse=True):
        ds = weeks[key]
        out.append({"key": key, "from": ds[0], "to": ds[-1], "days": ds, "ulnaps": per_week_n.get(key, len(ds)), "label": hu_span(ds[0], ds[-1])})
    inp["_weeks"] = out
    return out


def hu_span(a: str, b: str) -> str:
    """'2026. augusztus 10–11.' / '2026. július 27. – augusztus 2.' / one day '2026. augusztus 11.'"""
    if a == b:
        return hu_date(a)
    ya, ma, da = a.split("-"); yb, mb, db = b.split("-")
    if ya == yb and ma == mb:
        return f"{ya}. {HU_MONTHS[int(ma)-1]} {int(da)}–{int(db)}."
    if ya == yb:
        return f"{ya}. {HU_MONTHS[int(ma)-1]} {int(da)}. – {HU_MONTHS[int(mb)-1]} {int(db)}."
    return f"{hu_date(a)} – {hu_date(b)}"


def week_of(inp: dict, on_date: str) -> str | None:
    y, w, _ = _date.fromisoformat(on_date).isocalendar()
    return f"{y}-W{w:02d}"


def week_digests(inp: dict) -> dict[str, dict]:
    """Per sitting week: the cycle's numbers and every MP's — one pass over the alignment record and the speeches.
    week key → {"cycle": {roll_calls, votes, decisions, dissents, speeches, speakers}, "mps": {azon: {in_roll, cast, against, speeches: [rows]}}}"""
    if "_digests" in inp:
        return inp["_digests"]
    weeks = sitting_weeks(inp)
    by_key = {w["key"]: {"week": w, "cycle": {"roll_calls": 0, "votes": 0, "decisions": 0, "dissents": 0, "speeches": 0, "speakers": set()}, "mps": {}} for w in weeks}
    st = inp["store"]
    for ts in inp["order"]:
        v = inp["by_ts"][ts]
        k = week_of(inp, v["on_date"])
        if k not in by_key:
            by_key[k] = {"week": {"key": k, "from": v["on_date"], "to": v["on_date"], "days": [v["on_date"]], "ulnaps": 1, "label": hu_span(v["on_date"], v["on_date"])},
                         "cycle": {"roll_calls": 0, "votes": 0, "decisions": 0, "dissents": 0, "speeches": 0, "speakers": set()}, "mps": {}}
        c = by_key[k]["cycle"]
        c["votes"] += 1
        c["decisions"] += 1 if v.get("kind") == "dontes" else 0
        c["roll_calls"] += 1 if st["positions"].get(ts) else 0
    for azon, rec in inp["alignment"]["per_mp"].items():
        for vv in rec["votes"]:
            k = week_of(inp, inp["by_ts"][vv["ts"]]["on_date"])
            m = by_key[k]["mps"].setdefault(azon, {"in_roll": 0, "cast": 0, "against": 0, "independent": False, "speeches": []})
            m["in_roll"] += 1
            if vv["position"] in CAST:
                m["cast"] += 1
            if vv["faction"] == fd.INDEPENDENT:
                m["independent"] = True                        # no faction that week: "frakciója ellen" is not a number for them
            elif vv["align"] == "against":
                m["against"] += 1
                by_key[k]["cycle"]["dissents"] += 1
    for r in (inp["speeches"] or {}).get("speeches") or []:
        if r["technical"] or not r.get("date"):
            continue
        k = week_of(inp, r["date"])
        if k not in by_key:
            continue
        by_key[k]["cycle"]["speeches"] += 1                    # every substantive speech of the week, non-MPs included
        by_key[k]["cycle"]["speakers"].add(r.get("azon") or r.get("speaker_label"))
        if r.get("azon"):
            m = by_key[k]["mps"].setdefault(r["azon"], {"in_roll": 0, "cast": 0, "against": 0, "independent": False, "speeches": []})
            m["speeches"].append(r)
    for d in by_key.values():
        d["cycle"]["speakers"] = len(d["cycle"]["speakers"])
    inp["_digests"] = by_key
    return by_key


def speeches_cover(inp: dict, week: dict) -> bool:
    """Do the loaded speech lists reach this week's last sitting day? (The lists may lag the votes; then the week's
    speech count is unknown, not zero.)"""
    sp = inp["speeches"]
    return bool(sp and sp.get("as_of") and sp["as_of"] >= week["to"])


def digest_line(m: dict | None, roll_calls: int, speeches_known: bool = True) -> str:
    """One MP's week in label form (no suffixes on numerals): névsorban 12 / 14 · leadott 11 · frakciója ellen 1 · felszólalás 2.
    An independent has no faction plurality: 'frakciója ellen —'. When the speech lists do not cover the week: 'felszólalás —'."""
    sp = (str(len(m["speeches"])) if m else "0") if speeches_known else "—"
    if not m:
        return f"névsorban 0 / {roll_calls} · felszólalás {sp}"
    against = "—" if m.get("independent") else str(m["against"])
    return f"névsorban {m['in_roll']} / {roll_calls} · leadott {m['cast']} · frakciója ellen {against} · felszólalás {sp}"


def digest_html(inp: dict, m: dict | None, roll_calls: int, root: str = "../", speeches_known: bool = True) -> str:
    """The week's numbers, then the speeches as a list (agenda item · kind · length)."""
    html = f"<p>{esc(digest_line(m, roll_calls, speeches_known))}</p>"
    if not speeches_known:
        html += "<p>A felszólalás-listák erre a hétre nincsenek betöltve.</p>"
    if m and m["speeches"]:
        items = "".join(f'<li>{esc(r["date"])} · {esc(cut(r["event"] or "—", 120))} — {esc(r["kind"] or "")}{" · " + hu_mmss(r["duration_s"]) if r.get("duration_s") else ""}</li>'
                        for r in sorted(m["speeches"], key=lambda r: (r["date"], r["order"])))
        html += f"<ul>{items}</ul>"
    return html


def week_end_stamp(inp: dict, w: dict, updated: str) -> str:
    """An entry's <updated>: the week's last sitting day (end of day) — for the newest week the sync stamp, since it may
    still be growing."""
    if w is sitting_weeks(inp)[0] and not inp["closed"]:
        return updated
    return fd.rfc3339(w["to"].replace("-", ".") + ".23:59:59")


def mp_weekly_feed(inp: dict, azon: str, updated: str, root: str) -> str:
    """kepviselo/<azon>-heti.xml — one entry per sitting week: the MP's numbers and speeches, newest first."""
    mp = inp["mps"][azon]
    dg = week_digests(inp)
    cdir = cycle_dir(inp["cycle"])
    m_from, m_to = mp.get("mandate_from") or "", mp.get("mandate_to")
    weeks = [w for w in sitting_weeks(inp) if not (w["to"] < m_from or (m_to and w["from"] > m_to))]     # the mandate's weeks only
    ents = []
    for w in fd.recent(weeks, lambda w: w["to"]):
        d = dg.get(w["key"]) or {}
        m = (d.get("mps") or {}).get(azon)
        rc = (d.get("cycle") or {}).get("roll_calls", 0)
        known = speeches_cover(inp, w)
        if m and not m["in_roll"] and mp.get("faction") == fd.INDEPENDENT:
            m = dict(m, independent=True)
        title = f'{mp["name"]} · {w["label"]} — {digest_line(m, rc, known)}'
        ents.append(fd.entry(f'urn:karzat:{inp["cycle"]}:heti:{azon}:{w["key"]}', title, f'{root}{cdir}kepviselo/{azon}.html', week_end_stamp(inp, w, updated),
                             digest_html(inp, m, rc, root, known)))
    return fd.atom(f'urn:karzat:{inp["cycle"]}:heti:{azon}', f'karzat · {mp["name"]} — heti összefoglaló · {inp["cycle"]}. ciklus',
                   "Hetente, ülésnapos hetekre: hány név szerinti szavazás névsorában szerepelt, hányat adott le, hányat a frakciója többségével szemben, "
                   "és az érdemi felszólalásai (napirendi pont, fajta, hossz). Számok a napi listákból, nem minősítés.",
                   f'{root}{cdir}kepviselo/{azon}-heti.xml', f'{root}{cdir}kepviselo/{azon}.html', updated, ents)


def cycle_weekly_feed(inp: dict, updated: str, root: str) -> str:
    """feed/heti.xml — the cycle week by week: votes, decisions, roll calls, dissents, speeches, speakers."""
    dg = week_digests(inp)
    cdir = cycle_dir(inp["cycle"])
    ents = []
    for w in fd.recent(sitting_weeks(inp), lambda w: w["to"]):
        c = (dg.get(w["key"]) or {}).get("cycle") or {}
        sp_txt = (f'{c.get("speeches", 0)} érdemi felszólalás {c.get("speakers", 0)} felszólalótól' if speeches_cover(inp, w) else "felszólalás-lista nincs betöltve")
        line = (f'{w["ulnaps"]} ülésnap · {c.get("votes", 0)} szavazás ({c.get("decisions", 0)} döntés, {c.get("roll_calls", 0)} név szerinti) · '
                f'{c.get("dissents", 0)} különvélemény · {sp_txt}')
        html = f'<p><a href="{root}{cdir}index.html">{esc(w["label"])}</a> · {esc(line)}</p>'
        ents.append(fd.entry(f'urn:karzat:{inp["cycle"]}:heti:{w["key"]}', f'{w["label"]} — {line}', f'{root}{cdir}index.html', week_end_stamp(inp, w, updated), html))
    return fd.atom(f'urn:karzat:{inp["cycle"]}:heti', f'karzat · a hét számokban · {inp["cycle"]}. ciklus',
                   "Ülésnapos hetenként: szavazások, döntések, név szerinti szavazások, különvélemények (frakciótagoké; a függetleneké nem számít), érdemi felszólalások és felszólalók (nem képviselőkkel együtt). Számok, nem minősítések.",
                   f'{root}{cdir}feed/heti.xml', f'{root}{cdir}index.html', updated, ents)


# -- speech texts: pages, day lists, the prefix index, the channel ---------------------------------

def speech_id(r: dict) -> str:
    return f'{r["ulnap"]}-{r["seq"]}'


def fold_tokens(text: str) -> list[str]:
    """Search tokens: accents folded, lower-case, [a-z0-9]{3,} — the same rule the page's script applies to the query."""
    import unicodedata
    folded = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)).lower()
    return re.findall(r"[a-z0-9]{3,}", folded)


def substantive_rows(inp: dict) -> list[dict]:
    """The cycle's substantive, non-reprint speech rows in the days' order (the ones that get a page and a search entry)."""
    if "_sub_rows" in inp:
        return inp["_sub_rows"]
    rows = [r for r in ((inp["speeches"] or {}).get("speeches") or []) if not r["technical"] and r.get("seq") is not None]
    rows.sort(key=lambda r: (r["date"] or "", r["ulnap"] or 0, r["order"]))
    inp["_sub_rows"] = rows
    return rows


def speech_search_index(inp: dict) -> tuple[list[list], dict[str, dict[str, list[int]]]]:
    """meta (one compact row per substantive speech, chronological — its position is the id the shards use) and the
    shards: first two letters of the token → {token: [ids]}. Only speeches with a fetched text carry tokens; the others
    are in the meta (they list, they do not match)."""
    texts = (inp["texts"] or {}).get("texts") or {}
    meta: list[list] = []
    shards: dict[str, dict[str, list[int]]] = {}
    for i, r in enumerate(substantive_rows(inp)):
        sid = speech_id(r)
        t = texts.get(sid)
        azon = (t or {}).get("azon") or r.get("azon")               # the payload's own id first, the list's resolution otherwise
        meta.append([sid, r["date"], r.get("name") or r.get("speaker_label") or "—", azon if azon in inp["mps"] else None, r.get("kind"), cut(r.get("event") or "", 90),
                     hu_mmss(r["duration_s"]) if r.get("duration_s") else None])
        if not t:
            continue
        seen: set[str] = set()
        for tok in fold_tokens(" ".join(t["paragraphs"])):
            if tok in seen:
                continue
            seen.add(tok)
            shards.setdefault(tok[:2], {}).setdefault(tok, []).append(i)
    # a two-letter shard past the size limit is split into three-letter shards; a stub tells the script where to look
    out: dict[str, dict] = {}
    for key, toks in shards.items():
        size = sum(len(k) + 4 + 6 * len(v) for k, v in toks.items())        # a rough JSON byte estimate
        if size > SHARD_LIMIT:
            out[key] = {"__split": 1}
            for k, v in toks.items():
                out.setdefault(k[:3], {})[k] = v
        else:
            out[key] = toks
    return meta, out


SHARD_LIMIT = 250_000      # bytes of JSON, roughly; above it a first-two-letters shard is split by the third letter


def build_speech_page(inp: dict, r: dict, text: dict | None, prev_r: dict | None, next_r: dict | None, bs: dict | None) -> str:
    """felszolalas/<ülésnap>-<sorszám>.html — one speech: who, when, on what, the record's text."""
    sid = speech_id(r)
    who = r.get("name") or r.get("speaker_label") or "—"
    azon = (text or {}).get("azon") or r.get("azon")               # the payload's own id first
    who_html = f'<a href="../kepviselo/{esc(azon)}.html">{esc(who)}</a>' if azon and azon in inp["mps"] else esc(who)
    fac = f' ({esc(r["faction"])})' if r.get("faction") else ""
    role = (text or {}).get("position") or r.get("role")
    label = " · ".join(x for x in [esc(r["date"] or ""), f'{r["ulnap"]}. ülésnap', esc(r.get("kind") or ""), hu_mmss(r["duration_s"]) if r.get("duration_s") else "", esc(role or "")] if x)
    # the agenda item, with links to the bills it names when the cycle has their pages
    ev = r.get("event") or "—"
    ev_html = esc(ev)
    if bs:
        for num in (r.get("iromany") or "").split(" · "):
            n_, _ = an.bill_key(num) if num else (None, None)
            if n_ is not None and n_ in bs:
                ev_html = ev_html.replace(esc(num), f'<a href="../iromany/{n_}.html">{esc(num)}</a>', 1)
    if text and text.get("paragraphs"):
        body = '<div class="speech">' + "".join(f"<p>{esc(x)}</p>" for x in text["paragraphs"]) + "</div>"
        tag = f'{hu_num(text["chars"])} karakter · {len(text["paragraphs"])} bekezdés · <a href="{esc(sid)}.json">JSON</a>'
    else:
        body = '<div class="hero-meta">A szöveg nincs betöltve.</div>'
        tag = f'<a href="{esc(sid)}.json">JSON</a>'
    nav = ('<div class="pager">' + (f'<a href="{esc(speech_id(prev_r))}.html">← {esc(prev_r.get("name") or prev_r.get("speaker_label") or "")} · {esc(cut(prev_r.get("event") or "", 50))}</a>' if prev_r else "<span></span>")
           + (f'<a href="{esc(speech_id(next_r))}.html">{esc(next_r.get("name") or next_r.get("speaker_label") or "")} · {esc(cut(next_r.get("event") or "", 50))} →</a>' if next_r else "<span></span>") + "</div>")
    return page_head(f'{who} · {r["date"]} · {cut(r.get("kind") or "felszólalás", 40)}{" · " + str(inp["cycle"]) + ". ciklus" if inp["closed"] else ""} · karzat',
                     f'{who}{fac} felszólalása {r["date"]}: {r.get("kind") or ""} — {cut(ev, 120)}. Az Országgyűlés jegyzőkönyvi szövege.', 1 + inp["base_depth"]) + \
        topbar(inp, [("felszólalások", "index.html"), (f'{r["ulnap"]}. ülésnap', f'nap{r["ulnap"]}.html'), (f'{r["seq"]}.', None)], 1) + f"""
<div class="hero-h"><h1>{who_html}{fac}</h1><small class="label">{label}</small></div>
<p class="hero-meta">napirendi pont: {ev_html}</p>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Szöveg</span><span class="tag">{tag}</span></h2>
  {body}
  <div class="hero-meta prose" style="margin-top:10px">A jegyzőkönyv szövege, ahogy az Országgyűlés Web API-ja adja (felszolalas), a formázás nélkül. Nem összefoglaló.</div>
</section>
{nav}
{cite_html(inp, f'{cycle_dir(inp["cycle"])}felszolalas/{sid}.html', f'{who} — {r["date"]}, {r.get("kind") or "felszólalás"}', f'{inp["cycle"]}-felszolalas-{sid}', json_href=f"{sid}.json")}
""" + page_tail(inp, 1)


def build_day_page(inp: dict, ulnap: int, day_rows: list[dict], texts: dict) -> str:
    """felszolalas/nap<N>.html — one sitting day's list, in order, grouped by agenda item; procedural rows dimmed."""
    date = next((r["date"] for r in day_rows if r.get("date")), None)
    groups: list[tuple[str, list[dict]]] = []
    for r in sorted(day_rows, key=lambda r: r["order"]):
        ev = r.get("event") or "—"
        if not groups or groups[-1][0] != ev:
            groups.append((ev, []))
        groups[-1][1].append(r)
    trs = []
    for ev, rows in groups:
        trs.append(f'<tr class="grp"><td colspan="5"><b>{esc(ev)}</b></td></tr>')
        for r in rows:
            sid = speech_id(r)
            sub = not r["technical"] and r.get("seq") is not None
            who = r.get("name") or r.get("speaker_label") or "—"
            az = ((texts.get(sid) or {}).get("azon") if sub else None) or r.get("azon")
            who_html = f'<a href="../kepviselo/{esc(az)}.html">{esc(who)}</a>' if az and az in inp["mps"] else esc(who)
            link = f'<a href="{esc(sid)}.html">{"szöveg" if sid in texts else "lap"}</a>' if sub else ""
            trs.append(f'<tr data-sub="{1 if sub else 0}"{" class=dim" if not sub else ""}><td class="num mono">{r["seq"] if r.get("seq") is not None else ""}</td><td>{who_html}{(" <span class=" + chr(34) + "sub" + chr(34) + ">" + esc(r["faction"]) + "</span>") if r.get("faction") else ""}</td>'
                       f'<td>{esc(r.get("kind") or "")}{(" <span class=" + chr(34) + "sub" + chr(34) + ">" + esc(r["role"]) + "</span>") if r.get("role") else ""}</td><td class="num mono">{hu_mmss(r["duration_s"]) if r.get("duration_s") else ""}</td><td class="mono">{link}</td></tr>')
    n_sub = sum(1 for r in day_rows if not r["technical"])
    return page_head(f'{r["ulnap"]}. ülésnap · {date} · felszólalások{" · " + str(inp["cycle"]) + ". ciklus" if inp["closed"] else ""} · karzat', f'Az Országgyűlés {date} ({ulnap}. ülésnap) felszólalásai napirendi pontonként, sorban.', 1 + inp["base_depth"]) + \
        topbar(inp, [("felszólalások", "index.html"), (f'{ulnap}. ülésnap', None)], 1) + f"""
<div class="hero-h"><h1>{ulnap}. ülésnap</h1><small class="label" data-kz-text>{esc(hu_date(date)) if date else "—"} · {hu_num(len(day_rows))} sor · {hu_num(n_sub)} érdemi felszólalás</small></div>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>A nap sorban</span><span class="tag">napirendi pontonként · az eljárási sorok halványan</span></h2>
  <div class="filters" role="group" aria-label="Szűrés"><button type="button" data-rowf="all" data-table="day" class="on" aria-pressed="true">minden sor</button><button type="button" data-rowf="data-sub:1" data-table="day" aria-pressed="false">csak érdemi</button><input type="search" data-filter-table="day" placeholder="név, tárgy, fajta" aria-label="Szűrés" style="min-width:160px"></div>
  <div class="tablewrap"><table id="day"><thead><tr><th scope="col" class="num">Sorszám</th><th scope="col">Felszólaló</th><th scope="col">Fajta</th><th scope="col" class="num">Hossz</th><th scope="col">Lap</th></tr></thead><tbody>{"".join(trs)}</tbody></table></div>
</section>
""" + page_tail(inp, 1)


def build_speech_search_page(inp: dict, n_meta: int, n_texts: int) -> str:
    """felszolalas/kereses.html — a box over the fetched speech texts of the cycle."""
    return page_head(f'Keresés a felszólalásokban · {inp["cycle"]}. ciklus · karzat', f'Szókeresés a {inp["cycle"]}. ciklus felszólalásainak jegyzőkönyvi szövegében: előtag szerint, ékezet nélkül is; a találatok szövegrészlettel.', 1 + inp["base_depth"]) + \
        topbar(inp, [("felszólalások", "index.html"), ("keresés", None)], 1) + f"""
<div class="hero-h"><h1>Keresés a felszólalásokban</h1><small class="label" data-kz-text>{inp["cycle"]}. ciklus · {hu_num(n_meta)} érdemi felszólalás · {hu_num(n_texts)} szöveg betöltve</small></div>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Keresés</span><span class="tag">szó eleje szerint · ékezet nélkül is · több szó: mind szerepel</span></h2>
  <div class="filters"><input id="spq" type="search" placeholder="pl. oktatás · MÁV · vasút · adó" aria-label="Keresés a szövegekben" autofocus style="min-width:min(100%,420px)"><span class="n" id="spn" aria-live="polite"></span></div>
  <div class="tablewrap"><table data-page-size="20"><thead><tr><th scope="col">Nap</th><th scope="col">Felszólaló</th><th scope="col">Napirendi pont · részlet</th></tr></thead><tbody id="spres"><tr><td colspan="3" class="hero-meta">Kezdj el gépelni (legalább három betű).</td></tr></tbody></table></div>
  <noscript><div class="hero-meta prose" style="margin-top:8px">A kereső JavaScriptet használ. Nélküle: a napok listája az <a href="index.html">előző oldalon</a>, a szövegek a napok lapjairól.</div></noscript>
  <div class="hero-meta prose" style="margin-top:8px">A keresés a szó elejére illeszt („oktat” megtalálja az oktatást, oktatásit, oktatásügyet), legalább három betűtől, ékezetek nélkül is; több szó esetén mindegyiknek szerepelnie kell a szövegben. Szótövezés nincs. Csak a betöltött szövegekben keres; a szövegrészlet a jegyzőkönyvből való, a találat oldalán a teljes szöveg. A napok listái és a szövegek: <span class="mono">felszolalas/</span>; kulcsszóra figyelni csatornán: <a href="../feed/index.html">értesítések</a>.</div>
</section>
""" + page_tail(inp, 1)


def speeches_feed(inp: dict, updated: str, root: str) -> str:
    """feed/felszolalasok.xml — the recent substantive speeches with their full text: the keyword channel (a reader's
    filter does the watching)."""
    cdir = cycle_dir(inp["cycle"])
    texts = (inp["texts"] or {}).get("texts") or {}
    rows = [r for r in reversed(substantive_rows(inp)) if speech_id(r) in texts]
    ents = []
    for r in fd.recent(rows, lambda r: r["date"])[:200]:
        sid = speech_id(r); t = texts[sid]
        who = r.get("name") or r.get("speaker_label") or "—"
        title = f'{r["date"]} · {who}{" (" + r["faction"] + ")" if r.get("faction") else ""} — {r.get("kind") or "felszólalás"} · {cut(r.get("event") or "", 90)}'
        html = (f'<p><a href="{fd.a(root + cdir)}felszolalas/{fd.a(sid)}.html">{fd._x(r["date"])} · {fd._x(r.get("kind") or "")}{" · " + hu_mmss(r["duration_s"]) if r.get("duration_s") else ""}</a> · {fd._x(r.get("event") or "")}</p>'
                + "".join(f"<p>{fd._x(x)}</p>" for x in t["paragraphs"]))
        seq = int(r["seq"] or 0)
        stamp = fd.rfc3339(f'{r["date"].replace("-", ".")}.{min(23, seq // 3600):02d}:{(seq // 60) % 60:02d}:{seq % 60:02d}')   # the day, then the speech's order (the API gives no clock time)
        ents.append(fd.entry(f'urn:karzat:{inp["cycle"]}:felszolalas:{sid}', title, f'{root}{cdir}felszolalas/{sid}.html', stamp,
                             html, categories=[c for c in [r.get("faction"), r.get("kind")] if c]))
    return fd.atom(f'urn:karzat:{inp["cycle"]}:felszolalasok', f'karzat · felszólalások szövege · {inp["cycle"]}. ciklus',
                   "Az érdemi felszólalások teljes jegyzőkönyvi szövege, a legfrissebb elöl — kulcsszóra a hírolvasó szűrője figyel. Az utolsó napok tételei; a többi a felszólalások lapjain.",
                   f'{root}{cdir}feed/felszolalasok.xml', f'{root}{cdir}felszolalas/index.html', updated, ents)


def build_speeches_page(inp: dict) -> str:
    """felszolalas/index.html — the cycle's speech lists day by day, the kinds, the classification and its check."""
    sp = inp["speeches"] or {}
    days = sp.get("days") or []
    trs = "".join(f'<tr><td class="ts mono"><a href="nap{d["ulnap"]}.html">{esc(d["date"] or "")}</a></td><td class="num mono">{d["ulnap"]}</td><td class="num mono">{hu_num(d["speeches"])}</td><td class="num mono">{hu_num(d["substantive"])}</td></tr>' for d in reversed(days))
    tx = inp["texts"] or {}
    n_tx = len(tx.get("texts") or {})
    from karzat.normalise import SUBSTANTIVE_KINDS
    kinds = sp.get("kinds") or {}
    krows = "".join(f'<tr><td>{esc(k or "—")}</td><td class="num mono">{hu_num(n)}</td><td>{"érdemi" if (k or "").casefold() in SUBSTANTIVE_KINDS else "eljárási"}</td></tr>' for k, n in kinds.items())
    chk = sp.get("record_check") or {}
    return page_head(f'Felszólalások · {inp["cycle"]}. ciklus · karzat', f'A {inp["cycle"]}. ciklus felszólalásai ülésnaponként és fajtánként, a képviselői adatlapokkal egyeztetve.', 1 + inp["base_depth"]) + \
        topbar(inp, [("felszólalások", None)], 1) + f"""
<div class="hero-h"><h1>Felszólalások</h1><small class="label" data-kz-text>{inp["cycle"]}. ciklus · {hu_num(sp.get("count", 0))} sor · {hu_num(sp.get("substantive", 0))} érdemi felszólalás · {hu_num(len(days))} ülésnap · {hu_num(n_tx)} szöveg</small></div>
<p class="lede">Az ülésnapok felszólalás-listái az Országgyűlés Web API-jából (<span class="mono">felszolalasok</span>): ki, melyik napirendi pontnál, milyen fajta felszólalással, mennyi ideig — naponként egy lap, érdemi felszólalásonként egy lap a jegyzőkönyvi szöveggel. Érdemi az, ahol valaki a tárgyhoz szól; eljárási az ülésvezetés, a bejelentés, az eredmény kihirdetése — a besorolás a fajta neve szerint, lent. <a href="kereses.html">Keresés a szövegekben</a>.</p>
<section class="panel">{CORNERS}
  <h2><span data-kz-text>Ülésnaponként</span><span class="tag">a legfrissebb elöl · <a href="../adatok/felszolalasok.csv">CSV, mind</a></span></h2>
  <div class="tablewrap"><table data-page-size="25" data-counter="dn"><thead><tr><th scope="col">Nap</th><th scope="col" class="num">Ülésnap</th><th scope="col" class="num">Sor</th><th scope="col" class="num">Érdemi</th></tr></thead><tbody>{trs or '<tr><td colspan="4">—</td></tr>'}</tbody></table></div>
  <div class="filters"><span class="n" id="dn" aria-live="polite"></span></div>
</section>
<section class="panel">{CORNERS}
  <h2><span data-kz-text>Fajtánként</span><span class="tag">{len(kinds)} fajta</span></h2>
  <div class="tablewrap" style="border:0"><table><thead><tr><th scope="col">Fajta (az API szövege)</th><th scope="col" class="num">Sor</th><th scope="col">Besorolás</th></tr></thead><tbody>{krows}</tbody></table></div>
  <div class="hero-meta prose" style="margin-top:8px">Ellenőrzés: a képviselői adatlap saját „felszólalás” száma erre a ciklusra {hu_num(chk.get("agree", 0))} / {hu_num(chk.get("mps", 0))} képviselőnél megegyezik az itteni érdemi számmal (akinek valamelyik forrás szerint volt felszólalása); ahol nem, a képviselő oldala mindkettőt írja. Aki nem képviselő (miniszter, köztársasági elnök), névvel és tisztséggel szerepel, oldal nélkül.</div>
</section>
{cite_html(inp, f'{cycle_dir(inp["cycle"])}felszolalas/index.html', f'Felszólalások — {inp["cycle"]}. ciklus', f'{inp["cycle"]}-felszolalas')}
""" + page_tail(inp, 1)


def build_kepviselom_page(inp: dict) -> str:
    """kepviselom/index.html — Ki a képviselőm? By constituency (county → number → MP), the list MPs, a name box."""
    mps = inp["mps"]
    colour = {f["id"]: f["colour"] for f in inp["facs"]}
    keep = (lambda m: bool(m.get("current"))) if not inp["closed"] else (lambda m: True)      # today's sitters, or the whole cycle's roster
    egy = sorted((m for m in mps.values() if m.get("mandate_kind") == "egyeni" and keep(m)), key=lambda m: (name_key(m.get("county") or ""), m.get("constituency_no") or 0))
    lis = sorted((m for m in mps.values() if m.get("mandate_kind") == "lista" and keep(m)), key=lambda m: name_key(m["name"]))
    other = sorted((m for m in mps.values() if m.get("mandate_kind") not in ("egyeni", "lista") and keep(m)), key=lambda m: name_key(m["name"]))
    def row(m, oevk):
        c = colour.get(m.get("faction") or "", "#8a8a8a")
        return (f'<tr data-name="{esc(name_key(m["name"]))}"><td>{esc(oevk)}</td><td><a href="../kepviselo/{esc(m["p_azon"])}.html">{esc(m["name"])}</a></td>'
                f'<td><span class="pos"><i class="d" style="--c:{c}"></i>{esc(m.get("faction") or "—")}</span></td>'
                f'<td class="mono"><a href="../kepviselo/{esc(m["p_azon"])}.xml" title="különvéleményei">Atom</a> · <a href="../kepviselo/{esc(m["p_azon"])}-heti.xml" title="heti összefoglaló">heti</a></td></tr>')
    erows = "".join(row(m, f'{m.get("county") or "—"} {m.get("constituency_no") or ""}. OEVK') for m in egy)
    lrows = "".join(row(m, "országos lista") for m in lis) + "".join(row(m, "mandátum: —") for m in other)
    return page_head(f'Ki a képviselőm? · {inp["cycle"]}. ciklus · karzat', f'A {inp["cycle"]}. ciklus képviselői választókerület szerint (megye, OEVK) és az országos listáról; névre szűrhető; minden képviselőnél a heti összefoglaló és a különvélemény-csatorna.', 1 + inp["base_depth"]) + \
        topbar(inp, [("képviselőm", None)], 1) + f"""
<div class="hero-h"><h1>Ki a képviselőm?</h1><small class="label" data-kz-text>{inp["cycle"]}. ciklus · {hu_num(len(egy))} egyéni választókerület · {hu_num(len(lis) + len(other))} listás képviselő{" · a ciklus teljes névsora" if inp["closed"] else ""}</small></div>
<p class="lede">Egyéni választókerületben a megye és a kerület száma azonosítja a képviselőt; a listás képviselőknek nincs kerületük. Település szerint keresni még nem lehet: a választókerületek településlistája (valasztas.hu) nincs betöltve.{" Lezárt ciklus: mindenki, aki a ciklus névsoraiban szerepelt." if inp["closed"] else ""} Minden képviselő oldalán ott a heti összefoglalója és a különvélemény-csatornája.</p>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Egyéni választókerületek</span><span class="tag">megye · kerület · képviselő</span></h2>
  <div class="filters"><input type="search" data-filter-table="oevk" placeholder="megye, név" aria-label="Szűrés megyére vagy névre" style="min-width:200px"><span class="n" id="on" aria-live="polite"></span></div>
  <div class="tablewrap"><table id="oevk" data-page-size="30" data-counter="on"><thead><tr><th scope="col">Választókerület</th><th scope="col">Képviselő</th><th scope="col">Frakció</th><th scope="col">Csatornák</th></tr></thead><tbody>{erows or '<tr><td colspan="4">—</td></tr>'}</tbody></table></div>
</section>
<section class="panel">{CORNERS}
  <h2><span data-kz-text>Országos listáról</span><span class="tag">{hu_num(len(lis))} képviselő</span></h2>
  <div class="filters"><input type="search" data-filter-table="lista" placeholder="név, frakció" aria-label="Szűrés névre vagy frakcióra" style="min-width:200px"><span class="n" id="ln" aria-live="polite"></span></div>
  <div class="tablewrap"><table id="lista" data-page-size="30" data-counter="ln"><thead><tr><th scope="col">Mandátum</th><th scope="col">Képviselő</th><th scope="col">Frakció</th><th scope="col">Csatornák</th></tr></thead><tbody>{lrows or '<tr><td colspan="4">—</td></tr>'}</tbody></table></div>
</section>
""" + page_tail(inp, 1)


def prune(dir_: Path, keep: set[Path], suffixes: tuple[str, ...] = (".html", ".json", ".csv", ".xml")) -> int:
    """Remove generated files in dir_ that this build did not write (a vote or MP that left the inputs must not
    linger as a stale, wrongly attributed page). Only the listed extensions, only that directory."""
    n = 0
    if not dir_.is_dir():
        return 0
    for f in dir_.iterdir():
        if f.is_file() and f.suffix in suffixes and f.resolve() not in keep:
            f.unlink()
            n += 1
    return n


class _Writer:
    """Write text files and remember what was written, so the directory can be pruned afterwards."""
    def __init__(self):
        self.written: set[Path] = set()

    def __call__(self, path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        self.written.add(path.resolve())


def build_cycle(out_dir: Path, cycle: int, index_only: bool = False) -> dict:
    """One cycle's tree: index.html, szavazas/<slug>.html (+ .json/.csv), kepviselo/<azon>.html (+ .json/.csv) + index,
    adatok/ (the dump and its dictionary) — under out_dir. Returns per-MP summaries for the career pages."""
    inp = load_inputs(cycle)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(build_index(inp, pick_hero(inp)), encoding="utf-8")
    n = k = 0
    w = _Writer()
    if not index_only:
        vd = out_dir / "szavazas"
        vd.mkdir(parents=True, exist_ok=True)
        for ts in inp["order"]:
            slug = inp["by_ts"][ts]["slug"]
            w(vd / f"{slug}.html", build_vote_page(inp, ts))
            j, c = vote_exports(inp, ts)
            w(vd / f"{slug}.json", j)
            if inp["store"]["positions"].get(ts):
                w(vd / f"{slug}.csv", c)
            n += 1
        prune(vd, w.written)
        # feeds: the alert channel — dissents per cycle / faction / MP, and every vote; rewritten at each build
        all_events = dissent_events(inp)                       # every dissent, independents included (the CSV)
        events = channel_events(all_events)                    # what the channels carry (factions only)
        updated = feed_updated(inp)
        cdir = cycle_dir(cycle)
        # hrefs: the deployed origin when known, else relative to the feed file (feed/ and kepviselo/ sit one level
        # below the cycle root, so '../' climbs to the cycle root and one more '../' per cycle level to the site root)
        root = SITE_URL + "/" if SITE_URL else "../" * (inp["base_depth"] + 1)
        fdir = out_dir / "feed"; fdir.mkdir(parents=True, exist_ok=True)
        fw = _Writer()
        fw(fdir / "kulonvelemeny.xml", fd.dissent_feed(cycle, events, updated, root, cdir, "feed/kulonvelemeny.xml", "feed/index.html"))
        fac_counts: dict[str, int] = {}
        for e in events:
            for d in e["dissents"]:
                fac_counts[d["faction"]] = fac_counts.get(d["faction"], 0) + 1
        fac_order = {f["id"]: i for i, f in enumerate(inp["facs"])}
        fac_counts = dict(sorted(fac_counts.items(), key=lambda kv: (fac_order.get(kv[0], 999), kv[0])))
        for f in fac_counts:
            ev_f = [{"vote": e["vote"], "dissents": [d for d in e["dissents"] if d["faction"] == f]} for e in events if any(d["faction"] == f for d in e["dissents"])]
            fw(fdir / f"frakcio-{fd.slug(f)}.xml", fd.dissent_feed(cycle, ev_f, updated, root, cdir, f"feed/frakcio-{fd.slug(f)}.xml", "feed/index.html", faction=f))
        n_diss_by_ts = {e["vote"]["ts"]: len(e["dissents"]) for e in events}
        all_votes = [dict(feed_vote(inp, ts), n_dissent=n_diss_by_ts.get(ts, 0), secret=inp["by_ts"][ts].get("secret")) for ts in reversed(inp["order"])]
        fw(fdir / "szavazasok.xml", fd.votes_feed(cycle, all_votes, updated, root, cdir, "feed/szavazasok.xml"))
        fw(fdir / "index.html", build_feed_page(inp, events, fac_counts, n_independent=sum(1 for e in all_events for d in e["dissents"] if d["faction"] == fd.INDEPENDENT)))
        fw(fdir / "heti.xml", cycle_weekly_feed(inp, updated, root))
        fw(fdir / "felszolalasok.xml", speeches_feed(inp, updated, root))
        prune(fdir, fw.written)
        spd = out_dir / "felszolalas"; spd.mkdir(parents=True, exist_ok=True)
        sw = _Writer()
        sw(spd / "index.html", build_speeches_page(inp))
        # one page + JSON per substantive speech, one page per sitting day, the search page with its shards and meta, the channel
        texts_map = (inp["texts"] or {}).get("texts") or {}
        subs = substantive_rows(inp)
        bs = an.bills(inp)                                         # the cycle's bills: speech pages link the agenda item's bills
        by_day: dict[int, list[dict]] = {}
        for r in (inp["speeches"] or {}).get("speeches") or []:
            by_day.setdefault(r["ulnap"], []).append(r)
        for i, r in enumerate(subs):
            sid = speech_id(r)
            t = texts_map.get(sid)
            sw(spd / f"{sid}.html", build_speech_page(inp, r, t, subs[i - 1] if i > 0 else None, subs[i + 1] if i + 1 < len(subs) else None, bs))
            sw(spd / f"{sid}.json", json.dumps({"cycle": cycle, "id": sid, "date": r["date"], "ulnap": r["ulnap"], "seq": r["seq"], "speaker": r.get("speaker_label"), "p_azon": (t or {}).get("azon") or r.get("azon"),
                                                 "faction": r.get("faction"), "kind": r.get("kind"), "role": r.get("role"), "event": r.get("event"), "iromany": r.get("iromany"), "duration_s": r.get("duration_s"),
                                                 "position": (t or {}).get("position"), "chars": (t or {}).get("chars"), "paragraphs": (t or {}).get("paragraphs")}, ensure_ascii=False) + "\n")
        for uln, day_rows in by_day.items():
            sw(spd / f"nap{uln}.html", build_day_page(inp, uln, day_rows, texts_map))
        meta, shards = speech_search_index(inp)
        idx_dir = spd / "idx"; idx_dir.mkdir(parents=True, exist_ok=True)
        iw = _Writer()
        for key, toks in shards.items():
            iw(idx_dir / f"{key}.json", json.dumps(toks, ensure_ascii=False, separators=(",", ":")))
        prune(idx_dir, iw.written, (".json",))
        sw(spd / "meta.json", json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
        sw(spd / "kereses.html", build_speech_search_page(inp, len(meta), sum(1 for r in subs if speech_id(r) in texts_map)))
        prune(spd, sw.written)
        kmd = out_dir / "kepviselom"; kmd.mkdir(parents=True, exist_ok=True)
        (kmd / "index.html").write_text(build_kepviselom_page(inp), encoding="utf-8")
        by_mp_events: dict[str, list[dict]] = {}
        for e in events:
            for d in e["dissents"]:
                if d["azon"]:
                    by_mp_events.setdefault(d["azon"], []).append({"vote": e["vote"], "dissents": [d]})
        md = out_dir / "kepviselo"
        md.mkdir(parents=True, exist_ok=True)
        w(md / "index.html", build_mp_index(inp))
        for azon in inp["mps"]:
            w(md / f"{azon}.html", build_mp_page(inp, azon))
            j, c = mp_exports(inp, azon)
            w(md / f"{azon}.json", j)
            w(md / f"{azon}.csv", c)
            w(md / f"{azon}.xml", fd.dissent_feed(cycle, by_mp_events.get(azon, []), updated, root, cdir, f"kepviselo/{azon}.xml", f"kepviselo/{azon}.html",
                                                  mp={"azon": azon, "name": inp["mps"][azon]["name"]}))
            w(md / f"{azon}-heti.xml", mp_weekly_feed(inp, azon, updated, root))
            k += 1
        prune(md, w.written)
        ad = out_dir / "adatok"
        ad.mkdir(parents=True, exist_ok=True)
        (ad / "kulonvelemenyek.csv").write_text(ex.dissents_csv(all_events, cycle), encoding="utf-8")
        (ad / "felszolalasok.csv").write_text(ex.speeches_csv((inp["speeches"] or {}).get("speeches") or [], cycle), encoding="utf-8")
        (ad / "heti.csv").write_text(ex.weekly_csv(sitting_weeks(inp), week_digests(inp), inp["mps"], cycle), encoding="utf-8")
        j, c = ex.directory_export(inp["idx"]["votes"], cycle)
        (ad / "szavazasok.json").write_text(j, encoding="utf-8"); (ad / "szavazasok.csv").write_text(c, encoding="utf-8")
        align_by_vote = {}
        for azon, rec in inp["alignment"]["per_mp"].items():
            for v in rec["votes"]:
                align_by_vote.setdefault(v["ts"], {})[azon] = v.get("align")
        (ad / "nevsorok.csv").write_text(ex.positions_csv(inp["store"], inp["order"], align_by_vote), encoding="utf-8")
        j, c = ex.mps_export(inp["mps"], inp["alignment"]["per_mp"], cycle)
        (ad / "kepviselok.json").write_text(j, encoding="utf-8"); (ad / "kepviselok.csv").write_text(c, encoding="utf-8")
        if not inp["closed"]:
            j, c = ex.motions_export(inp["mps"], cycle)
            (ad / "inditvanyok.json").write_text(j, encoding="utf-8"); (ad / "inditvanyok.csv").write_text(c, encoding="utf-8")
        co = an.cohesion(inp)
        cl = an.close_votes(inp, co["plurality"])
        kd = out_dir / "kohezio"; kd.mkdir(parents=True, exist_ok=True)
        (kd / "index.html").write_text(build_cohesion_page(inp, co), encoding="utf-8")
        (kd / "frakciok.csv").write_text(ex.cohesion_factions_csv(co, cycle), encoding="utf-8")
        (kd / "frakcio_parok.csv").write_text(ex.faction_pairs_csv(co, cycle), encoding="utf-8")
        (kd / "kepviselo_parok.csv").write_text(ex.mp_pairs_csv(co, inp["mps"], cycle), encoding="utf-8")
        (kd / "szavazasonkent.csv").write_text(ex.cohesion_votes_csv(co, cycle), encoding="utf-8")
        sd = out_dir / "szoros"; sd.mkdir(parents=True, exist_ok=True)
        (sd / "index.html").write_text(build_close_page(inp, cl), encoding="utf-8")
        (sd / "dontesek.csv").write_text(ex.decisions_csv(cl, cycle), encoding="utf-8")
        ms = an.monthly(inp, co)["months"]
        nd = out_dir / "szamok"; nd.mkdir(parents=True, exist_ok=True)
        (nd / "index.html").write_text(build_numbers_page(inp, ms), encoding="utf-8")
        (nd / "havonta.csv").write_text(ex.monthly_csv(ms, cycle), encoding="utf-8")
        bd = out_dir / "iromany"; bd.mkdir(parents=True, exist_ok=True)
        w(bd / "index.html", build_bill_index(inp, bs))
        for bnum, b in bs.items():
            w(bd / f"{bnum}.html", build_bill_page(inp, b))
        w(bd / "iromanyok.csv", ex.bills_csv(bs, cycle))
        prune(bd, w.written)
        (ad / "index.html").write_text(build_data_page(inp, out_dir), encoding="utf-8")   # last: it lists the others with sizes
    search_items = ([{"k": "iromany", "c": cycle, "t": b["label"], "s": (b["title"] or "")[:140], "u": f"{cycle_dir(cycle)}iromany/{b['number']}.html", "d": b["first"], "n": len(b["votes"])} for b in bs.values()]
                    + [{"k": "kepviselo", "c": cycle, "t": mp["name"], "s": f'{mp.get("faction") or "—"} · {mandate_text(mp)}', "u": f"{cycle_dir(cycle)}kepviselo/{azon}.html"} for azon, mp in inp["mps"].items()]) if not index_only else []
    per_mp = {azon: {"cycle": cycle, "name": mp["name"], "faction": mp.get("faction"), "faction_first": mp.get("faction_first"),
                     "mandate": mandate_text(mp), "mandate_from": mp.get("mandate_from"), "mandate_to": mp.get("mandate_to"),
                     "current": mp.get("current"), "wikidata_qid": mp.get("wikidata_qid"), "parlament_url": mp.get("parlament_url"),
                     "factions": mp.get("factions") or [], "elections": mp.get("elections") or [], "motion_stats": mp.get("motion_stats") or [],
                     "in_roll": (inp["alignment"]["per_mp"].get(azon) or {}).get("in_roll", 0), "cast": (inp["alignment"]["per_mp"].get(azon) or {}).get("cast", 0),
                     "with": (inp["alignment"]["per_mp"].get(azon) or {}).get("with", 0), "against": (inp["alignment"]["per_mp"].get(azon) or {}).get("against", 0),
                     "href": f"{cycle_dir(cycle)}kepviselo/{azon}.html"} for azon, mp in inp["mps"].items()}
    return {"cycle": cycle, "index": str(out_dir / "index.html"), "vote_pages": n, "mp_pages": k, "per_mp": per_mp, "search": search_items}


def build_search_page(inp: dict, n_items: int) -> str:
    """kereses/index.html — one search box over every loaded cycle's bills, MPs and people; the index is a static
    JSON beside the page, loaded on the first keystroke; accent-insensitive."""
    cyc_buttons = '<button type="button" data-sc="all" class="on" aria-pressed="true">minden ciklus</button>' + "".join(f'<button type="button" data-sc="{c}" aria-pressed="false">{c}</button>' for c in inp["cycles"])
    return page_head("Keresés · karzat", "Keresés az összes betöltött ciklus irományai, képviselői és személyei között; ékezetek nélkül is.", 1) + \
        topbar(inp, [("keresés", None)], 1) + f"""
<div class="hero-h"><h1>Keresés</h1><small class="label" data-kz-text>{hu_num(n_items)} tétel · irományok, képviselők, pályaképek · minden betöltött ciklus</small></div>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Keresés</span><span class="tag">ékezet nélkül is · szám, cím, név</span></h2>
  <div class="filters"><input id="sq" type="search" placeholder="pl. T/51 · alaptörvény · Ágh" aria-label="Keresés" autofocus style="min-width:min(100%,420px)"><span class="n" id="sn" aria-live="polite"></span></div>
  <div class="filters" role="group" aria-label="Szűrés fajta szerint"><button type="button" data-sk="all" class="on" aria-pressed="true">mind</button><button type="button" data-sk="iromany" aria-pressed="false">irományok</button><button type="button" data-sk="kepviselo" aria-pressed="false">képviselők</button><button type="button" data-sk="szemely" aria-pressed="false">pályaképek</button></div>
  <div class="filters" role="group" aria-label="Szűrés ciklus szerint">{cyc_buttons}</div>
  <div class="tablewrap"><table><thead><tr><th scope="col">Találat</th><th scope="col">Mi</th><th scope="col">Ciklus</th></tr></thead><tbody id="sres"><tr><td colspan="3" class="hero-meta">Kezdj el gépelni.</td></tr></tbody></table></div>
  <noscript><div class="hero-meta prose" style="margin-top:8px">A kereső JavaScriptet használ. Nélküle a listák: <a href="../iromany/index.html">irományok</a> · <a href="../kepviselo/index.html">képviselők</a> · <a href="../szemely/index.html">személyek</a>.</div></noscript>
  <div class="hero-meta prose" style="margin-top:8px">A találatok listája a gépeléskor töltődik be (<span class="mono">kereses/index.json</span>); szavazások közvetlenül a ciklus címlapján kereshetők, tárgy szerint; a felszólalások szövegében a ciklus <a href="../felszolalas/kereses.html">felszólalás-keresője</a>.</div>
</section>
""" + page_tail(inp, 1)


def build_method_page(inp: dict) -> str:
    """modszer/index.html — how every number on the site is computed, generated from the code's own tables."""
    api_by_rule: dict[str, list[str]] = {}
    for label, rule in PAYLOAD_RULES.items():
        api_by_rule.setdefault(rule.value, []).append(label)
    rule_rows = "".join(
        f'<tr><td class="mono">{esc(r.value)}</td><td>{esc(info.label_hu)}</td><td class="mono">{esc(info.formula)}</td><td>{"jelenlévők" if info.base == "present" else "összes képviselő" if info.base == "seats" else "jelöltek" if info.base == "candidates" else esc(info.base)}</td>'
        f'<td class="mono">{esc(" · ".join(api_by_rule.get(r.value, [])))}</td></tr>' for r, info in RULES.items())
    tot = site_totals()
    return page_head("Módszer · karzat", "Hogyan számol az oldal: forrás, azonosítás, többségi szabályok, jelenlét, frakcióval és ellene, kohézió, szoros szavazások, ülésrend, frissesség, letöltések, adatbázis.", 1) + \
        topbar(inp, [("módszer", None)], 1) + f"""
<div class="hero-h"><h1>Módszer</h1><small class="label" data-kz-text>minden szám, ami az oldalon áll — honnan és hogyan</small></div>
<p class="lede">Ez az oldal a kódból készül: a szabályok táblája ugyanaz, amivel az oldal számol. Ahol valami feltevés, oda az van írva.</p>

<section class="panel" id="forras">{CORNERS}<h2><span data-kz-text>Forrás</span></h2>
<div class="hero-meta prose">Az Országgyűlés nyilvános Web API-ja (parlament.hu, XML): a szavazások listája ciklusonként és a szavazások név szerinti listája (<span class="mono">szavazasok</span>, <span class="mono">szavazas</span>), a képviselők adatlapjai (<span class="mono">kepviselo</span>: frakciótörténet, választások, ülőhely — az utóbbi csak a jelenlegi ciklusra), az ülésnapok (<span class="mono">ulesnap</span>, 1998-tól), a jelenlegi ciklus irományai (<span class="mono">iromanyok</span>). Az ülésterem alaprajza a parlament.hu saját képviselő-oldalainak ülésrend-lekérdezéséből, {hu_num(274)} hellyel. A képviselők névsora 1990 óta a parlament.hu képviselőlistájából. Ami itt van: {hu_num(tot["votes"])} szavazás, {hu_num(tot["roll_calls"])} név szerinti lista, {hu_num(tot["people"])} képviselő, {len(tot["cycles"])} ciklus.</div></section>

<section class="panel" id="azonositas">{CORNERS}<h2><span data-kz-text>Ki kicsoda</span></h2>
<div class="hero-meta prose">A névsor „Név (Frakció)” alakban ír, azonosító nélkül. A feloldás sorrendje: (1) a jelenlegi képviselőlista pontos „Név (Frakció)” címkéje; (2) a képviselői adatlapok saját névírása (a Dr., a második keresztnév, az ékezet ott úgy szerepel, ahogy a névsorban); (3) a puszta név minden forrásból (adatlapok, Wikidata címkék). Ha többen jönnek szóba, az marad, akinek az adatlapja szerinti frakciótörténete az adott ciklusban a névsor frakcióját mutatja; aki így sem egyértelmű, feloldatlan marad, névvel. Egy ellenőrzés minden ciklusra: minden feloldott személynek van az adatlapján sora arra a ciklusra, a névsor frakciójával. A Wikidata P4966 azonosítója az API <span class="mono">p_azon</span>-ja.</div></section>

<section class="panel" id="tobbseg">{CORNERS}<h2><span data-kz-text>Többségi szabály és jelenlét</span></h2>
<div class="tablewrap" style="border:0"><table><thead><tr><th scope="col">kód</th><th scope="col">szabály</th><th scope="col">küszöb</th><th scope="col">alap</th><th scope="col">az API „Szavazási mód” szövege</th></tr></thead><tbody>{rule_rows}</tbody></table></div>
<div class="hero-meta prose" style="margin-top:8px">A szabályt minden szavazásnál az Országgyűlés maga jelöli meg a „Szavazási mód” mezőben; az oldal ebből számolja a küszöböt és összeveti az „Elfogadás” mezővel („számítás egyezik / eltér”). Jelenlévő: aki igennel, nemmel, tartózkodással szavazott, vagy jelen volt és nem szavazott — a névsorból; ahol nincs névsor, az API „Összes szavazat” mezője. Ez értelmezés; a jogszabályi hivatkozások a kódban ellenőrizendőnek jelölve. Az összes képviselő 2014. május 6-a előtt 386, azóta 199.</div></section>

<section class="panel" id="frakcio">{CORNERS}<h2><span data-kz-text>Frakciójával és ellene</span></h2>
<div class="hero-meta prose">Egy szavazáson egy frakció többségi álláspontja a leadott szavazatai (igen, nem, tartózkodott) közül a legtöbb; ha kettő egyenlő, nincs többségi álláspont, és senki sem „vele” vagy „ellene” azon a szavazáson. Egy képviselő „frakciójával”: leadott szavazata megegyezik a frakciója többségi álláspontjával; „ellene”: eltér. A le nem adott állapotok (jelen, nem szavazott; nem szavazott; előre bejelentett hiányzó; igazoltan távol) részvétel, nem egyezés. A frakció az, amit a névsor az adott szavazásnál ír; a személy oldalán az utolsó név szerinti szavazásán feltüntetett frakció. A „független” nem frakció: a független képviselők együtt számolt száma nem összetartás.</div></section>

<section class="panel" id="kohezio">{CORNERS}<h2><span data-kz-text>Kohézió</span></h2>
<div class="hero-meta prose">Rice-index: |igen − nem| / (igen + nem). Egyetértési index (Hix–Noury–Roland): (legnagyobb − (összes − legnagyobb)/2) / összes, az igen–nem–tartózkodott leadott szavazatokból. Ciklusra a leadott szavazatokkal súlyozott átlag. Frakciók egyezése: azon szavazások hányada, ahol mindkettő adott le szavazatot és a többségi álláspontjuk azonos. Képviselőpárok: közös leadott szavazásaikból az azonos szavazatok hányada, legalább 20 közös szavazásnál.</div></section>

<section class="panel" id="szoros">{CORNERS}<h2><span data-kz-text>Szoros szavazások</span></h2>
<div class="hero-meta prose">Különbség = igen − szükséges. „Több igen, mint nem — mégis elutasítva”: minősített többséget kívánó döntés, ahol igen &gt; nem és a küszöb nem teljesült. „Ahol a hiányzók döntöttek”: számított feltevés — ha a névsorban szereplő, szavazatot le nem adó képviselők mind a frakciójuk többségével szavaztak volna, más lenne-e a kimenetel; jelenléthez kötött küszöbnél a jelenlét is nő velük. Feltevés, nem tény, és így is van feliratozva.</div></section>

<section class="panel" id="oldalak">{CORNERS}<h2><span data-kz-text>Az oldalak</span></h2>
<div class="hero-meta prose">A ciklus címlapján a kiemelt szavazás a ciklus utolsó, összes képviselő kétharmadával elfogadott döntése (a 43. és 42. ciklusban kézzel választva: a tizenhatodik és a tizenötödik Alaptörvény-módosítás). Az ülésrend a 43. ciklusra a parlament.hu alaprajza; a korábbi ciklusokra nincs ülőhely-adat, ott a szavazás-ábra frakciónként rendezett. A szavazás oldalán az ülőhely-vizsgáló „utolsó 20” csíkja a képviselő addig tartott utolsó húsz név szerinti szavazása: frakciójával (szín), ellene (fehér), többség nélkül (szürke), nem adott le (üres). Egy iromány oldala a szavazásait gyűjti a szám szerint: az „n/k” alak az n-es irományhoz tartozik; hogy a javaslatról magáról vagy egy módosítóról szólt-e, a kimenetel szövege mondja meg („önálló indítvány…”, „módosító…”). A frissesség mondata a legutóbbi ülésnaphoz méri a betöltött szavazásokat, és a szinkron időpontját írja, nem eltelt időt.</div></section>

<section class="panel" id="adat">{CORNERS}<h2><span data-kz-text>Letöltések és adatbázis</span></h2>
<div class="hero-meta prose">Minden tábla CSV-ben (UTF-8, BOM, vessző) és JSON-ban a ciklus <span class="mono">adatok/</span> oldalán, az adatszótárral; egy szavazás és egy képviselő oldala mellett a saját párja. Az egész egy SQLite-adatbázisba is betölthető a tárból (<span class="mono">schema.sql</span>: mp, mp_faction, mp_mandate, sitting_day, vote, vote_position, vote_faction_tally, vote_faction_majority, bill, bill_event, vote_motion; nézetek: v_vote, v_mp_alignment) — <span class="mono">python3 -m karzat load --since 1990-01-01</span>, aztán például:</div>
<pre class="mono" style="font-size:11px;color:var(--dim);background:rgba(0,0,0,.35);border:1px solid var(--border);padding:8px 10px;white-space:pre-wrap">-- a legszorosabb elfogadott döntések
SELECT ts, igen, needed, margin FROM vote WHERE passed = 1 ORDER BY margin, ts LIMIT 20;

-- egy képviselő frakció elleni szavazatai
SELECT p.vote_ts, p.position, m.majority_position
FROM vote_position p JOIN vote_faction_majority m ON m.vote_ts = p.vote_ts AND m.faction_id = p.faction_id
WHERE p.mp_azon = 'a011' AND p.position IN ('igen','nem','tartozkodott') AND m.majority_position IS NOT NULL AND p.position &lt;&gt; m.majority_position;

-- frakciónkénti egyezés a saját többséggel, ciklusonként
SELECT v.ckl, p.faction_id, SUM(p.position = m.majority_position) * 1.0 / COUNT(*) AS egyezes
FROM vote_position p JOIN vote v ON v.ts = p.vote_ts JOIN vote_faction_majority m ON m.vote_ts = p.vote_ts AND m.faction_id = p.faction_id
WHERE p.position IN ('igen','nem','tartozkodott') AND m.majority_position IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 3;</pre></section>

<section class="panel" id="felszolalas">{CORNERS}<h2><span data-kz-text>Felszólalások és a heti összefoglaló</span></h2>
<div class="hero-meta prose">Az ülésnapok felszólalás-listái (<span class="mono">felszolalasok</span>, 1998-tól) soronként: napirendi pont, felszólaló („Név (Frakció)”, ugyanazzal a feloldással, mint a névsorok; aki nem képviselő, névvel és tisztséggel marad), fajta, szerep, hossz. Érdemi felszólalás: a tárgyhoz szólás (felszólalás, kétperces, vezérszónoki, napirend előtti és utáni és hozzászólásaik), kérdés, interpelláció, azonnali kérdés és a válaszok, viszonválaszok, elfogadás/elutasítás, előadói válasz, előterjesztői nyitóbeszéd, bizottsági vélemény és kisebbségi vélemény, ügyrendi javaslat és kérdés; minden más eljárási (ülésvezetés, jegyzői ismertetés, megnyitás, bezárás, napirend elfogadása, eskü, személyes érintettség, az elnök eredmény- és állapotsorai). Ellenőrzés: a képviselői adatlap saját ciklusonkénti „felszólalás” száma ugyanezzel a határral a 43. ciklus képviselőinek túlnyomó többségénél megegyezik az itteni érdemi számmal (a felszólalások oldala írja, hány képviselőnél; ahol nem, a képviselő oldala mindkét számot). A heti összefoglaló: naptári hét ülésnapokkal; névsorban / a hét név szerinti szavazásai, leadott szavazatok, a frakció többségével szemben leadottak (döntetlennél nincs többség, jelenlét-megállapítás nem számít), érdemi felszólalások. Csatornái: képviselőnként <span class="mono">kepviselo/&lt;azonosító&gt;-heti.xml</span>, a ciklusé <span class="mono">feed/heti.xml</span>; a számok <span class="mono">adatok/heti.csv</span>-ben.</div></section>

<section class="panel" id="szoveg">{CORNERS}<h2><span data-kz-text>A felszólalások szövege és a keresés</span></h2>
<div class="hero-meta prose">Az érdemi felszólalások szövege az API <span class="mono">felszolalas</span> szolgáltatásából, felszólalásonként egy hívással; a szöveg a jegyzőkönyvé, bekezdésekre bontva, formázás nélkül, összefoglalás nélkül — egy lap és egy JSON felszólalásonként (<span class="mono">felszolalas/&lt;ülésnap&gt;-&lt;sorszám&gt;</span>), egy lap naponként. A felszólaló azonosítója a szöveg-adat saját hivatkozásából jön, ahol van (a képviselő oldalára mutat); a napi lista névfeloldása ugyanazt adja (a két forrás egyetlen esetben sem tér el). A kereső előtag szerint illeszt (legalább három betű, ékezet nélkül is): az index a szavak első két betűje szerint darabolt (<span class="mono">felszolalas/idx/</span>), a találat a szöveg részlete a jegyzőkönyvből; szótövezés, szinonima, témabesorolás nincs. A kulcsszó-figyelés csatornája <span class="mono">feed/felszolalasok.xml</span>: a friss felszólalások teljes szövege, a szűrést a hírolvasó végzi.</div></section>

<section class="panel" id="csatornak">{CORNERS}<h2><span data-kz-text>Csatornák</span></h2>
<div class="hero-meta prose">Atom-csatornák minden ciklusban (<span class="mono">feed/</span>): a különvélemények ciklusra (<span class="mono">kulonvelemeny.xml</span>), frakciónként (<span class="mono">frakcio-&lt;név&gt;.xml</span>) és képviselőnként (<span class="mono">kepviselo/&lt;azonosító&gt;.xml</span>), és minden szavazás (<span class="mono">szavazasok.xml</span>). Egy tétel egy szavazás; a benne álló különvélemények ugyanabból a számításból jönnek, mint a képviselőoldalak „frakció ellen” oszlopa (döntetlen = nincs többség; jelenlét-megállapítás nem számít). A tétel ideje a szavazásé, a csatorna frissítése a szinkroné — soha nem az építés pillanata, így ugyanabból a tárból ugyanaz a fájl épül. Egy csatornában az utolsó {fd.MAX_ENTRIES} tétel; a teljes lista <span class="mono">adatok/kulonvelemenyek.csv</span>. Értesítés az, hogy az oldal újraépül és a hírolvasó újraolvassa: nincs szerver, nincs fiók.</div></section>

<section class="panel" id="nem">{CORNERS}<h2><span data-kz-text>Amit az oldal nem állít</span></h2>
<div class="hero-meta prose">Semmit a szavazások okáról. Nem skáláz, nem súlyoz, nem jósol; egyetlen feltevését (a hiányzók) annak nevezi. A színek az oldal jelölései. A képviselői fényképeket nem ágyazza be, csak hivatkozza, mert a felhasználási feltételük nem tisztázott.</div></section>
{cite_html(inp, 'modszer/index.html', 'Módszer', 'modszer')}
""" + page_tail(inp, 1)


def build_person_page(inp: dict, azon: str, stints: list[dict]) -> str:
    """szemely/<azon>.html — one person across every loaded cycle: the cycle rows side by side, then the record."""
    stints = sorted(stints, key=lambda r: -r["cycle"])
    latest = stints[0]
    name = latest["name"]
    facs = inp["facs_all"]
    def col(f): return facs.get(f or "", "#8a8a8a")
    rows = []
    for st in stints:
        part = f'{100 * st["cast"] / st["in_roll"]:.0f}%' if st["in_roll"] else "—"
        agree = f'{100 * st["with"] / (st["with"] + st["against"]):.0f}%' if (st["with"] + st["against"]) else "—"
        switched = f' <span class="sub">korábban {esc(st["faction_first"])}</span>' if st.get("faction_first") and st["faction_first"] != st["faction"] else ""
        rows.append(f'<tr><td class="mono"><a href="../{esc(st["href"])}">{st["cycle"]}. ciklus</a><span class="sub">{esc(CYCLE_SPAN.get(st["cycle"], ""))}</span></td>'
                    f'<td><span class="pos"><i class="d" style="--c:{col(st["faction"])}"></i>{esc(st["faction"] or "—")}</span>{switched}</td>'
                    f'<td>{esc(st["mandate"])}<span class="sub">{esc(st.get("mandate_from") or "")} – {esc(st.get("mandate_to") or "")}</span></td>'
                    f'<td class="num mono">{st["in_roll"]}</td><td class="num mono">{st["cast"]}<span class="sub">{part}</span></td>'
                    f'<td class="num mono">{st["with"]}</td><td class="num mono">{st["against"]}<span class="sub">{agree} egyezés</span></td></tr>')
    total_roll = sum(st["in_roll"] for st in stints); total_cast = sum(st["cast"] for st in stints)
    total_with = sum(st["with"] for st in stints); total_against = sum(st["against"] for st in stints)
    fac_hist = "".join(f'<tr><td class="mono">{esc(f["ciklus"])}</td><td>{esc(f["faction"])}</td><td class="mono">{esc(f["from"] or "")} – {esc(f["to"] or "")}</td></tr>' for f in latest["factions"])
    elections = "".join(f'<tr><td class="mono">{esc(e["ciklus"])}</td><td>{esc(e["constituency"] or "—")}</td><td class="mono">{esc(e["elected_on"] or "")}</td><td class="mono">{esc(e["mandate_from"] or "")} – {esc(e["mandate_to"] or "")}</td></tr>' for e in latest["elections"])
    motions = "".join(f'<tr><td class="mono">{esc(m["ciklus"])}</td><td class="num mono">{esc(m["onallo"])}</td><td class="num mono">{esc(m["nem_onallo"])}</td></tr>' for m in latest["motion_stats"])
    links = " · ".join(x for x in [
        f'<a href="{esc(latest["parlament_url"])}" target="_blank" rel="noopener">parlament.hu adatlap ↗</a>' if latest.get("parlament_url") else "",
        f'<a href="https://www.wikidata.org/wiki/{esc(latest["wikidata_qid"])}" target="_blank" rel="noopener">Wikidata {esc(latest["wikidata_qid"])} ↗</a>' if latest.get("wikidata_qid") else ""] if x)
    loaded = ", ".join(f"{st['cycle']}." for st in stints)
    return page_head(f'{name} — pályakép · karzat', f'{name} az Országgyűlésben: ciklusonként a részvétele és a frakciójával való egyezése, a képviselői adatlap frakció- és mandátumtörténete.', 1) + \
        topbar(inp, [("személyek", "index.html"), (name, None)], 1) + f"""
<div class="hero-h"><h1>{esc(name)}</h1><small class="label" data-kz-text>pályakép · {len(stints)} betöltött ciklus{" · ma is képviselő" if latest.get("current") else ""}</small></div>
<p class="hero-meta">{links}</p>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Ciklusonként</span><span class="tag">{hu_num(total_roll)} névsorban · leadott {hu_num(total_cast)} · frakciójával {hu_num(total_with)} · ellene {hu_num(total_against)}</span></h2>
  <div class="tablewrap"><table><thead><tr><th scope="col">Ciklus</th><th scope="col">Frakció</th><th scope="col">Mandátum</th><th scope="col" class="num">Névsor</th><th scope="col" class="num">Leadott</th><th scope="col" class="num">Frakciójával</th><th scope="col" class="num">Ellene</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>
  <div class="hero-meta prose" style="margin-top:8px">A betöltött ciklusok ({loaded}) számai; a ciklus oldalán minden szavazása külön. „Frakciójával”: a frakciója leadott szavazatainak többségével egyezően.</div>
</section>
<section class="grid">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Frakciótagság és mandátum</span></h2>
    <div class="tablewrap" style="border:0"><table><thead><tr><th>Ciklus</th><th>Frakció</th><th>Időszak</th></tr></thead><tbody>{fac_hist or '<tr><td colspan="3">—</td></tr>'}</tbody></table></div>
    <div class="tablewrap" style="border:0;margin-top:10px"><table><thead><tr><th>Ciklus</th><th>Választókerület / lista</th><th>Választás</th><th>Mandátum</th></tr></thead><tbody>{elections or '<tr><td colspan="4">—</td></tr>'}</tbody></table></div>
  </section>
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Benyújtott indítványok</span></h2>
    <div class="tablewrap" style="border:0"><table><thead><tr><th>Ciklus</th><th class="num">önálló</th><th class="num">nem önálló</th></tr></thead><tbody>{motions or '<tr><td colspan="3">—</td></tr>'}</tbody></table></div>
  </section>
</section>
{cite_html(inp, f'szemely/{azon}.html', f'{name} — pályakép', f'szemely-{azon}')}
""" + page_tail(inp, 1)


def build_person_index(inp: dict, people: dict[str, list[dict]]) -> str:
    facs = inp["facs_all"]
    trs = []
    for azon, stints in sorted(people.items(), key=lambda kv: name_key(kv[1][0]["name"])):
        stints = sorted(stints, key=lambda r: -r["cycle"])
        latest = stints[0]
        cycles_txt = " · ".join(str(st["cycle"]) for st in reversed(stints))
        roll = sum(st["in_roll"] for st in stints); cast = sum(st["cast"] for st in stints); ag = sum(st["against"] for st in stints)
        trs.append(f'<tr data-f="{esc(latest["faction"] or "")}" data-name="{esc(name_key(latest["name"]))}" data-n="{len(stints)}" data-against="{ag}"><td><a href="{esc(azon)}.html">{esc(latest["name"])}</a></td>'
                   f'<td><span class="pos"><i class="d" style="--c:{facs.get(latest["faction"] or "", "#8a8a8a")}"></i>{esc(latest["faction"] or "—")}</span></td>'
                   f'<td class="mono">{cycles_txt}</td><td class="num mono">{len(stints)}</td><td class="num mono">{cast} / {roll}</td><td class="num mono">{ag}</td></tr>')
    return page_head("Személyek · karzat", "Mindenki, aki a betöltött ciklusok névsoraiban szerepel — egy pályakép-oldal személyenként, ciklusokon át.", 1) + \
        topbar(inp, [("személyek", None)], 1) + f"""
<div class="hero-h"><h1>Személyek</h1><small class="label" data-kz-text>{len(people)} képviselő a betöltött ciklusok névsoraiból · egy oldal személyenként, ciklusokon át</small></div>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Pályaképek</span><span class="tag">a fejlécre kattintva rendezhető</span></h2>
  <div class="filters"><input type="search" data-filter-table="roll" placeholder="név" aria-label="Szűrés névre" style="min-width:160px"><span class="n" id="rn" aria-live="polite"></span></div>
  <div class="tablewrap"><table id="roll" data-page-size="25" data-counter="rn"><thead><tr><th scope="col" class="sortable" data-key="name">Név</th><th scope="col" class="sortable" data-key="f">Utolsó frakció</th><th scope="col">Ciklusok</th><th scope="col" class="sortable num" data-key="n">Ciklus</th><th scope="col" class="num">Leadott / névsorban</th><th scope="col" class="sortable num" data-key="against">Frakció ellen</th></tr></thead><tbody>{"".join(trs)}</tbody></table></div>
</section>
""" + page_tail(inp, 1)


def build_all(out_dir: Path, index_only: bool = False, cycles: list[int] | None = None) -> dict:
    """Every cycle with derived inputs: the current one at out_dir, earlier ones under out_dir/ckl<N>/; then one
    career page per person under out_dir/szemely/, assembled from every cycle's roster."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "assets").mkdir(parents=True, exist_ok=True)
    for name, body in build_assets().items():
        (out_dir / "assets" / name).write_text(body, encoding="utf-8")
    res = []
    for cycle in (cycles or available_cycles()):
        res.append(build_cycle(out_dir / cycle_dir(cycle), cycle, index_only=index_only))
    people_n = 0
    # the cross-cycle pages (careers, search index, method) are assembled from every cycle's build: a partial
    # `--cycle N` run leaves them alone rather than shrinking them to one cycle
    full = not cycles or sorted(cycles) == sorted(available_cycles())
    if not index_only and full:
        people: dict[str, list[dict]] = {}
        for r in res:
            for azon, st in r["per_mp"].items():
                people.setdefault(azon, []).append(st)
        inp = load_inputs(CURRENT_CYCLE)
        inp["facs_all"] = {}
        for c in available_cycles():
            for f in factions(c):
                inp["facs_all"].setdefault(f["id"], f["colour"])
        md_ = out_dir / "modszer"; md_.mkdir(parents=True, exist_ok=True)
        (md_ / "index.html").write_text(build_method_page(inp), encoding="utf-8")
        items = [it for r in res for it in r["search"]]
        items += [{"k": "szemely", "c": 0, "t": stints[0]["name"], "s": f'pályakép · {", ".join(str(st["cycle"]) for st in sorted(stints, key=lambda r: r["cycle"]))}. ciklus', "u": f"szemely/{azon}.html"} for azon, stints in people.items()]
        sd_ = out_dir / "kereses"; sd_.mkdir(parents=True, exist_ok=True)
        (sd_ / "index.json").write_text(json.dumps(items, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        (sd_ / "index.html").write_text(build_search_page(inp, len(items)), encoding="utf-8")
        pd = out_dir / "szemely"
        pd.mkdir(parents=True, exist_ok=True)
        pw = _Writer()
        pw(pd / "index.html", build_person_index(inp, people))
        for azon, stints in people.items():
            pw(pd / f"{azon}.html", build_person_page(inp, azon, stints))
            people_n += 1
        prune(pd, pw.written)
    return {"index": res[0]["index"], "vote_pages": sum(r["vote_pages"] for r in res), "mp_pages": sum(r["mp_pages"] for r in res),
            "people": people_n, "cycles": res, "partial": not full}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="compare a fresh build of site/index.html and site/assets/* with the committed files; exit 1 on difference")
    ap.add_argument("--index-only", action="store_true", help="do not (re)generate the per-vote pages")
    ap.add_argument("--out", type=Path, default=SITE_DIR)
    ap.add_argument("--cycle", type=int, action="append", help="build only this cycle (repeatable); default: every cycle with derived inputs")
    args = ap.parse_args(argv)
    if args.check:
        page = build()
        current = SITE.read_text(encoding="utf-8") if SITE.exists() else ""
        stale = [str(SITE)] if current != page else []
        for name, body in build_assets().items():
            f = ASSETS_DIR / name
            if not f.exists() or f.read_text(encoding="utf-8") != body:
                stale.append(str(f))
        if stale:
            print("stale: " + ", ".join(stale) + " — rebuild with `python3 -m scripts.build_site`", file=sys.stderr)
            return 1
        print(f"ok: {SITE} and site/assets/* match a fresh build ({len(page):,} bytes index)")
        return 0
    res = build_all(args.out, index_only=args.index_only, cycles=args.cycle)
    for r in res["cycles"]:
        print(f"cycle {r['cycle']}: {r['index']} + {r['vote_pages']} vote pages + {r['mp_pages']} MP pages (+ .json/.csv, adatok/)")
    if res.get("people"):
        print(f"szemely/: {res['people']} career pages")
    if res.get("partial") and not args.index_only:
        print("partial build: szemely/, kereses/ and modszer/ left as they were (they span every cycle)")
    if not SITE_URL and not args.index_only:
        print("note: KARZAT_SITE_URL is not set — feed links are relative to the feed file and citations resolve in the browser; set it for the published build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
