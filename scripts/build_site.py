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
import json
import math
import re
import sys
from datetime import date as _date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karzat.freshness import BUDAPEST, assess  # noqa: E402
from karzat.load import cycle_of_date  # noqa: E402
from karzat.majority import RULES, Rule  # noqa: E402

DERIVED = ROOT / "data" / "derived"
SEATING = DERIVED / "seating.json"
SITE_DIR = ROOT / "site"
SITE = SITE_DIR / "index.html"
VOTES_DIR = SITE_DIR / "szavazas"
MPS_DIR = SITE_DIR / "kepviselo"
ASSETS_DIR = SITE_DIR / "assets"
CORNERS = '<span class="kz-corner tl"></span><span class="kz-corner tr"></span><span class="kz-corner bl"></span><span class="kz-corner br"></span>'
FACTIONS = ROOT / "config" / "factions.yml"

POSITION_ORDER = ["igen", "nem", "tartozkodott", "jelen_nem_szavazott", "nem_szavazott", "bejelentett_hianyzo", "igazoltan_tavol"]
POSITION_LABEL = {"igen": "igen", "nem": "nem", "tartozkodott": "tartózkodott", "jelen_nem_szavazott": "jelen, nem szavazott",
                  "nem_szavazott": "nem szavazott", "bejelentett_hianyzo": "előre bejelentett hiányzó", "igazoltan_tavol": "igazoltan távol"}
HU_MONTHS = ['január', 'február', 'március', 'április', 'május', 'június', 'július', 'augusztus', 'szeptember', 'október', 'november', 'december']


# -- inputs -----------------------------------------------------------------------------------

def factions() -> list[dict]:
    """Minimal reader for config/factions.yml (id, name, colour, sort_order) — no YAML dependency."""
    out, cur = [], None
    for line in FACTIONS.read_text(encoding="utf-8").splitlines():
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


def load_inputs() -> dict:
    idx = json.loads((DERIVED / "votes_index.json").read_text(encoding="utf-8"))
    store = json.loads((DERIVED / "votes_positions.json").read_text(encoding="utf-8"))
    fl = json.loads((DERIVED / "first_light.json").read_text(encoding="utf-8"))
    plan = json.loads(SEATING.read_text(encoding="utf-8")) if SEATING.exists() else None
    mps = json.loads((DERIVED / "mps.json").read_text(encoding="utf-8"))["mps"] if (DERIVED / "mps.json").exists() else {}
    facs = factions()
    inp = {"idx": idx, "store": store, "fl": fl, "plan": plan, "facs": facs, "mps": mps,
           "by_ts": {v["ts"]: v for v in idx["votes"]}, "order": [v["ts"] for v in idx["votes"]]}
    inp["alignment"] = compute_alignment(inp)
    inp["cycle"] = cycle_of_date(idx["window"]["from"]) if idx.get("window") else None
    return inp


CAST = ("igen", "nem", "tartozkodott")


