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

Outputs: site/index.html — the landing page: the chamber as it sits today (every seat the member who holds it), the
ten cycles since 1990, the last sitting day, the doors — and site/assets/karzat.css + karzat.js (committed, checked);
then one tree per cycle under site/ckl<N>/ (index, szavazas/<slug>.html × votes, kepviselo/<p_azon>.html × MPs,
felszolalas/, bizottsag/, feed/, adatok/ …) and the cross-cycle szemely/, kereses/, modszer/ (generated, git-ignored).
Every cycle lives one level down, the current one included: a vote's address never changes when the next cycle
starts. The look is
Konzol's console language (see reference/konzol/): dark ground, dot grid, corner-bracketed panels, mono labels,
a terminal footer whose log lines are real values, and a boot sequence that reduced-motion users never see.

Deterministic: no clock is read (the freshness sentence is computed at the derive timestamp
stored in the inputs). site/index.html is committed and guarded by --check + tests; every other
page is generated (git-ignored) and reproducible from the committed inputs.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
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
ARCHIVE_MAX_CYCLE = 41   # cycles up to this one are built light: 60 000 roll calls at full weight would be ~20 GB of pages
CYCLE_DIR: dict[int, str] = {}           # every cycle lives one level down, site/ckl<N>/ — the current one too, so a vote's
                                         # address never changes when the next cycle starts; the site root is the landing page
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


PHOTO_BASE = "https://www.parlament.hu/felicitas/api/query/resource/kepviseloexportok/kepviselo-exported-queries-provider/kepviselo-kepek/"


def portrait_html(mp: dict, cls: str = "") -> str:
    """A portrait, loaded from wherever it lives — not copied — lazily, without a referrer, in the site's
    black-and-white treatment, attributed in the title. parlament.hu for the members it photographs (the record's
    <fenykep> URL, 195×260); a Wikimedia Commons file with its author and licence for the House's non-MP members,
    whom parlament.hu does not photograph. Empty when there is no picture at all."""
    url = mp.get("photo_url")
    if not url:
        return ""
    klass = ("portrait " + cls).strip()
    credit = mp.get("photo_credit") or "fénykép: parlament.hu"
    return (f'<img class="{klass}" src="{esc(url)}" alt="{esc(mp.get("name") or "")}" width="195" height="260" loading="lazy" decoding="async" '
            f'referrerpolicy="no-referrer" title="{esc(credit)}" onerror="this.remove()">')


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
    per_cycle: dict[int, dict] = {}
    for c in cycles:
        sfx = "" if c == CURRENT_CYCLE else f"_ckl{c}"
        fl = load_json(DERIVED / f"first_light{sfx}.json")
        idx = load_json(DERIVED / f"votes_index{sfx}.json")
        votes += fl["votes"]
        # a roll call is a vote whose record names the members: before 1998 the API prints none (empty <nev_szerint>)
        n_rolls = sum(1 for v in idx["votes"] if (v.get("position_counts") or {}) and sum((v.get("position_counts") or {}).values()) > 0)
        rolls += n_rolls
        disagreements += len(idx.get("disagreements") or [])
        starts.append(fl["window"]["from"]); ends.append(fl["window"]["to"])
        mp_path = DERIVED / f"mps{sfx}.json"
        roster = load_json(mp_path)["mps"] if mp_path.exists() else {}
        people |= set(roster)
        per_cycle[c] = {"votes": fl["votes"], "roll_calls": n_rolls, "from": fl["window"]["from"], "to": fl["window"]["to"],
                        "people": len(roster), "sitting_days": (fl.get("sitting_days") or {}).get("count") or 0,
                        "disagreements": len(idx.get("disagreements") or []), "last_sync_at": idx.get("last_sync_at")}
    _TOTALS.update({"cycles": cycles, "from": min(starts), "to": max(ends), "votes": votes, "roll_calls": rolls,
                    "disagreements": disagreements, "people": len(people), "per_cycle": per_cycle})
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
    cm_path = DERIVED / "committees.json"
    committees = load_json(cm_path) if (cycle == CURRENT_CYCLE and cm_path.exists()) else None                     # the API's committees are present-tense
    sz_path = DERIVED / "szoszolok.json"
    szoszolok = load_json(sz_path) if (cycle == CURRENT_CYCLE and sz_path.exists()) else None                      # so is the spokesperson list
    km_path = DERIVED / "kormany.json"
    kormany = load_json(km_path) if (cycle == CURRENT_CYCLE and km_path.exists()) else None                        # the ministerial bench's non-MP members
    ir_path = DERIVED / "iromany_records.json"
    bill_recs = ((load_json(ir_path).get("records") or {}) if (cycle == CURRENT_CYCLE and ir_path.exists()) else {})  # motion records: current cycle only, izon is per-cycle
    inp = {"idx": idx, "store": store, "fl": fl, "plan": plan, "facs": facs, "mps": mps, "speeches": speeches, "texts": texts, "committees": committees, "szoszolok": szoszolok, "kormany": kormany, "bill_recs": bill_recs,
           "by_ts": {v["ts"]: v for v in idx["votes"]}, "order": [v["ts"] for v in idx["votes"]]}
    inp["alignment"] = compute_alignment(inp)
    inp["cycle"] = cycle
    inp["closed"] = cycle != CURRENT_CYCLE
    inp["archive"] = cycle <= ARCHIVE_MAX_CYCLE                              # light pages: the roll calls live in adatok/ and the database
    inp["base_depth"] = 1                                                    # every cycle's tree is one level below the site root
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

HOUSE_OFFICES = {"az Országgyűlés elnöke": "elnök", "az Országgyűlés alelnöke": "alelnök", "jegyző": "jegyző", "háznagy": "háznagy"}


def presidium(mps: dict, on_day: str | None = None) -> dict[str, list[dict]]:
    """Who presides, from the records' dated <tisztseg> rows: {'elnök': [mp], 'alelnök': [...], 'jegyző': [...]} —
    the offices in force on `on_day` (today when None). The Speaker's platform is not a seat in the plan: the Speaker
    and the deputy Speakers keep their seats on the floor and take the chair in turn; the clerks (jegyzők) sit by the
    chair in rotation. So the drawing marks the platform with the office-holders rather than moving anyone's seat."""
    out: dict[str, list[dict]] = {v: [] for v in HOUSE_OFFICES.values()}
    for azon, mp in mps.items():
        for o in mp.get("offices") or []:
            role = HOUSE_OFFICES.get(o.get("office") or "")
            if not role:
                continue
            frm, to = o.get("from"), o.get("to")
            if on_day:
                if (frm and frm > on_day) or (to and to < on_day):
                    continue
            elif to:
                continue                                       # today: only the offices still in force
            out[role].append({"azon": azon, "name": mp["name"], "faction": mp.get("faction"), "from": frm, "to": to})
    for lst in out.values():
        lst.sort(key=lambda m: name_key(m["name"]))
    return out


def podium_svg(pres: dict[str, list[dict]] | None, facs: list[dict], k: float = 1.0, width: float = 44.0) -> str:
    """The Speaker's platform with its office-holders — a dais the width of the aisle between the bench's arms, the
    Speaker's chair centred and forward on a marked place, the deputy Speakers in an open arc behind, each in their
    faction colour and each a real seat group (hover/click as on the floor; the member's floor seat lights with it).
    Without data, the bare platform. The dais is furniture: it takes the room's scale (`width`), not the glyph's;
    the dots take the floor seats' own size (`k`), so the same people carry the same mark. Nothing is moved — these
    members are drawn on the floor too; this says who may take the chair."""
    if not pres or not (pres.get("elnök") or pres.get("alelnök")):
        return PODIUM
    colour = {f["id"]: f["colour"] for f in facs}
    speaker = pres.get("elnök") or []
    deps = pres.get("alelnök") or []
    r = 2.8 * k
    W = width / 2
    H = width * 0.42
    parts = [f'<path d="M {-W:.1f} 3 L {-W:.1f} {-H * 0.6:.1f} Q 0 {-H:.1f} {W:.1f} {-H * 0.6:.1f} L {W:.1f} 3 Z" class="rostrum"/>',
             f'<path d="M {-W + 1.4:.1f} 3 L {-W + 1.4:.1f} {-H * 0.6 + 0.8:.1f} Q 0 {-H + 1.4:.1f} {W - 1.4:.1f} {-H * 0.6 + 0.8:.1f} L {W - 1.4:.1f} 3 Z" class="rostrum edge"/>']
    def dot(m, cx, cy, role):
        c = colour.get(m.get("faction") or "", "#8a8a8a")
        return (f'<g class="seat pres" role="button" data-az="{esc(m["azon"])}" data-f="{esc(m.get("faction") or "")}" data-role="{esc(role)}" aria-label="{esc(m["name"])} — az Országgyűlés {esc(role)}e">'
                f'<title>{esc(m["name"])} ({esc(m.get("faction") or "")}) — az Országgyűlés {esc(role)}e · az elnöki emelvény</title>'
                f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="{c}"/>'
                f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r * 1.8:.2f}" class="hit"/></g>')
    # the deputies: an open arc along the dais' back edge, left to right in the order the records list them
    n = len(deps)
    span = W * 1.42
    for i, m in enumerate(deps):
        t = (i / (n - 1) - 0.5) if n > 1 else 0.0                 # -0.5 … 0.5
        x = t * span
        y = -H * 0.50 - (H * 0.24) * (1 - (2 * t) ** 2)           # inside the dais, bowing up with its back edge
        parts.append(dot(m, x, y, "alelnök"))
    # the Speaker's chair: centred, forward of the arc, on a marked place
    for m in speaker:
        box = max(7.0, r * 3.4)
        parts.append(f'<rect x="{-box / 2:.2f}" y="{-H * 0.2 - box / 2:.2f}" width="{box:.2f}" height="{box:.2f}" rx="{box * 0.2:.2f}" class="rostrum hi"/>')
        parts.append(dot(m, 0, -H * 0.2, "elnök"))
    return "".join(parts)


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


def seat_svg_real(view: dict, facs: list[dict], plan: dict, align: dict | None = None, szoszolok: dict | None = None,
                  kormany: dict | None = None, pres: dict | None = None) -> tuple[str, dict]:
    """The reconstructed chamber: every voting MP at their (sector,row,seat). Returns (svg, info).

    `szoszolok`: (sector,row,seat) → the nationality spokesperson holding that seat. No roll call ever names them —
    they may not vote — so their seats stay outlines here, but the outline says whose seat it is instead of "üres"."""
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
    aisle = 44.0
    if outlines:
        # the platform sits in the mouth of the horseshoe: its width is the gap between the bench's two arms
        fb = [o for o in outlines if o["sector"] == geo.get("front_bench_sector")]
        if fb:
            aisle = max(26.0, (max(o["x"] for o in fb) - min(o["x"] for o in fb)) * 0.62)
        # parlament.hu's own floor plan: every seat's outline is the floor; empty seats stay outlines
        occupied = {(c["sector"], c["row"], c["seat"]) for c in coords.values()}
        sz = szoszolok or {}
        km = kormany or {}
        def _empty_title(o):
            key = (o["sector"], o["row"], o["seat"])
            if key in occupied:
                return ""
            place = "miniszteri pad" if o["sector"] == geo.get("front_bench_sector") else f'{o["sector"]}. szektor, {o["row"]}. sor'
            r = sz.get(key)
            if r:
                return f'<title>{html.escape(r["name"] or "")} — {html.escape(r["nationality"] or "")} nemzetiségi szószóló, nem szavaz · {html.escape(place)}, {o["seat"]}. szék</title>'
            g = km.get(key)
            if g:
                return f'<title>{html.escape(g["name"] or "")} — {html.escape(g.get("office") or "kormánytag")}, nem képviselő, nem szavaz · {html.escape(place)}, {o["seat"]}. szék</title>'
            return f'<title>üres hely · {html.escape(place)}, {o["seat"]}. szék</title>'
        empties = "".join(
            f'<polygon points="{o["points"]}" class="seatshape{" occ" if (o["sector"], o["row"], o["seat"]) in occupied else ""}">'
            + _empty_title(o) + '</polygon>' for o in outlines)
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
           + "".join(floor) + arc + podium_svg(pres, facs, k, aisle) + '<text x="0" y="9" class="seclabel s" text-anchor="middle">elnöki emelvény</text>'
           + "".join(labels) + empties + "".join(parts) + tray + '</svg>')
    return svg, {"placed": len(view["positions"]) - len(unplaced), "unplaced": len(unplaced), "seated_total": plan["seated"], "empty": len(plan.get("empty_seats") or []),
                 "seats_in_plan": len(outlines) if outlines else None}


def _seat_cell(cx: float, cy: float, angle: float, w: float, h: float) -> str:
    """One seat of the floor, drawn like the real plan's outline: a small rounded cell turned to face the platform."""
    deg = math.degrees(math.atan2(-math.cos(angle), -math.sin(angle)))
    return (f'<rect x="{-w / 2:.2f}" y="{-h / 2:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{min(w, h) * 0.22:.2f}" class="seatshape occ" '
            f'transform="translate({cx:.2f} {cy:.2f}) rotate({deg:.1f})"/>')