def compute_alignment(inp: dict) -> dict:
    """One pass over every roll call: per vote the faction pluralities, per MP the record.

    Definitions (also printed on the pages): an MP voted *with* their faction when their cast
    position (igen / nem / tartózkodott) equals the plurality of the faction's cast positions in
    that vote; *against* when it differs; positions that are not a cast vote (jelen, nem szavazott;
    nem szavazott; előre bejelentett hiányzó) are participation, not alignment.
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
        for f, c in by_f.items():
            pos, n = max(c.items(), key=lambda kv: (kv[1], CAST.index(kv[0])))
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


def _node(cx: float, cy: float, pos: str, c: str) -> str:
    if pos == "igen":
        return f'<circle cx="{cx}" cy="{cy}" r="5.2" fill="{c}"/>'
    if pos == "nem":
        return f'<circle cx="{cx}" cy="{cy}" r="4.6" fill="none" stroke="{c}" stroke-width="1.6"/>'
    if pos == "tartozkodott":
        return (f'<circle cx="{cx}" cy="{cy}" r="4.6" fill="none" stroke="{c}" stroke-width="1.6"/>'
                f'<path d="M {cx-4.6} {cy} A 4.6 4.6 0 0 1 {cx+4.6} {cy} Z" fill="{c}"/>')
    if pos == "jelen_nem_szavazott":
        return f'<circle cx="{cx}" cy="{cy}" r="4.6" fill="none" stroke="{c}" stroke-width="1.6" stroke-dasharray="2.4 2"/>'
    if pos == "nem_szavazott":
        return f'<circle cx="{cx}" cy="{cy}" r="4.6" fill="{c}" fill-opacity="0.35" stroke="{c}" stroke-opacity="0.6" stroke-width="1"/>'
    if pos == "igazoltan_tavol":   # excused absence (cycle 42 onwards): the small dot inside a faint ring
        return (f'<circle cx="{cx}" cy="{cy}" r="4.6" fill="none" stroke="{c}" stroke-opacity="0.5" stroke-width="1"/>'
                f'<circle cx="{cx}" cy="{cy}" r="2.2" fill="{c}" fill-opacity="0.6"/>')
    return f'<circle cx="{cx}" cy="{cy}" r="2.2" fill="{c}" fill-opacity="0.6"/>'


def seat_svg_real(view: dict, facs: list[dict], plan: dict) -> tuple[str, dict]:
    """The reconstructed chamber: every voting MP at their (sector,row,seat). Returns (svg, info)."""
    colour = {f["id"]: f["colour"] for f in facs}
    coords = plan["coords"]; geo = plan["geometry"]
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
            parts.append(f'<g class="seat" data-pos="{pos}" data-f="{html.escape(m["faction"] or "")}"><title>{title}{html.escape(where)}</title>{_node(xy["x"], xy["y"], pos, c)}</g>')
        else:
            unplaced.append(m)
    for i, m in enumerate(unplaced):
        c = colour.get(m["faction"] or "", "#8a8a8a")
        why = "azonosítatlan név" if not m.get("mp_azon") else "nincs ülőhely a képviselői adatlapon (a mandátum azóta megszűnt)"
        parts.append(f'<g class="seat" data-pos="{m["position"]}"><title>{html.escape(m["name"])} ({html.escape(m["faction"] or "")}) — {POSITION_LABEL.get(m["position"], m["position"])} · {why}</title>{_node(-150 + i * 12, 12, m["position"], c)}</g>')
    R = geo["outer_radius"] + 14
    labels = []
    for sec, (a0, a1) in geo["sector_wedges_rad"].items():
        mid = (a0 + a1) / 2
        labels.append(f'<text x="{R*math.cos(mid):.1f}" y="{-R*math.sin(mid):.1f}" class="seclabel" text-anchor="middle" dominant-baseline="middle">{sec}</text>')
        for a in (a0, a1):
            r_in, r_out = geo["r0"] - 8, geo["outer_radius"] + 6
            labels.append(f'<line x1="{r_in*math.cos(a):.1f}" y1="{-r_in*math.sin(a):.1f}" x2="{r_out*math.cos(a):.1f}" y2="{-r_out*math.sin(a):.1f}" class="aisle"/>')
    fb = geo["front_bench_radius"]; fs = geo["front_bench_span_rad"]
    a0, a1 = math.pi / 2 + fs / 2, math.pi / 2 - fs / 2
    arc = (f'<path d="M {fb*math.cos(a0):.1f} {-fb*math.sin(a0):.1f} A {fb} {fb} 0 0 1 {fb*math.cos(a1):.1f} {-fb*math.sin(a1):.1f}" class="fbarc"/>'
           f'<text x="0" y="{-fb+20:.1f}" class="seclabel" text-anchor="middle">miniszteri pad</text>')
    svg = (f'<svg viewBox="-172 -{R+12:.0f} 344 {R+30:.0f}" role="img" aria-label="Az ülésterem rekonstruált ülésrendje: {len(view["positions"])} képviselő, frakció és szavazat szerint">'
           f'<rect x="-16" y="-7" width="32" height="8" rx="1.5" class="rostrum"/><text x="0" y="12" class="seclabel" text-anchor="middle">elnöki emelvény</text>'
           + arc + "".join(labels) + "".join(parts) + '</svg>')
    return svg, {"placed": len(view["positions"]) - len(unplaced), "unplaced": len(unplaced), "seated_total": plan["seated"]}


def seat_svg_fallback(view: dict, facs: list[dict]) -> str:
    order = {f["id"]: f["sort_order"] for f in facs}
    colour = {f["id"]: f["colour"] for f in facs}
    pos_rank = {p: i for i, p in enumerate(POSITION_ORDER)}
    members = sorted(view["positions"], key=lambda p: (order.get(p["faction"] or "", 999), pos_rank.get(p["position"], 9), p["name"]))
    seats = hemicycle_layout(len(members))
    parts = []
    for seat, m in zip(seats, members):
        c = colour.get(m["faction"] or "", "#8a8a8a")
        title = html.escape(f'{m["name"]} ({m["faction"]}) — {POSITION_LABEL.get(m["position"], m["position"])}')
        parts.append(f'<g class="seat" data-pos="{m["position"]}"><title>{title}</title>{_node(seat["cx"], seat["cy"], m["position"], c)}</g>')
    return ('<svg viewBox="-200 -200 400 214" role="img" aria-label="Ülésrend-diagram">'
            '<rect x="-14" y="-6" width="28" height="7" rx="1.5" class="rostrum"/>' + "".join(parts) + '</svg>')


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
--border:#27272a;--border-hi:#3f3f46;--corner:#777;--text:#e4e4e7;--dim:#a1a1aa;--dim2:#8a8a93;--dim3:#71717a;--line2:#18181b;--grid:rgba(255,255,255,.05);
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
.kz-topbar{position:sticky;top:0;z-index:20;height:48px;border-bottom:1px solid var(--border);background:var(--topbar);backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:space-between;padding:0 16px;font-family:var(--mono);font-size:10px;letter-spacing:.2em;text-transform:uppercase;user-select:none}
.kz-topbar .l{display:flex;align-items:center;gap:14px;min-width:0;flex:1 1 auto}.kz-topbar .r{display:flex;align-items:center;gap:14px;flex:0 0 auto;white-space:nowrap}
.kz-topbar .brand{display:flex;align-items:center;gap:10px;color:var(--white);font-weight:500;letter-spacing:.35em;flex-shrink:0}
.kz-topbar .brand i{width:8px;height:8px;background:var(--white);display:inline-block;transition:box-shadow .2s}.kz-topbar .brand:hover i{box-shadow:0 0 8px rgba(255,255,255,.8)}
.kz-topbar .sep{width:1px;height:16px;background:var(--border)}
.kz-topbar nav{display:flex;align-items:center;gap:8px;min-width:0;color:var(--dim2);white-space:nowrap}.kz-topbar nav a,.kz-topbar nav .cur{min-width:0;overflow:hidden;text-overflow:ellipsis}.kz-topbar nav a:hover{color:var(--white)}.kz-topbar nav .cur{color:var(--white)}.sl{color:var(--border-hi)}
.kz-topbar .kv{color:var(--dim2);white-space:nowrap}.kz-topbar .kv b{color:#d4d4d8;font-weight:400;margin-left:6px}
.kz-topbar .dot{width:6px;height:6px;border-radius:50%;background:#52525b;display:inline-block;margin-right:6px}
@media(max-width:760px){.kz-topbar .hide-sm{display:none}}@media(max-width:600px){.kz-topbar .hide-xs{display:none}}
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
.grid{display:grid;grid-template-columns:1.25fr .9fr;gap:16px}.grid .panel{margin-top:16px;min-width:0}
@media(max-width:900px){.grid{grid-template-columns:1fr}}
.fresh{margin-top:14px;padding:10px 14px;border:1px solid var(--border);border-left:2px solid var(--dim2);background:var(--panel-deep);font-family:var(--mono);font-size:11px;color:var(--dim);letter-spacing:.02em;position:relative}
.fresh .en{color:var(--dim2);display:block;margin-top:2px}
.fresh:before{content:">";color:var(--dim3);margin-right:8px}
/* text */
.hero-title{font-size:22px;font-weight:300;letter-spacing:-.01em;line-height:1.3;margin:2px 0 8px;color:var(--white)}
.hero-title a{color:var(--white);border-bottom:1px solid var(--dim3)}.hero-title a:hover{border-bottom-color:var(--white)}
.hero-meta{color:var(--dim2);font-size:12px;font-family:var(--mono);letter-spacing:.02em}
.hero-meta.prose{font-family:var(--sans);font-size:12.5px;color:var(--dim);letter-spacing:0}
/* chart */
.chart svg{width:100%;height:auto;display:block;margin-top:6px}
.seat circle,.seat path{transition:opacity .15s,filter .2s;cursor:crosshair}
.chart svg:hover .seat{opacity:.45}.chart svg .seat:hover{opacity:1;filter:drop-shadow(0 0 4px rgba(255,255,255,.6))}
.rostrum{fill:var(--border)}.seclabel{font-size:10px;fill:var(--dim2);font-family:var(--mono);letter-spacing:.1em}.aisle{stroke:var(--line2);stroke-width:.8}.fbarc{fill:none;stroke:var(--border-hi);stroke-width:.8;stroke-dasharray:2 2}
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
.fbars{margin-top:12px;font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase}.fbars .row{display:grid;grid-template-columns:78px 1fr 96px;gap:8px;align-items:center;margin:5px 0;color:var(--dim)}
.fbars .stack{display:flex;height:6px;overflow:hidden;background:var(--line2)}.fbars .stack i{display:block;height:100%}
.counts{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));margin-top:16px;border-top:1px solid var(--border);border-left:1px solid var(--border)}
@media(max-width:1100px){.counts{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:560px){.counts{grid-template-columns:repeat(2,minmax(0,1fr))}}
.counts .c{background:var(--panel-deep);padding:14px 16px;position:relative;border-right:1px solid var(--border);border-bottom:1px solid var(--border)}
.counts b{display:block;font-size:34px;font-weight:300;letter-spacing:-.03em;color:var(--white);font-family:var(--sans);font-variant-numeric:tabular-nums;line-height:1}
.counts span{display:block;font-family:var(--mono);font-size:9px;color:var(--dim2);text-transform:uppercase;letter-spacing:.2em;margin-top:8px}
.counts span.sub{color:var(--dim2);text-transform:none;letter-spacing:.05em;margin-top:3px}
/* directory / tables */
.filters{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 10px;align-items:center}
.filters button{border:1px solid var(--border);background:transparent;color:var(--dim2);padding:4px 10px;font-family:var(--mono);font-size:10px;letter-spacing:.15em;text-transform:uppercase;cursor:pointer;transition:color .15s,border-color .15s}
.filters button:hover{color:var(--white);border-color:var(--border-hi)}.filters button.on{border-color:var(--dim3);color:var(--white);background:rgba(24,24,27,.6)}
.filters input{border:1px solid var(--dim3);background:rgba(0,0,0,.4);color:var(--text);padding:5px 9px;font-family:var(--mono);font-size:11px;min-width:220px}
.filters input::placeholder{color:var(--dim2)}
.filters .n{color:var(--dim2);font-family:var(--mono);font-size:10px;letter-spacing:.15em;margin-left:auto}
.tablewrap{overflow-x:auto;border:1px solid var(--border);background:rgba(0,0,0,.35)}tr[hidden]{display:none}
table{border-collapse:collapse;width:100%;font-size:12px}
th,td{padding:7px 10px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line2)}
th{font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.2em;color:var(--dim2);font-weight:400;background:rgba(5,5,5,.95)}
th.sortable{cursor:pointer}th.sortable:hover{color:var(--white)}th.sortable[aria-sort="ascending"]:after{content:" ↑"}th.sortable[aria-sort="descending"]:after{content:" ↓"}th.num{text-align:right}
tr:last-child td{border-bottom:0}tbody tr:hover td{background:rgba(24,24,27,.5)}
td.num{text-align:right;white-space:nowrap;font-family:var(--mono)}td.ts{white-space:nowrap;color:var(--dim2);font-family:var(--mono);font-size:11px}td.ts a:hover{color:var(--white)}
td .sub{color:var(--dim2);font-size:11px;display:block;font-family:var(--mono);letter-spacing:.02em}
td a{color:#d4d4d8;text-decoration:underline;text-decoration-color:var(--dim3);text-underline-offset:2px}td a:hover{color:var(--white);text-decoration-color:var(--white)}
.pos{display:inline-flex;align-items:center;gap:6px}.pos svg{width:12px;height:12px;flex:none}
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
"""

JS_INDEX = """
(function(){
  var tbody = document.getElementById('rows'), n = document.getElementById('n'); if (!tbody) return;
  var rows = Array.prototype.slice.call(tbody.rows), rule = 'all', result = 'all', q = '';
  function press(sel, on){ document.querySelectorAll(sel).forEach(function(x){ var isOn = x === on; x.classList.toggle('on', isOn); x.setAttribute('aria-pressed', isOn ? 'true' : 'false'); }); }
  function render(){
    var k = 0;
    rows.forEach(function(r){
      var ok = (rule === 'all' || r.getAttribute('data-rule') === rule) && (result === 'all' || r.getAttribute('data-result') === result) && (!q || (r.getAttribute('data-hay') || '').indexOf(q) >= 0);
      if (ok) { r.removeAttribute('hidden'); k++; } else r.setAttribute('hidden', '');
    });
    n.textContent = k + ' / ' + rows.length;
  }
  document.querySelectorAll('button[data-rule]').forEach(function(b){ b.addEventListener('click', function(){ rule = b.getAttribute('data-rule'); press('button[data-rule]', b); render(); }); });
  document.querySelectorAll('button[data-result]').forEach(function(b){ b.addEventListener('click', function(){ result = b.getAttribute('data-result'); press('button[data-result]', b); render(); }); });
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
    rows.forEach(function(r){ var ok = (fac === 'all' || r.getAttribute('data-f') === fac) && (pos === 'all' || r.getAttribute('data-p') === pos); if (ok) { r.removeAttribute('hidden'); k++; } else r.setAttribute('hidden', ''); });
    document.getElementById('rn').textContent = k + ' / ' + rows.length;
  }
  document.querySelectorAll('button[data-fac]').forEach(function(b){ b.addEventListener('click', function(){ fac = b.getAttribute('data-fac'); press('button[data-fac]', b); apply(); }); });
  document.querySelectorAll('button[data-posf]').forEach(function(b){ b.addEventListener('click', function(){ pos = b.getAttribute('data-posf'); press('button[data-posf]', b); apply(); }); });
  var heads = table.querySelectorAll('th.sortable');
  heads.forEach(function(th, i){
    th.setAttribute('tabindex', '0'); th.setAttribute('role', 'button'); th.setAttribute('aria-sort', 'none');
    function sort(){
      var d = dir[i] = -(dir[i] || -1);
      var key = th.getAttribute('data-key');
      rows.sort(function(a, b){ var x = a.getAttribute('data-' + key) || '', y = b.getAttribute('data-' + key) || ''; var nx = parseFloat(x), ny = parseFloat(y); if (!isNaN(nx) && !isNaN(ny)) return (nx - ny) * d; return x.localeCompare(y, 'hu') * d; });
      rows.forEach(function(r){ tbody.appendChild(r); });
      heads.forEach(function(h){ h.setAttribute('aria-sort', h === th ? (d > 0 ? 'ascending' : 'descending') : 'none'); });
    }
    th.addEventListener('click', sort);
    th.addEventListener('keydown', function(e){ if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); sort(); } });
  });
  apply();
})();
"""

JS_MP = """
(function(){
  var t = document.getElementById('mine'); if (!t) return;
  var rows = Array.prototype.slice.call(t.tBodies[0].rows), n = document.getElementById('rn');
  function apply(f){ var k = 0; rows.forEach(function(r){ var ok = f === 'all' || r.getAttribute('data-al') === f; if (ok) { r.removeAttribute('hidden'); k++; } else r.setAttribute('hidden', ''); }); n.textContent = k + ' / ' + rows.length; }
  document.querySelectorAll('button[data-alf]').forEach(function(b){ b.addEventListener('click', function(){ document.querySelectorAll('button[data-alf]').forEach(function(x){ var isOn = x === b; x.classList.toggle('on', isOn); x.setAttribute('aria-pressed', isOn ? 'true' : 'false'); }); apply(b.getAttribute('data-alf')); }); });
  apply('all');
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

def page_head(title: str, description: str, depth: int = 0) -> str:
    rel = "../" * depth
    return f"""<!doctype html>