def seat_svg_fallback(view: dict, facs: list[dict], align: dict | None = None, pres: dict | None = None) -> str:
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
    # the floor first, so the glyphs sit in seats — the same reading as the reconstructed chamber, only the placement
    # is ours (faction, then vote), which the note under the drawing says
    cells = "".join(_seat_cell(seat["cx"], seat["cy"], seat["angle"], dmin * 1.04, dmin * 0.9) for seat in seats[:len(members)])
    parts = []
    for seat, m in zip(seats, members):
        c = colour.get(m["faction"] or "", "#8a8a8a")
        title = html.escape(f'{m["name"]} ({m["faction"]}) — {POSITION_LABEL.get(m["position"], m["position"])}')
        parts.append(_seat_group(m["position"], m["faction"], m.get("mp_azon"), align.get(m.get("mp_azon") or ""), title,
                                 _node(seat["cx"], seat["cy"], m["position"], c, k), 5.2 * k * 1.55, k, dmin * 0.55))
    return ('<svg viewBox="-200 -200 400 222" role="img" aria-label="Ülésrend-diagram: minden képviselő egy helyen, frakciónként és szavazat szerint">'
            + podium_svg(pres, facs, k) + '<text x="0" y="9" class="seclabel s" text-anchor="middle">elnöki emelvény</text>'
            + cells + "".join(parts) + '</svg>')


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
:root{--sz:#b9a06a;--bg:#050505;--panel:rgba(10,10,10,.9);--panel-deep:rgba(5,5,5,.95);--topbar:rgba(5,5,5,.9);--terminal:#000;
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
.kz-topbar{position:sticky;top:0;z-index:20;height:48px;border-bottom:1px solid var(--border);background:var(--topbar);backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:space-between;gap:16px;padding:0 16px;font-family:var(--mono);font-size:10px;letter-spacing:.2em;text-transform:uppercase}
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
/* the cycle page's head: the term as the title, the cycle's pages grouped at the side */
.cyc-head{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(300px,.9fr);gap:14px 40px;align-items:start;padding:6px 0 18px;border-bottom:1px solid var(--border);margin-bottom:16px}
.cyc-title .kicker{display:block;font-family:var(--mono);font-size:10px;letter-spacing:.3em;text-transform:uppercase;color:var(--dim2);margin-bottom:8px}
.cyc-title h1{font-size:34px;line-height:1.1;letter-spacing:-.02em}.cyc-title .lede{margin-top:10px;max-width:58ch}
.cyc-nav{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px 18px;padding-top:8px}
.cyc-nav .grp{display:flex;flex-direction:column;gap:3px;font-family:var(--mono);font-size:11px;letter-spacing:.04em}
.cyc-nav .lbl{font-size:9px;letter-spacing:.25em;text-transform:uppercase;color:var(--dim3);margin-bottom:4px;padding-bottom:4px;border-bottom:1px solid var(--border)}
.cyc-nav a{color:var(--dim);padding:2px 0;border-bottom:1px solid transparent}.cyc-nav a:hover,.cyc-nav a:focus-visible{color:var(--white)}
@media(max-width:900px){.cyc-head{grid-template-columns:1fr}.cyc-nav{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:600px){.cyc-nav{grid-template-columns:1fr 1fr}}
a:focus-visible,button:focus-visible,input:focus-visible,[tabindex]:focus-visible{outline:1px solid var(--dim);outline-offset:2px}
.hero-h{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 18px;margin:0 0 6px}
.portrait{filter:grayscale(1) contrast(1.12) brightness(.92);border:1px solid var(--border);background:#111;object-fit:cover;display:block;flex:0 0 auto}
.portrait.hero{width:72px;height:96px}.portrait.thumb{width:27px;height:36px;vertical-align:middle;margin-right:8px;display:inline-block}.portrait.insp{width:48px;height:64px;float:left;margin:2px 12px 4px 0}
.hero-h.withpic{align-items:flex-start;gap:14px}.hero-h.withpic>div{min-width:0}.hero-h.withpic h1{margin-top:2px}.hero-h.withpic .hero-meta{margin-top:6px}
td.withthumb{white-space:nowrap}td.withthumb a{vertical-align:middle}
.townhit{font-family:var(--sans);font-size:13.5px;color:var(--text);margin:8px 0;padding:8px 10px;border:1px solid var(--border);background:rgba(0,0,0,.3)}.townhit ul{margin:4px 0 0 18px;padding:0}.townhit .sub{color:var(--dim2);font-size:12px}
@media print{.portrait{filter:grayscale(1)}}
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
.inspector{display:flow-root;margin-top:10px;min-height:66px;border:1px solid var(--border);background:var(--panel-deep);padding:9px 12px;font-family:var(--mono);font-size:11px;letter-spacing:.02em;color:var(--dim);position:relative}
.inspector .insp-hint{color:var(--dim2);font-size:10px;letter-spacing:.08em;padding-top:14px}
.inspector .row1{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 14px}
.inspector .name{font-family:var(--sans);font-size:15px;font-weight:400;color:var(--white)}.inspector .name a{color:var(--white);border-bottom:1px solid var(--dim3)}.inspector .name a:hover{border-bottom-color:var(--white)}
.inspector .meta{color:var(--dim2);font-size:10px;letter-spacing:.06em}.inspector .meta b{color:var(--text);font-weight:400}
.chart svg .seat.pres circle,.landing .chamber-today .seat.pres circle{transition:opacity .15s}
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
.rostrum{fill:var(--border)}.rostrum.hi{fill:var(--border-hi)}.rostrum.edge{fill:var(--panel-deep);fill-opacity:.55}.floor{fill:rgba(255,255,255,.028)}.rowline{fill:none;stroke:rgba(255,255,255,.07);stroke-width:.5}
.seatshape{fill:rgba(255,255,255,.035);stroke:rgba(255,255,255,.12);stroke-width:.35;vector-effect:non-scaling-stroke}.seatshape.occ{fill:rgba(255,255,255,.06)}
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
/* the landing page */
.landing .masthead{position:relative;border:1px solid var(--border);background:var(--panel-deep);padding:24px 26px 16px;display:grid;grid-template-columns:minmax(240px,.85fr) 1.7fr;gap:18px 32px;align-items:center}
.landing .mast-h h1{font-size:52px;margin:0 0 12px}
.landing .mast-lede{color:var(--dim);font-size:14px;max-width:40ch;margin:0;line-height:1.55}
.landing .chamber-today{margin:0;min-width:0}.landing .chamber-today svg{width:100%;height:auto;display:block;outline:none}
.landing .chamber-today figcaption{margin-top:4px;font-family:var(--mono);font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:var(--dim2)}
.landing .chamber-today figcaption .sub{text-transform:none;letter-spacing:.05em;margin-left:6px}.landing .chamber-today .legend{margin-top:6px}
.landing .chamber-today .today{transition:opacity .15s}
.landing .chamber-today .seat{transition:opacity .15s;outline:none;cursor:pointer}
.landing .chamber-today .seat.dim{opacity:.12}
.landing .chamber-today .legend .f[data-f="szószóló"] i{box-sizing:border-box}
.landing .chamber-today .seat.hl,.landing .chamber-today .seat:hover,.landing .chamber-today .seat:focus-visible{opacity:1}
.landing .chamber-today .hit{fill:#fff;fill-opacity:0;stroke:none;transition:fill-opacity .12s}
.landing .chamber-today .seat.hl .hit,.landing .chamber-today .seat:hover .hit,.landing .chamber-today .seat:focus-visible .hit{fill-opacity:.22}
.landing .chamber-today .seat:focus-visible .hit{stroke:#fff;stroke-width:1.2;stroke-opacity:.9}
.landing .chamber-today.pinned .seat:not(.hl){opacity:.35}
.landing .chamber-today .legend button.f{border:1px solid transparent;background:transparent;color:inherit;font:inherit;padding:2px 6px 2px 2px;cursor:pointer}
.landing .chamber-today .legend button.f:hover,.landing .chamber-today .legend button.f:focus-visible{border-color:var(--border-hi);color:var(--white)}
.landing .chamber-today .legend button.f.on{border-color:var(--dim3);color:var(--white);background:rgba(24,24,27,.6)}
.landing .inspector{min-height:74px}
.counts.land{grid-template-columns:repeat(4,minmax(0,1fr))}
.grid.land{grid-template-columns:1.35fr .95fr;margin-top:16px}
.cycles{display:flex;flex-direction:column;gap:4px;margin-top:4px}
.cyc{display:grid;grid-template-columns:44px minmax(0,1fr);gap:2px 14px;align-items:start;padding:9px 10px;border:1px solid transparent;color:inherit;text-decoration:none}
.cyc .top{display:flex;justify-content:space-between;gap:8px 16px;flex-wrap:wrap;align-items:baseline}
.cyc:hover,.cyc:focus-visible{border-color:var(--border-hi);background:rgba(255,255,255,.025)}.cyc.now{border-color:var(--border)}
.cyc .num{font-size:18px;color:var(--white);letter-spacing:-.02em;line-height:1.2}
.cyc .body{display:block;min-width:0}
.cyc .span{display:block;font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim2)}
.cyc .compbar{display:flex;height:12px;margin:6px 0 5px;overflow:hidden;background:var(--line2)}.cyc .compbar i{display:block;height:100%;position:static}
.cyc .leg{display:block;font-size:9px;letter-spacing:.05em;color:var(--dim2);line-height:1.8}
.cyc .leg i{display:inline-block;width:7px;height:7px;margin-right:4px;vertical-align:0}.cyc .leg b{color:var(--text);font-weight:400}
.cyc .stat{font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim2);text-align:right}
.doors{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-top:16px}
.door{display:flex;flex-direction:column;color:inherit;text-decoration:none;min-height:150px}
.door p{color:var(--dim);font-size:13px;margin:0 0 12px;line-height:1.5}
.door .row{display:flex;gap:8px;margin-top:auto}.door .row input{flex:1 1 auto;min-width:0;border:1px solid var(--dim3);background:rgba(0,0,0,.4);color:var(--text);padding:7px 10px;font-family:var(--mono);font-size:12px}
.door .row input::placeholder{color:var(--dim2)}
.door .row button{border:1px solid var(--border);background:transparent;color:var(--dim2);padding:6px 12px;font-family:var(--mono);font-size:10px;letter-spacing:.15em;text-transform:uppercase;cursor:pointer}
.door .row button:hover,.door .row button:focus-visible{color:var(--white);border-color:var(--border-hi)}
.door .go{display:block;margin-top:auto;color:var(--dim2);font-size:10px;letter-spacing:.15em;text-transform:uppercase}
a.door:hover,a.door:focus-visible{border-color:var(--border-hi)}a.door:hover .go{color:var(--white)}
@media(max-width:900px){.landing .masthead{grid-template-columns:1fr;padding:18px 16px 14px}.landing .mast-h h1{font-size:40px}.counts.land{grid-template-columns:repeat(2,minmax(0,1fr))}.grid.land{grid-template-columns:1fr}.doors{grid-template-columns:1fr}.cyc{grid-template-columns:40px minmax(0,1fr)}.cyc .stat{text-align:left}}
/* the sitting day as a strip: one bar per vote, the margin above or below the axis */
.strip .stripwrap{overflow-x:auto;margin-top:6px;padding:2px 0}
.strip svg{width:100%;height:56px;display:block;min-width:320px}
.strip .axis{stroke:var(--border-hi);stroke-width:.4}
.strip .b rect{fill:var(--dim3);transition:fill .12s}
.strip .b .hit{fill:transparent}
.strip .b.pass rect:first-child{fill:#3f7a55}.strip .b.fail rect:first-child{fill:#8a4a4a}.strip .b.q rect{fill:var(--border-hi)}
.strip .b:hover rect:first-child,.strip .b:focus-visible rect:first-child{fill:var(--white)}
.strip .b.now rect:first-child{fill:var(--white)}
.strip .b.now .hit{fill:rgba(255,255,255,.07)}
/* one person on one time axis: mandates, offices, local-government seats, the years of the degrees */
.life svg{width:100%;height:auto;display:block;margin-top:4px;overflow:visible}
.life .g{stroke:var(--grid);stroke-width:1}
.life .ax{stroke:var(--border-hi);stroke-width:.5}
.life .yr,.life .ln{font-family:var(--mono);font-size:10px;fill:var(--dim3)}
.life .yr{text-anchor:middle}.life .ln{text-anchor:end;letter-spacing:.12em;text-transform:uppercase;font-size:9px}
.life .sp rect,.life .sp circle{transition:opacity .12s}.life .sp:hover rect,.life .sp:hover circle{opacity:.72}
.life .sch{fill:var(--dim2)}
/* the asset declarations: one square per document, grouped by the year the record states */
.vny{display:flex;flex-wrap:wrap;gap:12px;margin-top:4px}
.vny .yr{display:flex;flex-direction:column;align-items:center;gap:4px}
.vny .yr b{font-family:var(--mono);font-size:10px;font-weight:400;color:var(--dim3)}
.vny .sq{display:flex;gap:3px}
.vny a{width:13px;height:13px;background:var(--dim3);display:block;position:relative}
.vny a:hover,.vny a:focus-visible{background:var(--white)}
.vny .vs{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.vny .due{width:13px;height:13px;display:block;box-shadow:inset 0 0 0 1px var(--border-hi)}
.paybar{display:flex;height:10px;margin:2px 0 10px;background:var(--line2);gap:1px}
.paybar i{width:var(--w);background:var(--white);opacity:var(--o,1)}
.paybar i:nth-child(2){opacity:.7}.paybar i:nth-child(3){opacity:.48}.paybar i:nth-child(4){opacity:.32}.paybar i:nth-child(5){opacity:.22}
/* the record's own edge: a month per column, bright where the House recorded names */
.cov .covwrap{overflow-x:auto;margin-top:6px}
.cov svg{width:100%;height:210px;display:block;min-width:520px}
.cov .all{fill:var(--border-hi)}.cov .named{fill:#d4d4d8}
.cov .hit{fill:transparent}.cov .mc:hover .all,.cov .mc:hover .named{fill:var(--white)}
.cov .ax{stroke:var(--border-hi);stroke-width:.5}
.cov .cyc{stroke:var(--grid);stroke-width:1}
.cov .short{fill:#8a4a4a}.cov .secret{fill:var(--sz)}
.cov .cl,.cov .yl{font-family:var(--mono);font-size:9px;fill:var(--dim3)}
.covkey{display:flex;flex-wrap:wrap;gap:14px;margin-top:8px;font-family:var(--mono);font-size:10px;letter-spacing:.1em;color:var(--dim2)}
.covkey i.k{display:inline-block;width:10px;height:10px;margin-right:5px;vertical-align:-1px}
.covkey i.named{background:#d4d4d8}.covkey i.all{background:var(--border-hi)}.covkey i.short{background:#8a4a4a}.covkey i.secret{background:var(--sz)}
/* first-term share, cycle by cycle */
.turn .turnwrap{overflow-x:auto;margin-top:6px}
.turn svg{width:100%;height:190px;display:block;min-width:560px}
.turn .first{fill:#d4d4d8}.turn .back{fill:var(--line2)}
.turn .tc:hover .first{fill:var(--white)}
.turn .pc,.turn .cl2,.turn .cl3{font-family:var(--mono);text-anchor:middle}
.turn .pc{font-size:11px;fill:var(--text)}.turn .cl2{font-size:10px;fill:var(--dim2)}.turn .cl3{font-size:9px;fill:var(--dim3)}
td.lb{width:40%}td.lb i{display:block;height:6px;width:var(--w);background:var(--dim2)}
/* one motion's road: submitted, the votes it took, promulgated */
.road svg{width:100%;height:62px;display:block;margin-top:4px}
.road .ax{stroke:var(--border-hi);stroke-width:.5}
.road .start{fill:var(--dim)}.road .prom{fill:var(--white)}
.road .ev{stroke:var(--border-hi);stroke-width:1}
.road .bm.v rect:first-child{fill:var(--dim2)}.road .bm.v .hit{fill:transparent}
.road .bm.v:hover rect:first-child,.road .bm.v:focus-visible rect:first-child{fill:var(--white)}
.road .dl{font-family:var(--mono);font-size:10px;fill:var(--dim3)}.road .dl.end{text-anchor:end}
.roadkey{display:flex;flex-wrap:wrap;gap:14px;font-family:var(--mono);font-size:10px;letter-spacing:.1em;color:var(--dim2)}
.roadkey i.k{display:inline-block;width:10px;height:10px;margin-right:5px;vertical-align:-1px}
.roadkey i.start{background:var(--dim);border-radius:50%}.roadkey i.v{background:var(--dim2);width:3px;margin-right:8px}.roadkey i.prom{background:var(--white)}
.evlog{margin-top:12px}
.evlog summary{font-family:var(--mono);font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--dim2);cursor:pointer;padding:6px 0}
.evlog summary:hover{color:var(--white)}
.tablewrap{overflow-x:auto;border:1px solid var(--border);background:rgba(0,0,0,.35)}tr[hidden]{display:none}tr[id]{scroll-margin-top:72px}tr:target td{background:rgba(255,255,255,.07);box-shadow:inset 2px 0 0 var(--white)}
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
.kz-terminal .factline{color:var(--dim)}.kz-terminal .factline a{color:var(--text);border-bottom:1px solid var(--border-hi)}.kz-terminal .factline a:hover{color:var(--white);border-color:var(--white)}.kz-terminal .factline .sub{color:var(--dim3);font-style:normal;margin-left:8px;font-size:9px;letter-spacing:.12em;text-transform:uppercase}.kz-terminal .factline .more{margin-left:10px;border:1px solid var(--border);background:transparent;color:var(--dim2);cursor:pointer;font:inherit;line-height:1;padding:1px 6px}.kz-terminal .factline .more:hover,.kz-terminal .factline .more:focus-visible{color:var(--white);border-color:var(--border-hi)}
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
  var PHOTO_BASE = 'https://www.parlament.hu/felicitas/api/query/resource/kepviseloexportok/kepviselo-exported-queries-provider/kepviselo-kepek/';
  var root = (document.querySelector('a.brand') || {}).getAttribute ? document.querySelector('a.brand').getAttribute('href').replace(/index\.html$/, '') : '';
  var mpBase = (svg.closest('body').querySelector('.pager') ? '../kepviselo/' : 'kepviselo/');
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function render(az){
    var d = data[az]; if (!d) return;
    var name = d[0], fac = d[1], mandate = d[2], seat = d[3], pos = d[4], cast = d[5], inroll = d[6], w = d[7], a = d[8], streak = d[9], office = d[10] || '';
    var c = colours[fac] || '#8a8a8a';
    var part = inroll ? Math.round(100 * cast / inroll) : 0, agree = (w + a) ? Math.round(100 * w / (w + a)) : null;
    var sq = '';
    for (var i = 0; i < streak.length; i++) { var ch = streak[i]; sq += '<i class="' + (ch === '.' ? 'x' : ch) + (i === streak.length - 1 ? ' now' : '') + '" style="--c:' + c + '"></i>'; }
    box.innerHTML = '<img class="portrait insp" src="' + PHOTO_BASE + esc(az) + '" alt="" width="195" height="260" loading="lazy" decoding="async" referrerpolicy="no-referrer" title="fénykép: parlament.hu" onerror="this.remove()">' +
      '<div class="row1"><span class="name"><a href="' + mpBase + esc(az) + '.html">' + esc(name) + '</a></span>' +
      '<span class="meta"><i class="d" style="--c:' + c + '"></i> ' + esc(fac) + (office ? ' · <b>' + esc(office) + '</b>' : '') + ' · ' + esc(mandate) + (seat ? ' · ' + esc(seat) : '') + '</span>' +
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

JS_HALL = """
(function(){
  // The landing page's chamber: every seat is the member who sits in it. Hover or focus names them and shows the
  // cycle record the MP page prints; a click pins the card (Esc or a click on the floor releases it, a click on the
  // name opens the page); the legend filters to one faction. Keyboard: the chamber is one tab stop, arrows walk the
  // seats in seating order, Enter pins. Everything is in the page — no request is made.
  var hall = document.querySelector('.chamber-today'), box = document.getElementById('hall'), src = document.getElementById('hall-data');
  if (!hall || !box || !src) return;
  var data; try { data = JSON.parse(src.textContent); } catch (e) { return; }
  var svg = hall.querySelector('svg'); if (!svg) return;
  var hint = box.innerHTML, pinned = null, colours = {};
  document.querySelectorAll('.chamber-today .legend .f[data-f] i').forEach(function(i){ colours[i.parentNode.getAttribute('data-f')] = i.style.background; });
  var PHOTO_BASE = 'https://www.parlament.hu/felicitas/api/query/resource/kepviseloexportok/kepviselo-exported-queries-provider/kepviselo-kepek/';
  var mpBase = src.getAttribute('data-mp-base') || '';
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  var seats = Array.prototype.slice.call(svg.querySelectorAll('.seat[data-az]'));
  seats.forEach(function(g, i){ g.setAttribute('tabindex', i === 0 ? '0' : '-1'); });
  function focusSeat(i){ if (!seats.length) return; i = (i + seats.length) % seats.length; seats.forEach(function(g, k){ g.setAttribute('tabindex', k === i ? '0' : '-1'); }); seats[i].focus(); }
  function render(az){
    var d = data[az]; if (!d) return;
    var name = d[0], fac = d[1], mandate = d[2], seat = d[3], cast = d[4], inroll = d[5], w = d[6], a = d[7], sp = d[8], com = d[9];
    var photo = d[10] || (PHOTO_BASE + esc(az)), credit = d[11] || 'fénykép: parlament.hu';   // the House's non-MP members are not on parlament.hu's portrait endpoint
    var office = d[12] || '';                            // Speaker, deputy Speaker, clerk — drawn on the platform too
    var sz = (fac === 'szószóló');                       // a nationality spokesperson: sits and speaks, never votes
    var km = (fac === 'kormánytag');                     // a member of the government without a mandate: the same
    var c = colours[fac] || '#8a8a8a';
    var part = inroll ? Math.round(100 * cast / inroll) : null, agree = (w + a) ? Math.round(100 * w / (w + a)) : null;
    var who = (sz || km) ? esc(name) : '<a href="' + mpBase + esc(az) + '.html">' + esc(name) + '</a>';
    box.innerHTML = '<img class="portrait insp" src="' + esc(photo) + '" alt="" width="195" height="260" loading="lazy" decoding="async" referrerpolicy="no-referrer" title="' + esc(credit) + '" onerror="this.remove()">' +
      '<div class="row1"><span class="name">' + who + '</span>' +
      '<span class="meta"><i class="d" style="--c:' + c + '"></i> ' + (sz ? 'nemzetiségi szószóló' : km ? 'kormánytag, nem képviselő' : esc(fac)) + (office ? ' · <b>' + esc(office) + '</b>' : '') + ' · ' + esc(mandate) + (seat ? ' · ' + esc(seat) : '') + '</span></div>' +
      '<div class="row2"><span class="rec"><span class="lbl">a ciklusban</span>' +
      (sz ? 'nem szavaz — a szószóló felszólalhat és bizottságban dolgozik' + (com ? ' · bizottság <b>' + com + '</b>' : '')
        : km ? 'nem szavaz — a kormány tagja mandátum nélkül; a miniszteri padban ül és felszólal'
          : (inroll ? 'leadott <b>' + cast + '</b> / ' + inroll + ' (' + part + '%) · frakciójával <b>' + w + '</b> · ellene <b>' + a + '</b>' + (agree !== null ? ' · egyetért <b>' + agree + '%</b>' : '') : 'még nincs név szerinti szavazása')) +
      (sp ? ' · felszólalás <b>' + sp + '</b>' : '') + '</span>' +
      (pinned ? '<span class="pin">rögzítve<button type="button" data-unpin>Esc</button></span>' : '') + '</div>';
  }
  function mark(az, on){ svg.querySelectorAll('.seat[data-az="' + az + '"]').forEach(function(g){ g.classList.toggle('hl', on); }); }
  function show(az){ if (pinned && pinned !== az) return; render(az); }
  function reset(){ if (pinned) return; box.innerHTML = hint; }
  svg.addEventListener('mouseover', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) return; mark(g.getAttribute('data-az'), true); show(g.getAttribute('data-az')); });
  svg.addEventListener('mouseout', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) return; if (pinned !== g.getAttribute('data-az')) mark(g.getAttribute('data-az'), false); reset(); });
  svg.addEventListener('focusin', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) return; mark(g.getAttribute('data-az'), true); show(g.getAttribute('data-az')); });
  svg.addEventListener('focusout', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) return; if (pinned !== g.getAttribute('data-az')) mark(g.getAttribute('data-az'), false); reset(); });
  function pin(az){ if (pinned) mark(pinned, false); pinned = az; hall.classList.add('pinned'); mark(az, true); render(az); }
  function unpin(){ if (pinned) mark(pinned, false); pinned = null; hall.classList.remove('pinned'); box.innerHTML = hint; }
  svg.addEventListener('click', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) { unpin(); return; } var az = g.getAttribute('data-az'); if (pinned === az) unpin(); else pin(az); });
  svg.addEventListener('keydown', function(e){
    var g = e.target.closest('.seat[data-az]'); if (!g) return; var i = seats.indexOf(g);
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); focusSeat(i + 1); }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { e.preventDefault(); focusSeat(i - 1); }
    else if (e.key === 'Home') { e.preventDefault(); focusSeat(0); }
    else if (e.key === 'End') { e.preventDefault(); focusSeat(seats.length - 1); }
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); var az = g.getAttribute('data-az'); if (pinned === az) unpin(); else pin(az); }
  });
  box.addEventListener('click', function(e){ if (e.target.closest('[data-unpin]')) unpin(); });
  document.addEventListener('keydown', function(e){ if (e.key === 'Escape') unpin(); });
  // the legend filters: one faction at a time, the rest dimmed (a second click clears it)
  var fac = 'all';
  document.querySelectorAll('.chamber-today .legend button[data-hf]').forEach(function(b){
    b.addEventListener('click', function(){
      fac = (fac === b.getAttribute('data-hf')) ? 'all' : b.getAttribute('data-hf');
      document.querySelectorAll('.chamber-today .legend button[data-hf]').forEach(function(x){ var on = fac !== 'all' && x === b; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on ? 'true' : 'false'); });
      svg.querySelectorAll('.seat[data-f]').forEach(function(g){ g.classList.toggle('dim', fac !== 'all' && g.getAttribute('data-f') !== fac); });
    });
  });
})();
"""

JS_FACT = """
(function(){
  // The footer's fact rotates on a click. The pool is fetched once from the site root and cached in the tab, so a
  // reader who keeps clicking never pays for it twice; with JavaScript off the page's own fact stands as built.
  var line = document.querySelector('.factline'); if (!line) return;
  var btn = line.querySelector('[data-fact-next]'), holder = line.querySelector('[data-fact]');
  if (!btn || !holder) return;
  var sc = document.querySelector('script[src$="assets/karzat.js"]');
  var root = sc ? sc.getAttribute('src').replace(/assets\/karzat\.js$/, '') : '';
  var pool = null, i = -1;
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function show(){
    if (!pool || !pool.length) return;
    i = (i + 1) % pool.length;
    var f = pool[i];
    holder.innerHTML = (f.href ? '<a href="' + esc(root + f.href) + '">' + esc(f.hu) + '</a>' : esc(f.hu))
      + ' <i class="sub">' + esc(f.scope || '') + '</i>';
  }
  btn.addEventListener('click', function(){
    if (pool) return show();
    var x = new XMLHttpRequest(); x.open('GET', root + 'tenyek.json');
    x.onload = function(){ try { pool = (JSON.parse(x.responseText) || {}).facts || []; } catch (e) { pool = []; }
      if (!pool.length) { btn.remove(); return; }
      var here = holder.textContent.trim();
      for (var k = 0; k < pool.length; k++) if (here.indexOf(pool[k].hu.slice(0, 24)) === 0) i = k;   // continue from the one on the page
      show(); };
    x.onerror = function(){ btn.remove(); };
    x.send();
  });
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

JS_TOWN = """
(function(){
  // kepviselom/index.html: a settlement or a postcode (Budapest: a district) → its OEVK(s) → the MP; the lists are
  // telepules.json and iranyitoszam.json, loaded on the first keystroke; a city the annex splits by streets lists
  // every candidate and says the address decides. A postcode names a settlement, never a district of the annex's own.
  var q = document.getElementById('town'), out = document.getElementById('townres'), mapEl = document.getElementById('oevk-map'); if (!q || !out || !mapEl) return;
  var map; try { map = JSON.parse(mapEl.textContent); } catch (e) { return; }
  function fold(s){ return String(s || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase().trim(); }
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  var data = null, loading = false, keys = null, zips = null;
  function get(url, cb){ var x = new XMLHttpRequest(); x.open('GET', url); x.onload = function(){ var v = null; try { v = JSON.parse(x.responseText); } catch (e) { v = null; } cb(v); }; x.onerror = function(){ cb(null); }; x.send(); }
  function load(cb){
    if (data) return cb();
    if (loading) return;
    loading = true;
    get('telepules.json', function(v){
      data = v || {}; keys = Object.keys(data).map(function(k){ return [fold(k), k]; });
      get('iranyitoszam.json', function(z){ zips = z || {}; cb(); });
    });
  }
  var urlTimer = null;
  function syncUrl(){ clearTimeout(urlTimer); urlTimer = setTimeout(function(){ if (!history.replaceState) return; var v = q.value.trim(); history.replaceState(null, '', v ? '?t=' + encodeURIComponent(v) : location.pathname); }, 300); }
  function render(){
    var raw = String(q.value || '').trim(), t = fold(raw);
    if (t.length < 2) { out.innerHTML = ''; return; }
    var zip = /^\d{4}$/.test(raw) ? raw : null, names = null, note = '';
    if (zip) {
      names = (zips && zips[zip]) || null;
      if (!names) { out.innerHTML = '<div class="hero-meta">Ehhez az irányítószámhoz nincs település a listánkban. Próbáld a település nevével.</div>'; return; }
      note = '<div class="hero-meta" style="margin-bottom:6px">' + esc(zip) + ' · ' + (names.length > 1 ? esc(names.length + ' település ezzel az irányítószámmal (közös posta)') : esc(names[0])) + '</div>';
    } else if (/^\d+$/.test(raw)) {
      out.innerHTML = '<div class="hero-meta">Az irányítószám négy számjegy — például 1114 vagy 9021.</div>'; return;
    }
    var hits;
    if (names) {
      hits = names.map(function(n){ return [fold(n), n]; });
    } else {
      var exact = keys.filter(function(k){ return k[0] === t; }), pre = keys.filter(function(k){ return k[0].indexOf(t) === 0 && k[0] !== t; }).slice(0, 12);
      hits = exact.concat(pre);
    }
    if (!hits.length) { out.innerHTML = '<div class="hero-meta">Nincs ilyen település a választókerületi mellékletben.</div>'; return; }
    out.innerHTML = note + hits.map(function(k){
      var name = k[1], list = data[name] || [];
      var parts = list.map(function(o){ var key = o[0] + '-' + o[1], mp = map[key], who = mp ? (mp[0] ? '<a href="../kepviselo/' + esc(mp[0]) + '.html">' + esc(mp[1]) + '</a>' : esc(mp[1])) + (mp[2] ? ' (' + esc(mp[2]) + ')' : '') : '—';
        return esc(o[0]) + ' ' + o[1] + '. OEVK → ' + who + (o[2] ? ' <span class="sub">csak a település egy része — a cím dönt</span>' : ''); });
      return '<div class="townhit"><b>' + esc(name) + '</b>' + (list.length > 1 ? ' <span class="sub">több választókerület, utcák szerint — a cím dönt</span>' : '') + '<ul>' + parts.map(function(p){ return '<li>' + p + '</li>'; }).join('') + '</ul></div>'; }).join('');
  }
  q.addEventListener('input', function(){ syncUrl(); load(render); });
  if (location.search) { var m = /[?&]t=([^&]+)/.exec(location.search); if (m) { q.value = decodeURIComponent(m[1].replace(/\\+/g, ' ')); load(render); } }
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
  var KIND = {iromany: 'iromány', kepviselo: 'képviselő', szemely: 'pályakép', szoszolo: 'szószóló', kormany: 'kormánytag'};
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
<link rel="icon" href="{rel}assets/favicon.svg" type="image/svg+xml">
<link rel="alternate icon" href="{rel}assets/favicon.ico" sizes="32x32">
<link rel="apple-touch-icon" href="{rel}assets/apple-touch-icon.png">
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
    return (f'<header class="kz-topbar"><div class="l"><a class="brand" href="{rel}index.html"><i></i>karzat</a>'
            + (f'<span class="sep"></span><nav aria-label="Útvonal">{"".join(parts)}</nav>' if crumbs else "")
            + f'</div><div class="r"><nav class="kv cyc" aria-label="Ciklus"><span class="hide-xs">Ciklus </span>{switch}</nav><span class="sep hide-sm"></span>'
            f'<span class="kv sync"><span class="dot"></span><span class="hide-xs">Szinkron</span><b>{esc(sync_txt)}</b></span></div></header>\n<main class="kz-main"><div class="wrap">')


ROBOTS = """# karzat — az Országgyűlés szavazásai. A tartalom közadat, a másolás szabad; a gépi olvasáshoz viszont
# ott vannak a letölthető táblák (adatok/) és a csatornák (feed/) — azokból egy kéréssel megvan, amiért itt
# tízezer oldalt kellene végigjárni.
User-agent: *
Allow: /
Crawl-delay: 2
"""

SITE_URL = os.environ.get("KARZAT_SITE_URL", "").rstrip("/")     # the deployed origin, when there is one; else paths are cited


def cite_html(inp: dict, path: str, title: str, key: str, json_href: str | None = None, csv_href: str | None = None) -> str:
    inp["_page_path"] = path                      # the page's own address: also the seed for its footer fact
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


def page_tail(inp: dict, depth: int = 0, extra_script: str = "", page: str = "") -> str:
    rel = "../" * (depth + inp["base_depth"])
    inp["_depth"] = depth
    inp["_page_path"] = page or inp.get("_page_path") or ""      # the seed for this page's fact; set by the callers that know it
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
        office = next((HOUSE_OFFICES[o["office"]] for o in (mp.get("offices") or []) if o.get("office") in HOUSE_OFFICES
                       and (not o.get("from") or o["from"] <= view["on_date"]) and (not o.get("to") or o["to"] >= view["on_date"])), "") if mp else ""
        data[azon] = [pnt["name"], pnt["faction"] or "", mandate_text(mp) if mp else "—",
                      seat_text(mp) if (mp and inp["plan"]) else None, pnt["position"],
                      rec["cast"], rec["in_roll"], rec["with"], rec["against"], streak,
                      ("az Országgyűlés " + office + "e") if office else ""]
    return data, align_here


def chart_block(view: dict, inp: dict) -> str:
    plan, facs = inp["plan"], inp["facs"]
    if not view["roll_call_available"]:
        return ('<div class="hero-meta" style="margin:14px 0">Titkos szavazás — nincs név szerinti lista.</div>'
                if view.get("secret") else '<div class="hero-meta" style="margin:14px 0">Nincs név szerinti lista.</div>')
    _, data, align_here = vote_bundle(inp, view["ts"])
    n_against = sum(1 for a in align_here.values() if a == "against")
    if plan:
        sz_seats, km_seats = szoszolo_seats(inp), kormany_seats(inp)
        pres = presidium(inp["mps"], view["on_date"])
        svg, info = seat_svg_real(view, facs, plan, align_here, sz_seats, km_seats, pres)
        geo = plan["geometry"]
        n_sz, n_km = len(sz_seats), len(km_seats)
        note = ((f'Az Országház alaprajza, mindenki a saját helyén. Az emelvény felől nézve balra az ellenzék, jobbra a kormányoldal. '
                 + (f'{hu_num(n_sz)} helyen nemzetiségi szószóló, {hu_num(n_km)} helyen mandátum nélküli kormánytag ül — ők nem szavaznak. ' if n_sz or n_km else
                    'A szószólók és a mandátum nélküli kormánytagok nincsenek berajzolva: az Országgyűlés mindkét listát csak jelen időben adja ki, lezárt ciklusra tehát nem tudjuk, ki hol ült. ')
                 + f'{info.get("empty", 0) - n_sz - n_km} üres hely.')
                if info.get("seats_in_plan") else
                (f'Ülésrend a képviselői adatlapokból, az emelvény felől nézve: balra az ellenzék, jobbra a kormányoldal. Halvány karika: üres hely ({info.get("empty", 0)}).'))
    else:
        svg = seat_svg_fallback(view, facs, align_here, presidium(inp["mps"], view["on_date"]))
        note = ("Ugyanaz a terem ugyanúgy olvasva, de a helyek kiosztása a miénk: frakciónként, azon belül szavazat szerint. "
                "Az ülésrendet az Országgyűlés csak a jelenlegi ciklusra adja meg — ez tehát nem a valódi ülésrend.")
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


FACTS_FILE = DERIVED / "tenyek.json"


def facts() -> list[dict]:
    """The footer's facts, computed by scripts/derive_facts.py from the corpus (empty when the file is absent)."""
    if "_facts" not in _TOTALS:
        _TOTALS["_facts"] = (json.loads(FACTS_FILE.read_text(encoding="utf-8")).get("facts") or []) if FACTS_FILE.exists() else []
    return _TOTALS["_facts"]


def fact_for(page_path: str, depth: int) -> str:
    """One fact per page, chosen by the page's own address so the build stays reproducible and a reader who walks
    the site meets a different one each time. The link points at the page that proves it; the script rotates the
    line on a click, drawing from the same pool."""
    pool = facts()
    if not pool:
        return ""
    f = pool[int(hashlib.sha1(page_path.encode("utf-8")).hexdigest(), 16) % len(pool)]
    rel = "../" * depth
    text = esc(f["hu"])
    if f.get("href"):
        text = f'<a href="{esc(rel + f["href"])}">{text}</a>'
    return f'<span class="fact" data-fact>{text} <i class="sub">{esc(f.get("scope") or "")}</i></span>'


def terminal_html(inp: dict, lines: list[str] | None = None) -> str:
    """The footer as a terminal: a boot log of true values, then the method notes."""
    idx, fl, plan = inp["idx"], inp["fl"], inp["plan"]
    tot = site_totals()
    n_cyc = len(tot["cycles"])
    roster_n = fl["mps"]["total"] if not inp["closed"] else len(inp["mps"])
    days = (fl.get("sitting_days") or {}).get("count") or 0
    check = ("a számított eredmény minden szavazásnál egyezik a jegyzőkönyvivel" if tot["disagreements"] == 0
             else f"a számított eredmény {hu_num(tot['disagreements'])} szavazásnál eltér a jegyzőkönyvitől — jelölve")
    seats_line = (f"ülésrend: az Országház alaprajza, {hu_num(len(plan['seat_outlines']))} hely — csak a jelenlegi ciklusból ismert"
                  if plan and plan.get("seat_outlines") else "ülésrend: csak a jelenlegi ciklusból ismert")
    default_lines = [
        f"{n_cyc} ciklus · {hu_date(tot['from'])} – {hu_date(tot['to'])} · {hu_num(tot['votes'])} szavazás · {hu_num(tot['roll_calls'])} név szerinti lista · {hu_num(tot['people'])} képviselő",
        f"ezen az oldalon: {inp['cycle']}. ciklus · {hu_num(fl['votes'])} szavazás · {hu_num(days) if days else '—'} ülésnap · {hu_num(roster_n)} képviselő",
        f"{check} · {seats_line}",
        (f"frissítve {hu_date(sync_stamp(inp)[:10])} {sync_stamp(inp)[11:]}" if sync_stamp(inp) != "—" else "frissítve —"),
    ]
    log = "".join(f"<span>&gt; {esc(l)}</span>" for l in (lines or default_lines))
    fact = fact_for(inp.get("_page_path") or f'c{inp["cycle"]}d{inp.get("_depth", 0)}', inp.get("_depth", 0) + inp["base_depth"])
    if fact:
        log += f'<span class="factline">&gt; {fact}<button type="button" class="more" data-fact-next title="másik tény">↻</button></span>'
    return f"""<footer class="kz-terminal"><div class="scan"></div>{CORNERS}
  <div class="log">{log}</div>
  <div class="method">
    <h3>Honnan</h3>
    Az Országgyűlés nyilvános Web API-jából, szavazásonként és név szerint; a képviselőket a Wikidata azonosítói kötik össze a ciklusokon át. A szükséges többség az, amit az Országgyűlés maga jelöl meg minden szavazásnál — az oldal csak utánaszámol. Jelenlévőnek azt vesszük, aki igennel, nemmel vagy tartózkodással szavazott — a leadott szavazatok, ahogy az Országgyűlés maga számol.
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


def cycle_title(cycle: int) -> str:
    """'2014. május 6. — 2018. május 7.' for a closed cycle; '2026. május 9. óta' for the open one."""
    span = CYCLE_SPAN.get(cycle, "")
    a, _, b = span.partition(" –")
    a, b = a.strip(), b.strip()
    try:
        return f"{hu_date(a)} — {hu_date(b)}" if b else f"{hu_date(a)} óta"
    except ValueError:
        return span


def build_index(inp: dict, hero_ts: str) -> str:
    idx, fl = inp["idx"], inp["fl"]
    hero, _, _ = vote_bundle(inp, hero_ts)
    title_html, meta_html = motion_title_html(hero, up="")
    roster = roster_summary(inp)
    sd = fl.get("sitting_days") or {"count": 0, "by_session": {}}     # no ulesnap before 1998
    sessions = sd.get("by_session") or {}
    dated = [d for d in idx["sitting_days"] if d.get("date")]                # date-sorted by the derive
    sessions_txt = (" · ".join(f'{k}: {v}' for k, v in sessions.items()) if len(sessions) <= 4
                    else f'{len(sessions)} ülésszak, {dated[0]["session"]} … {dated[-1]["session"]}' if dated else f'{len(sessions)} ülésszak')
    no_ulesnap = not sd.get("count")
    n_rolls = sum(1 for v in inp["idx"]["votes"] if (v.get("position_counts") or {}))
    counts_cards = [
        (fl["votes"], "szavazás", f'{hu_date(fl["window"]["from"])} – {hu_date(fl["window"]["to"])}'),
        (sd["count"] if not no_ulesnap else "—", "ülésnap", sessions_txt if not no_ulesnap else "ülésnap-lista az API-ban csak 1998-tól"),
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
    short = truncated_days(inp["cycle"])
    if not inp["closed"]:
        closed_line = freshness_html(inp)
    elif short:
        # the API's listing cap cuts these days short and no parameter reaches the rest: say it, do not imply completeness
        closed_line = (f'<div class="fresh">{CORNERS}<span class="hu">Lezárt ciklus, {esc(CYCLE_SPAN.get(inp["cycle"], ""))}: {hu_num(fl["votes"])} szavazás. '
                       f'{hu_num(len(short))} ülésnap hiányos — ezeken a napokon 400-nál több szavazás volt, és a lista szolgáltatás egy kérésre legfeljebb 400-at ad, a nap utolsó 400-át '
                       f'(<a href="{"../" * inp["base_depth"]}modszer/index.html#listakorlat">módszer</a>): {esc(", ".join(hu_date(d) for d in short[:6]))}{"…" if len(short) > 6 else ""}</span>'
                       f'<span class="en" lang="en">A closed term, {esc(CYCLE_SPAN.get(inp["cycle"], ""))}: {hu_num(fl["votes"])} votes. {hu_num(len(short))} sitting days are incomplete — '
                       f'the listing service returns at most 400 votes per request and keeps the last 400 of the day.</span></div>')
    else:
        closed_line = (f'<div class="fresh">{CORNERS}<span class="hu">Lezárt ciklus, {esc(CYCLE_SPAN.get(inp["cycle"], ""))}: mind a {hu_num(fl["votes"])} szavazás itt van.</span>'
                       f'<span class="en" lang="en">A closed term, {esc(CYCLE_SPAN.get(inp["cycle"], ""))}: all {hu_num(fl["votes"])} votes are here.</span></div>')
    return page_head((f'karzat — a {inp["cycle"]}. ciklus szavazásai, név szerint' if not inp["closed"] else f'karzat — {inp["cycle"]}. ciklus, az Országgyűlés szavazásai'),
                     f"Az Országgyűlés szavazásai a {inp['cycle']}. ciklusban: minden szavazás a saját szükséges többségével, egy szavazás {'név szerint, az ülésterem rendje szerint' if not inp['closed'] else 'név szerint, frakciónként rendezve'} kirajzolva. Forrás: parlament.hu Web API.", inp["base_depth"],
                     feeds=[("feed/kulonvelemeny.xml", f'karzat · különvélemények · {inp["cycle"]}. ciklus'), ("feed/szavazasok.xml", f'karzat · szavazások · {inp["cycle"]}. ciklus'), ("feed/heti.xml", f'karzat · a hét számokban · {inp["cycle"]}. ciklus')]) + topbar(inp, [], 0) + f"""
<div class="cyc-head">
  <div class="cyc-title"><span class="kicker" data-kz-text>{inp["cycle"]}. ciklus{"" if inp["closed"] else " · folyamatban"}</span><h1>{esc(cycle_title(inp["cycle"]))}</h1>
    <p class="lede">{hu_num(fl["votes"])} szavazás{" az első " + hu_num(sd["count"]) + " ülésnapról" if not inp["closed"] else ""}, mindegyik a saját oldalán: {"ki hogyan szavazott, és mennyi kellett hozzá" if not inp["archive"] else ("az arányok frakciónként és a szükséges többség; a név szerinti listák az adatállományban" if n_rolls > len(inp["order"]) // 10 else "az arányok frakciónként és a szükséges többség — név szerinti listát és tárgyat az API 1998 előtt nem ad")}.</p></div>
  <nav class="cyc-nav" aria-label="A ciklus oldalai">
    <div class="grp"><span class="lbl">szavazások</span><a href="#dir">lista</a><a href="szoros/index.html">szoros szavazások</a><a href="kohezio/index.html">kohézió</a><a href="szamok/index.html">számok</a><a href="iromany/index.html">irományok</a></div>
    <div class="grp"><span class="lbl">emberek</span><a href="kepviselo/index.html">képviselők</a><a href="bizottsag/index.html">bizottságok</a><a href="felszolalas/index.html">felszólalások</a><a href="kepviselom/index.html">képviselőm</a></div>
    <div class="grp"><span class="lbl">eszközök</span><a href="feed/index.html">értesítések</a><a href="adatok/index.html">adatok</a><a href="{"../" * inp["base_depth"]}kereses/index.html">keresés</a><a href="{"../" * inp["base_depth"]}szemely/index.html">pályaképek</a><a href="{"../" * inp["base_depth"]}modszer/index.html">módszer</a></div>
  </nav>
</div>
{closed_line}
<section class="grid">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>{"Egy szavazás az ülésteremben" if not inp["closed"] else "Egy szavazás, frakciónként"}</span><span class="tag"><a href="szavazas/{esc(hero["slug"])}.html">{esc(hero_date)} {esc(hero["ts"][11:16])} ↗</a></span></h2>
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


def archive_note(inp: dict, view: dict) -> str:
    """What an archive cycle's vote page says instead of the chamber and the roll call."""
    if view.get("roll_call_available"):
        return (f'<div class="hero-meta prose" style="margin-top:14px">Archív ciklus: a név szerinti lista ({hu_num(len(view["positions"]))} sor) nem oldalanként, hanem a ciklus '
                f'<a href="../adatok/index.html">adatállományában</a> (<span class="mono">nevsorok.csv.gz</span>) és az adatbázisban van; itt a frakciónkénti számok.</div>')
    return '<div class="hero-meta prose" style="margin-top:14px">Archív ciklus, név szerinti lista nélkül: az API 1998 előtt csak a frakciónkénti számokat adja.</div>'


STRIP_MAX = 160          # bars drawn around the current vote; a long sitting day is windowed, and the page says so


def day_strip(inp: dict, ts: str) -> str:
    """The sitting day as one strip: a bar per vote in the order they were taken, the margin above the axis when the
    decision passed and below when it did not, the vote you are reading marked. It answers the question a single vote
    page cannot — was this one decision, or the fortieth of an afternoon that all went the same way.

    The height is the margin (igen − szükséges) scaled to the day's largest; a quorum check has no margin and gets a
    tick on the axis. Every bar links to its own page. Nothing here is new data: it is the cycle's own index, read
    day by day."""
    day = inp["by_ts"][ts]["on_date"]
    votes = [v for v in inp["idx"]["votes"] if v["on_date"] == day]
    if len(votes) < 2:
        return ""
    votes.sort(key=lambda v: v["ts"])
    here = next(i for i, v in enumerate(votes) if v["ts"] == ts)
    total = len(votes)
    if total > STRIP_MAX:                                   # window it around the vote being read, keeping the order
        lo = max(0, min(here - STRIP_MAX // 2, total - STRIP_MAX))
        votes, here = votes[lo:lo + STRIP_MAX], here - lo
    n = len(votes)
    peak = max((abs((v.get("majority") or {}).get("margin") or 0) for v in votes), default=0) or 1
    w, h, gap = 6.0, 34.0, 1.6                              # one bar's cell; the axis sits in the middle
    width = n * w
    bars = []
    for i, v in enumerate(votes):
        m = (v.get("majority") or {}).get("margin")
        x = i * w
        cls = "b now" if i == here else "b"
        title = (f'{v["time"]} · {cut((v["motions"][0].get("iromany") + " " + (v["motions"][0].get("title") or "")).strip() if v.get("motions") else (v.get("remark") or ""), 70) or "szavazás"}'
                 f' · {v["result_raw"] or ""}{f" · különbség {m:+d}" if m is not None else ""}')
        if m is None:                                        # a quorum check: no threshold, no margin
            bars.append(f'<a href="{esc(v["slug"])}.html" class="{cls} q"><rect x="{x:.1f}" y="{h / 2 - 1:.1f}" width="{w - gap:.1f}" height="2"/><title>{esc(title)}</title></a>')
            continue
        frac = min(1.0, abs(m) / peak)
        bh = max(1.6, frac * (h / 2 - 2))
        y = (h / 2 - bh) if m >= 0 else h / 2
        state = "pass" if m >= 0 else "fail"
        bars.append(f'<a href="{esc(v["slug"])}.html" class="{cls} {state}"><rect x="{x:.1f}" y="{y:.1f}" width="{w - gap:.1f}" height="{bh:.1f}"/>'
                    f'<rect x="{x:.1f}" y="0" width="{w - gap:.1f}" height="{h:.1f}" class="hit"/><title>{esc(title)}</title></a>')
    shown = f'{hu_num(n)} szavazás' if n == total else f'a nap {hu_num(total)} szavazásából {hu_num(n)}'
    return (f'<section class="panel strip">{CORNERS}'
            f'<h2><span data-kz-text>A nap menete</span><span class="tag">{esc(hu_date(day))} · {shown} · a sáv fölött elfogadva, alatta elutasítva</span></h2>'
            f'<div class="stripwrap"><svg viewBox="0 0 {width:.0f} {h:.0f}" preserveAspectRatio="none" role="img" '
            f'aria-label="A nap {hu_num(total)} szavazása időrendben; az oszlop magassága a különbség a szükséges többséghez képest, ez a szavazás kiemelve">'
            f'<line x1="0" y1="{h / 2:.1f}" x2="{width:.0f}" y2="{h / 2:.1f}" class="axis"/>{"".join(bars)}</svg></div>'
            f'<div class="hero-meta">Az oszlop magassága a különbség: mennyivel lett több (vagy kevesebb) az igen, mint amennyi kellett. A legnagyobb oszlop a napon {hu_num(peak)} szavazat.</div>'
            f'</section>')


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

    return page_head(f'{when} — {label}{" · " + str(inp["cycle"]) + ". ciklus" if inp["closed"] else ""} · karzat', f'Az Országgyűlés {when} szavazása név szerint{", székenként" if inp["plan"] else ""}, a szükséges többséggel: {label}', 1 + inp["base_depth"]) + \
        topbar(inp, [("szavazások", "../index.html#dir"), (f'{view["on_date"]} {view["ts"][11:16]}', None)], 1) + f"""
<div class="hero-h"><h1>{esc(when)}</h1><small class="label" data-kz-text>{esc(view["mode"])}</small></div>
<section class="grid">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>A szavazás</span><span class="tag">{esc(view["ts"])}</span></h2>
    {title_html}{meta_html}
    {chart_block(view, inp) if not inp["archive"] else archive_note(inp, view)}
  </section>
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Eredmény és a szükséges többség</span><span class="tag">{esc(needed_tag(view))} · <a href="{"../" * (1 + inp["base_depth"])}modszer/index.html#tobbseg">módszer</a></span></h2>
    {verdict_block(view, inp)}
  </section>
</section>
{day_strip(inp, ts)}
{roll_call_table(view, inp) if not inp["archive"] else ""}
{cite_html(inp, f'{cycle_dir(inp["cycle"])}szavazas/{view["slug"]}.html', f'{when} — {label}', f'{inp["cycle"]}-{view["slug"]}', None if inp["archive"] else f'{view["slug"]}.json', f'{view["slug"]}.csv' if (view["roll_call_available"] and not inp["archive"]) else None)}
<nav class="pager" aria-label="Előző és következő szavazás">{pager_link(prev_ts, True)}{pager_link(next_ts, False)}</nav>
""" + page_tail(inp, 1, page=f'{cycle_dir(inp["cycle"])}szavazas/{view["slug"]}')


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
    all_rows = "".join(vote_row(v) for v in reversed(al["votes"])) if not inp["archive"] else ""     # an archive MP's votes: in the cycle's data files, not on the page
    all_votes_panel = ""      # filled below once the year buttons exist
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
    sp_trs = "".join(f'<tr><td class="ts mono"><a href="{speech_href(inp, r, "../felszolalas/")}">{esc(r["date"] or "")}</a></td><td>{esc(cut(r["event"] or "—", 120))}{(" <span class=" + chr(34) + "sub" + chr(34) + ">" + esc(r["iromany"]) + "</span>") if r.get("iromany") else ""}</td>'
                     f'<td>{esc(r["kind"] or "")}</td><td class="mono">{esc(r["role"] or "")}</td><td class="num mono">{hu_mmss(r["duration_s"]) if r.get("duration_s") else "—"}</td></tr>' for r in sp_sub)
    coms = committees_in_cycle(mp, inp["cycle"])
    com_rows = "".join(f'<tr><td><a href="../bizottsag/{cslug(c["committee"])}.html">{esc(c["committee"] or "")}</a>{(" <span class=" + chr(34) + "sub" + chr(34) + ">" + esc(c["subcommittee"]) + "</span>") if c.get("subcommittee") else ""}</td><td>{esc(c.get("role") or "")}</td>'
                       f'<td class="ts mono">{esc(c.get("from") or "")}{" – " + esc(c["to"]) if c.get("to") else " –"}</td></tr>' for c in coms)
    all_votes_panel = ("" if inp["archive"] else f'''<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Minden szavazása</span><span class="tag">{hu_num(in_roll)} tétel · a legfrissebb elöl · <a href="{esc(azon)}.csv">CSV</a> · <a href="{esc(azon)}.json">JSON</a></span></h2>
  {mp_year_buttons}
  <div class="filters" role="group" aria-label="Szűrés egyezés szerint"><button type="button" data-alf="all" class="on" aria-pressed="true">mind</button><button type="button" data-alf="with" aria-pressed="false">frakcióval</button><button type="button" data-alf="against" aria-pressed="false">frakció ellen</button><button type="button" data-alf="none" aria-pressed="false">nem adott le szavazatot</button><input type="search" data-filter-table="mine" placeholder="tárgy, szám" aria-label="Szűrés tárgyra" style="min-width:160px"><span class="n" id="rn" aria-live="polite"></span></div>
  <div class="tablewrap"><table id="mine" data-page-size="25" data-counter="rn"><thead><tr><th>Időpont</th><th>Tárgy</th><th>Szavazata</th><th>Frakció többsége</th><th></th></tr></thead><tbody>{all_rows}</tbody></table></div>
</section>''')
    if inp["archive"]:
        all_votes_panel = (f'<section class="panel deep">{CORNERS}<h2><span data-kz-text>Minden szavazása</span><span class="tag">{hu_num(in_roll)} tétel</span></h2>'
                           f'<div class="hero-meta prose">Archív ciklus: a képviselő minden név szerinti szavazata a ciklus <a href="../adatok/index.html">adatállományában</a> (<span class="mono">nevsorok.csv.gz</span>, p_azon szerint) és az adatbázisban; itt a frakciója többségével szembeni szavazatai vannak felsorolva.</div></section>')
    return page_head(f'{mp["name"]} ({mp.get("faction") or "—"}){" · " + str(inp["cycle"]) + ". ciklus" if inp["closed"] else ""} · karzat',
                     f'{mp["name"]} — {mp.get("faction") or ""}, {mandate_text(mp)}: szavazási részvétel és a frakcióval való egyezés a {inp["cycle"]}. ciklusban.', 1 + inp["base_depth"],
                     feeds=([(f"{azon}.xml", f'karzat · {mp["name"]} — különvéleményei · {inp["cycle"]}. ciklus')] if has_channel else []) + [(f"{azon}-heti.xml", f'karzat · {mp["name"]} — heti összefoglaló · {inp["cycle"]}. ciklus')]) + \
        topbar(inp, [("képviselők", "index.html"), (mp["name"], None)], 1) + f"""
<div class="hero-h withpic">{portrait_html(mp, "hero")}<div><h1>{esc(mp["name"])}</h1><small class="label"><span class="pos"><i class="d" style="--c:{c}"></i>{esc(mp.get("faction") or "—")}</span> · {esc(mandate_text(mp))}{seat_label}</small>
<p class="hero-meta">{links}</p></div></div>
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
{all_votes_panel}
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
                   f'<td class="withthumb">{portrait_html(mp, "thumb")}<a href="{esc(mp["p_azon"])}.html">{esc(mp["name"])}</a>{former_badge}</td>'
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
    """The site root — the landing page (the name tests and --check use)."""
    return build_landing()


# The mark: the chamber as the gallery sees it — a dome of seats with the Speaker's platform notched out of its
# base. One shape, no letters, no gradient; it survives 16 pixels, which is the only test a favicon has to pass.
# Dark ground by default, inverted under a light UI (SVG favicons honour prefers-color-scheme).
FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="karzat">
<style>
  .bg{fill:#050505}.fg{fill:#fafafa}
  @media (prefers-color-scheme: light){.bg{fill:#fafafa}.fg{fill:#050505}}
</style>
<rect class="bg" width="64" height="64" rx="13"/>
<path class="fg" d="M12 40a20 20 0 0 1 40 0h-9a11 11 0 0 0-22 0z"/>
<rect class="fg" x="27" y="44" width="10" height="6" rx="2"/>
</svg>
"""


def build_assets() -> dict[str, str]:
    """The shared stylesheet, script and favicon (committed, checked, never hand-edited)."""
    return {"favicon.svg": FAVICON_SVG, "karzat.css": CSS.strip() + "\n", "karzat.js": (JS_PAGER + "\n" + JS_FACT + "\n" + JS_TEXTFILTER + "\n" + JS_SPEECHSEARCH + "\n" + JS_TOWN + "\n" + JS_HALL + "\n" + JS_INDEX + "\n" + JS_INSPECT + "\n" + JS_VOTE + "\n" + JS_MP + "\n" + JS_CITE + "\n" + JS_SEARCH + "\n" + JS_BOOT).strip() + "\n"}


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


def bill_path_html(inp: dict, rec: dict) -> str:
    """The motion's own road: submitted, every vote the record dates, promulgated — on one axis, then the full event
    log underneath.

    The markers are the record's dated structure, not a reading of Hungarian procedure: <tulajdonsagok> gives the
    submission and the promulgation, <szavazasok> gives every vote with its outcome. The log below is printed whole,
    unfiltered, so nothing the record says is hidden behind my choice of what counts as a milestone."""
    start = rec.get("submitted_on")
    prom = (rec.get("promulgation") or {}).get("date")
    dates = [d for d in [start, prom] if d] + [e["date"] for e in rec.get("events") or [] if e.get("date")] \
        + [v["ts"][:10].replace(".", "-") for v in rec.get("votes") or [] if v.get("ts")]
    if not dates:
        return ""
    lo, hi = min(dates), max(dates)
    d0, d1 = _date.fromisoformat(lo), _date.fromisoformat(hi)
    span = max((d1 - d0).days, 1)
    W, L, R, Y = 1000.0, 14.0, 14.0, 30.0
    def x(iso: str) -> float:
        return L + (_date.fromisoformat(iso) - d0).days / span * (W - L - R)
    marks = [f'<line x1="{L:.1f}" y1="{Y:.1f}" x2="{W - R:.1f}" y2="{Y:.1f}" class="ax"/>']
    for day in sorted({e["date"] for e in rec.get("events") or [] if e.get("date")}):   # a hair per day the log touches
        marks.append(f'<line x1="{x(day):.1f}" y1="{Y + 2:.1f}" x2="{x(day):.1f}" y2="{Y + 7:.1f}" class="ev"/>')
    if start:
        marks.append(f'<g class="bm"><circle cx="{x(start):.1f}" cy="{Y:.1f}" r="5" class="start"/>'
                     f'<title>Benyújtva · {esc(hu_date(start))}</title></g>')
    for v in rec.get("votes") or []:
        if not v.get("ts"):
            continue
        day = v["ts"][:10].replace(".", "-")
        slug = f'{day}T{v["ts"][11:].replace(":", "-")}'          # '2026.06.08.15:12:50' -> '2026-06-08T15-12-50'
        tip = f'{hu_date(day)} · {v.get("outcome") or ""} · {v.get("igen")}–{v.get("nem")}–{v.get("tartozkodott")} · {v.get("result_raw") or ""}'
        marks.append(f'<a href="../szavazas/{esc(slug)}.html" class="bm v"><rect x="{x(day) - 1.5:.1f}" y="{Y - 11:.1f}" width="3" height="22"/>'
                     f'<rect x="{x(day) - 7:.1f}" y="{Y - 16:.1f}" width="14" height="32" class="hit"/><title>{esc(tip)}</title></a>')
    if prom:
        law = (rec.get("promulgation") or {}).get("law_ref") or (rec.get("promulgation") or {}).get("number") or ""
        marks.append(f'<g class="bm"><rect x="{x(prom) - 5:.1f}" y="{Y - 5:.1f}" width="10" height="10" class="prom"/>'
                     f'<title>Kihirdetve · {esc(hu_date(prom))}{" · " + esc(law) if law else ""}</title></g>')
    marks.append(f'<text x="{L:.1f}" y="{Y + 24:.1f}" class="dl">{esc(hu_date(lo))}</text>'
                 f'<text x="{W - R:.1f}" y="{Y + 24:.1f}" class="dl end">{esc(hu_date(hi))}</text>')

    have_speech = {f'{s["ulnap"]}-{s["seq"]}' for s in ((inp.get("speeches") or {}).get("speeches") or [])}
    log = []
    for e in sorted(rec.get("events") or [], key=lambda e: (e.get("date") or "", e.get("text") or "")):
        sid = (e.get("speech") or "").replace("/", "-")
        ref = f'<a href="../felszolalas/{esc(sid)}.html">{esc(e["speech"])}</a>' if sid in have_speech else esc(e.get("speech") or "")
        rel = f'<span class="sub">{esc(e["related"])}</span>' if e.get("related") else ""
        log.append(f'<tr><td class="ts mono">{esc(hu_date(e["date"]) if e.get("date") else "—")}</td>'
                   f'<td>{esc(e.get("text") or "")}{rel}</td><td class="mono">{ref}</td></tr>')
    coms = "".join(f'<tr><td>{esc(c["name"] or "")}</td><td>{esc(c.get("role") or "")}</td><td class="mono">{esc(c.get("rule") or "")}</td></tr>'
                   for c in rec.get("committees") or [])
    days = rec.get("days_submission_to_promulgation")
    head = " · ".join(x for x in [
        f'benyújtva {hu_date(start)}' if start else "",
        f'kihirdetve {hu_date(prom)}' if prom else (esc(rec.get("status") or "")),
        f'{hu_num(days)} nap' if days is not None else "",
        esc((rec.get("promulgation") or {}).get("law_ref") or ""),
        esc(rec.get("procedure_mode") or "")] if x)
    return (f'<section class="panel deep road">{CORNERS}'
            f'<h2><span data-kz-text>A törvény útja</span><span class="tag">{head}</span></h2>'
            f'<svg viewBox="0 0 {W:.0f} {Y + 30:.0f}" role="img" aria-label="Az iromány útja {esc(hu_date(lo))} és {esc(hu_date(hi))} között: '
            f'benyújtás, {hu_num(len(rec.get("votes") or []))} szavazás{", kihirdetés" if prom else ""}.">{"".join(marks)}</svg>'
            f'<div class="roadkey"><span><i class="k start"></i>benyújtva</span><span><i class="k v"></i>szavazás (a szavazás oldalára visz)</span>'
            + (f'<span><i class="k prom"></i>kihirdetve</span>' if prom else "") + '</div>'
            + (f'<div class="tablewrap" style="margin-top:12px"><table><thead><tr><th>Tárgyaló bizottság</th><th>Jogcím</th><th>Házszabály</th></tr></thead><tbody>{coms}</tbody></table></div>' if coms else "")
            + f'<details class="evlog"><summary>Az adatlap teljes eseménynaplója ({hu_num(len(log))} sor)</summary>'
              f'<div class="tablewrap"><table data-page-size="60"><thead><tr><th>Dátum</th><th>Esemény</th><th>Felszólalás</th></tr></thead><tbody>{"".join(log)}</tbody></table></div></details>'
            + '</section>')


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
{bill_path_html(inp, (inp.get("bill_recs") or {}).get(label) or {}) if (inp.get("bill_recs") or {}).get(label) else ""}
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
             (("nevsorok.csv.gz" if inp["archive"] else "nevsorok.csv"), None, "minden név szerinti szavazat, egy sor egy képviselő egy szavazáson" + (" — gzip (archív ciklus: kicsomagolva több száz MB)" if inp["archive"] else "")),
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


# -- committees ---------------------------------------------------------------------------------

def cslug(name: str) -> str:
    """Committee file name: the slug capped at 100 characters — some inquiry committees are named by a whole sentence
    (cycle 39 has one of 380 characters, longer than a file name may be) — with a short hash of the full slug when cut,
    so two long names that share their first hundred characters still get two files."""
    s = fd.slug(name or "")
    if len(s) <= 100:
        return s
    return s[:100].rstrip("-") + "-" + hashlib.sha1(s.encode("utf-8")).hexdigest()[:8]


def committee_roster(inp: dict) -> dict[str, dict]:
    """slug → {name, members: [{azon, name, faction, role, from, to, subcommittee}], api: record | None} — from the MP
    records' membership histories filtered to the cycle (every cycle), joined with the API's committee record where the
    cycle is the current one (present-tense: type, chair, vice-chairs, members as of now, next sitting)."""
    if "_committees" in inp:
        return inp["_committees"]
    out: dict[str, dict] = {}
    for azon, mp in inp["mps"].items():
        for c in committees_in_cycle(mp, inp["cycle"]):
            key = cslug(c["committee"])
            e = out.setdefault(key, {"name": c["committee"], "members": [], "api": None, "id": None})
            e["members"].append({"azon": azon, "name": mp["name"], "faction": mp.get("faction"), "role": c.get("role"), "from": c.get("from"), "to": c.get("to"), "subcommittee": c.get("subcommittee")})
    api = inp.get("committees") or {}
    for bid, rec in (api.get("records") or {}).items():
        if not rec.get("name"):
            continue
        if rec.get("parent") and rec["parent"].get("name"):          # a subcommittee lives on its parent's page (names repeat across parents)
            pk = cslug(rec["parent"]["name"])
            pe = out.setdefault(pk, {"name": rec["parent"]["name"], "members": [], "api": None, "id": None})
            pe.setdefault("subs", []).append(dict(rec, id=bid))
            continue
        key = cslug(rec["name"])
        e = out.setdefault(key, {"name": rec["name"], "members": [], "api": None, "id": None})
        e["api"] = rec
        e["id"] = bid
    order = {f["id"]: i for i, f in enumerate(inp["facs"])}
    for e in out.values():
        e["members"].sort(key=lambda m: (m["to"] is not None, {"elnök": 0, "alelnök": 1}.get((m["role"] or "").lower(), 2), order.get(m["faction"] or "", 999), name_key(m["name"])))
        e["current"] = [m for m in e["members"] if m["to"] is None and not m.get("subcommittee")]
    inp["_committees"] = dict(sorted(out.items(), key=lambda kv: name_key(kv[1]["name"])))
    return inp["_committees"]


def _person_link(inp: dict, azon, name) -> str:
    return f'<a href="../kepviselo/{esc(azon)}.html">{esc(name or "")}</a>' if azon in inp["mps"] else esc(name or "")


def build_committee_index(inp: dict) -> str:
    cm = committee_roster(inp)
    rows = []
    for key, e in cm.items():
        api = e.get("api") or {}
        ch = api.get("chair")
        if ch and ch.get("name"):
            chair = _person_link(inp, ch.get("azon"), ch["name"])
        else:
            heads = [m for m in e["members"] if (m["role"] or "").lower() == "elnök" and m["to"] is None]
            chair = ", ".join(_person_link(inp, m["azon"], m["name"]) for m in heads) or "—"
        n_now = (len(api.get("members") or []) + len(api.get("vice_chairs") or []) + (1 if api.get("chair") else 0)) if api else len(e["current"])
        typ = f' <span class="sub">{esc(api["type"])}</span>' if api.get("type") else ""
        rows.append(f'<tr><td><a href="{esc(key)}.html">{esc(e["name"])}</a>{typ}</td><td>{chair}</td><td class="num mono">{n_now}</td>'
                    f'<td class="ts mono">{esc(api.get("next_meeting") or "—") if api else "—"}</td></tr>')
    n_meet = sum(1 for e in cm.values() if (e.get("api") or {}).get("next_meeting"))
    has_api = bool(inp["committees"])
    src = ("Az API bizottsági listája és adatlapjai (jelen idejűek: mai összetétel, típus, a következő ülés meghívója) és a képviselői adatlapok tagságtörténete."
           if has_api else "A képviselői adatlapok tagságtörténetéből (az API bizottsági adatlapjai csak a jelenlegi ciklusra vannak).")
    chan = ' Ülésmeghívó csatornaként: <a href="../feed/bizottsagok.xml">bizottsagok.xml</a> (az értesítések oldalán is).' if has_api else ""
    label = f'{inp["cycle"]}. ciklus · {hu_num(len(cm))} bizottság' + (f' · {hu_num(n_meet)} kitűzött ülés' if has_api else "")
    return page_head(f'Bizottságok · {inp["cycle"]}. ciklus · karzat', f'Az Országgyűlés bizottságai a {inp["cycle"]}. ciklusban: összetétel, elnök, tagok, a következő ülés meghívója.', 1 + inp["base_depth"],
                     feeds=[("../feed/bizottsagok.xml", f'karzat · bizottsági ülések meghívói · {inp["cycle"]}. ciklus')] if has_api else []) + \
        topbar(inp, [("bizottságok", None)], 1) + f"""
<div class="hero-h"><h1>Bizottságok</h1><small class="label" data-kz-text>{esc(label)}</small></div>
<p class="lede">{src}{chan}</p>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Bizottságok</span><span class="tag">név szerint</span></h2>
  <div class="filters"><input type="search" data-filter-table="biz" placeholder="név" aria-label="Szűrés névre" style="min-width:200px"><span class="n" id="bn" aria-live="polite"></span></div>
  <div class="tablewrap"><table id="biz" data-page-size="50" data-counter="bn"><thead><tr><th scope="col">Bizottság</th><th scope="col">Elnök</th><th scope="col" class="num">Tag</th><th scope="col">Következő ülés</th></tr></thead><tbody>{"".join(rows) or '<tr><td colspan="4">—</td></tr>'}</tbody></table></div>
</section>
""" + page_tail(inp, 1)


def build_committee_page(inp: dict, key: str, e: dict) -> str:
    colour = {f["id"]: f["colour"] for f in inp["facs"]}
    api = e.get("api") or {}
    def prow(p, role):
        c = colour.get(p.get("faction") or "", "#8a8a8a")
        return f'<tr><td>{_person_link(inp, p.get("azon"), p.get("name"))}</td><td><span class="pos"><i class="d" style="--c:{c}"></i>{esc(p.get("faction") or "—")}</span></td><td>{esc(role)}</td></tr>'
    now_panel = ""
    if api:
        now_rows = ((prow(api["chair"], "elnök") if api.get("chair") else "") + "".join(prow(p, "alelnök") for p in api.get("vice_chairs") or []) + "".join(prow(p, "tag") for p in api.get("members") or []))
        n_now = len(api.get("members") or []) + len(api.get("vice_chairs") or []) + (1 if api.get("chair") else 0)
        now_panel = (f'<section class="panel">{CORNERS}<h2><span data-kz-text>Mai összetétel</span><span class="tag">az API bizottsági adatlapja szerint · {n_now} fő</span></h2>'
                     f'<div class="tablewrap" style="border:0"><table><thead><tr><th scope="col">Név</th><th scope="col">Frakció</th><th scope="col">Tisztség</th></tr></thead><tbody>{now_rows or "<tr><td colspan=3>—</td></tr>"}</tbody></table></div></section>')
    hist = "".join(f'<tr><td>{_person_link(inp, m["azon"], m["name"])}{(" <span class=" + chr(34) + "sub" + chr(34) + ">" + esc(m["subcommittee"]) + "</span>") if m.get("subcommittee") else ""}</td>'
                   f'<td><span class="pos"><i class="d" style="--c:{colour.get(m["faction"] or "", "#8a8a8a")}"></i>{esc(m["faction"] or "—")}</span></td><td>{esc(m["role"] or "")}</td><td class="ts mono">{esc(m["from"] or "")}{" – " + esc(m["to"]) if m["to"] else " –"}</td></tr>' for m in e["members"])
    meta = " · ".join(x for x in [esc(api.get("type") or ""), ("alakult " + esc(api["created"])) if api.get("created") else "", esc(api["email"]) if api.get("email") else ""] if x)
    if api.get("next_meeting"):
        link = f' · <a href="{esc(ext_url(api["next_meeting_url"]))}" target="_blank" rel="noopener">meghívó ↗</a>' if api.get("next_meeting_url") and ext_url(api["next_meeting_url"]) else ""
        meet = f'<div class="hero-meta">Következő ülés: {esc(api["next_meeting"])}{link}</div>'
    elif api:
        meet = '<div class="hero-meta">Kitűzött ülés az API szerint most nincs.</div>'
    else:
        meet = ""
    parent = f'<div class="hero-meta">Albizottság — főbizottsága: {esc(api["parent"]["name"])}</div>' if api.get("parent") else ""
    subs_html = ""
    for sub in e.get("subs") or []:
        srows = ((prow(sub["chair"], "elnök") if sub.get("chair") else "") + "".join(prow(p, "alelnök") for p in sub.get("vice_chairs") or []) + "".join(prow(p, "tag") for p in sub.get("members") or []))
        subs_html += (f'<section class="panel">{CORNERS}<h2><span data-kz-text>Albizottság</span><span class="tag">{esc(sub["name"])}</span></h2>'
                      f'<div class="tablewrap" style="border:0"><table><thead><tr><th scope="col">Név</th><th scope="col">Frakció</th><th scope="col">Tisztség</th></tr></thead><tbody>{srows or "<tr><td colspan=3>—</td></tr>"}</tbody></table></div></section>')
    return page_head(f'{e["name"]} · {inp["cycle"]}. ciklus · karzat', f'{e["name"]}: összetétel, elnök és tagok, tagságtörténet a {inp["cycle"]}. ciklusban.', 1 + inp["base_depth"]) + \
        topbar(inp, [("bizottságok", "index.html"), (cut(e["name"], 40), None)], 1) + f"""
<div class="hero-h"><h1>{esc(e["name"])}</h1><small class="label" data-kz-text>{inp["cycle"]}. ciklus{(" · " + meta) if meta else ""}</small></div>
{parent}{meet}
{now_panel}
{subs_html}
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Tagságok a ciklusban</span><span class="tag">a képviselői adatlapok szerint · {len(e["members"])} tagsági sor</span></h2>
  <div class="tablewrap"><table data-page-size="50"><thead><tr><th scope="col">Név</th><th scope="col">Frakció</th><th scope="col">Tisztség</th><th scope="col">Időszak</th></tr></thead><tbody>{hist or '<tr><td colspan="4">—</td></tr>'}</tbody></table></div>
</section>
{cite_html(inp, f'{cycle_dir(inp["cycle"])}bizottsag/{key}.html', f'{e["name"]} — {inp["cycle"]}. ciklus', f'{inp["cycle"]}-bizottsag-{key}')}
""" + page_tail(inp, 1)


def committees_feed(inp: dict, updated: str, root: str) -> str:
    """feed/bizottsagok.xml — one entry per committee with a sitting announced (invitation date + URL), as the API shows it."""
    cdir = cycle_dir(inp["cycle"])
    ents = []
    for key, e in committee_roster(inp).items():
        api = e.get("api") or {}
        if not api.get("next_meeting"):
            continue
        link = f' · <a href="{fd.a(ext_url(api["next_meeting_url"]))}">meghívó</a>' if api.get("next_meeting_url") and ext_url(api["next_meeting_url"]) else ""
        html = f'<p><a href="{fd.a(root + cdir)}bizottsag/{fd.a(key)}.html">{fd._x(e["name"])}</a> · ülés: {fd._x(api["next_meeting"])}{link}</p>'
        ents.append(fd.entry(f'urn:karzat:{inp["cycle"]}:bizottsag:{key}:{api["next_meeting"]}', f'{e["name"]} — ülés {api["next_meeting"]}', f'{root}{cdir}bizottsag/{key}.html',
                             fd.rfc3339(f'{api["next_meeting"].replace("-", ".")}.00:00:00'), html))
    return fd.atom(f'urn:karzat:{inp["cycle"]}:bizottsagok', f'karzat · bizottsági ülések meghívói · {inp["cycle"]}. ciklus',
                   "Amelyik bizottságnak az API adatlapja kitűzött ülést mutat: a dátum és a meghívó. Az anyagok és a jegyzőkönyvek nincsenek az API-ban.",
                   f'{root}{cdir}feed/bizottsagok.xml', f'{root}{cdir}bizottsag/index.html', updated, ents)


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
  <h2 style="margin-top:14px"><span data-kz-text>Bizottsági ülések</span><span class="tag">meghívók az API bizottsági adatlapjairól · <a href="../bizottsag/index.html">bizottságok</a></span></h2>
  <div class="tablewrap" style="border:0"><table><thead><tr><th scope="col">Kör</th><th scope="col">Mi van benne</th><th scope="col">Fájl</th></tr></thead><tbody>
    <tr><td>a ciklus</td><td>amelyik bizottság adatlapja kitűzött ülést mutat: dátum és meghívó — anyagok, jegyzőkönyvek nincsenek az API-ban</td><td class="mono">{'<a href="bizottsagok.xml">bizottsagok.xml</a>' if inp["committees"] else "— (csak a jelenlegi ciklusra)"}</td></tr>
  </tbody></table></div>
  <div class="hero-meta prose" style="margin-top:8px">Ebben a csatornában az utolsó 200 érdemi felszólalás van, teljes szöveggel; a tétel ideje a nap és a napi sorrend (az API nem ad órát); szöveges CSV nincs, a lapok és a JSON-ok igen. Kulcsszóra így lehet figyelni: a hírolvasó szűrője vagy szabálya a tétel <em>tartalmára</em> — a címben csak nap, felszólaló és fajta áll (Miniflux: keep rule a tartalomra, Inoreader Pro: szabály, Thunderbird: üzenetszűrő a törzsre) — a szöveg teljes, ezért a szűrő mindent lát. Az oldal nem sorol témába és nem foglal össze: a találat a jegyzőkönyv szava.</div>
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
                   "és az érdemi felszólalásai (napirendi pont, fajta, hossz). Számok a napi listákból; az oldal nem ítél.",
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
                   "Ülésnapos hetenként: szavazások, döntések, név szerinti szavazások, különvélemények (frakciótagoké; a függetleneké nem számít), érdemi felszólalások és felszólalók (nem képviselőkkel együtt). Számok, az oldal nem ítélek.",
                   f'{root}{cdir}feed/heti.xml', f'{root}{cdir}index.html', updated, ents)


# -- speech texts: pages, day lists, the prefix index, the channel ---------------------------------

def speech_id(r: dict) -> str:
    return f'{r["ulnap"]}-{r["seq"]}'


def fold_tokens(text: str) -> list[str]:
    """Search tokens: accents folded, lower-case, [a-z0-9]{3,} — the same rule the page's script applies to the query."""
    import unicodedata
    folded = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)).lower()
    return re.findall(r"[a-z0-9]{3,}", folded)


def speech_has_page(inp: dict, r: dict, texts: dict | None = None) -> bool:
    """A page of its own for every substantive speech in the live cycles; in an archive cycle only where the record's
    text is loaded — the lists alone would be tens of thousands of near-empty pages per cycle."""
    if not inp["archive"]:
        return True
    texts = texts if texts is not None else ((inp["texts"] or {}).get("texts") or {})
    return speech_id(r) in texts


def speech_href(inp: dict, r: dict, up: str = "", texts: dict | None = None) -> str:
    """Link target of a speech row from a page `up` levels away from felszolalas/ (own page or the day-page anchor)."""
    sid = speech_id(r)
    return f"{up}{sid}.html" if speech_has_page(inp, r, texts) else f"{up}nap{r['ulnap']}.html#s{sid}"


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
            link = (f'<a href="{esc(sid)}.html">{"szöveg" if sid in texts else "lap"}</a>' if speech_has_page(inp, r, texts) else "") if sub else ""
            trs.append(f'<tr id="s{esc(sid)}" data-sub="{1 if sub else 0}"{" class=dim" if not sub else ""}><td class="num mono">{r["seq"] if r.get("seq") is not None else ""}</td><td>{who_html}{(" <span class=" + chr(34) + "sub" + chr(34) + ">" + esc(r["faction"]) + "</span>") if r.get("faction") else ""}</td>'
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
<p class="lede">Az ülésnapok felszólalás-listái az Országgyűlés Web API-jából (<span class="mono">felszolalasok</span>): ki, melyik napirendi pontnál, milyen fajta felszólalással, mennyi ideig — naponként egy lap, {"érdemi felszólalásonként egy lap a jegyzőkönyvi szöveggel" if not inp["archive"] else "érdemi felszólalásonként egy lap ott, ahol a jegyzőkönyvi szöveg be van töltve (archív ciklus: a sorok a napi lapokon)"}. Érdemi az, ahol valaki a tárgyhoz szól; eljárási az ülésvezetés, a bejelentés, az eredmény kihirdetése — a besorolás a fajta neve szerint, lent. <a href="kereses.html">Keresés a szövegekben</a>.</p>
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


OEVK_FILE = ROOT / "reference" / "valasztas" / "oevk_telepules.json"


LISTCAP_FILE = ROOT / "reference" / "parlament" / "listakorlat.json"


def list_cap() -> dict:
    """What the vote-listing service's 400-row cap costs us (reference/parlament/listakorlat.json).

    szavazasok.cgi answers at most 400 votes per request and keeps the latest ones; its parameters take dates only.
    Month-sized requests were therefore truncated — the day-level re-listing fixed every month, except the days that
    on their own held more than 400 votes. Those days keep their last 400 and the earlier ones are unreachable, so
    every page that would otherwise claim completeness says how many days are short instead."""
    if not LISTCAP_FILE.exists():
        return {}
    return json.loads(LISTCAP_FILE.read_text(encoding="utf-8"))


def truncated_days(cycle: int) -> list[str]:
    """The cycle's sitting days that the 400-row cap still cuts short, newest first."""
    cap = list_cap()
    from karzat.normalise import CYCLE_STARTS as starts               # cycle → the day its constituent sitting fell on
    out = []
    for day in cap.get("days_still_truncated") or []:
        iso = day.replace(".", "-")
        c = max((k for k, v in starts.items() if iso >= v), default=None)
        if c == cycle:
            out.append(iso)
    return sorted(out, reverse=True)


POSTCODE_FILE = ROOT / "reference" / "valasztas" / "iranyitoszam.json"


def postcodes() -> dict:
    """Postcode → the annex's settlement names (reference/valasztas/iranyitoszam.json; empty when absent)."""
    if not POSTCODE_FILE.exists():
        return {}
    return json.loads(POSTCODE_FILE.read_text(encoding="utf-8"))


def oevk_settlements() -> dict:
    """The electoral-district annex as parsed into reference/valasztas/oevk_telepules.json (empty when absent)."""
    if not OEVK_FILE.exists():
        return {}
    return json.loads(OEVK_FILE.read_text(encoding="utf-8"))


COUNTY_ALIAS = {"Csongrád": "Csongrád-Csanád"}     # the API's records keep the pre-2020 county name; the annex has the current one


def county_name(c: str | None) -> str | None:
    return COUNTY_ALIAS.get(c or "", c)


def build_kepviselom_page(inp: dict) -> str:
    """kepviselom/index.html — Ki a képviselőm? By constituency (county → number → MP), the list MPs, a name box."""
    mps = inp["mps"]
    colour = {f["id"]: f["colour"] for f in inp["facs"]}
    keep = (lambda m: bool(m.get("current"))) if not inp["closed"] else (lambda m: True)      # today's sitters, or the whole cycle's roster
    egy = sorted((m for m in mps.values() if m.get("mandate_kind") == "egyeni" and keep(m)), key=lambda m: (name_key(county_name(m.get("county")) or ""), m.get("constituency_no") or 0))
    lis = sorted((m for m in mps.values() if m.get("mandate_kind") == "lista" and keep(m)), key=lambda m: name_key(m["name"]))
    other = sorted((m for m in mps.values() if m.get("mandate_kind") not in ("egyeni", "lista") and keep(m)), key=lambda m: name_key(m["name"]))
    def row(m, oevk):
        c = colour.get(m.get("faction") or "", "#8a8a8a")
        return (f'<tr data-name="{esc(name_key(m["name"]))}"><td>{esc(oevk)}</td><td class="withthumb">{portrait_html(m, "thumb")}<a href="../kepviselo/{esc(m["p_azon"])}.html">{esc(m["name"])}</a></td>'
                f'<td><span class="pos"><i class="d" style="--c:{c}"></i>{esc(m.get("faction") or "—")}</span></td>'
                f'<td class="mono"><a href="../kepviselo/{esc(m["p_azon"])}.xml" title="különvéleményei">Atom</a> · <a href="../kepviselo/{esc(m["p_azon"])}-heti.xml" title="heti összefoglaló">heti</a></td></tr>')
    erows = "".join(row(m, f'{county_name(m.get("county")) or "—"} {m.get("constituency_no") or ""}. OEVK') for m in egy)
    oevk_map = {f'{county_name(m.get("county"))}-{m.get("constituency_no")}': [m["p_azon"], m["name"], m.get("faction")] for m in egy if m.get("county") and m.get("constituency_no")}
    oevk_map_json = json.dumps(oevk_map, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    oevk_n_settlements = len(oevk_settlements().get("settlements") or {})
    n_zip = len(postcodes().get("map") or {})
    lrows = "".join(row(m, "országos lista") for m in lis) + "".join(row(m, "mandátum: —") for m in other)
    return page_head(f'Ki a képviselőm? · {inp["cycle"]}. ciklus · karzat', f'A {inp["cycle"]}. ciklus képviselői választókerület szerint (megye, OEVK) és az országos listáról; névre szűrhető; minden képviselőnél a heti összefoglaló és a különvélemény-csatorna.', 1 + inp["base_depth"]) + \
        topbar(inp, [("képviselőm", None)], 1) + f"""
<div class="hero-h"><h1>Ki a képviselőm?</h1><small class="label" data-kz-text>{inp["cycle"]}. ciklus · {hu_num(len(egy))} egyéni választókerület · {hu_num(len(lis) + len(other))} listás képviselő{" · a ciklus teljes névsora" if inp["closed"] else ""}</small></div>
<p class="lede">Egyéni választókerületben a megye és a kerület száma azonosítja a képviselőt; a listás képviselőknek nincs kerületük.{" Lezárt ciklus: mindenki, aki a ciklus névsoraiban szerepelt." if inp["closed"] else ""} Minden képviselő oldalán ott a heti összefoglalója és a különvélemény-csatornája.</p>
<section class="panel">{CORNERS}
  <h2><span data-kz-text>Irányítószám vagy település</span><span class="tag">a választókerületi törvény melléklete · {hu_num(oevk_n_settlements)} település és kerület · {hu_num(n_zip)} irányítószám</span></h2>
  <div class="filters"><input id="town" type="search" placeholder="pl. 1114 · 9021 · Szombathely · Budapest XI. kerület" aria-label="Irányítószám vagy település" autocomplete="off" inputmode="text" style="min-width:min(100%,360px)"></div>
  <div id="townres" aria-live="polite"></div>
  <script type="application/json" id="oevk-map">{oevk_map_json}</script>
  <div class="hero-meta prose" style="margin-top:8px">Négy számjegy irányítószámként, minden más településnévként keres. Az irányítószám a települést (Budapesten a kerületet) azonosítja, nem a választókerületet: a kódok a Wikidata adataiból (P281) illeszkednek a melléklet neveihez, Budapesten pedig a kódképzés szabályából (1XYZ → XY. kerület). Egy irányítószám több települést is takarhat (közös posta) — ilyenkor mind látszik. A választókerületek településlistája a választókerületi törvény mellékletéből (2011. évi CCIII. tv., 2. melléklet, a hatályos szöveg); ahol egy város vagy budapesti kerület több választókerülethez tartozik, a melléklet utcánként húzza a határt — ott minden szóba jövő kerület látszik, és a lakcím dönt (a pontos kerület a valasztas.hu keresőjében). Település nélküli képviselő a listás.</div>
</section>
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
            if not inp["archive"]:
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
        if inp["committees"]:
            fw(fdir / "bizottsagok.xml", committees_feed(inp, updated, root))
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
        paged = [r for r in subs if speech_has_page(inp, r, texts_map)]
        for i, r in enumerate(paged):
            sid = speech_id(r)
            t = texts_map.get(sid)
            sw(spd / f"{sid}.html", build_speech_page(inp, r, t, paged[i - 1] if i > 0 else None, paged[i + 1] if i + 1 < len(paged) else None, bs))
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
        compact = {name: [[x["county"], x["no"], 1 if x["partial"] else 0] for x in lst] for name, lst in (oevk_settlements().get("settlements") or {}).items()}
        (kmd / "telepules.json").write_text(json.dumps(compact, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        zips = {c: names for c, names in (postcodes().get("map") or {}).items() if any(n in compact for n in names)}
        (kmd / "iranyitoszam.json").write_text(json.dumps(zips, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        bzd = out_dir / "bizottsag"; bzd.mkdir(parents=True, exist_ok=True)
        bw = _Writer()
        bw(bzd / "index.html", build_committee_index(inp))
        for key, e in committee_roster(inp).items():
            bw(bzd / f"{key}.html", build_committee_page(inp, key, e))
        prune(bzd, bw.written)
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
            if not inp["archive"]:
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
        if inp["archive"]:                                # 150–190 MB plain for a 1998–2010 cycle: gzip it (mtime 0, byte-stable)
            with gzip.GzipFile(filename="", mode="wb", fileobj=(ad / "nevsorok.csv.gz").open("wb"), mtime=0) as gz:
                gz.write(ex.positions_csv(inp["store"], inp["order"], align_by_vote).encode("utf-8"))
        else:
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
                     "current": mp.get("current"), "wikidata_qid": mp.get("wikidata_qid"), "parlament_url": mp.get("parlament_url"), "photo_url": mp.get("photo_url"),
                     "factions": mp.get("factions") or [], "elections": mp.get("elections") or [], "motion_stats": mp.get("motion_stats") or [],
                     "offices": mp.get("offices") or [], "schools": mp.get("schools") or [], "languages": mp.get("languages") or [],
                     "highest_degree": mp.get("highest_degree"), "declarations": mp.get("declarations") or [],
                     "remuneration": mp.get("remuneration") or [], "local_offices": mp.get("local_offices") or [],
                     "biography_url": mp.get("biography_url"),
                     "in_roll": (inp["alignment"]["per_mp"].get(azon) or {}).get("in_roll", 0), "cast": (inp["alignment"]["per_mp"].get(azon) or {}).get("cast", 0),
                     "with": (inp["alignment"]["per_mp"].get(azon) or {}).get("with", 0), "against": (inp["alignment"]["per_mp"].get(azon) or {}).get("against", 0),
                     "href": f"{cycle_dir(cycle)}kepviselo/{azon}.html"} for azon, mp in inp["mps"].items()}
    return {"cycle": cycle, "index": str(out_dir / "index.html"), "vote_pages": n, "mp_pages": k, "per_mp": per_mp,
            "search": search_items, "coverage": coverage_rows(inp)}


def coverage_rows(inp: dict) -> list[dict]:
    """Per month of this cycle: how many votes the listing holds, how many of them the record names, how many were
    secret. The named count is the roll-call store's own keys — not the mode the payload prints, because before 1998
    a vote can say 'Listás' and carry no list at all."""
    # the store keeps a key for every vote, empty list and all — before 1998 every one of them is empty, so the test
    # has to be "does this vote name anybody", not "is it in the store"
    have = {ts for ts, rows in ((inp.get("store") or {}).get("positions") or {}).items() if rows}
    out: dict[str, dict] = {}
    for v in inp["idx"]["votes"]:
        m = (v.get("on_date") or "")[:7]
        if not m:
            continue
        r = out.setdefault(m, {"month": m, "votes": 0, "named": 0, "secret": 0})
        r["votes"] += 1
        r["named"] += 1 if v["ts"] in have else 0
        r["secret"] += 1 if v.get("secret") else 0
    return [out[m] for m in sorted(out)]


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
  <div class="filters" role="group" aria-label="Szűrés fajta szerint"><button type="button" data-sk="all" class="on" aria-pressed="true">mind</button><button type="button" data-sk="iromany" aria-pressed="false">irományok</button><button type="button" data-sk="kepviselo" aria-pressed="false">képviselők</button><button type="button" data-sk="szemely" aria-pressed="false">pályaképek</button><button type="button" data-sk="szoszolo" aria-pressed="false">szószólók</button><button type="button" data-sk="kormany" aria-pressed="false">kormánytagok</button></div>
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
        f'<td class="mono">{esc(" · ".join(api_by_rule.get(r.value, [])))}</td><td>{esc("; ".join(info.basis))}</td></tr>' for r, info in RULES.items())
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
<div class="tablewrap" style="border:0"><table><thead><tr><th scope="col">kód</th><th scope="col">szabály</th><th scope="col">küszöb</th><th scope="col">alap</th><th scope="col">az API „Szavazási mód” szövege</th><th scope="col">jogszabályi hely</th></tr></thead><tbody>{rule_rows}</tbody></table></div>
<div class="hero-meta prose" style="margin-top:8px">A szabályt minden szavazásnál az Országgyűlés maga jelöli meg a „Szavazási mód” mezőben; az oldal ebből számolja a küszöböt és összeveti az „Elfogadás” mezővel („számítás egyezik / eltér”). Jelenlévő: aki igennel, nemmel vagy tartózkodással szavazott — a leadott szavazatok, az API „Összes szavazat” mezője; aki „jelen, nem szavazott”, nincs az alapban. Ez nem értelmezés, a jegyzőkönyv dönti el: a 38. ciklus 272 szavazásán mond ellent az Elfogadva/Elutasítva mezőnek, ha a jelen lévő nem szavazókat is beleszámoljuk, és egyen sem, ha nem. Az összes képviselő: az aznap érvényes mandátumok száma (üresedéskor 199-nél kevesebb — egy 2014. december 1-jei kétharmados titkos szavazás 132 igennel ment át: a 197 mandátum kétharmada), ahol az adatlapok ismerik; különben 2014. május 6-a előtt 386, azóta 199. A szabályok jogszabályi helye az Alaptörvény és a Házszabály (10/2014. (II. 24.) OGY határozat) hatályos szövegével egyeztetve (njt.hu, 2026. augusztus 19.); az egyetlen, amire ott nincs alap, az API 2014-es „az összes képviselő 4/5-ével” címkéje.</div></section>

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

<section class="panel" id="kep">{CORNERS}<h2><span data-kz-text>Fényképek, bizottságok, választókerületek</span></h2>
<div class="hero-meta prose">A képviselők fényképe a parlament.hu-ról töltődik be (a képviselői adatlap <span class="mono">fenykep</span> hivatkozása; az oldal nem másolja, onnan mutatja, fekete-fehérben, forrásmegjelöléssel); a felhasználás feltételeit a parlament.hu nem írja ki — a beágyazás az oldal tulajdonosának döntése. Akit a parlament.hu nem fényképez (a mandátum nélküli kormánytagokat: a portré-végpont 404-et ad rájuk), annak a képe — ha van — Wikimedia Commons-fájl, a Wikidata P18 alapján, szerzővel és licenccel megnevezve a kép címkéjében és az adatlapján; a párosítás akkor fogadható el, ha a tétel ember, a neve a jegyzőkönyvi névvel egyezik, és vagy a saját p_azon-ját viseli (P4966), vagy a tisztsége (P39) az, amit a jegyzőkönyv ír. Minden fénykép fekete-fehéren, ugyanazzal a szűrővel jelenik meg. A bizottságok a jelenlegi ciklusra az API bizottsági listájából és adatlapjaiból (típus, mai összetétel, a következő ülés meghívója — jelen idejű adat), minden ciklusra a képviselői adatlapok tagságtörténetéből; ülésmeghívó-csatorna: <span class="mono">feed/bizottsagok.xml</span>. „Ki a képviselőm?” település szerint: a választókerületi törvény mellékletéből (2011. évi CCIII. tv., 2. melléklet, hatályos szöveg, njt.hu); ahol egy várost vagy kerületet a melléklet utcánként oszt meg, minden szóba jövő választókerület látszik és a lakcím dönt.</div></section>

<section class="panel" id="szoszolo">{CORNERS}<h2><span data-kz-text>Nemzetiségi szószólók</span></h2>
<div class="hero-meta prose">A nemzetiségi listáról szószóló kerülhet az Országgyűlésbe, ha a lista nem szerez mandátumot: a szószóló ül az ülésteremben, felszólalhat és bizottságban dolgozik, de nem szavaz — a név szerinti listák sosem tartalmazzák. Az oldal külön kezeli őket: a nyitólap patkóján gyűrűvel vannak jelölve, a kártyájuk a nemzetiséget, a bizottságaikat és a felszólalásaik számát mutatja, és semmilyen névsor-, részvételi vagy frakciófegyelem-szám nem tartalmazza őket. Forrás: <span class="mono">szoszolok.cgi</span> és a saját képviselői adatlapjuk (ülőhely, bizottságok, felszólalásszám). Jelenleg {hu_num(len(((inp.get("szoszolok") or {{}}).get("people") or {{}})))} szószóló ül a teremben.</div></section>

<section class="panel" id="listakorlat">{CORNERS}<h2><span data-kz-text>A lista 400-as korlátja</span></h2>
<div class="hero-meta prose">A szavazások listája (<span class="mono">szavazasok.cgi</span>) egy kérésre legfeljebb {hu_num(400)} szavazást ad vissza, mégpedig a legkésőbbieket, és a paraméterei csak dátumot fogadnak el, időpontot nem. Emiatt a hónapos lekérdezések a régebbi ciklusokban csonkák voltak: {hu_num(len((list_cap().get("months_originally_truncated") or [])))} hónap pontosan 400 sorral tért vissza. Az oldal ezeket a hónapokat naponként listázta újra — így {hu_num(list_cap().get("votes_recovered_by_day_scan") or 0)} korábban hiányzó szavazás került elő. Ami így sem érhető el: az a {hu_num(len((list_cap().get("days_still_truncated") or [])))} ülésnap, amelyen önmagában több mint 400 szavazás volt — ezeken a nap utolsó 400 szavazása van meg, a korábbiak nem, és a szolgáltatáson nincs olyan paraméter, amivel elkérhetők lennének. A ciklusok oldala kiírja, hány ilyen napja van; a korpusz számai tehát alsó becslések ezekre a napokra.</div></section>

<section class="panel" id="kormanytag">{CORNERS}<h2><span data-kz-text>Miniszteri pad: kormánytagok mandátum nélkül</span></h2>
<div class="hero-meta prose">A kormány tagjának nem kell képviselőnek lennie: aki nem az, a miniszteri padban ül és felszólal, de nem szavaz. Ilyen névsort az API nem ad, ezért az oldal onnan ismeri fel őket, ahogy megszólalnak: a felszólalás jegyzőkönyvi payloadja azonosítóval nevezi meg a felszólalót (<span class="mono">felszolalas.cgi</span>), és ha ez az azonosító sem a képviselői, sem a szószólói listában nincs, de a saját adatlapján a miniszteri padban van ülőhelye, akkor ő a padban ülő kormánytag. A tisztség az, ahogy a jegyzőkönyv megnevezi (<span class="mono">beosztás</span>). Amit ez nem talál meg: aki a ciklusban még nem szólalt fel — ezért maradhat üres hely a padban. Egy adatlap régi ülőhelyet is őrizhet (egy volt képviselőé); az ilyen igényt az oldal eldobja, ha a helyet ma más foglalja el vagy nem a padban van. Jelenleg {hu_num(len(((inp.get("kormany") or {{}}).get("people") or {{}})))} kormánytag ül a padban mandátum nélkül.</div></section>

<section class="panel" id="nem">{CORNERS}<h2><span data-kz-text>Amit az oldal nem állít</span></h2>
<div class="hero-meta prose">Semmit a szavazások okáról. Nem skáláz, nem súlyoz, nem jósol; egyetlen feltevését (a hiányzók) annak nevezi. A színek az oldal jelölései.</div></section>
{cite_html(inp, 'modszer/index.html', 'Módszer', 'modszer')}
""" + page_tail(inp, 1)


CYCLE_BY_LABEL = {v: k for k, v in CYCLE_LABELS_.items()}       # '2018-2022' -> 41, as kepviselo.cgi prints the cycle
CAP_FILE = ROOT / "reference" / "parlament" / "listakorlat.json"


DEGREE_ORDER = ["egyetem", "főiskola", "középfok", "alapfok"]


def build_profile_page(inp: dict, rows: list[dict]) -> str:
    """arcel/index.html — the House as a body of people, across the ten cycles.

    One measure here is a time series and the rest deliberately is not, and the difference is the point. Mandates
    are recorded completely for every cycle since 1990, so how many members are serving a first term can be
    compared cycle to cycle and means what it says. Degrees, languages and local-government service come from each
    person's record as it stands today: they carry no date the House can be held to, and they are filled in
    unevenly — 273 of the 416 members of the 34th cycle have a schooling row against 383 of 386 in the 35th. A
    column chart of that would show the archive being tidied up, not the Parliament changing. So those appear once,
    over everyone who has sat, with the recording gap printed beside them."""
    people: dict[str, dict] = {}
    for r in rows:
        for azon, mp in (r["mps"] or {}).items():
            people.setdefault(azon, mp)
    turn = []
    for r in sorted(rows, key=lambda r: r["cycle"]):
        c = r["cycle"]
        first = 0
        known = 0
        for mp in (r["mps"] or {}).values():
            cy = [CYCLE_BY_LABEL.get(e.get("ciklus")) for e in (mp.get("elections") or [])]
            cy = [k for k in cy if k]
            if not cy:
                continue
            known += 1
            first += 1 if min(cy) == c else 0
        turn.append({"cycle": c, "n": known, "first": first, "back": known - first,
                     "share": (100 * first / known) if known else 0.0})
    if not turn:
        return ""
    cw, cg, ch = 78.0, 16.0, 150.0
    width = len(turn) * (cw + cg)
    bars = []
    for i, t in enumerate(turn):
        x = i * (cw + cg)
        hf = t["share"] / 100 * ch
        bars.append(f'<g class="tc"><rect x="{x:.1f}" y="{ch - hf:.1f}" width="{cw:.1f}" height="{hf:.1f}" class="first"/>'
                    f'<rect x="{x:.1f}" y="0" width="{cw:.1f}" height="{ch - hf:.1f}" class="back"/>'
                    f'<text x="{x + cw / 2:.1f}" y="{ch - hf - 6:.1f}" class="pc">{t["share"]:.0f}%</text>'
                    f'<text x="{x + cw / 2:.1f}" y="{ch + 14:.1f}" class="cl2">{t["cycle"]}.</text>'
                    f'<text x="{x + cw / 2:.1f}" y="{ch + 26:.1f}" class="cl3">{esc(CYCLE_SPAN.get(t["cycle"], "").split(" – ")[0][:4])}</text>'
                    f'<title>{t["cycle"]}. ciklus · {hu_num(t["first"])} első ciklusos a {hu_num(t["n"])} mandátumot viselőből</title></g>')
    peak = max(turn, key=lambda t: t["share"])
    low = min(turn, key=lambda t: t["share"])

    deg = Counter((p.get("highest_degree") or "").strip() for p in people.values())
    deg_known = sum(v for k, v in deg.items() if k)
    seg, drows = [], []
    for name in DEGREE_ORDER + sorted(k for k in deg if k and k not in DEGREE_ORDER):
        v = deg.get(name, 0)
        if not v:
            continue
        seg.append(f'<i style="--w:{100 * v / deg_known:.2f}%" title="{esc(name)}: {hu_num(v)}"></i>')
        drows.append(f'<tr><td>{esc(name)}</td><td class="num mono">{hu_num(v)}</td><td class="num mono">{100 * v / deg_known:.0f}%</td></tr>')
    lang = Counter()
    for p in people.values():
        for l in {(x.get("language") or "").strip() for x in p.get("languages") or []} - {""}:
            lang[l] += 1
    lang_people = sum(1 for p in people.values() if p.get("languages"))
    lmax = max(lang.values(), default=1)
    lrows = "".join(f'<tr><td>{esc(k)}</td><td class="num mono">{hu_num(v)}</td>'
                    f'<td class="lb"><i style="--w:{100 * v / lmax:.1f}%"></i></td></tr>' for k, v in lang.most_common(10))
    local = sum(1 for p in people.values() if p.get("local_offices"))
    kinds = Counter()
    for p in people.values():
        for t in p.get("local_offices") or []:
            if t.get("body_kind"):
                kinds[t["body_kind"]] += 1
    krows = "".join(f'<tr><td>{esc(k)}</td><td class="num mono">{hu_num(v)}</td></tr>' for k, v in kinds.most_common(6))
    fill = "".join(f'<tr><td class="mono">{r["cycle"]}.</td><td class="num mono">{hu_num(len(r["mps"] or {}))}</td>'
                   f'<td class="num mono">{100 * sum(1 for m in (r["mps"] or {}).values() if m.get("schools")) / max(len(r["mps"] or {}), 1):.0f}%</td>'
                   f'<td class="num mono">{100 * sum(1 for m in (r["mps"] or {}).values() if m.get("languages")) / max(len(r["mps"] or {}), 1):.0f}%</td>'
                   f'<td class="num mono">{100 * sum(1 for m in (r["mps"] or {}).values() if m.get("local_offices")) / max(len(r["mps"] or {}), 1):.0f}%</td></tr>'
                   for r in sorted(rows, key=lambda r: -r["cycle"]))
    return page_head("A Ház arcéle · karzat",
                     "Kik ülnek az Országgyűlésben: ciklusonként hányan vannak első ciklusban, és mit rögzít a nyilvántartás "
                     "az 1990 óta megválasztottak végzettségéről, nyelveiről és önkormányzati múltjáról.", 1) + \
        topbar(inp, [("arcél", None)], 1) + f"""
<div class="hero-h"><h1>A Ház arcéle</h1><small class="label" data-kz-text>{hu_num(len(people))} ember {hu_num(len(turn))} ciklusban · 1990 óta</small>
<p class="lede">Egyetlen mutató hasonlítható itt ciklusról ciklusra: hányan ülnek először a Házban. A mandátumokat 1990 óta hiánytalanul rögzítik, ezt tehát össze lehet vetni. A végzettség, a nyelv és az önkormányzati múlt nem ilyen — azok az adatlap mai állapotát mutatják, dátum nélkül, egyenetlenül kitöltve, ezért itt egyszer szerepelnek, mindenkire együtt.</p></div>
<section class="panel deep turn">{CORNERS}
  <h2><span data-kz-text>Újak és visszatérők</span><span class="tag">világos: első ciklusos · sötét: már ült a Házban</span></h2>
  <div class="turnwrap"><svg viewBox="0 -14 {width:.0f} {ch + 42:.0f}" role="img"
    aria-label="Ciklusonként az első ciklusos képviselők aránya 1990-től: {", ".join(f'{t["cycle"]}. ciklus {t["share"]:.0f} százalék' for t in turn)}.">{"".join(bars)}</svg></div>
  <div class="hero-meta">A ciklus névsora mindenki, aki a ciklus alatt mandátumot viselt, az évközi belépőkkel együtt — ezért nagyobb a szám a 199 fős Házban is. „Első ciklusos”: a képviselői adatlap választási sorai szerint ez az első ciklusa. A legnagyobb csere a {peak["cycle"]}. ciklusé ({peak["share"]:.0f}%), a legkisebb a {low["cycle"]}. ciklusé ({low["share"]:.0f}%).</div>
</section>
<section class="grid">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Legmagasabb végzettség</span><span class="tag">{hu_num(deg_known)} emberről van adat</span></h2>
    <div class="paybar">{"".join(seg)}</div>
    <div class="tablewrap" style="border:0"><table><tbody>{"".join(drows)}</tbody></table></div>
  </section>
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Önkormányzati múlt</span><span class="tag">{hu_num(local)} embernél szerepel ilyen tisztség</span></h2>
    <div class="tablewrap" style="border:0"><table><tbody>{krows}</tbody></table></div>
    <div class="hero-meta">Az adatlap „egyéb választott testületi tagság” sora: önkormányzati képviselő, polgármester, megyei közgyűlési tag. Évszámmal, nap nélkül.</div>
  </section>
</section>
<section class="grid">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Nyelvek</span><span class="tag">{hu_num(lang_people)} embernél van nyelvsor</span></h2>
    <div class="tablewrap" style="border:0"><table><tbody>{lrows}</tbody></table></div>
  </section>
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Mit rögzít a nyilvántartás</span><span class="tag">a ciklus névsorának hány százalékánál van kitöltve</span></h2>
    <div class="tablewrap" style="border:0"><table><thead><tr><th>Ciklus</th><th class="num">Fő</th><th class="num">Végzettség</th><th class="num">Nyelv</th><th class="num">Önkormányzat</th></tr></thead><tbody>{fill}</tbody></table></div>
    <div class="hero-meta">Ezért nem bontja az oldal ciklusra a fenti hármat: a különbség nagyrészt azt mutatja, mennyire van kitöltve a régi adatlap, nem azt, milyen volt akkor a Ház.</div>
  </section>
</section>
{cite_html(inp, 'arcel/index.html', 'A Ház arcéle', 'arcel')}
""" + page_tail(inp, 1)


def build_coverage_page(inp: dict, by_cycle: dict[int, list[dict]]) -> str:
    """lefedettseg/index.html — what the record reaches, and where it stops.

    Every other page on the site answers a question with data. This one draws the data's own edge: month by month
    since 1990, how many votes the listing holds and how many of those the House recorded by name. Two facts do the
    work. Before 1998 no vote carries a name list — that is the institution, not a gap in the fetch. And 26 sitting
    days are permanently short, because the listing service returns at most 400 rows per response, keeps the latest,
    and accepts no time, offset or page to reach past them.

    Nothing here is an estimate: the counts are the same derived indexes every cycle page is built from, and the
    truncated days are the ones the day-level rescan could not close (reference/parlament/listakorlat.json)."""
    cap = load_json(CAP_FILE) if CAP_FILE.exists() else {}
    short_days = [d.replace(".", "-").strip("-") for d in cap.get("days_still_truncated") or []]
    short_months = Counter(d[:7] for d in short_days)
    months = sorted({r["month"] for rows in by_cycle.values() for r in rows})
    if not months:
        return ""
    # a cycle ends and the next begins inside one month (2026-05 belongs to both 42 and 43), so the months add up
    # across cycles rather than the later cycle's row replacing the earlier one
    by_month: dict[str, dict] = {}
    for rows in by_cycle.values():
        for r in rows:
            t = by_month.setdefault(r["month"], {"month": r["month"], "votes": 0, "named": 0, "secret": 0})
            for k in ("votes", "named", "secret"):
                t[k] += r[k]
    first_month = {c: min(r["month"] for r in rows) for c, rows in by_cycle.items() if rows}
    idx = {m: i for i, m in enumerate(months)}
    w, gap, band = 3.0, 0.5, 130.0
    width = len(months) * w
    peak = max(r["votes"] for r in by_month.values()) or 1
    top, lane_a, lane_b = 14.0, band + 20.0, band + 32.0
    height = lane_b + 26.0

    cols, marks, rules = [], [], []
    for m in months:
        r = by_month[m]
        x = idx[m] * w
        h = r["votes"] / peak * band
        y = top + band - h
        hn = (r["named"] / peak * band) if r["named"] else 0.0
        tip = f'{m} · {hu_num(r["votes"])} szavazás · név szerint {hu_num(r["named"])}'
        if r["secret"]:
            tip += f' · titkos {hu_num(r["secret"])}'
        if short_months.get(m):
            tip += f' · {short_months[m]} csonka nap'
        cols.append(f'<g class="mc"><rect x="{x:.2f}" y="{y:.2f}" width="{w - gap:.2f}" height="{h:.2f}" class="all"/>'
                    + (f'<rect x="{x:.2f}" y="{top + band - hn:.2f}" width="{w - gap:.2f}" height="{hn:.2f}" class="named"/>' if hn else "")
                    + f'<rect x="{x:.2f}" y="{top:.2f}" width="{w - gap:.2f}" height="{band:.2f}" class="hit"/><title>{esc(tip)}</title></g>')
        if short_months.get(m):
            marks.append(f'<rect x="{x:.2f}" y="{lane_a:.2f}" width="{w - gap:.2f}" height="7" class="short"/>')
        if r["secret"]:
            marks.append(f'<rect x="{x:.2f}" y="{lane_b:.2f}" width="{w - gap:.2f}" height="7" class="secret"/>')
    for c, m in sorted(first_month.items()):
        x = idx[m] * w
        rules.append(f'<line x1="{x:.2f}" y1="{top - 8:.1f}" x2="{x:.2f}" y2="{top + band:.1f}" class="cyc"/>'
                     f'<text x="{x + 3:.2f}" y="{top - 3:.1f}" class="cl">{c}</text>')
    years = []
    for m in months:
        if m.endswith("-01") and int(m[:4]) % 5 == 0:
            years.append(f'<text x="{idx[m] * w:.2f}" y="{height - 4:.1f}" class="yl">{m[:4]}</text>')

    tot_v = sum(r["votes"] for r in by_month.values())
    tot_n = sum(r["named"] for r in by_month.values())
    tot_s = sum(r["secret"] for r in by_month.values())
    trs = []
    for c in sorted(by_cycle, reverse=True):
        rows = by_cycle[c]
        v = sum(r["votes"] for r in rows); nm = sum(r["named"] for r in rows); sc = sum(r["secret"] for r in rows)
        sd = len([d for d in short_days if any(d.startswith(r["month"]) for r in rows)])
        share = f'{100 * nm / v:.0f}%' if v else "—"
        trs.append(f'<tr><td class="mono"><a href="../{esc(cycle_dir(c))}index.html">{c}. ciklus</a>'
                   f'<span class="sub">{esc(CYCLE_SPAN.get(c, ""))}</span></td>'
                   f'<td class="num mono">{hu_num(v)}</td><td class="num mono">{hu_num(nm)}<span class="sub">{share}</span></td>'
                   f'<td class="num mono">{hu_num(sc) if sc else "—"}</td><td class="num mono">{hu_num(sd) if sd else "—"}</td></tr>')
    cap_note = (f'{hu_num(cap.get("votes_recovered_by_day_scan") or 0)} szavazás került elő a napi bontású újralistázással, '
                f'{hu_num(len(cap.get("months_originally_truncated") or []))} hónapból.') if cap else ""
    return page_head("Ameddig a jegyzőkönyv elér · karzat",
                     "Mit tartalmaz az Országgyűlés nyilvános adata és mit nem: hónapról hónapra a szavazások száma és az, "
                     "hányat rögzítettek név szerint — 1990 óta.", 1) + \
        topbar(inp, [("lefedettség", None)], 1) + f"""
<div class="hero-h"><h1>Ameddig a jegyzőkönyv elér</h1><small class="label" data-kz-text>{hu_num(tot_v)} szavazás · név szerint {hu_num(tot_n)} · {hu_num(len(months))} hónap 1990 óta</small>
<p class="lede">Az oldal minden más lapja az adatból válaszol. Ez a lap az adat szélét rajzolja meg: hol van név szerinti lista, hol csak összesítés, és hol hiányzik olyan, amit már nem lehet pótolni.</p></div>
<section class="panel deep cov">{CORNERS}
  <h2><span data-kz-text>Hónapról hónapra</span><span class="tag">világos: név szerint rögzítve · sötét: csak összesítés · az oszlop magassága a szavazások száma</span></h2>
  <div class="covwrap"><svg viewBox="0 0 {width:.0f} {height:.0f}" preserveAspectRatio="none" role="img"
    aria-label="Havi bontás 1990-től: az oszlop magassága az adott hónap szavazásainak száma, a világos rész a név szerint rögzítetteké. Alatta két jelölősáv: a lista 400-as korlátja miatt csonka napok, és a titkos szavazások hónapjai.">
    {"".join(rules)}{"".join(cols)}
    <line x1="0" y1="{top + band:.1f}" x2="{width:.0f}" y2="{top + band:.1f}" class="ax"/>
    {"".join(marks)}{"".join(years)}
  </svg></div>
  <div class="covkey"><span><i class="k named"></i>név szerinti lista</span><span><i class="k all"></i>csak összesítés</span>
    <span><i class="k short"></i>csonka nap ({hu_num(len(short_days))})</span><span><i class="k secret"></i>titkos szavazás ({hu_num(tot_s)})</span></div>
</section>
<section class="grid">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Ciklusonként</span></h2>
    <div class="tablewrap" style="border:0"><table><thead><tr><th>Ciklus</th><th class="num">Szavazás</th><th class="num">Név szerint</th><th class="num">Titkos</th><th class="num">Csonka nap</th></tr></thead><tbody>{"".join(trs)}</tbody></table></div>
  </section>
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Amit nem lehet pótolni</span></h2>
    <div class="prose hero-meta">
      <p><b>Név szerinti lista 1998 előtt nincs.</b> A 34. és a 35. ciklus szavazásainál a válasz üres névsort ad — nem a lekérdezés hiányos, az Országgyűlés akkor még nem így rögzítette. Ezeknél a szavazásoknál csak az igen / nem / tartózkodás összesítés van meg, és az oldal sem mutat többet.</p>
      <p><b>{hu_num(len(short_days))} ülésnap véglegesen csonka.</b> A szavazáslista egy válaszban legfeljebb 400 sort ad, és a legkésőbbieket tartja meg; a paraméterei csak dátumot fogadnak, időpontot nem. Amelyik napon egymagában több mint 400 szavazás volt, annak a korábbi szavazásaihoz ezen a végponton nem vezet út. {cap_note}</p>
      <p><b>{hu_num(tot_s)} titkos szavazás.</b> Ezeknél nincs mit letölteni: a szavazás titkos volt, az eredmény nyilvános, a leadott szavazatok nem.</p>
    </div>
  </section>
</section>
{cite_html(inp, 'lefedettseg/index.html', 'Ameddig a jegyzőkönyv elér', 'lefedettseg')}
""" + page_tail(inp, 1)


def site_today(inp: dict) -> str:
    """The newest vote's day — the site's own present tense. Deterministic: it comes from the committed index, not
    from the clock, so a rebuild of the same inputs draws the same open-ended bar."""
    newest = max((v["ts"] for v in inp["idx"]["votes"]), default=None)
    return f"{newest[:4]}-{newest[5:7]}-{newest[8:10]}" if newest else "2026-01-01"


def _yr(d: str | None) -> float | None:
    """A year as a number on the axis: '1998-06-18' -> 1998.42, '2010' -> 2010.0, '' -> None. The record dates
    mandates and offices to the day and local-government seats to the year, so both forms have to land."""
    if not d:
        return None
    s = str(d).strip()
    m = re.match(r"^(\d{4})-(\d{2})", s)
    if m:
        return int(m.group(1)) + (int(m.group(2)) - 1) / 12.0
    m = re.match(r"^(\d{4})$", s)
    return float(m.group(1)) if m else None


LIFE_W, LIFE_L, LIFE_R = 1000.0, 116.0, 12.0        # viewBox width, and the gutters the lane names and the last label need
LIFE_LANE, LIFE_BAR = 26.0, 12.0                    # one lane's height, and the bar drawn inside it


def life_path_svg(inp: dict, latest: dict, stints: list[dict]) -> str:
    """One person on one time axis: the cycles they sat, the offices the record dates, the local-government seats
    they held before or between, and the years their degrees carry. The tables above say the same things in
    columns; this says when, and against each other — which is the one question a column cannot answer.

    Every span is the record's own: <valasztasok> for mandates, <tisztsegek> for offices, <egyeb-tagsagok> for the
    local seats (years, so a year is drawn whole), <iskolak> for the degree years. A span the record leaves open
    runs to the newest vote on the site, not to the clock."""
    facs = inp["facs_all"]
    end_open = _yr(site_today(inp)) or 2026.0
    fac_by_cycle = {st["cycle"]: st.get("faction") for st in stints if st.get("faction")}
    # a cycle the site has not loaded still has a colour: the record's own faction rows carry the label, latest first
    for fr in latest.get("factions") or []:
        c = CYCLE_BY_LABEL.get(fr.get("ciklus"))
        if c and fr.get("faction"):
            fac_by_cycle.setdefault(c, fr["faction"])
    lanes: list[tuple[str, list[tuple[float, float, str, bool, str]]]] = []

    mandates = []
    for e in latest.get("elections") or []:
        a = _yr(e.get("mandate_from"))
        if a is None:
            continue
        c = CYCLE_BY_LABEL.get(e.get("ciklus"))
        col = facs.get(fac_by_cycle.get(c) or "", "#8a8a8a")
        where = e.get("constituency") or ""
        mandates.append((a, _yr(e.get("mandate_to")) or end_open, col, False,
                         " · ".join(x for x in [f"{c}. ciklus" if c else (e.get("ciklus") or ""), where] if x)))
    if mandates:
        lanes.append(("Országgyűlés", mandates))

    offices = []
    for o in latest.get("offices") or []:
        a = _yr(o.get("from"))
        if a is None:
            continue
        offices.append((a, _yr(o.get("to")) or end_open, "#d4d4d8", False, o.get("office") or ""))
    if offices:
        lanes.append(("Tisztség", offices))

    local = []
    for t in latest.get("local_offices") or []:
        a = _yr(t.get("from"))
        if a is None:
            continue
        b = _yr(t.get("to"))
        local.append((a, (b + 1.0) if b is not None else end_open, "#8a8a93", True,   # a year is inclusive: 2006–2010 ends when 2010 does
                      " · ".join(x for x in [t.get("body") or "", t.get("office") or ""] if x)))
    if local:
        lanes.append(("Önkormányzat", local))

    by_year: dict[int, list[str]] = {}                  # two degrees in one year are one dot, not two on top of each other
    for s in latest.get("schools") or []:
        if s.get("year"):
            by_year.setdefault(s["year"], []).append(" · ".join(x for x in [s.get("institution") or "", s.get("degree") or ""] if x))
    schools = sorted((y, " / ".join(v)) for y, v in by_year.items())
    if not lanes:
        return ""
    lo = min([sp[0] for _, rows in lanes for sp in rows] + [float(y) for y, _ in schools] or [end_open])
    hi = max([sp[1] for _, rows in lanes for sp in rows] + [float(y) for y, _ in schools] or [end_open])
    lo, hi = math.floor(lo) - 1, math.ceil(hi) + 1
    span = max(hi - lo, 1)
    plot = LIFE_W - LIFE_L - LIFE_R
    def x(v: float) -> float:
        return LIFE_L + (v - lo) / span * plot
    axis_y = len(lanes) * LIFE_LANE + (18.0 if schools else 4.0)
    height = axis_y + 22.0

    parts = []
    step = 10 if span > 28 else 5
    for yr in range(lo - lo % step, hi + 1, step):
        if yr < lo:
            continue
        parts.append(f'<line x1="{x(yr):.1f}" y1="0" x2="{x(yr):.1f}" y2="{axis_y:.1f}" class="g"/>'
                     f'<text x="{x(yr):.1f}" y="{height - 6:.1f}" class="yr">{yr}</text>')
    for i, (name, rows) in enumerate(lanes):
        cy = i * LIFE_LANE + LIFE_LANE / 2
        parts.append(f'<text x="{LIFE_L - 10:.1f}" y="{cy + 3.5:.1f}" class="ln">{esc(name)}</text>')
        for a, b, col, hollow, label in sorted(rows):
            x0, x1 = x(a), max(x(b), x(a) + 2.4)
            style = (f'fill="none" stroke="{col}" stroke-width="1"' if hollow else f'fill="{col}"')
            parts.append(f'<g class="sp"><rect x="{x0:.1f}" y="{cy - LIFE_BAR / 2:.1f}" width="{x1 - x0:.1f}" height="{LIFE_BAR:.1f}" {style}/>'
                         f'<title>{esc(label)}</title></g>')
    for yr, what in schools:
        parts.append(f'<g class="sp"><circle cx="{x(float(yr)):.1f}" cy="{axis_y - 9:.1f}" r="3" class="sch"/>'
                     f'<title>{esc(f"{yr} · {what}")}</title></g>')
    if schools:
        parts.append(f'<text x="{LIFE_L - 10:.1f}" y="{axis_y - 5.5:.1f}" class="ln">Tanulmány</text>')
    parts.append(f'<line x1="{LIFE_L:.1f}" y1="{axis_y:.1f}" x2="{LIFE_W - LIFE_R:.1f}" y2="{axis_y:.1f}" class="ax"/>')
    legend = " · ".join(x for x in ["tömör sáv: mandátum, a frakció színével" if mandates else "",
                                    "világos sáv: tisztség" if offices else "",
                                    "üres sáv: önkormányzati tisztség" if local else "",
                                    "pont: a végzettség éve" if schools else ""] if x)
    return (f'<section class="panel life">{CORNERS}'
            f'<h2><span data-kz-text>Az életút</span><span class="tag">{esc(legend)}</span></h2>'
            f'<svg viewBox="0 0 {LIFE_W:.0f} {height:.0f}" role="img" aria-label="{esc(latest["name"])} pályája időrendben: '
            f'{esc(", ".join(n.lower() for n, _ in lanes))}{", a végzettségek éve" if schools else ""}, {lo + 1}-tól {hi - 1}-ig.">'
            f'{"".join(parts)}</svg>'
            f'<div class="hero-meta">A képviselői adatlap dátumozott sorai egy tengelyen. A nyitva hagyott sáv a legutóbbi '
            f'szavazás napjáig fut ({esc(hu_date(site_today(inp)))}), nem a mai napig.</div></section>')


def declarations_html(latest: dict) -> str:
    """The asset declarations the record lists: one square per document, grouped by the year it states, linking the
    published PDF. A square the record marks as due but carries no link for is left hollow — that is all the API
    says about it, and the page says no more."""
    ds = [d for d in latest.get("declarations") or [] if d.get("as_of") or d.get("title")]
    if not ds:
        return ""
    by_year: dict[str, list[dict]] = {}
    for d in sorted(ds, key=lambda d: (d.get("as_of") or "", d.get("title") or "")):
        yr = (d.get("as_of") or "")[:4] or "".join(ch for ch in (d.get("title") or "") if ch.isdigit())[:4] or "—"
        by_year.setdefault(yr, []).append(d)
    filed = sum(1 for d in ds if d.get("filed") and d.get("url"))
    groups = []
    for yr, items in sorted(by_year.items()):
        cells = []
        for d in items:
            tip = " · ".join(x for x in [d.get("title") or "", f'állapot {hu_date(d["as_of"])}' if d.get("as_of") else "",
                                         f'határidő {hu_date(d["due"])}' if d.get("due") else ""] if x)
            if d.get("url"):
                cells.append(f'<a href="{esc(d["url"])}" target="_blank" rel="noopener" title="{esc(tip)}"><span class="vs">{esc(tip)}</span></a>')
            else:
                cells.append(f'<span class="due" title="{esc(tip + " · nincs közzétett dokumentum")}"></span>')
        groups.append(f'<span class="yr"><span class="sq">{"".join(cells)}</span><b>{esc(yr)}</b></span>')
    due_only = len(ds) - filed
    tag = f'{hu_num(filed)} közzétett dokumentum' + (f' · {hu_num(due_only)} sornál nincs' if due_only else "")
    return (f'<section class="panel">{CORNERS}'
            f'<h2><span data-kz-text>Vagyonnyilatkozatok</span><span class="tag">{esc(tag)}</span></h2>'
            f'<div class="vny">{"".join(groups)}</div>'
            f'<div class="hero-meta">A négyzet a képviselői adatlapon szereplő nyilatkozat; a kitöltött a parlament.hu-n '
            f'közzétett PDF-re visz, az üresnél az adatlap nem ad hivatkozást. A dokumentumok tartalmát az oldal nem dolgozza fel.</div></section>')


def remuneration_html(latest: dict) -> str:
    """The one month of remuneration the record carries. The service publishes no history, so the page says which
    month this is and does not imply a series."""
    rows = [p for p in latest.get("remuneration") or [] if p.get("gross")]
    if not rows:
        return ""
    p = rows[0]
    parts = [("tiszteletdíj", p.get("gross") or 0), ("választókerületi pótlék", p.get("constituency_allowance") or 0),
             ("lakhatási támogatás", p.get("housing_allowance") or 0)]
    total = sum(v for _, v in parts) or 1
    bar = "".join(f'<i style="--w:{100 * v / total:.2f}%;--o:{1 - 0.3 * i:.2f}" title="{esc(lbl)}: {hu_num(v)} Ft"></i>'
                  for i, (lbl, v) in enumerate(parts) if v)
    shown = [(lbl, v) for lbl, v in parts if v]
    lines = "".join(f'<tr><td>{esc(lbl)}</td><td class="num mono">{hu_num(v)} Ft</td></tr>' for lbl, v in shown)
    if len(shown) > 1:                                  # a total repeats the single line when there is only one
        lines += f'<tr><td><b>összesen</b></td><td class="num mono"><b>{hu_num(total)} Ft</b></td></tr>'
    return (f'<section class="panel">{CORNERS}'
            f'<h2><span data-kz-text>Javadalmazás</span><span class="tag">{esc(p.get("period") or "")} · bruttó</span></h2>'
            f'<div class="paybar">{bar}</div>'
            f'<div class="tablewrap" style="border:0"><table><tbody>{lines}</tbody></table></div>'
            f'<div class="hero-meta">Egyetlen hónap: az adatlap ennyit közöl, előzményt nem. Ez a lekérdezés hónapja, nem havi átlag.</div></section>')


def schooling_html(latest: dict) -> str:
    """Degrees and languages, as the record states them — the two sections that describe the person rather than the
    mandate. Nothing is inferred: an empty level or faculty is simply left out."""
    sc = latest.get("schools") or []
    lg = latest.get("languages") or []
    if not sc and not lg:
        return ""
    srows = []
    for s in sorted(sc, key=lambda s: -(s.get("year") or 0)):
        fac = f'<span class="sub">{esc(s["faculty"])}</span>' if s.get("faculty") else ""
        srows.append(f'<tr><td class="mono">{esc(s.get("year") or "")}</td><td>{esc(s.get("institution") or "—")}{fac}</td>'
                     f'<td>{esc(s.get("degree") or "—")}</td></tr>')
    srows = "".join(srows)
    langs = " · ".join(f'{esc(l["language"])} <span class="sub">{esc(l["level"] or "")}</span>' for l in lg if l.get("language"))
    hi = latest.get("highest_degree")
    hi_tag = f'<span class="tag">legmagasabb: {esc(hi)}</span>' if hi else ""
    return (f'<section class="panel">{CORNERS}'
            f'<h2><span data-kz-text>Végzettség és nyelvek</span>{hi_tag}</h2>'
            + (f'<div class="tablewrap" style="border:0"><table><thead><tr><th>Év</th><th>Intézmény</th><th>Végzettség</th></tr></thead>'
               f'<tbody>{srows}</tbody></table></div>' if srows else "")
            + (f'<div class="hero-meta" style="margin-top:8px">Nyelvismeret: {langs}.</div>' if langs else "")
            + '</section>')


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
        f'<a href="{esc(latest["biography_url"])}" target="_blank" rel="noopener">önéletrajz ↗</a>' if latest.get("biography_url") else "",
        f'<a href="https://www.wikidata.org/wiki/{esc(latest["wikidata_qid"])}" target="_blank" rel="noopener">Wikidata {esc(latest["wikidata_qid"])} ↗</a>' if latest.get("wikidata_qid") else ""] if x)
    loaded = ", ".join(f"{st['cycle']}." for st in stints)
    return page_head(f'{name} — pályakép · karzat', f'{name} az Országgyűlésben: ciklusonként a részvétele és a frakciójával való egyezése, a képviselői adatlap frakció- és mandátumtörténete.', 1) + \
        topbar(inp, [("személyek", "index.html"), (name, None)], 1) + f"""
<div class="hero-h withpic">{portrait_html({"photo_url": latest.get("photo_url"), "name": name}, "hero")}<div><h1>{esc(name)}</h1><small class="label" data-kz-text>pályakép · {len(stints)} betöltött ciklus{" · ma is képviselő" if latest.get("current") else ""}</small>
<p class="hero-meta">{links}</p></div></div>
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
{life_path_svg(inp, latest, stints)}
<section class="grid">{schooling_html(latest)}{remuneration_html(latest)}</section>
{declarations_html(latest)}
{cite_html(inp, f'szemely/{azon}.html', f'{name} — pályakép', f'szemely-{azon}')}
""" + page_tail(inp, 1)


def build_spokesperson_page(inp: dict, azon: str, r: dict) -> str:
    """szemely/<azon>.html for a nationality spokesperson: the same address as a career page, a different content —
    there is no vote to count. Cycle by cycle: the committees the record dates to that cycle and the record's own
    speech count; then the whole committee history and the seat. What is missing is named: szoszolok.cgi is
    present-tense, so an earlier term is only here if the record's dated rows show it."""
    cycles = r.get("cycles") or []
    rows = []
    for st in cycles:
        coms = st["committees"]
        names = " · ".join(dict.fromkeys(f'{c["committee"]}{" / " + c["subcommittee"] if c.get("subcommittee") else ""}{" (" + c["role"] + ")" if c.get("role") else ""}' for c in coms))
        sp = st.get("speeches")
        rows.append(f'<tr><td class="mono">{st["cycle"]}. ciklus<span class="sub">{esc(CYCLE_SPAN.get(st["cycle"], ""))}</span></td>'
                    f'<td>{esc(cut(names, 140)) or "—"}<span class="sub">{hu_num(len(coms))} tagsági sor</span></td>'
                    f'<td class="num mono">{hu_num(sp) if sp is not None else "—"}</td></tr>')
    com_rows = "".join(f'<tr><td>{esc(c["committee"] or "")}{(" <span class=" + chr(34) + "sub" + chr(34) + ">" + esc(c["subcommittee"]) + "</span>") if c.get("subcommittee") else ""}</td>'
                       f'<td>{esc(c.get("role") or "")}</td><td class="ts mono">{esc(c.get("from") or "")}{" – " + esc(c["to"]) if c.get("to") else " –"}</td></tr>'
                       for st in cycles for c in st["committees"])
    seat = r.get("seat") or {}
    seat_txt = f'{seat["sector"]}. szektor, {seat["row"]}. sor, {seat["seat"]}. szék' if seat.get("sector") is not None else "—"
    n_sp = sum(st["speeches"] or 0 for st in cycles)
    links = " · ".join(x for x in [
        f'<a href="{esc(ext_url(r.get("parlament_url")))}" target="_blank" rel="noopener">parlament.hu adatlap ↗</a>' if ext_url(r.get("parlament_url")) else "",
        f'<a href="{esc(ext_url(r.get("website")))}" target="_blank" rel="noopener">honlap ↗</a>' if ext_url(r.get("website")) else "",
        ] if x)
    name = r["name"]
    return page_head(f'{name} — nemzetiségi szószóló · karzat',
                     f'{name}, a {r["nationality"]} nemzetiség szószólója az Országgyűlésben: ülőhely, bizottságok, felszólalások ciklusonként. A szószóló nem szavaz.', 1) + \
        topbar(inp, [("személyek", "index.html"), (name, None)], 1) + f"""
<div class="hero-h withpic">{portrait_html({"photo_url": r.get("photo_url"), "name": name}, "hero")}<div><h1>{esc(name)}</h1><small class="label" data-kz-text>nemzetiségi szószóló · {esc(r["nationality"] or "")} · {hu_num(len(cycles))} ciklus</small>
<p class="hero-meta">{links}</p></div></div>
<div class="fresh">{CORNERS}<span class="hu">A nemzetiségi szószóló ül az ülésteremben, felszólalhat és bizottságban dolgozik, de nem szavaz: a név szerinti listák sosem tartalmazzák, így itt nincs részvételi vagy frakciófegyelem-szám.</span>
<span class="en" lang="en">A nationality spokesperson sits, speaks and works in committees but casts no vote: no roll call names them, so there is no participation or discipline figure here.</span></div>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Ciklusonként</span><span class="tag">a saját adatlapja dátumozott sorai szerint · összesen {hu_num(n_sp)} felszólalás</span></h2>
  <div class="tablewrap"><table><thead><tr><th scope="col">Ciklus</th><th scope="col">Bizottságok</th><th scope="col" class="num">Felszólalás</th></tr></thead><tbody>{"".join(rows) or '<tr><td colspan="3">—</td></tr>'}</tbody></table></div>
  <div class="hero-meta prose" style="margin-top:8px">A szószólók listája (<span class="mono">szoszolok.cgi</span>) jelen idejű: a mai szószólókat adja. A korábbi ciklusok innen csak akkor látszanak, ha a képviselői adatlap dátumozott sorai (bizottsági tagság, felszólalás-statisztika) mutatják őket — ezért lehet, hogy egy régebbi megbízatás hiányzik.</div>
</section>
<section class="grid">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Bizottsági tagságok</span><span class="tag">{hu_num(sum(len(st["committees"]) for st in cycles))} sor</span></h2>
    <div class="tablewrap" style="border:0"><table><thead><tr><th>Bizottság</th><th>Tisztség</th><th>Időszak</th></tr></thead><tbody>{com_rows or '<tr><td colspan="3">—</td></tr>'}</tbody></table></div>
  </section>
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Ülőhely</span></h2>
    <div class="hero-meta">A jelenlegi ciklusban: {esc(seat_txt)} — a <a href="../index.html">nyitólap patkóján</a> gyűrűvel jelölve.</div>
    <div class="hero-meta prose" style="margin-top:8px">Nemzetiség: {esc(r["nationality"] or "—")}. A szószólót a nemzetiségi lista küldi, ha a lista nem szerez mandátumot.</div>
  </section>
</section>
{cite_html(inp, f'szemely/{azon}.html', f'{name} — nemzetiségi szószóló', f'szemely-{azon}')}
""" + page_tail(inp, 1)


def build_kormany_page(inp: dict, azon: str, r: dict) -> str:
    """szemely/<azon>.html for someone who sits in the House without a mandate — a minister who is not an MP. What
    exists of them here: the office the record of their speeches prints, their seat on the ministerial bench, and
    every speech of theirs in the current cycle. No vote, no faction, no participation: they hold no mandate."""
    texts = (inp["texts"] or {}).get("texts") or {}
    mine = sorted(((sid, t) for sid, t in texts.items() if t.get("azon") == azon), key=lambda kv: (kv[1].get("date") or "", kv[1].get("seq") or 0), reverse=True)
    cdir = cycle_dir(CURRENT_CYCLE)
    sp_rows = "".join(f'<tr><td class="ts mono"><a href="../{cdir}felszolalas/{esc(sid)}.html">{esc(t.get("date") or "")}</a></td>'
                      f'<td>{esc(cut(t.get("position") or "", 60))}</td><td>{esc(t.get("kind") or "")}</td>'
                      f'<td class="num mono">{hu_num(t.get("chars") or 0)}</td></tr>' for sid, t in mine)
    seat = r.get("seat") or {}
    seat_txt = f'miniszteri pad, {seat["seat"]}. szék' if seat.get("sector") == 0 else "—"
    offices = " · ".join(r.get("offices") or ([r["office"]] if r.get("office") else [])) or "—"
    links = " · ".join(x for x in [
        f'<a href="{esc(ext_url(r.get("parlament_url")))}" target="_blank" rel="noopener">parlament.hu adatlap ↗</a>' if ext_url(r.get("parlament_url")) else "",
        f'<a href="../{cdir}felszolalas/index.html">a ciklus felszólalásai ↗</a>'] if x)
    name = r["name"]
    # parlament.hu photographs the members it lists and answers 404 for everyone else, so the picture — when there is
    # one — is a Commons file, and it is credited here as its licence asks
    if r.get("photo_url"):
        lic = (f'<a href="{esc(ext_url(r["photo_licence_url"]))}" target="_blank" rel="noopener">{esc(r.get("photo_licence") or "licenc")}</a>'
               if r.get("photo_licence_url") else esc(r.get("photo_licence") or ""))
        wd = f' · <a href="https://www.wikidata.org/wiki/{esc(r["photo_qid"])}" target="_blank" rel="noopener">Wikidata {esc(r["photo_qid"])} ↗</a>' if r.get("photo_qid") else ""
        credit = (r.get("photo_credit") or "")
        if r.get("photo_licence") and lic and credit.endswith(r["photo_licence"]):
            credit = credit[:-len(r["photo_licence"])].rstrip(" ·")             # the licence follows as a link
        photo_note = f'{esc(credit)}{" · " + lic if lic else ""}{wd}'
    else:
        photo_note = "Nincs: a parlament.hu csak a képviselőket fényképezi, és szabad licencű képet sem találtunk hozzá."
    return page_head(f'{name} — kormánytag, nem képviselő · karzat',
                     f'{name} ({offices}) az Országgyűlésben: a miniszteri padban ül és felszólal, de nem képviselő — nem szavaz.', 1) + \
        topbar(inp, [("személyek", "index.html"), (name, None)], 1) + f"""
<div class="hero-h withpic">{portrait_html({"photo_url": r.get("photo_url"), "photo_credit": r.get("photo_credit"), "name": name}, "hero")}<div><h1>{esc(name)}</h1><small class="label" data-kz-text>kormánytag, nem képviselő · {esc(cut(offices, 90))}</small>
<p class="hero-meta">{links}</p></div></div>
<div class="fresh">{CORNERS}<span class="hu">A kormány tagjának nem kell képviselőnek lennie. Aki nem az, az ülésteremben a miniszteri padban ül és felszólal, de nem szavaz: a név szerinti listák sosem tartalmazzák, mandátuma, frakciója, választókerülete nincs.</span>
<span class="en" lang="en">A minister need not hold a mandate. One who does not sits on the ministerial bench and speaks, but casts no vote: no roll call names them, and they have no mandate, faction or constituency.</span></div>
<section class="grid">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Tisztség és hely</span></h2>
    <div class="tablewrap" style="border:0"><table><tbody>
      <tr><td>Tisztség</td><td>{esc(offices)}</td></tr>
      <tr><td>Ülőhely</td><td>{esc(seat_txt)} — a <a href="../index.html">nyitólap patkóján</a> halvány koronggal</td></tr>
      <tr><td>Felszólalás a {CURRENT_CYCLE}. ciklusban</td><td class="mono">{hu_num(r.get("speeches") or 0)}{" · legutóbb " + esc(hu_date(r["last_spoke"])) if r.get("last_spoke") else ""}</td></tr>
      <tr><td>Fénykép</td><td>{photo_note}</td></tr>
    </tbody></table></div>
    <div class="hero-meta prose" style="margin-top:8px">A tisztség onnan van, ahogy a jegyzőkönyv a felszólalásainál megnevezi (<span class="mono">beosztás</span>); az oldal nem tart nyilván kormánynévsort. Aki a ciklusban nem szólalt fel, az így nem is látszik — ezért lehet, hogy a miniszteri padban marad üres hely.</div>
  </section>
  <section class="panel deep">{CORNERS}
    <h2><span data-kz-text>Felszólalásai</span><span class="tag">{hu_num(len(mine))} tétel · jegyzőkönyvi szöveggel</span></h2>
    <div class="tablewrap"><table data-page-size="25"><thead><tr><th scope="col">Nap</th><th scope="col">Tisztség</th><th scope="col">Fajta</th><th scope="col" class="num">Karakter</th></tr></thead><tbody>{sp_rows or '<tr><td colspan="4">—</td></tr>'}</tbody></table></div>
  </section>
</section>
{cite_html(inp, f'szemely/{azon}.html', f'{name} — kormánytag, nem képviselő', f'szemely-{azon}')}
""" + page_tail(inp, 1)


def build_person_index(inp: dict, people: dict[str, list[dict]], szoszolok: dict | None = None, kormany: dict | None = None) -> str:
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
    # the nationality spokespersons sit in the same rows, with a dash where a vote count would be: they cast none
    sz_rows = []
    for azon, r in sorted((szoszolok or {}).items(), key=lambda kv: name_key(kv[1]["name"])):
        cyc = [st["cycle"] for st in r.get("cycles") or []]
        sz_rows.append(f'<tr data-f="szószóló" data-name="{esc(name_key(r["name"]))}" data-n="{len(cyc)}" data-against="0"><td><a href="{esc(azon)}.html">{esc(r["name"])}</a></td>'
                       f'<td><span class="pos"><i class="d" style="--c:{SZOSZOLO_COLOUR};background:transparent;box-shadow:inset 0 0 0 2px {SZOSZOLO_COLOUR}"></i>{esc(r["nationality"] or "")} szószóló</span></td>'
                       f'<td class="mono">{" · ".join(str(c) for c in sorted(cyc))}</td><td class="num mono">{len(cyc)}</td><td class="num mono">—</td><td class="num mono">—</td></tr>')
    km_rows = []
    for azon, r in sorted((kormany or {}).items(), key=lambda kv: name_key(kv[1]["name"])):
        km_rows.append(f'<tr data-f="kormánytag" data-name="{esc(name_key(r["name"]))}" data-n="1" data-against="0"><td><a href="{esc(azon)}.html">{esc(r["name"])}</a></td>'
                       f'<td><span class="pos"><i class="d" style="--c:{KORMANY_COLOUR}"></i>{esc(cut(r.get("office") or "kormánytag", 40))}</span></td>'
                       f'<td class="mono">{CURRENT_CYCLE}</td><td class="num mono">1</td><td class="num mono">—</td><td class="num mono">—</td></tr>')
    trs = sorted(trs + sz_rows + km_rows, key=lambda t: name_key(re.search(r'data-name="([^"]*)"', t).group(1)))
    n_sz = len(szoszolok or {})
    n_km = len(kormany or {})
    return page_head("Személyek · karzat", "Mindenki, aki a betöltött ciklusok névsoraiban szerepel, és a nemzetiségi szószólók — egy pályakép-oldal személyenként, ciklusokon át.", 1) + \
        topbar(inp, [("személyek", None)], 1) + f"""
<div class="hero-h"><h1>Személyek</h1><small class="label" data-kz-text>{len(people)} képviselő a betöltött ciklusok névsoraiból{f" · {n_sz} nemzetiségi szószóló" if n_sz else ""}{f" · {n_km} mandátum nélküli kormánytag" if n_km else ""} · egy oldal személyenként, ciklusokon át</small></div>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Pályaképek</span><span class="tag">a fejlécre kattintva rendezhető</span></h2>
  <div class="filters"><input type="search" data-filter-table="roll" placeholder="név" aria-label="Szűrés névre" style="min-width:160px"><span class="n" id="rn" aria-live="polite"></span></div>
  <div class="tablewrap"><table id="roll" data-page-size="25" data-counter="rn"><thead><tr><th scope="col" class="sortable" data-key="name">Név</th><th scope="col" class="sortable" data-key="f">Utolsó frakció</th><th scope="col">Ciklusok</th><th scope="col" class="sortable num" data-key="n">Ciklus</th><th scope="col" class="num">Leadott / névsorban</th><th scope="col" class="sortable num" data-key="against">Frakció ellen</th></tr></thead><tbody>{"".join(trs)}</tbody></table></div>
</section>
""" + page_tail(inp, 1)


# -- the landing page -----------------------------------------------------------------------------
# The site root: the view from the gallery — the chamber as it sits today, the ten cycles since 1990 as the way in,
# the last sitting day, the doors (my MP, search, the words, the channels, the data, the method). Every number on it
# is computed from the same derived inputs as the cycle pages; nothing here is typed.

CYCLE_START = {c: span.split(" – ")[0] for c, span in CYCLE_SPAN.items()}    # the constituent sitting = every mandate's first day


def composition_on(mps: dict, day: str, facs: list[dict]) -> list[tuple[str, int, str]]:
    """[(faction, mandates, colour)] in force on `day` from the records' dated mandate and faction rows — a mandate in
    force without a faction row that day counts as független. Ordered as the cycle's faction config orders the
    chamber (opposition left, government right); factions the config does not know come last, grey."""
    order = {f["id"]: f["sort_order"] for f in facs}
    colour = {f["id"]: f["colour"] for f in facs}
    n: Counter = Counter()
    for m in mps.values():
        mf, mt = m.get("mandate_from"), m.get("mandate_to")
        if not mf or mf > day or (mt and mt < day):
            continue
        fac = next((fr["faction"] for fr in m.get("factions") or [] if fr.get("from") and fr["from"] <= day and (not fr.get("to") or fr["to"] >= day)), None)
        n[fac or "független"] += 1
    return [(f, k, colour.get(f, "#8a8a8a")) for f, k in sorted(n.items(), key=lambda kv: (order.get(kv[0], 999), -kv[1], kv[0]))]


def landing_inputs() -> dict:
    """The current cycle in full (plan, votes, roster) and, for every cycle, the roster and the faction config."""
    cur = load_inputs(CURRENT_CYCLE)
    rows = []
    for c in available_cycles():
        sfx = "" if c == CURRENT_CYCLE else f"_ckl{c}"
        mp_path = DERIVED / f"mps{sfx}.json"
        mps = cur["mps"] if c == CURRENT_CYCLE else (load_json(mp_path)["mps"] if mp_path.exists() else {})
        rows.append({"cycle": c, "mps": mps, "facs": factions(c)})
    return {"cur": cur, "rows": rows, "totals": site_totals()}


SZOSZOLO_COLOUR = "#b9a06a"      # the spokespersons' own tone: a faction colour would say they belong to one
KORMANY_COLOUR = "#9aa7b8"       # the ministerial bench's non-MP members: a steel tone, again not a faction's


def kormany_seats(cur: dict) -> dict[tuple[int, int, int], dict]:
    """(sector, row, seat) → the government member sitting there who holds no mandate, from kormany.json."""
    out = {}
    for r in ((cur.get("kormany") or {}).get("people") or {}).values():
        s = r.get("seat") or {}
        if s.get("sector") is not None:
            out[(s["sector"], s["row"], s["seat"])] = r
    return out


def szoszolo_seats(cur: dict) -> dict[tuple[int, int, int], dict]:
    """(sector, row, seat) → the nationality spokesperson sitting there, from szoszolok.json."""
    out = {}
    for azon, r in ((cur.get("szoszolok") or {}).get("people") or {}).items():
        s = r.get("seat") or {}
        if s.get("sector") is not None:
            out[(s["sector"], s["row"], s["seat"])] = r
    return out


def chamber_today_svg(cur: dict) -> tuple[str, list[tuple[str, int, str]]]:
    """The chamber as the records place today's House: parlament.hu's seat outlines as the floor, each MP's seat
    filled with their faction colour, each nationality spokesperson's seat drawn as a ring — they sit and speak but
    cast no vote — and the seats nobody holds left as outlines. Returns (svg, [(faction, seated, colour)])."""
    plan = cur["plan"]
    if not plan or not plan.get("seat_outlines"):
        return "", []
    geo = plan["geometry"]
    colour = {f["id"]: f["colour"] for f in cur["facs"]}
    order = {f["id"]: f["sort_order"] for f in cur["facs"]}
    k = geo.get("node_radius", 5.2) / 5.2
    occupied = {(c["sector"], c["row"], c["seat"]) for c in plan["coords"].values()}
    sz_by_seat = szoszolo_seats(cur)
    km_by_seat = kormany_seats(cur)
    def place(o):
        return "miniszteri pad" if o["sector"] == geo.get("front_bench_sector") else f'{o["sector"]}. szektor, {o["row"]}. sor'
    def outline_title(o):
        key = (o["sector"], o["row"], o["seat"])
        if key in occupied:
            return ""
        r = sz_by_seat.get(key)
        if r:
            return f'<title>{esc(r["name"] or "")} — {esc(r["nationality"] or "")} nemzetiségi szószóló · {esc(place(o))}, {o["seat"]}. szék</title>'
        g = km_by_seat.get(key)
        if g:
            return f'<title>{esc(g["name"] or "")} — {esc(g.get("office") or "kormánytag")}, nem képviselő · {esc(place(o))}, {o["seat"]}. szék</title>'
        return f'<title>üres hely · {esc(place(o))}, {o["seat"]}. szék</title>'
    parts = [f'<polygon points="{o["points"]}" class="seatshape{" occ" if (o["sector"], o["row"], o["seat"]) in occupied or (o["sector"], o["row"], o["seat"]) in sz_by_seat or (o["sector"], o["row"], o["seat"]) in km_by_seat else ""}">'
             + outline_title(o) + "</polygon>" for o in plan["seat_outlines"]]
    by_seat = {(o["sector"], o["row"], o["seat"]): o for o in plan["seat_outlines"]}
    seated: Counter = Counter()
    hit_r = geo.get("seat_pitch", 5.2 * k * 2) * 0.55            # the whole seat cell answers to the pointer, as on a vote page
    fb = [o for o in plan["seat_outlines"] if o["sector"] == geo.get("front_bench_sector")]
    aisle = max(26.0, (max(o["x"] for o in fb) - min(o["x"] for o in fb)) * 0.62) if fb else 44.0
    for azon, xy in sorted(plan["coords"].items(), key=lambda kv: (kv[1]["sector"], kv[1]["row"], kv[1]["seat"])):
        mp = cur["mps"].get(azon) or {}
        fac = mp.get("faction") or "független"
        seated[fac] += 1
        place = "miniszteri pad" if xy.get("front_bench") else f'{xy["sector"]}. szektor, {xy["row"]}. sor'
        parts.append(f'<g class="seat" role="button" data-az="{esc(azon)}" data-f="{esc(fac)}" aria-label="{esc(mp.get("name") or azon)} ({esc(fac)})">'
                     f'<title>{esc(mp.get("name") or azon)} ({esc(fac)}) · {esc(place)}, {xy["seat"]}. szék</title>'
                     f'<circle cx="{xy["x"]}" cy="{xy["y"]}" r="{5.2 * k:.2f}" fill="{colour.get(fac, "#8a8a8a")}" class="today"/>'
                     f'<circle cx="{xy["x"]}" cy="{xy["y"]}" r="{hit_r:.2f}" class="hit"/></g>')
    n_sz = 0
    for key, r in sorted(sz_by_seat.items()):
        o = by_seat.get(key)
        if not o:
            continue
        n_sz += 1
        parts.append(f'<g class="seat sz" role="button" data-az="{esc(r["p_azon"])}" data-f="szószóló" aria-label="{esc(r["name"] or "")} ({esc(r["nationality"] or "")} nemzetiségi szószóló)">'
                     f'<title>{esc(r["name"] or "")} — {esc(r["nationality"] or "")} nemzetiségi szószóló · {key[0]}. szektor, {key[1]}. sor, {key[2]}. szék</title>'
                     f'<circle cx="{o["x"]}" cy="{o["y"]}" r="{4.6 * k:.2f}" fill="none" stroke="{SZOSZOLO_COLOUR}" stroke-width="{1.6 * k:.2f}" class="today"/>'
                     f'<circle cx="{o["x"]}" cy="{o["y"]}" r="{hit_r:.2f}" class="hit"/></g>')
    n_km = 0
    for key, r in sorted(km_by_seat.items()):
        o = by_seat.get(key)
        if not o:
            continue
        n_km += 1
        parts.append(f'<g class="seat km" role="button" data-az="{esc(r["p_azon"])}" data-f="kormánytag" aria-label="{esc(r["name"] or "")} ({esc(r.get("office") or "kormánytag")}, nem képviselő)">'
                     f'<title>{esc(r["name"] or "")} — {esc(r.get("office") or "kormánytag")}, nem képviselő · miniszteri pad, {key[2]}. szék</title>'
                     f'<circle cx="{o["x"]}" cy="{o["y"]}" r="{5.2 * k:.2f}" fill="{KORMANY_COLOUR}" fill-opacity="0.5" stroke="{KORMANY_COLOUR}" stroke-width="{1.2 * k:.2f}" class="today"/>'
                     f'<circle cx="{o["x"]}" cy="{o["y"]}" r="{hit_r:.2f}" class="hit"/></g>')
    W = max(abs(geo["x_min"]), abs(geo["x_max"])) + 22
    R = -geo["y_min"] + 10
    labels = "".join(f'<text x="{ax}" y="{ay}" class="seclabel" text-anchor="middle" dominant-baseline="middle">{sec}</text>'
                     for sec, (ax, ay) in geo["sector_label_anchors"].items() if int(sec) != geo.get("front_bench_sector"))
    fx, fy = geo["sector_label_anchors"].get(str(geo.get("front_bench_sector")), (0, -40))
    legend = [(f, n, colour.get(f, "#8a8a8a")) for f, n in sorted(seated.items(), key=lambda kv: (order.get(kv[0], 999), kv[0]))]
    if n_sz:
        legend.append(("szószóló", n_sz, SZOSZOLO_COLOUR))
    if n_km:
        legend.append(("kormánytag", n_km, KORMANY_COLOUR))
    svg = (f'<svg viewBox="{-W:.0f} -{R + 8:.0f} {2 * W:.0f} {R + 26:.0f}" role="group" aria-label="Az ülésterem ma: {len(plan["coords"])} képviselő, {n_sz} nemzetiségi szószóló és {n_km} mandátum nélküli kormánytag a helyén; {len(plan["seat_outlines"])} hely az Országház alaprajzán. Egy hely: a neve és a mérlege; kattintásra rögzül, nyilakkal is járható">'
           + f'<text x="{fx}" y="{fy}" class="seclabel s" text-anchor="middle">miniszteri pad</text>' + podium_svg(presidium(cur["mps"]), cur["facs"], k, aisle)
           + '<text x="0" y="9" class="seclabel s" text-anchor="middle">elnöki emelvény</text>' + labels + "".join(parts) + "</svg>")
    return svg, legend


def cycle_strip_html(rows: list[dict], totals: dict) -> str:
    """The ten cycles, newest first: the composition at the constituent sitting as a stacked bar, the span, the numbers."""
    out = []
    for r in rows:
        c = r["cycle"]
        pc = totals["per_cycle"].get(c) or {}
        start = CYCLE_START.get(c)
        comp = composition_on(r["mps"], start, r["facs"]) if start else []
        total = sum(n for _, n, _ in comp)
        segs = "".join(f'<i style="width:{100 * n / total:.2f}%;background:{col}" title="{esc(f)} {n}"></i>' for f, n, col in comp) if total else ""
        legend = " · ".join(f'<span><i style="background:{col}"></i>{esc(f)} <b>{n}</b></span>' for f, n, col in comp)
        span = CYCLE_SPAN.get(c, "")
        nums = [f'{hu_num(pc.get("votes", 0))} szavazás']
        if pc.get("roll_calls"):
            nums.append(f'{hu_num(pc["roll_calls"])} név szerinti')
        if pc.get("sitting_days"):
            nums.append(f'{hu_num(pc["sitting_days"])} ülésnap')
        nums.append(f'{hu_num(pc.get("people", 0))} képviselő')
        cur = c == CURRENT_CYCLE
        out.append(f'''<a class="cyc{" now" if cur else ""}" href="{cycle_dir(c)}index.html" aria-label="{c}. ciklus, {esc(span)}">
  <span class="num mono">{c}.</span>
  <span class="body"><span class="top"><span class="span mono">{esc(span)}{" · folyamatban" if cur else ""}</span><span class="stat mono">{" · ".join(nums)}</span></span>
    <span class="compbar" role="img" aria-label="{esc(", ".join(f"{f} {n}" for f, n, _ in comp))} — mandátumok az alakuló ülés napján">{segs}</span>
    <span class="leg mono">{legend}{(" · <b>" + hu_num(total) + "</b> mandátum") if total else ""}</span></span></a>''')
    return "".join(out)


def latest_votes_html(cur: dict, n_max: int = 8) -> tuple[str, str, int]:
    """The current cycle's last sitting day with votes: (rows html, date, count)."""
    votes = cur["idx"]["votes"]
    if not votes:
        return "", "", 0
    last = max(v["on_date"] for v in votes)
    day = [v for v in votes if v["on_date"] == last]
    rows = []
    for v in sorted(day, key=lambda v: v["ts"], reverse=True)[:n_max]:
        mo = v["motions"][0] if v.get("motions") else None
        subj = (f'{esc(mo.get("iromany") or "")} {esc(cut(mo.get("title") or "", 80))}'.strip() if mo
                else ("<em>Jelenlét megállapítás</em>" if v["kind"] == "jelenlet" else esc(cut(v.get("remark") or "—", 80))))
        rows.append(f'<tr><td class="ts mono"><a href="{cycle_dir(CURRENT_CYCLE)}szavazas/{esc(v["slug"])}.html">{esc(v["time"])}</a></td><td>{subj}</td><td>{result_badge(v)}</td></tr>')
    return "".join(rows), last, len(day)


def hall_data(cur: dict) -> dict[str, list]:
    """What the landing chamber's card shows for each seated member, from the same alignment the MP pages print:
    name, faction, mandate, seat, cast / in roll call, with / against the faction's plurality, substantive speeches."""
    per_mp = cur["alignment"]["per_mp"]
    speeches = speeches_by_mp(cur)
    out = {}
    for azon in (cur["plan"] or {}).get("coords", {}):
        mp = cur["mps"].get(azon)
        if not mp:
            continue
        rec = per_mp.get(azon) or {"cast": 0, "in_roll": 0, "with": 0, "against": 0}
        office = next((HOUSE_OFFICES[o["office"]] for o in mp.get("offices") or [] if o.get("office") in HOUSE_OFFICES and not o.get("to")), "")
        out[azon] = [mp["name"], mp.get("faction") or "független", mandate_text(mp), seat_text(mp),
                     rec["cast"], rec["in_roll"], rec["with"], rec["against"],
                     sum(1 for r in speeches.get(azon, []) if not r["technical"]), 0, "", "",
                     ("az Országgyűlés " + office + "e") if office else ""]
    # the ministerial bench's non-MP members: a minister need not hold a mandate; the card names the office and says
    # plainly that no vote of theirs exists. d[9] carries the office instead of a committee count.
    for azon, r in ((cur.get("kormany") or {}).get("people") or {}).items():
        s = r.get("seat") or {}
        out[azon] = [r["name"], "kormánytag", r.get("office") or "kormánytag",
                     (f'miniszteri pad, {s["seat"]}. szék' if s.get("sector") == 0 else ""),
                     0, 0, 0, 0, r.get("speeches") or 0, 0,
                     r.get("photo_url") or "", r.get("photo_credit") or ""]      # parlament.hu has no photo of them
    # the spokespersons: no roll call ever names them, so the card says what they do instead — nationality,
    # committees, the record's speech count. The 0s in the vote slots are what the card reads as "no vote".
    for azon, r in ((cur.get("szoszolok") or {}).get("people") or {}).items():
        s = r.get("seat") or {}
        out[azon] = [r["name"], "szószóló", f'{r["nationality"]} nemzetiségi lista' if r.get("nationality") else "nemzetiségi lista",
                     (f'{s["sector"]}. szektor, {s["row"]}. sor, {s["seat"]}. szék' if s.get("sector") is not None else ""),
                     0, 0, 0, 0, r.get("speeches") or sum(1 for x in speeches.get(azon, []) if not x["technical"]),
                     len(r.get("committees") or []), r.get("photo_url") or "", r.get("photo_credit") or ""]
    return out


def build_404() -> str:
    """404.html — the page a wrong address lands on. Static hosts serve it for anything missing, so it must stand on
    its own: no cycle context, no numbers that could go stale, just the way back."""
    cur = load_inputs(CURRENT_CYCLE)
    cur["base_depth"] = 0
    tot = site_totals()
    return page_head("Nincs ilyen oldal · karzat", "A keresett oldal nincs meg — vissza a karzat nyitólapjára.", 0) + f"""
<header class="kz-topbar"><div class="l"><a class="brand" href="/index.html"><i></i>karzat</a></div>
<div class="r"><span class="kv"><span class="hide-xs">404</span></span></div></header>
<main class="kz-main"><div class="wrap">
<div class="hero-h"><h1>Nincs ilyen oldal</h1><small class="label" data-kz-text>404</small></div>
<p class="lede">Ez a cím nem vezet sehová. Lehet, hogy elgépelés, lehet, hogy egy régi hivatkozás — a karzat oldalai
a ciklusok könyvtáraiban élnek (<span class="mono">/ckl43/…</span>), és minden szavazásnak, képviselőnek és
felszólalásnak saját, állandó címe van.</p>
<section class="doors">
  <a class="panel door" href="/index.html">{CORNERS}<h2><span data-kz-text>Nyitólap</span></h2><p>Az ülésterem ma, a {len(tot["cycles"])} ciklus {hu_date(tot["from"])[:4]} óta, a legutóbbi ülésnap.</p><span class="go mono">/ →</span></a>
  <a class="panel door" href="/kereses/index.html">{CORNERS}<h2><span data-kz-text>Keresés</span></h2><p>Képviselők, pályaképek és irományok minden ciklusból.</p><span class="go mono">kereses/ →</span></a>
  <a class="panel door" href="/lefedettseg/index.html">{CORNERS}<h2><span data-kz-text>Lefedettség</span></h2><p>Meddig ér a jegyzőkönyv, és hol nem ér el.</p><span class="go mono">lefedettseg/ →</span></a>
  <a class="panel door" href="/modszer/index.html">{CORNERS}<h2><span data-kz-text>Módszer</span></h2><p>Mit honnan vesz az oldal, és mit nem állít.</p><span class="go mono">modszer/ →</span></a>
</section>
</div></main>
{terminal_html(cur, [f"{len(tot['cycles'])} ciklus · {hu_date(tot['from'])} – {hu_date(tot['to'])} · {hu_num(tot['votes'])} szavazás", "404 — a cím nem található"])}<script src="/assets/karzat.js"></script>
</body>
</html>
"""


def build_landing() -> str:
    li = landing_inputs()
    cur, tot = li["cur"], li["totals"]
    inp = cur                                                     # the footer and the top bar speak from the current cycle
    inp["base_depth"] = 0                                         # this page is the root itself
    cdir = cycle_dir(CURRENT_CYCLE)
    chamber, legend = chamber_today_svg(cur)
    legend_html = "".join(f'<button type="button" class="f" data-hf="{esc(f)}" data-f="{esc(f)}" aria-pressed="false" title="csak {esc(f)} — mégegyszer: mind">'
                          f'<i style="{"border:2px solid " + col + ";background:transparent" if f == "szószóló" else "background:" + col}"></i>{esc("nemzetiségi szószóló" if f == "szószóló" else f)} <span class="mono">{n}</span></button>' for f, n, col in legend)
    n_sz = next((n for f, n, _ in legend if f == "szószóló"), 0)
    n_km = next((n for f, n, _ in legend if f == "kormánytag"), 0)
    seated = sum(n for f, n, _ in legend if f not in ("szószóló", "kormánytag"))
    plan = cur["plan"] or {}
    n_seats = len(plan.get("seat_outlines") or [])
    rows_html, last_day, n_day = latest_votes_html(cur)
    fl = cur["fl"]
    roster_n = fl["mps"]["total"]
    roster_now = sum(1 for m in cur["mps"].values() if m.get("current"))
    unplaced = roster_now - seated
    hero_note = ((f'Mind a {hu_num(seated)} mandátummal rendelkező képviselő a helyén, ahogy az adatlapja megadja'
                  + (f' · {hu_num(unplaced)} képviselőnek nincs ülőhely az adatlapján' if unplaced else "")
                  + (f' · velük a {hu_num(n_sz)} nemzetiségi szószóló (gyűrűvel) és {hu_num(n_km)} mandátum nélküli kormánytag a miniszteri padban (halvány koronggal) — ülnek és felszólalnak, de nem szavaznak' if n_sz or n_km else "")
                  + f' · a terem {hu_num(n_seats)} széke közül {hu_num(n_seats - seated - n_sz - n_km)} üres: a Ház 2014 óta 199 mandátumos, a terem 386-ra épült'
                  + ' · a színek az oldal jelölései')
                 if chamber else "Az ülésrend csak a jelenlegi ciklusból ismert.")
    n_cyc = len(tot["cycles"])
    lines = [
        f"{n_cyc} ciklus · {hu_date(tot['from'])} – {hu_date(tot['to'])} · {hu_num(tot['votes'])} szavazás · {hu_num(tot['roll_calls'])} név szerinti lista · {hu_num(tot['people'])} képviselő",
        ("a számított eredmény minden szavazásnál egyezik a jegyzőkönyvivel" if tot["disagreements"] == 0
         else f"a számított eredmény {hu_num(tot['disagreements'])} szavazásnál eltér a jegyzőkönyvitől — jelölve"),
        (f"ülésrend: az Országház alaprajza, {hu_num(n_seats)} hely — csak a jelenlegi ciklusból ismert" if n_seats else "ülésrend: csak a jelenlegi ciklusból ismert"),
        (f"frissítve {hu_date(sync_stamp(inp)[:10])} {sync_stamp(inp)[11:]}" if sync_stamp(inp) != "—" else "frissítve —"),
    ]
    page = page_head("karzat — az Országgyűlés szavazásai 1990 óta",
                     f"Az Országgyűlés szavazásai, név szerinti listái és felszólalásai {hu_date(tot['from'])[:4]} óta, ciklusonként: ki hogyan szavazott, mennyi kellett a döntéshez, ki mit mondott. Forrás: parlament.hu Web API.", 0,
                     feeds=[(f"{cdir}feed/kulonvelemeny.xml", f'karzat · különvélemények · {CURRENT_CYCLE}. ciklus'), (f"{cdir}feed/heti.xml", f'karzat · a hét számokban · {CURRENT_CYCLE}. ciklus'),
                            (f"{cdir}feed/felszolalasok.xml", f'karzat · felszólalások szövege · {CURRENT_CYCLE}. ciklus')]) + f"""
<header class="kz-topbar"><div class="l"><a class="brand" href="index.html" aria-current="page"><i></i>karzat</a></div><div class="r"><nav class="kv cyc" aria-label="Ciklus"><span class="hide-xs">Ciklus </span>{' <span class="sl">·</span> '.join(f'<a href="{cycle_dir(c)}index.html"{" class=near" if c == CURRENT_CYCLE else ""}>{c}</a>' for c in tot["cycles"])}</nav><span class="sep hide-sm"></span><span class="kv sync"><span class="dot"></span><span class="hide-xs">Szinkron</span><b>{esc(sync_stamp(inp))}</b></span></div></header>
<main class="kz-main"><div class="wrap landing">
<section class="masthead">{CORNERS}
  <div class="mast-h"><h1>karzat</h1><p class="mast-lede">Az Országgyűlés {hu_date(tot['from'])[:4]} óta. Ki hogyan szavazott, mennyi kellett a döntéshez, ki mit mondott. Tiszta adat, kommentár nélkül.</p></div>
  <figure class="chamber-today">{chamber}
    <figcaption><span class="label" data-kz-text>Az ülésterem ma</span> <span class="sub">{esc(hero_note)}</span><div class="legend">{legend_html}</div></figcaption>
    <div class="inspector" id="hall" aria-live="polite"><span class="insp-hint">Egy helyre mutatva: ki ül ott, és mit tett eddig a ciklusban. Kattintásra rögzül; a névre kattintva a képviselő oldala. Nyilakkal is járható.</span></div>
    <script type="application/json" id="hall-data" data-mp-base="{cdir}kepviselo/">{json.dumps(hall_data(cur), ensure_ascii=False, separators=(",", ":"))}</script>
  </figure>
</section>
<section class="counts land">
  <div class="c">{CORNERS}<b class="mono" data-kz-number="{n_cyc}">{n_cyc}</b><span data-kz-text>ciklus</span><span class="sub">{hu_date(tot['from'])} – {hu_date(tot['to'])}</span></div>
  <div class="c">{CORNERS}<b class="mono" data-kz-number="{tot['votes']}">{hu_num(tot['votes'])}</b><span data-kz-text>szavazás</span><span class="sub">mindegyik a saját oldalán, a szükséges többséggel</span></div>
  <div class="c">{CORNERS}<b class="mono" data-kz-number="{tot['roll_calls']}">{hu_num(tot['roll_calls'])}</b><span data-kz-text>név szerinti lista</span><span class="sub">1998 óta; előtte az API csak frakciónként számol</span></div>
  <div class="c">{CORNERS}<b class="mono" data-kz-number="{tot['people']}">{hu_num(tot['people'])}</b><span data-kz-text>képviselő</span><span class="sub">egy pályakép mindenkinek, ciklusokon át</span></div>
</section>
<section class="grid land">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Ciklusok {hu_date(tot['from'])[:4]} óta</span><span class="tag">mandátumok az alakuló ülés napján · a képviselői adatlapokból</span></h2>
    <div class="cycles">{cycle_strip_html(li["rows"], tot)}</div>
  </section>
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>A legutóbbi ülésnap</span><span class="tag">{esc(hu_date(last_day)) if last_day else "—"} · {hu_num(n_day)} szavazás · <a href="{cdir}index.html#dir">a {CURRENT_CYCLE}. ciklus szavazásai</a></span></h2>
    <div class="tablewrap" style="border:0"><table><thead><tr><th scope="col">Idő</th><th scope="col">Tárgy</th><th scope="col">Eredmény</th></tr></thead><tbody>{rows_html or '<tr><td colspan="3">—</td></tr>'}</tbody></table></div>
    {(f'<div class="hero-meta" style="margin-top:6px">és még {hu_num(n_day - 8)} a ciklus oldalán</div>' if n_day > 8 else "")}
    {freshness_html(cur)}
    <div class="hero-meta" style="margin-top:10px">Csatornák: <a href="{cdir}feed/kulonvelemeny.xml">különvélemények</a> · <a href="{cdir}feed/heti.xml">a hét számokban</a> · <a href="{cdir}feed/felszolalasok.xml">felszólalások szövege</a> · <a href="{cdir}feed/index.html">minden értesítés</a></div>
  </section>
</section>
<section class="doors">
  <form class="panel door" action="{cdir}kepviselom/index.html" method="get">{CORNERS}
    <h2><span data-kz-text>Ki a képviselőm?</span></h2>
    <p>Település vagy budapesti kerület → választókerület → a képviselő; a listás képviselők külön.</p>
    <div class="row"><input type="search" name="t" placeholder="település" aria-label="Település vagy kerület" autocomplete="off"><button type="submit">keresés</button></div>
  </form>
  <form class="panel door" action="kereses/index.html" method="get">{CORNERS}
    <h2><span data-kz-text>Keresés</span></h2>
    <p>Képviselők, pályaképek és irományok minden ciklusból, egy mezőben.</p>
    <div class="row"><input type="search" name="q" placeholder="név vagy iromány" aria-label="Keresés" autocomplete="off"><button type="submit">keresés</button></div>
  </form>
  <form class="panel door" action="{cdir}felszolalas/kereses.html" method="get">{CORNERS}
    <h2><span data-kz-text>A szavak</span></h2>
    <p>A felszólalások jegyzőkönyvi szövege szó szerint; a {CURRENT_CYCLE}. ciklus szövegeiben keres.</p>
    <div class="row"><input type="search" name="q" placeholder="szó, legalább 3 betű" aria-label="Keresés a felszólalásokban" autocomplete="off"><button type="submit">keresés</button></div>
  </form>
  <a class="panel door" href="{cdir}index.html">{CORNERS}<h2><span data-kz-text>A {CURRENT_CYCLE}. ciklus</span></h2><p>{hu_num(fl["votes"])} szavazás, {hu_num(roster_n)} képviselő, mindenki a maga helyén az ülésteremben; szavazásonként a szükséges többség, képviselőnként a hét számokban.</p><span class="go mono">ckl{CURRENT_CYCLE}/ →</span></a>
  <a class="panel door" href="szemely/index.html">{CORNERS}<h2><span data-kz-text>Pályaképek</span></h2><p>{hu_num(tot['people'])} személy {hu_date(tot['from'])[:4]} óta: mandátumok, frakciók, szavazási mérleg ciklusonként, és az életút egy tengelyen.</p><span class="go mono">szemely/ →</span></a>
  <a class="panel door" href="arcel/index.html">{CORNERS}<h2><span data-kz-text>A Ház arcéle</span></h2><p>Ciklusonként hányan ülnek először a Házban, és mit rögzít a nyilvántartás a megválasztottak végzettségéről, nyelveiről, önkormányzati múltjáról.</p><span class="go mono">arcel/ →</span></a>
  <a class="panel door" href="lefedettseg/index.html">{CORNERS}<h2><span data-kz-text>Ameddig a jegyzőkönyv elér</span></h2><p>Hónapról hónapra: hol van név szerinti lista, hol csak összesítés, és mi az, ami már nem pótolható.</p><span class="go mono">lefedettseg/ →</span></a>
  <a class="panel door" href="modszer/index.html">{CORNERS}<h2><span data-kz-text>Módszer és adatok</span></h2><p>Minden szabály és számítás leírva; minden tábla letölthető CSV-ben és JSON-ban, ciklusonként az <span class="mono">adatok/</span> alatt.</p><span class="go mono">modszer/ →</span></a>
</section>
</div></main>
{terminal_html(inp, lines)}<script src="assets/karzat.js"></script>
</body>
</html>
"""
    return page


def build_all(out_dir: Path, index_only: bool = False, cycles: list[int] | None = None) -> dict:
    """Every cycle with derived inputs under out_dir/ckl<N>/, the landing page at out_dir/index.html, then one
    career page per person under out_dir/szemely/, assembled from every cycle's roster."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "assets").mkdir(parents=True, exist_ok=True)
    for name, body in build_assets().items():
        (out_dir / "assets" / name).write_text(body, encoding="utf-8")
    res = []
    for cycle in (cycles or available_cycles()):
        res.append(build_cycle(out_dir / cycle_dir(cycle), cycle, index_only=index_only))
    people_n = 0
    full = not cycles or sorted(cycles) == sorted(available_cycles())
    if full:
        (out_dir / "index.html").write_text(build_landing(), encoding="utf-8")      # the site root: every cycle's door
        (out_dir / "404.html").write_text(build_404(), encoding="utf-8")            # what a static host serves for a wrong address
        (out_dir / "robots.txt").write_text(ROBOTS, encoding="utf-8")
        if FACTS_FILE.exists():                                                     # the footer's pool, for the rotate button
            (out_dir / "tenyek.json").write_text(json.dumps({"facts": facts()}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    # the cross-cycle pages (careers, search index, method) are assembled from every cycle's build: a partial
    # `--cycle N` run leaves them alone rather than shrinking them to one cycle
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
        cd_ = out_dir / "lefedettseg"; cd_.mkdir(parents=True, exist_ok=True)     # where the record reaches, and where it stops
        (cd_ / "index.html").write_text(build_coverage_page(inp, {r["cycle"]: r["coverage"] for r in res}), encoding="utf-8")
        ad_ = out_dir / "arcel"; ad_.mkdir(parents=True, exist_ok=True)           # who sits there, across the ten cycles
        (ad_ / "index.html").write_text(build_profile_page(inp, landing_inputs()["rows"]), encoding="utf-8")
        items = [it for r in res for it in r["search"]]
        items += [{"k": "szemely", "c": 0, "t": stints[0]["name"], "s": f'pályakép · {", ".join(str(st["cycle"]) for st in sorted(stints, key=lambda r: r["cycle"]))}. ciklus', "u": f"szemely/{azon}.html"} for azon, stints in people.items()]
        items += [{"k": "szoszolo", "c": CURRENT_CYCLE, "t": r["name"],
                   "s": f'{r["nationality"] or ""} nemzetiségi szószóló · {", ".join(str(st["cycle"]) for st in sorted(r.get("cycles") or [], key=lambda x: x["cycle"]))}. ciklus'.strip(),
                   "u": f"szemely/{azon}.html"} for azon, r in ((inp["szoszolok"] or {}).get("people") or {}).items()]
        items += [{"k": "kormany", "c": CURRENT_CYCLE, "t": r["name"], "s": f'{r.get("office") or "kormánytag"} · kormánytag, nem képviselő',
                   "u": f"szemely/{azon}.html"} for azon, r in ((inp["kormany"] or {}).get("people") or {}).items()]
        sd_ = out_dir / "kereses"; sd_.mkdir(parents=True, exist_ok=True)
        (sd_ / "index.json").write_text(json.dumps(items, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        (sd_ / "index.html").write_text(build_search_page(inp, len(items)), encoding="utf-8")
        pd = out_dir / "szemely"
        pd.mkdir(parents=True, exist_ok=True)
        pw = _Writer()
        sz_people = (inp["szoszolok"] or {}).get("people") or {}
        km_people = (inp["kormany"] or {}).get("people") or {}
        pw(pd / "index.html", build_person_index(inp, people, sz_people, km_people))
        for azon, stints in people.items():
            pw(pd / f"{azon}.html", build_person_page(inp, azon, stints))
            people_n += 1
        for azon, r in sz_people.items():
            pw(pd / f"{azon}.html", build_spokesperson_page(inp, azon, r))     # the same address, no vote to count
            people_n += 1
        for azon, r in km_people.items():
            pw(pd / f"{azon}.html", build_kormany_page(inp, azon, r))          # a minister without a mandate: likewise
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