<html lang="hu">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="stylesheet" href="{rel}assets/karzat.css">
</head>
<body>"""


def topbar(inp: dict, crumbs: list[tuple[str, str | None]], depth: int = 0) -> str:
    """Konzol-style top bar: brand square, breadcrumb, cycle and the sync time — no 'live' badge."""
    rel = "../" * depth
    sync_txt = sync_stamp(inp)
    parts = []
    for i, (name, href) in enumerate(crumbs):
        if i:
            parts.append('<span class="sl">/</span>')
        parts.append(f'<a href="{esc(href)}">{esc(name)}</a>' if href else f'<span class="cur" aria-current="page">{esc(name)}</span>')
    return (f'<header class="kz-topbar"><div class="l"><a class="brand" href="{rel}index.html"><i></i>karzat</a>'
            + (f'<span class="sep"></span><nav aria-label="Útvonal">{"".join(parts)}</nav>' if crumbs else "")
            + f'</div><div class="r"><span class="kv hide-sm">Ciklus<b>{esc(inp["cycle"] or "—")}</b></span><span class="sep hide-sm"></span>'
            f'<span class="kv"><span class="dot"></span><span class="hide-xs">Szinkron</span><b>{esc(sync_txt)}</b></span></div></header>\n<main class="kz-main"><div class="wrap">')


def sync_stamp(inp: dict) -> str:
    """The last sync as a Budapest timestamp, or an honest dash: the derive time is not a sync."""
    ls = inp["idx"].get("last_sync_at")
    return datetime.fromisoformat(ls).astimezone(BUDAPEST).strftime("%Y-%m-%d %H:%M") if ls else "—"


def page_tail(inp: dict, depth: int = 0, extra_script: str = "") -> str:
    rel = "../" * depth
    return f'</div></main>\n{terminal_html(inp)}{extra_script}<script src="{rel}assets/karzat.js"></script>\n</body>\n</html>\n'


def legend_html(view: dict, facs: list[dict]) -> str:
    fac_by_id = {f["id"]: f for f in facs}
    legend_f = "".join(
        f'<span class="f"><i style="background:{fac_by_id.get(t["faction"], {"colour": "#8a8a8a"})["colour"]}"></i>{esc(t["faction"])} <span class="mono">{esc(t["osszesen"])}</span></span>'
        for t in view.get("faction_tallies") or [])
    legend_p = ('<span class="f"><svg viewBox="-7 -7 14 14" aria-hidden="true" focusable="false"><circle r="5" fill="currentColor"/></svg>igen</span>'
                '<span class="f"><svg viewBox="-7 -7 14 14" aria-hidden="true" focusable="false"><circle r="4.6" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>nem</span>'
                '<span class="f"><svg viewBox="-7 -7 14 14" aria-hidden="true" focusable="false"><circle r="4.6" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M-4.6 0 A4.6 4.6 0 0 1 4.6 0Z" fill="currentColor"/></svg>tartózkodott</span>'
                '<span class="f"><svg viewBox="-7 -7 14 14" aria-hidden="true" focusable="false"><circle r="4.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.4 2"/></svg>jelen, nem szavazott</span>'
                '<span class="f"><svg viewBox="-7 -7 14 14" aria-hidden="true" focusable="false"><circle r="4.6" fill="currentColor" fill-opacity=".35" stroke="currentColor" stroke-opacity=".6"/></svg>nem szavazott</span>'
                '<span class="f"><svg viewBox="-7 -7 14 14" aria-hidden="true" focusable="false"><circle r="2.2" fill="currentColor" fill-opacity=".6"/></svg>előre bejelentett hiányzó</span>'
                + ('<span class="f"><svg viewBox="-7 -7 14 14" aria-hidden="true" focusable="false"><circle r="4.6" fill="none" stroke="currentColor" stroke-opacity=".5"/><circle r="2.2" fill="currentColor" fill-opacity=".6"/></svg>igazoltan távol</span>'
                   if (view.get("position_counts") or {}).get("igazoltan_tavol") else ""))
    return f'<div class="legend">{legend_f}</div><div class="legend">{legend_p}</div>'


def chart_block(view: dict, inp: dict) -> str:
    plan, facs = inp["plan"], inp["facs"]
    if not view["roll_call_available"]:
        return ('<div class="hero-meta" style="margin:14px 0">Titkos szavazás: nincs név szerinti lista, ezért ülésrend sincs — csak az összesített eredmény.</div>'
                if view.get("secret") else '<div class="hero-meta" style="margin:14px 0">Ehhez a szavazáshoz nincs név szerinti lista.</div>')
    if plan:
        svg, info = seat_svg_real(view, facs, plan)
        geo = plan["geometry"]
        note = (f'Az ülésrend a képviselői adatlapok szektor/sor/szék adataiból rekonstruálva ({info["placed"]} képviselő a helyén'
                + (f', {info["unplaced"]} balra lent, ülőhely-adat nélkül' if info["unplaced"] else '') + '), az elnöki emelvény felől nézve: '
                f'balra az ellenzék ({", ".join(str(x) for x in geo["sector_order_left_to_right"][:2])}. szektor), jobbra a kormányoldal; '
                'a 0. szektor a miniszteri pad. Becsült: a szektorok szélessége (a legszélesebb sorral arányos), a sorok távolsága és a székek balról jobbra sorrendje.')
    else:
        svg = seat_svg_fallback(view, facs)
        note = "Sorrend: frakció, azon belül szavazat. A helyek nem a valódi ülésrend."
    return f'<div class="chart">{svg}</div>{legend_html(view, facs)}<div class="hero-meta" style="margin-top:8px">{note}</div>'


def verdict_block(view: dict, inp: dict) -> str:
    facs = inp["facs"]
    fac_by_id = {f["id"]: f for f in facs}
    m = view.get("majority") or {}
    rule_info = RULES[Rule(m["rule"])] if m else None
    counts = view.get("position_counts") or {}
    tally = (f'<div class="tally"><div><b class="mono" data-kz-number="{view["igen"]}">{view["igen"]}</b><span>igen</span></div><div><b class="mono" data-kz-number="{view["nem"]}">{view["nem"]}</b><span>nem</span></div>'
             f'<div><b class="mono" data-kz-number="{view["tartozkodott"]}">{view["tartozkodott"]}</b><span>tartózkodott</span></div></div>')
    if view["kind"] == "jelenlet":
        return (tally + '<div class="hero-meta">Jelenlét megállapítása — nem döntés: a gombnyomások a jelenlétet rögzítik, eredménye „Határozatképes”. '
                f'Jelen: {esc(view.get("present"))}.</div>')
    if not m:
        return tally + '<div class="hero-meta">Ehhez a szavazáshoz nincs számított többségi küszöb.</div>'
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
        fb.append(f'<div class="row"><span>{esc(t["faction"])}</span><span class="stack">{seg("igen",1)}{seg("tartozkodott",.6)}{seg("nem",.35)}'
                  f'<i style="width:{100*rest/n:.1f}%;background:transparent" title="{esc(t["faction"])} nem szavazott / hiányzó: {rest}"></i></span>'
                  f'<span class="mono" style="text-align:right">{t["igen"]} – {t["nem"]} – {t["tartozkodott"]}</span></div>')
    base_word = "képviselő" if rule_info and rule_info.base == "seats" else "jelenlévő"
    base_note = " · jelenlét = igen+nem+tartózkodott+jelen, nem szavazott" if rule_info and rule_info.base == "present" and view["roll_call_available"] else (
        " · jelenlét = az „Összes szavazat” (nincs névsor)" if rule_info and rule_info.base == "present" else "")
    return (tally +
            f'<div class="bar"><i style="width:{share}%"></i><em style="left:{need_pct}%"></em></div>'
            f'<div class="hero-meta mono">{view["igen"]} igen · szükséges {m.get("needed")} a {m.get("base")}-ból ({esc(rule_info.label_hu if rule_info else "")}) · különbség {("+" if (m.get("margin") or 0) >= 0 else "") + str(m.get("margin"))}</div>'
            '<dl class="verdict">'
            f'<dt>Szavazási mód</dt><dd>{esc(view["mode"])} <span class="hero-meta">(az API saját megjelölése)</span></dd>'
            f'<dt>Szabály</dt><dd>{esc(rule_info.label_hu if rule_info else "—")} — {esc(rule_info.formula if rule_info else "")}</dd>'
            f'<dt>Alap</dt><dd class="mono">{m.get("base")} {base_word}{base_note}</dd>'
            f'<dt>Eredmény</dt><dd>{result_badge(view)} <span class="hero-meta">forrás szerint · számítás {"egyezik" if verdict_ok else ("eltér" if verdict_ok is False else "—")}</span></dd>'
            + (f'<dt>Nem szavazott</dt><dd class="mono">{counts.get("nem_szavazott", 0)} · jelen, nem szavazott {counts.get("jelen_nem_szavazott", 0)} · előre bejelentett hiányzó {counts.get("bejelentett_hianyzo", 0)}' + (f' · igazoltan távol {counts["igazoltan_tavol"]}' if counts.get("igazoltan_tavol") else "") + '</dd>' if view["roll_call_available"] else '')
            + '</dl>'
            + (f'<div class="fbars">{"".join(fb)}</div><div class="hero-meta" style="margin-top:10px">Frakciónként: igen (teli), tartózkodott (fél), nem (halvány), a maradék nem szavazott vagy hiányzott. Számok: igen – nem – tartózkodott.</div>' if fb else ''))


def needed_tag(view: dict) -> str:
    m = view.get("majority") or {}
    if view["kind"] == "jelenlet":
        return "jelenlét"
    return f'szükséges {m["needed"]} / {m["base"]}' if m.get("needed") and m.get("base") else "—"


def motion_title_html(view: dict) -> tuple[str, str]:
    mo = view["motions"][0] if view.get("motions") else {}
    if not mo:
        title = "Jelenlét megállapítása" if view["kind"] == "jelenlet" else (view.get("remark") or "Szavazás")
        return f'<div class="hero-title">{esc(title)}</div>', ""
    link = (f'<a href="{esc(mo.get("href"))}" target="_blank" rel="noopener">{esc(mo.get("iromany"))}</a> ' if mo.get("href") else esc(mo.get("iromany", "")) + " ")
    meta = f'{esc(mo.get("outcome", ""))} · benyújtó: {esc(", ".join(mo.get("submitters") or []))}' if mo.get("submitters") else esc(mo.get("outcome", ""))
    return f'<div class="hero-title">{link}{esc(mo.get("title", ""))}</div>', f'<div class="hero-meta">{meta}</div>'


def _glyph(pos: str, c: str) -> str:
    return f'<svg viewBox="-7 -7 14 14" aria-hidden="true" focusable="false">{_node(0, 0, pos, c)}</svg>'


def roll_call_table(view: dict, inp: dict) -> str:
    plan, facs = inp["plan"], inp["facs"]
    if not view["roll_call_available"]:
        return ""
    colour = {f["id"]: f["colour"] for f in facs}
    coords = (plan or {}).get("coords", {})
    order = {f["id"]: f["sort_order"] for f in facs}
    pos_rank = {p: i for i, p in enumerate(POSITION_ORDER)}
    rows = sorted(view["positions"], key=lambda p: (order.get(p["faction"] or "", 999), pos_rank.get(p["position"], 9), p["name"]))
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
        trs.append(f'<tr data-f="{esc(p["faction"])}" data-p="{esc(p["position"])}" data-name="{esc(p["name"])}" data-fac="{esc(p["faction"])}" data-posrank="{pos_rank.get(p["position"], 9)}" data-seat="{seatkey}">'
                   f'<td>{name_html}</td><td><span class="pos"><i style="width:9px;height:9px;border-radius:50%;background:{c};display:inline-block"></i>{esc(p["faction"])}</span></td>'
                   f'<td><span class="pos">{_glyph(p["position"], c)}{esc(POSITION_LABEL.get(p["position"], p["position"]))}</span></td><td class="mono">{esc(seat_full)}</td></tr>')
    fac_buttons = '<button type="button" data-fac="all" class="on" aria-pressed="true">minden frakció</button>' + "".join(f'<button type="button" data-fac="{esc(t["faction"])}" aria-pressed="false">{esc(t["faction"])}</button>' for t in view.get("faction_tallies") or [])
    pos_buttons = '<button type="button" data-posf="all" class="on" aria-pressed="true">minden szavazat</button>' + "".join(f'<button type="button" data-posf="{p}" aria-pressed="false">{esc(POSITION_LABEL[p])}</button>' for p in POSITION_ORDER if (view.get("position_counts") or {}).get(p))
    return (f'<section class="panel deep">{CORNERS}<h2><span data-kz-text>Név szerinti lista</span><span class="tag">{len(rows)} képviselő</span></h2>'
            f'<div class="filters" role="group" aria-label="Szűrés frakció szerint">{fac_buttons}</div><div class="filters" role="group" aria-label="Szűrés szavazat szerint">{pos_buttons}<span class="n" id="rn" aria-live="polite"></span></div>'
            '<div class="tablewrap"><table id="roll"><thead><tr><th scope="col" class="sortable" data-key="name">Képviselő</th><th scope="col" class="sortable" data-key="fac">Frakció</th>'
            '<th scope="col" class="sortable" data-key="posrank">Szavazat</th><th scope="col" class="sortable" data-key="seat">Ülőhely</th></tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table></div>'
            '<div class="hero-meta" style="margin-top:8px">A fejlécre kattintva rendezhető · ülőhely: szektor, sor, szék a képviselői adatlapról; a miniszteri pad a 0. szektor.</div></section>')


def terminal_html(inp: dict, lines: list[str] | None = None) -> str:
    """The footer as a terminal: a boot log of true values, then the method notes."""
    idx, fl, plan = inp["idx"], inp["fl"], inp["plan"]
    roll_calls = sum(1 for rows in inp["store"]["positions"].values() if rows)      # secret ballots have no list
    derived_txt = datetime.fromisoformat(idx["derived_at"]).astimezone(BUDAPEST).strftime("%Y-%m-%d %H:%M")
    seating_txt = (f"ülésrend: {len(plan['geometry']['sector_order_left_to_right'])} szektor + miniszteri pad" if plan
                   else "ülésrend: becsült elrendezés")
    default_lines = [
        f"parlament.hu/cgi-bin/web-api-pub — személyes hozzáférési kód, minden lekérés naplózva",
        f"kepviselok.cgi → {fl['mps']['total']} képviselő · " + " · ".join(f"{k} {v}" for k, v in fl["mps"]["by_faction"].items()),
        f"szavazasok.cgi → {fl['votes']} szavazás {fl['window']['from']} … {fl['window']['to']} · szavazas.cgi → {idx['details_cached']} részlet, {roll_calls} név szerinti lista",
        f"ulesnap.cgi → {fl['sitting_days']['count']} ülésnap · kepviselo.cgi → {seating_txt}",
        f"többségi szabály: az API „Szavazási mód” mezője · számítás és forrás eltér: {len(idx['disagreements'])} esetben",
        f"wikidata P4966 = p_azon · szinkron {sync_stamp(inp)} · levezetés {derived_txt} (budapesti idő)",
        f"kész — {len(idx['votes'])} szavazás-oldal · {len(inp['mps'])} képviselő-oldal · statikus, hálózat nélkül",
    ]
    log = "".join(f"<span>&gt; {esc(l)}</span>" for l in (lines or default_lines))
    return f"""<footer class="kz-terminal"><div class="scan"></div>{CORNERS}
  <div class="log">{log}</div>
  <div class="method">
    <h3>Forrás és módszer</h3>
    Adatok: az Országgyűlés Web API-ja (parlament.hu, XML). A személyek azonosítása a Wikidata P4966 azonosítóján keresztül, ami megegyezik az API <span class="mono">p_azon</span> értékével. Ez az oldal a <span class="mono">{esc(derived_txt)}</span> (budapesti idő) időpontban levezetett adatokból épült, {idx["details_cached"]} szavazás részletével és {roll_calls} név szerinti listával.
    <h3>Amit az oldal nem állít</h3>
    Semmit a képviselők szavazási szokásairól, a frakciófegyelemről vagy a törvényalkotás általános tempójáról — ezek számok, nem megállapítások. A „jelenlévő” fogalmának jogi értelmezése (jelenlét = igen + nem + tartózkodott + jelen, nem szavazott) még ellenőrzendő; a többségi szabályokat az API saját „Szavazási mód” mezője adja, a jogszabályi hivatkozások a kódban VERIFY jelöléssel szerepelnek. Színek: az oldal saját jelölései, nem a pártok arculata.
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


def directory_row(v: dict) -> str:
    """One row of the vote directory, rendered at build time (the JS only hides/shows rows)."""
    m = v.get("majority") or None
    rule = m["rule"] if m else ("jelenlet" if v["kind"] == "jelenlet" else "none")
    mo = v["motions"][0] if v.get("motions") else None
    if mo:
        subj = ((f'<a href="{esc(mo["href"])}" target="_blank" rel="noopener">{esc(mo.get("iromany") or "")}</a>' if mo.get("href") else esc(mo.get("iromany") or ""))
                + " " + esc((mo.get("title") or "")[:110]) + ("…" if len(mo.get("title") or "") > 110 else "")
                + f'<span class="sub">{esc(mo.get("outcome") or "")}</span>')
        hay = f'{mo.get("iromany") or ""} {mo.get("title") or ""} {mo.get("outcome") or ""}'
    else:
        subj = "<em>Jelenlét megállapítás</em>" if v["kind"] == "jelenlet" else esc(v.get("remark") or "—")
        hay = v.get("remark") or ""
    need = f'{esc(rule_short(m["rule"]))} <span class="sub mono">{m["needed"]} / {m["base"]}</span>' if m else "—"
    res = result_badge(v)
    margin = ("+" if m["margin"] >= 0 else "") + str(m["margin"]) if m and m.get("margin") is not None else ""
    flag = ' <span class="badge hi" title="a számított és a forrás szerinti eredmény eltér">?</span>' if m and m.get("agrees_with_source") is False else ""
    return (f'<tr data-rule="{esc(rule)}" data-result="{esc(v["result_raw"])}" data-hay="{esc(hay.casefold())}">'
            f'<td class="ts mono"><a href="szavazas/{esc(v["slug"])}.html">{esc(v["on_date"])} {esc(v["time"])}</a></td><td>{subj}</td><td>{need}</td>'
            f'<td class="num mono">{v["igen"]} – {v["nem"]} – {v["tartozkodott"]}</td><td class="num mono">{margin}</td><td>{res}{flag}</td></tr>')


def build_index(inp: dict, hero_ts: str) -> str:
    idx, fl = inp["idx"], inp["fl"]
    hero = vote_view(inp, hero_ts)
    title_html, meta_html = motion_title_html(hero)
    counts_cards = [
        (fl["votes"], "szavazás", f'{hu_date(fl["window"]["from"])} – {hu_date(fl["window"]["to"])}'),
        (fl["sitting_days"]["count"], "ülésnap", " · ".join(f'{k}: {v}' for k, v in fl["sitting_days"]["by_session"].items())),
        (fl["qualified_majority_votes"], "minősített többségű szavazás", f'{fl["decisions"]} döntésből'),
        (fl["kiveteles_votes"], "kivételes eljárás elrendelése", f'{fl["surgos_votes"]} sürgős tárgyalás'),
        (fl["hazszabalytol_elteres_votes"], "Házszabálytól eltérés", f'{fl["interpellation_answers"]} interpellációs válasz elfogadva'),
        (fl["mps"]["total"], "képviselő", " · ".join(f'{k}: {v}' for k, v in fl["mps"]["by_faction"].items())),
    ]
    counts_html = "".join(f'<div class="c">{CORNERS}<b class="mono" data-kz-number="{c}">{c}</b><span data-kz-text>{esc(l)}</span><span class="sub">{esc(s)}</span></div>' for c, l, s in counts_cards)
    mode_rows = "".join(f'<tr><td>{esc(k)}</td><td class="num mono">{v}</td></tr>' for k, v in fl["by_mode"].items())
    rules_present = sorted({(v["majority"] or {}).get("rule") for v in idx["votes"] if v.get("majority")}, key=lambda r: list(Rule).index(Rule(r)) if r else 99)
    rule_buttons = '<button type="button" data-rule="all" class="on" aria-pressed="true">mind</button>' + "".join(f'<button type="button" data-rule="{r}" aria-pressed="false">{esc(rule_short(r))}</button>' for r in rules_present) + '<button type="button" data-rule="jelenlet" aria-pressed="false">jelenlét</button>'
    result_buttons = '<button type="button" data-result="all" class="on" aria-pressed="true">minden eredmény</button><button type="button" data-result="Elfogadva" aria-pressed="false">elfogadva</button><button type="button" data-result="Elutasítva" aria-pressed="false">elutasítva</button>'
    dir_rows = "".join(directory_row(row) for row in reversed(idx["votes"]))          # newest first, complete without JS
    hero_date = hu_date(hero["on_date"])
    return page_head("karzat — az Országgyűlés szavazásai, ülőhelyenként",
                     f"Az Országgyűlés szavazásai a {inp['cycle']}. ciklusban: minden szavazás a saját szükséges többségével, egy szavazás ülőhelyenként kirajzolva. Forrás: parlament.hu Web API.") + topbar(inp, [], 0) + f"""
<div class="hero-h"><h1>karzat</h1><small class="label" data-kz-text>az Országgyűlés szavazásai, ülőhelyenként — a szükséges többséggel együtt</small></div>
<nav class="crumbs" aria-label="Szakaszok"><a href="#dir">szavazások</a> <span class="sl">/</span> <a href="kepviselo/index.html">képviselők</a></nav>
<p class="lede">Egy szavazás kirajzolva, {fl["votes"]} szavazás listázva a {inp["cycle"]}. ciklus első {fl["sitting_days"]["count"]} ülésnapjáról — mindegyik a saját oldalán, ülőhelyenként — minden döntés mellett azzal a többségi szabállyal, amelyet az Országgyűlés maga jelöl meg. Forrás: parlament.hu Web API; a személyek azonosítása Wikidatával egyeztetve.</p>
{freshness_html(inp)}
<section class="grid">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Egy szavazás, ülőhelyenként</span><span class="tag"><a href="szavazas/{esc(hero["slug"])}.html">{esc(hero_date)} {esc(hero["ts"][11:16])} ↗</a></span></h2>
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
    <h2><span data-kz-text>Szavazási módok</span><span class="tag">az API megjelölése szerint</span></h2>
    <div class="tablewrap" style="border:0"><table><thead><tr><th>Szavazási mód</th><th class="num">db</th></tr></thead><tbody>{mode_rows}</tbody></table></div>
    <div class="hero-meta prose" style="margin-top:8px">A „Listás” az egyszerű többségű gépi szavazás; a többi a szükséges többséget nevezi meg. Titkos szavazásnál nincs név szerinti lista.</div>
  </section>
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Egy törvény útja</span><span class="tag">T/71</span></h2>
    <div class="hero-title" style="font-size:16px">A Nemzetközi Büntetőbíróság Statútumából való kilépés visszavonása — 2026. évi XV. törvény</div>
    <div class="timeline"><div><b>máj. 25.</b>benyújtva (kormány)</div><div><b>máj. 26.</b>kivételes eljárás elrendelve</div><div><b>máj. 27.</b>zárószavazás</div><div><b>máj. 28.</b>kihirdetve, Magyar Közlöny 60</div></div>
    <div class="hero-meta prose" style="margin-top:8px">3 nap a benyújtástól a kihirdetésig. Az adatok — állapot, kihirdetés száma, MK száma, tárgyalási mód — mind az iromány-adatlapból jönnek; közlönyt nem kell böngészni.</div>
  </section>
</section>
<section class="panel deep" id="dir">{CORNERS}
  <h2><span data-kz-text>Szavazások</span><span class="tag">{fl["votes"]} tétel · a legfrissebb elöl · az időpontra kattintva a szavazás oldala</span></h2>
  <div class="filters" role="group" aria-label="Szűrés szükséges többség szerint">{rule_buttons}</div>
  <div class="filters" role="group" aria-label="Szűrés eredmény szerint">{result_buttons}<input id="q" type="search" placeholder="keresés --tárgy" aria-label="Keresés a tárgyban"><span class="n" id="n" aria-live="polite"></span></div>
  <div class="tablewrap"><table><thead><tr><th scope="col">Időpont</th><th scope="col">Tárgy</th><th scope="col">Szükséges többség</th><th scope="col" class="num">igen – nem – tart.</th><th scope="col" class="num">különbség</th><th scope="col">Eredmény</th></tr></thead><tbody id="rows">{dir_rows}</tbody></table></div>
  <div class="hero-meta prose" style="margin-top:8px">„Szükséges”: a szabály és a küszöb / az alap. A „?” jelzés azt jelenti, hogy a számított és a forrás szerinti eredmény eltér — ilyen jelenleg {len(idx["disagreements"])} van.</div>
</section>
""" + page_tail(inp, 0)


def build_vote_page(inp: dict, ts: str) -> str:
    order = inp["order"]
    i = order.index(ts)
    prev_ts = order[i - 1] if i > 0 else None
    next_ts = order[i + 1] if i + 1 < len(order) else None
    view = vote_view(inp, ts)
    title_html, meta_html = motion_title_html(view)
    mo = view["motions"][0] if view.get("motions") else {}
    label = f'{mo.get("iromany") or ""} {(mo.get("title") or "")[:90]}'.strip() if mo else ("Jelenlét megállapítása" if view["kind"] == "jelenlet" else "Szavazás")
    when = f'{hu_date(view["on_date"])} {view["ts"][11:16]}'

    def pager_link(t, arrow_left):
        if not t:
            return "<span></span>"
        vv = inp["by_ts"][t]
        m0 = vv["motions"][0] if vv["motions"] else {}
        txt = f'{vv["on_date"]} {vv["time"]} · {(m0.get("iromany") or "")} {(m0.get("title") or vv.get("remark") or "")[:60]}'
        return f'<a href="{esc(vv["slug"])}.html">{"← " if arrow_left else ""}{esc(txt)}{"" if arrow_left else " →"}</a>'

    return page_head(f'{when} — {label} · karzat', f'Az Országgyűlés {when} szavazása ülőhelyenként, a szükséges többséggel: {label}', 1) + \
        topbar(inp, [("szavazások", "../index.html#dir"), (f'{view["on_date"]} {view["ts"][11:16]}', None)], 1) + f"""
<div class="hero-h"><h1>{esc(when)}</h1><small class="label" data-kz-text>{esc(view["mode"])}</small></div>
<section class="grid">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>A szavazás</span><span class="tag">{esc(view["ts"])}</span></h2>
    {title_html}{meta_html}
    {chart_block(view, inp)}
  </section>
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Eredmény és a szükséges többség</span><span class="tag">{esc(needed_tag(view))}</span></h2>
    {verdict_block(view, inp)}
  </section>
</section>
{roll_call_table(view, inp)}
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
    R = geo["outer_radius"] + 14
    parts = []
    for a, xy in plan["coords"].items():
        if a == azon:
            continue
        parts.append(f'<circle cx="{xy["x"]}" cy="{xy["y"]}" r="3.6" fill="#a1a1aa" fill-opacity=".14"/>')
    me = plan["coords"][azon]
    c = colour.get(inp["mps"].get(azon, {}).get("faction") or "", "#8a8a8a")
    parts.append(f'<circle cx="{me["x"]}" cy="{me["y"]}" r="9" fill="{c}" fill-opacity=".25"/><circle cx="{me["x"]}" cy="{me["y"]}" r="5" fill="{c}"/>')
    return (f'<svg viewBox="-172 -{R+12:.0f} 344 {R+30:.0f}" role="img" aria-label="Ülőhely az ülésteremben">'
            f'<rect x="-16" y="-7" width="32" height="8" rx="1.5" class="rostrum"/>' + "".join(parts) + '</svg>')


def mandate_text(mp: dict) -> str:
    if mp.get("mandate_kind") == "egyeni":
        return f'egyéni választókerület — {mp.get("county")} {mp.get("constituency_no")}. OEVK'
    if mp.get("mandate_kind") == "lista":
        return "országos lista"
    return "—"


def seat_text(mp: dict) -> str:
    s = mp.get("seat") or {}
    if s.get("sector") is None:
        return "nincs ülőhely-adat"
    if s["sector"] == 0:
        return f'miniszteri pad, {s["seat"]}. szék'
    return f'{s["sector"]}. szektor, {s["row"]}. sor, {s["seat"]}. szék'


def build_mp_page(inp: dict, azon: str) -> str:
    mp = inp["mps"][azon]
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
        subj = f'{esc(mo.get("iromany") or "")} {esc((mo.get("title") or vv.get("remark") or ("Jelenlét megállapítása" if vv["kind"] == "jelenlet" else ""))[:80])}'
        pos_html = f'<span class="pos">{_glyph(v["position"], c)}{esc(POSITION_LABEL.get(v["position"], v["position"]))}</span>'
        fp = POSITION_LABEL.get(v["faction_plurality"] or "", "—") if v["faction_plurality"] else "—"
        flag = {"with": '<span class="badge">frakcióval</span>', "against": '<span class="badge hi">frakció ellen</span>'}.get(v["align"] or "", '<span class="badge mid">nem szavazott</span>' if v["position"] not in CAST else "")
        return (f'<tr data-al="{esc(v["align"] or "none")}" data-p="{esc(v["position"])}"><td class="ts mono"><a href="../szavazas/{esc(vv["slug"])}.html">{esc(vv["on_date"])} {esc(vv["time"])}</a></td>'
                f'<td>{subj}<span class="sub">{esc(rule_short((vv.get("majority") or {}).get("rule")))} · {esc(vv["result_raw"])}</span></td><td>{pos_html}</td><td>{esc(fp)}</td><td>{flag}</td></tr>')
    dev_rows = "".join(vote_row(v) for v in reversed(devs))
    all_rows = "".join(vote_row(v) for v in reversed(al["votes"]))
    fac_hist = "".join(f'<tr><td class="mono">{esc(f["ciklus"])}</td><td>{esc(f["faction"])}</td><td class="mono">{esc(f["from"] or "")} – {esc(f["to"] or "")}</td></tr>' for f in mp.get("factions") or [])
    elections = "".join(f'<tr><td class="mono">{esc(e["ciklus"])}</td><td>{esc(e["constituency"] or "—")}</td><td class="mono">{esc(e["elected_on"] or "")}</td><td class="mono">{esc(e["mandate_from"] or "")} – {esc(e["mandate_to"] or "")}</td></tr>' for e in mp.get("elections") or [])
    motions = "".join(f'<tr><td class="mono">{esc(m["ciklus"])}</td><td class="num mono">{esc(m["onallo"])}</td><td class="num mono">{esc(m["nem_onallo"])}</td></tr>' for m in mp.get("motion_stats") or [])
    former_note = "" if mp.get("current") else f'<div class="hero-meta">Mandátuma megszűnt{(" " + hu_date(mp["wikidata_end"])) if mp.get("wikidata_end") else ""}; a képviselői adatlap ma már nem tartalmaz ülőhelyet.</div>'
    links = " · ".join(x for x in [
        f'<a href="{esc(mp["parlament_url"])}" target="_blank" rel="noopener">parlament.hu adatlap ↗</a>',
        f'<a href="{esc(mp["photo_url"])}" target="_blank" rel="noopener">fénykép (parlament.hu) ↗</a>' if mp.get("photo_url") else "",
        f'<a href="https://www.wikidata.org/wiki/{esc(mp["wikidata_qid"])}" target="_blank" rel="noopener">Wikidata {esc(mp["wikidata_qid"])} ↗</a>' if mp.get("wikidata_qid") else "",
        f'<a href="https://{esc(mp["website"])}" target="_blank" rel="noopener">{esc(mp["website"])} ↗</a>' if mp.get("website") and "." in mp["website"] else "",
    ] if x)
    shown = [p for p in POSITION_ORDER if p != "igazoltan_tavol" or counts.get(p)]     # the seventh state only when it occurs
    pos_cells = "".join(f'<div><b class="mono" data-kz-number="{counts.get(p, 0)}">{counts.get(p, 0)}</b><span>{esc(POSITION_LABEL[p])}</span></div>' for p in shown)
    tally_cls = "tally seven" if len(shown) == 7 else "tally six"
    return page_head(f'{mp["name"]} ({mp.get("faction") or "—"}) · karzat',
                     f'{mp["name"]} — {mp.get("faction") or ""}, {mandate_text(mp)}: szavazási részvétel és a frakcióval való egyezés a {inp["cycle"]}. ciklusban.', 1) + \
        topbar(inp, [("képviselők", "index.html"), (mp["name"], None)], 1) + f"""
<div class="hero-h"><h1>{esc(mp["name"])}</h1><small class="label"><span class="pos"><i style="width:9px;height:9px;border-radius:50%;background:{c};display:inline-block"></i>{esc(mp.get("faction") or "—")}</span> · {esc(mandate_text(mp))} · {esc(seat_text(mp))}</small></div>
<p class="hero-meta">{links}</p>
{former_note}
<section class="grid">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Szavazási részvétel a {inp["cycle"]}. ciklusban</span><span class="tag">{in_roll} név szerinti szavazás</span></h2>
    <div class="{tally_cls}">{pos_cells}</div>
    <dl class="verdict">
      <dt>Leadott szavazat</dt><dd class="mono">{cast} / {in_roll} ({part})</dd>
      <dt>Frakciójával</dt><dd class="mono">{al["with"]} · ellene {al["against"]} · egyezés {with_pct}</dd>
    </dl>
    <div class="hero-meta prose" style="margin-top:8px">„Frakciójával”: a leadott szavazata (igen / nem / tartózkodott) megegyezik a frakciója leadott szavazatainak többségi álláspontjával az adott szavazáson; „ellene”: eltér attól. A nem leadott állapotok (jelen, nem szavazott; nem szavazott; előre bejelentett hiányzó; igazoltan távol) részvétel, nem egyezés. Ezek számok, nem minősítések: egy „ellene” lehet lelkiismereti szavazás, egy „nem szavazott” lehet betegség.</div>
  </section>
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Ülőhely az ülésteremben</span><span class="tag">{esc(seat_text(mp))}</span></h2>
    <div class="chart">{chamber_mini_svg(inp, azon) or '<div class="hero-meta">Nincs ülőhely-adat.</div>'}</div>
    <div class="hero-meta">az elnöki emelvény felől nézve · a rekonstrukció feltevéseit a szavazás-oldalak jegyzete sorolja</div>
  </section>
</section>
<section class="grid">
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Frakciótagság és mandátum</span><span class="tag">a képviselői adatlap szerint</span></h2>
    <div class="tablewrap" style="border:0"><table><thead><tr><th>Ciklus</th><th>Frakció</th><th>Időszak</th></tr></thead><tbody>{fac_hist or '<tr><td colspan="3">—</td></tr>'}</tbody></table></div>
    <div class="tablewrap" style="border:0;margin-top:10px"><table><thead><tr><th>Ciklus</th><th>Választókerület / lista</th><th>Választás</th><th>Mandátum</th></tr></thead><tbody>{elections or '<tr><td colspan="4">—</td></tr>'}</tbody></table></div>
  </section>
  <section class="panel">{CORNERS}
    <h2><span data-kz-text>Benyújtott indítványok</span><span class="tag">ciklusonként</span></h2>
    <div class="tablewrap" style="border:0"><table><thead><tr><th>Ciklus</th><th class="num">önálló</th><th class="num">nem önálló</th></tr></thead><tbody>{motions or '<tr><td colspan="3">—</td></tr>'}</tbody></table></div>
    <div class="hero-meta prose" style="margin-top:8px">Az API <span class="mono">&lt;inditvanyok&gt;</span> összesítése; a tételek maguk még nincsenek betöltve.</div>
  </section>
</section>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Szavazatok a frakció többségével szemben</span><span class="tag">{len(devs)}</span></h2>
  <div class="tablewrap"><table><thead><tr><th>Időpont</th><th>Tárgy</th><th>Szavazata</th><th>Frakció többsége</th><th></th></tr></thead><tbody>{dev_rows or '<tr><td colspan="5">Nincs ilyen szavazás.</td></tr>'}</tbody></table></div>
</section>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Minden szavazása</span><span class="tag">{in_roll} tétel · a legfrissebb elöl</span></h2>
  <div class="filters" role="group" aria-label="Szűrés egyezés szerint"><button type="button" data-alf="all" class="on" aria-pressed="true">mind</button><button type="button" data-alf="with" aria-pressed="false">frakcióval</button><button type="button" data-alf="against" aria-pressed="false">frakció ellen</button><button type="button" data-alf="none" aria-pressed="false">nem adott le szavazatot</button><span class="n" id="rn" aria-live="polite"></span></div>
  <div class="tablewrap"><table id="mine"><thead><tr><th>Időpont</th><th>Tárgy</th><th>Szavazata</th><th>Frakció többsége</th><th></th></tr></thead><tbody>{all_rows}</tbody></table></div>
</section>
""" + page_tail(inp, 1)


def build_mp_index(inp: dict) -> str:
    facs = inp["facs"]
    colour = {f["id"]: f["colour"] for f in facs}
    order = {f["id"]: f["sort_order"] for f in facs}
    mps = sorted(inp["mps"].values(), key=lambda m: (not m["current"], order.get(m.get("faction") or "", 999), m["name"]))
    trs = []
    for mp in mps:
        al = inp["alignment"]["per_mp"].get(mp["p_azon"]) or {"with": 0, "against": 0, "cast": 0, "in_roll": 0}
        c = colour.get(mp.get("faction") or "", "#8a8a8a")
        part = f'{100*al["cast"]/al["in_roll"]:.0f}%' if al["in_roll"] else "—"
        former_badge = "" if mp["current"] else ' <span class="badge mid">volt képviselő</span>'
        part_key = f'{al["cast"]/al["in_roll"]:.4f}' if al["in_roll"] else "-1"
        trs.append(f'<tr data-f="{esc(mp.get("faction") or "")}" data-name="{esc(mp["name"])}" data-fac="{esc(mp.get("faction") or "")}" data-part="{part_key}" data-cast="{al["cast"]}" data-against="{al["against"]}" data-cur="{1 if mp["current"] else 0}">'
                   f'<td><a href="{esc(mp["p_azon"])}.html">{esc(mp["name"])}</a>{former_badge}</td>'
                   f'<td><span class="pos"><i style="width:9px;height:9px;border-radius:50%;background:{c};display:inline-block"></i>{esc(mp.get("faction") or "—")}</span></td>'
                   f'<td>{esc(mandate_text(mp))}</td><td class="mono">{esc(seat_text(mp))}</td>'
                   f'<td class="num mono">{al["cast"]} / {al["in_roll"]}</td><td class="num mono">{part}</td><td class="num mono">{al["against"]}</td></tr>')
    fac_buttons = '<button type="button" data-fac="all" class="on" aria-pressed="true">minden frakció</button>' + "".join(f'<button type="button" data-fac="{esc(f["id"])}" aria-pressed="false">{esc(f["id"])}</button>' for f in facs if any(m.get("faction") == f["id"] for m in mps))
    n_cur = sum(1 for m in mps if m["current"]); n_former = len(mps) - n_cur
    return page_head("Képviselők · karzat", f"Az Országgyűlés képviselői a {inp['cycle']}. ciklusban: mandátum, ülőhely, szavazási részvétel és a frakcióval való egyezés.", 1) + \
        topbar(inp, [("képviselők", None)], 1) + f"""
<div class="hero-h"><h1>Képviselők</h1><small class="label" data-kz-text>{n_cur} jelenlegi és {n_former} volt képviselő a névsorokból</small></div>
<section class="panel deep">{CORNERS}
  <h2><span data-kz-text>Névsor</span><span class="tag">a fejlécre kattintva rendezhető</span></h2>
  <div class="filters" role="group" aria-label="Szűrés frakció szerint">{fac_buttons}<span class="n" id="rn" aria-live="polite"></span></div>
  <div class="tablewrap"><table id="roll"><thead><tr><th scope="col" class="sortable" data-key="name">Képviselő</th><th scope="col" class="sortable" data-key="fac">Frakció</th><th scope="col">Mandátum</th><th scope="col">Ülőhely</th><th scope="col" class="sortable num" data-key="cast">Leadott / névsor</th><th scope="col" class="sortable num" data-key="part">Részvétel</th><th scope="col" class="sortable num" data-key="against">Frakció ellen</th></tr></thead><tbody>{"".join(trs)}</tbody></table></div>
  <div class="hero-meta prose" style="margin-top:8px">„Leadott”: igen / nem / tartózkodott a név szerinti szavazásokon; „részvétel”: leadott / névsorban szereplő; „frakció ellen”: a frakció leadott szavazatainak többségétől eltérő leadott szavazatok száma. Számok, nem minősítések.</div>
</section>
""" + page_tail(inp, 1)


# -- main -------------------------------------------------------------------------------------

HERO_TS = "2026.06.15.17:20:04"


def build() -> str:
    """The index page (the name tests and --check use)."""
    return build_index(load_inputs(), HERO_TS)


def build_assets() -> dict[str, str]:
    """The shared stylesheet and script, generated like the pages (committed, checked, never hand-edited)."""
    return {"karzat.css": CSS.strip() + "\n", "karzat.js": (JS_INDEX + "\n" + JS_VOTE + "\n" + JS_MP + "\n" + JS_BOOT).strip() + "\n"}


def build_all(out_dir: Path, index_only: bool = False) -> dict:
    inp = load_inputs()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "assets").mkdir(parents=True, exist_ok=True)
    for name, body in build_assets().items():
        (out_dir / "assets" / name).write_text(body, encoding="utf-8")
    (out_dir / "index.html").write_text(build_index(inp, HERO_TS), encoding="utf-8")
    n = k = 0
    if not index_only:
        vd = out_dir / "szavazas"
        vd.mkdir(parents=True, exist_ok=True)
        for ts in inp["order"]:
            (vd / f'{inp["by_ts"][ts]["slug"]}.html').write_text(build_vote_page(inp, ts), encoding="utf-8")
            n += 1
        md = out_dir / "kepviselo"
        md.mkdir(parents=True, exist_ok=True)
        (md / "index.html").write_text(build_mp_index(inp), encoding="utf-8")
        for azon in inp["mps"]:
            (md / f"{azon}.html").write_text(build_mp_page(inp, azon), encoding="utf-8")
            k += 1
    return {"index": str(out_dir / "index.html"), "vote_pages": n, "mp_pages": k}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="compare a fresh build of site/index.html and site/assets/* with the committed files; exit 1 on difference")
    ap.add_argument("--index-only", action="store_true", help="do not (re)generate the per-vote pages")
    ap.add_argument("--out", type=Path, default=SITE_DIR)
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
    res = build_all(args.out, index_only=args.index_only)
    print(f"written {res['index']} + {res['vote_pages']} vote pages under {args.out / 'szavazas'} + {res['mp_pages']} MP pages under {args.out / 'kepviselo'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
