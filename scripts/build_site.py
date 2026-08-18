#!/usr/bin/env python3
"""Build the site — site/index.html and one page per vote under site/szavazas/ — from committed inputs.

    python3 -m scripts.build_site               # index + every vote page
    python3 -m scripts.build_site --index-only  # just the index
    python3 -m scripts.build_site --check       # exit 1 if the committed index differs from a fresh build

Inputs (all committed, all produced by scripts/derive_* from the cache):
  data/derived/votes_index.json      every listed vote: motion, majority verdict, tallies, sitting days
  data/derived/votes_positions.json  every roll call, compact (azon, faction, position code)
  data/derived/seating.json          the reconstructed chamber (coordinates per p_azon)
  data/derived/first_light.json      the headline counts
  data/derived/hero_vote.json        kept for the README gate; the hero itself is rendered like any vote page
  config/factions.yml                faction colours and left→right order

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
from karzat.majority import RULES, Rule  # noqa: E402

DERIVED = ROOT / "data" / "derived"
SEATING = DERIVED / "seating.json"
SITE_DIR = ROOT / "site"
SITE = SITE_DIR / "index.html"
VOTES_DIR = SITE_DIR / "szavazas"
FACTIONS = ROOT / "config" / "factions.yml"

POSITION_ORDER = ["igen", "tartozkodott", "jelen_nem_szavazott", "nem", "nem_szavazott", "bejelentett_hianyzo"]
POSITION_LABEL = {"igen": "igen", "nem": "nem", "tartozkodott": "tartózkodott", "jelen_nem_szavazott": "jelen, nem szavazott",
                  "nem_szavazott": "nem szavazott", "bejelentett_hianyzo": "előre bejelentett hiányzó"}
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
    facs = factions()
    return {"idx": idx, "store": store, "fl": fl, "plan": plan, "facs": facs,
            "by_ts": {v["ts"]: v for v in idx["votes"]}, "order": [v["ts"] for v in idx["votes"]]}


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
        return f'<circle cx="{cx}" cy="{cy}" r="4.6" fill="{c}" fill-opacity="0.22" stroke="{c}" stroke-opacity="0.35" stroke-width="1"/>'
    return f'<circle cx="{cx}" cy="{cy}" r="2.2" fill="{c}" fill-opacity="0.35"/>'


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
:root{--bg:#f6f7f9;--card:#fff;--ink:#1a2230;--muted:#5b6675;--line:#dde2e9;--accent:#2563eb;--ok:#15803d;--okbg:#e8f7ee;--warn:#b91c1c;--warnbg:#fde8e8;--chip:#eef1f5;--rostrum:#c9d0da}
@media (prefers-color-scheme:dark){:root{--bg:#10141c;--card:#161b26;--ink:#e6ebf2;--muted:#93a0b4;--line:#242c3a;--accent:#5b8def;--ok:#4ade80;--okbg:#12281c;--warn:#fca5a5;--warnbg:#2a1416;--chip:#1c2330;--rostrum:#2c3545}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 72px}
header h1{margin:0;font-size:34px;font-weight:600;letter-spacing:-.02em}header h1 small{font-weight:400;color:var(--muted);font-size:16px;margin-left:10px}
header h1 a{color:inherit}
.crumbs{font-size:13px;color:var(--muted);margin:0 0 6px}.crumbs a{color:var(--muted)}
.lede{margin:6px 0 0;color:var(--muted);max-width:70ch}
.fresh{margin:16px 0 0;padding:10px 14px;border:1px solid var(--line);border-left:3px solid var(--accent);background:var(--card);border-radius:6px;font-size:14px}
.fresh .en{color:var(--muted);display:block;margin-top:2px}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}
.grid{display:grid;grid-template-columns:1.25fr .9fr;gap:20px;margin-top:26px}
@media(max-width:860px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:18px 20px}
.card h2{margin:0 0 6px;font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:600}
.hero-title{font-size:20px;font-weight:600;line-height:1.3;margin:2px 0 8px}
.hero-meta{color:var(--muted);font-size:13.5px}
.chart svg{width:100%;height:auto;display:block;margin-top:6px}
.seat circle,.seat path{transition:opacity .15s}
.chart svg:hover .seat{opacity:.55}.chart svg .seat:hover{opacity:1}
.rostrum{fill:var(--rostrum)}.seclabel{font-size:8px;fill:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}.aisle{stroke:var(--line);stroke-width:.8}.fbarc{fill:none;stroke:var(--line);stroke-width:.8;stroke-dasharray:2 2}
.legend{display:flex;flex-wrap:wrap;gap:8px 16px;margin-top:8px;font-size:12.5px;color:var(--muted)}
.legend .f{display:inline-flex;align-items:center;gap:6px}.legend i{width:11px;height:11px;border-radius:50%;display:inline-block}.legend svg{width:14px;height:14px;flex:none}
.tally{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:8px 0 12px}
.tally div{border:1px solid var(--line);border-radius:6px;padding:8px 10px}
.tally b{display:block;font-size:26px;font-weight:600;letter-spacing:-.02em}
.tally span{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.verdict{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;font-size:14px;margin-top:6px}
.verdict dt{color:var(--muted)}.verdict dd{margin:0}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600}
.ok{background:var(--okbg);color:var(--ok)}.no{background:var(--warnbg);color:var(--warn)}.mid{background:var(--chip);color:var(--muted)}
.bar{height:8px;background:var(--chip);border-radius:4px;position:relative;overflow:hidden;margin:6px 0 2px}
.bar i{position:absolute;left:0;top:0;bottom:0;background:var(--ink);opacity:.85}
.bar em{position:absolute;top:-2px;bottom:-2px;width:2px;background:var(--accent)}
.fbars{margin-top:12px;font-size:12.5px}.fbars .row{display:grid;grid-template-columns:78px 1fr 92px;gap:8px;align-items:center;margin:4px 0}
.fbars .stack{display:flex;height:9px;border-radius:3px;overflow:hidden;background:var(--chip)}.fbars .stack i{display:block;height:100%}
.counts{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:20px}
.counts .card{padding:12px 14px}.counts b{display:block;font-size:26px;font-weight:600;letter-spacing:-.02em}.counts span{font-size:12px;color:var(--muted)}
.dir{margin-top:26px}
.filters{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 10px;align-items:center}
.filters button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:4px 10px;font-size:12.5px;cursor:pointer}
.filters button.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.filters input{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:6px;padding:5px 9px;font-size:13px;min-width:220px}
.filters .n{color:var(--muted);font-size:12.5px;margin-left:auto}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:7px 10px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}
th{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;background:var(--bg);position:sticky;top:0}
th.sortable{cursor:pointer}th.sortable:hover{color:var(--ink)}
tr:last-child td{border-bottom:0}
td.num{text-align:right;white-space:nowrap}td.ts{white-space:nowrap;color:var(--muted)}
td .sub{color:var(--muted);font-size:12px;display:block}
.pos{display:inline-flex;align-items:center;gap:6px}.pos svg{width:13px;height:13px;flex:none}
.tl{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:8px;font-size:12.5px}.tl div{border-left:2px solid var(--accent);padding-left:8px}.tl b{display:block}
.pager{display:flex;justify-content:space-between;gap:12px;margin:18px 0 0;font-size:13.5px}.pager a{max-width:46%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
footer{margin-top:36px;color:var(--muted);font-size:12.5px;line-height:1.6}
footer h3{font-size:12px;text-transform:uppercase;letter-spacing:.08em;margin:18px 0 6px;color:var(--muted)}
"""

JS_INDEX = """
(function(){
  var rows = window.__KARZAT_VOTES__ || [];
  var tbody = document.getElementById('rows'), n = document.getElementById('n');
  var rule = 'all', result = 'all', q = '';
  function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
  function short(r){return {egyszeru:'egyszerű',abszolut:'abszolút (összes ½)',ketharmad_jelenlevo:'⅔ jelenlévők',ketharmad_osszes:'⅔ összes',negyotod_jelenlevo:'⅘ jelenlévők',negyotod_osszes:'⅘ összes',relativ:'relatív'}[r]||'—';}
  function render(){
    var out = [], k = 0;
    for (var i = rows.length - 1; i >= 0; i--) {
      var v = rows[i], m = v.majority, mr = m ? m.rule : (v.kind === 'jelenlet' ? 'jelenlet' : 'none');
      if (rule !== 'all' && mr !== rule) continue;
      if (result !== 'all' && v.result_raw !== result) continue;
      var mo = v.motions && v.motions[0];
      var hay = ((mo && (mo.iromany + ' ' + mo.title + ' ' + mo.outcome)) || v.remark || '').toLowerCase();
      if (q && hay.indexOf(q) < 0) continue;
      k++;
      var subj = mo ? ((mo.href ? '<a href="' + esc(mo.href) + '" target="_blank" rel="noopener">' + esc(mo.iromany) + '</a>' : esc(mo.iromany || '')) +
                 ' ' + esc((mo.title || '').slice(0, 110)) + ((mo.title||'').length > 110 ? '…' : '') + '<span class="sub">' + esc(mo.outcome || '') + '</span>')
               : (v.kind === 'jelenlet' ? '<em>Jelenlét megállapítás</em>' : esc(v.remark || '—'));
      var need = m ? (short(m.rule) + ' <span class="sub mono">' + m.needed + ' / ' + m.base + '</span>') : '—';
      var res = v.result_raw === 'Elfogadva' ? '<span class="badge ok">Elfogadva</span>' : v.result_raw === 'Elutasítva' ? '<span class="badge no">Elutasítva</span>' : '<span class="badge mid">' + esc(v.result_raw) + '</span>';
      var margin = m && m.margin != null ? (m.margin >= 0 ? '+' : '') + m.margin : '';
      var flag = m && m.agrees_with_source === false ? ' <span class="badge no" title="a számított és a forrás szerinti eredmény eltér">?</span>' : '';
      out.push('<tr><td class="ts mono"><a href="szavazas/' + esc(v.slug) + '.html">' + esc(v.on_date) + ' ' + esc(v.time) + '</a></td><td>' + subj + '</td><td>' + need + '</td>' +
               '<td class="num mono">' + esc(v.igen) + ' – ' + esc(v.nem) + ' – ' + esc(v.tartozkodott) + '</td><td class="num mono">' + margin + '</td><td>' + res + flag + '</td></tr>');
    }
    tbody.innerHTML = out.join('');
    n.textContent = k + ' / ' + rows.length;
  }
  document.querySelectorAll('[data-rule]').forEach(function(b){ b.addEventListener('click', function(){ rule = b.getAttribute('data-rule'); document.querySelectorAll('[data-rule]').forEach(function(x){x.classList.toggle('on', x===b);}); render(); }); });
  document.querySelectorAll('[data-result]').forEach(function(b){ b.addEventListener('click', function(){ result = b.getAttribute('data-result'); document.querySelectorAll('[data-result]').forEach(function(x){x.classList.toggle('on', x===b);}); render(); }); });
  document.getElementById('q').addEventListener('input', function(e){ q = e.target.value.trim().toLowerCase(); render(); });
  render();
})();
"""

JS_VOTE = """
(function(){
  var table = document.getElementById('roll'); if (!table) return;
  var tbody = table.tBodies[0], rows = Array.prototype.slice.call(tbody.rows), dir = {};
  var fac = 'all', pos = 'all';
  function apply(){
    var k = 0;
    rows.forEach(function(r){ var ok = (fac === 'all' || r.getAttribute('data-f') === fac) && (pos === 'all' || r.getAttribute('data-p') === pos); r.style.display = ok ? '' : 'none'; if (ok) k++; });
    document.getElementById('rn').textContent = k + ' / ' + rows.length;
  }
  document.querySelectorAll('[data-fac]').forEach(function(b){ b.addEventListener('click', function(){ fac = b.getAttribute('data-fac'); document.querySelectorAll('[data-fac]').forEach(function(x){x.classList.toggle('on', x===b);}); apply(); }); });
  document.querySelectorAll('[data-posf]').forEach(function(b){ b.addEventListener('click', function(){ pos = b.getAttribute('data-posf'); document.querySelectorAll('[data-posf]').forEach(function(x){x.classList.toggle('on', x===b);}); apply(); }); });
  table.querySelectorAll('th.sortable').forEach(function(th, i){
    th.addEventListener('click', function(){
      var d = dir[i] = -(dir[i] || -1);
      var key = th.getAttribute('data-key');
      rows.sort(function(a, b){ var x = a.getAttribute('data-' + key) || '', y = b.getAttribute('data-' + key) || ''; var nx = parseFloat(x), ny = parseFloat(y); if (!isNaN(nx) && !isNaN(ny)) return (nx - ny) * d; return x.localeCompare(y, 'hu') * d; });
      rows.forEach(function(r){ tbody.appendChild(r); });
    });
  });
  apply();
})();
"""


# -- shared fragments -------------------------------------------------------------------------

def page_head(title: str, description: str) -> str:
    return f"""<!doctype html>
<html lang="hu">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">"""


def legend_html(view: dict, facs: list[dict]) -> str:
    fac_by_id = {f["id"]: f for f in facs}
    legend_f = "".join(
        f'<span class="f"><i style="background:{fac_by_id.get(t["faction"], {"colour": "#8a8a8a"})["colour"]}"></i>{esc(t["faction"])} <span class="mono">{esc(t["osszesen"])}</span></span>'
        for t in view.get("faction_tallies") or [])
    legend_p = ('<span class="f"><svg viewBox="-7 -7 14 14"><circle r="5" fill="currentColor"/></svg>igen</span>'
                '<span class="f"><svg viewBox="-7 -7 14 14"><circle r="4.6" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M-4.6 0 A4.6 4.6 0 0 1 4.6 0Z" fill="currentColor"/></svg>tartózkodott</span>'
                '<span class="f"><svg viewBox="-7 -7 14 14"><circle r="4.6" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>nem</span>'
                '<span class="f"><svg viewBox="-7 -7 14 14"><circle r="4.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.4 2"/></svg>jelen, nem szavazott</span>'
                '<span class="f"><svg viewBox="-7 -7 14 14"><circle r="4.6" fill="currentColor" fill-opacity=".22" stroke="currentColor" stroke-opacity=".35"/></svg>nem szavazott</span>'
                '<span class="f"><svg viewBox="-7 -7 14 14"><circle r="2.2" fill="currentColor" fill-opacity=".35"/></svg>előre bejelentett hiányzó</span>')
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
    tally = (f'<div class="tally"><div><b class="mono">{view["igen"]}</b><span>igen</span></div><div><b class="mono">{view["nem"]}</b><span>nem</span></div>'
             f'<div><b class="mono">{view["tartozkodott"]}</b><span>tartózkodott</span></div></div>')
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
        f = fac_by_id.get(t["faction"], {"colour": "#8a8a8a"})
        n = t["osszesen"] or 1
        seg = lambda k, op: f'<i style="width:{100*(t[k] or 0)/n:.1f}%;background:{f["colour"]};opacity:{op}" title="{esc(t["faction"])} {POSITION_LABEL.get(k,k)}: {t[k]}"></i>'
        rest = n - (t["igen"] or 0) - (t["tartozkodott"] or 0) - (t["nem"] or 0)
        fb.append(f'<div class="row"><span>{esc(t["faction"])}</span><span class="stack">{seg("igen",1)}{seg("tartozkodott",.55)}{seg("nem",.25)}'
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
            + (f'<dt>Nem szavazott</dt><dd class="mono">{counts.get("nem_szavazott", 0)} · jelen, nem szavazott {counts.get("jelen_nem_szavazott", 0)} · előre bejelentett hiányzó {counts.get("bejelentett_hianyzo", 0)}</dd>' if view["roll_call_available"] else '')
            + '</dl>'
            + (f'<div class="fbars">{"".join(fb)}</div><div class="hero-meta" style="margin-top:10px">Frakciónként: igen (teli), tartózkodott (fél), nem (halvány), a maradék nem szavazott vagy hiányzott. Számok: igen – nem – tartózkodott.</div>' if fb else ''))


def motion_title_html(view: dict) -> tuple[str, str]:
    mo = view["motions"][0] if view.get("motions") else {}
    if not mo:
        title = "Jelenlét megállapítása" if view["kind"] == "jelenlet" else (view.get("remark") or "Szavazás")
        return f'<div class="hero-title">{esc(title)}</div>', ""
    link = (f'<a href="{esc(mo.get("href"))}" target="_blank" rel="noopener">{esc(mo.get("iromany"))}</a> ' if mo.get("href") else esc(mo.get("iromany", "")) + " ")
    meta = f'{esc(mo.get("outcome", ""))} · benyújtó: {esc(", ".join(mo.get("submitters") or []))}' if mo.get("submitters") else esc(mo.get("outcome", ""))
    return f'<div class="hero-title">{link}{esc(mo.get("title", ""))}</div>', f'<div class="hero-meta">{meta}</div>'


def _glyph(pos: str, c: str) -> str:
    return f'<svg viewBox="-7 -7 14 14">{_node(0, 0, pos, c)}</svg>'


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
        trs.append(f'<tr data-f="{esc(p["faction"])}" data-p="{esc(p["position"])}" data-name="{esc(p["name"])}" data-fac="{esc(p["faction"])}" data-pos="{pos_rank.get(p["position"], 9)}" data-seat="{seatkey}">'
                   f'<td>{esc(p["name"])}</td><td><span class="pos"><i style="width:9px;height:9px;border-radius:50%;background:{c};display:inline-block"></i>{esc(p["faction"])}</span></td>'
                   f'<td><span class="pos">{_glyph(p["position"], c)}{esc(POSITION_LABEL.get(p["position"], p["position"]))}</span></td><td class="mono">{esc(seat_full)}</td></tr>')
    fac_buttons = '<button data-fac="all" class="on">minden frakció</button>' + "".join(f'<button data-fac="{esc(t["faction"])}">{esc(t["faction"])}</button>' for t in view.get("faction_tallies") or [])
    pos_buttons = '<button data-posf="all" class="on">minden szavazat</button>' + "".join(f'<button data-posf="{p}">{esc(POSITION_LABEL[p])}</button>' for p in POSITION_ORDER if (view.get("position_counts") or {}).get(p))
    return (f'<section class="dir"><div class="card"><h2>Név szerinti lista — {len(rows)} képviselő</h2>'
            f'<div class="filters">{fac_buttons}</div><div class="filters">{pos_buttons}<span class="n" id="rn"></span></div>'
            '<div class="tablewrap"><table id="roll"><thead><tr><th class="sortable" data-key="name">Képviselő</th><th class="sortable" data-key="fac">Frakció</th>'
            '<th class="sortable" data-key="pos">Szavazat</th><th class="sortable" data-key="seat">Ülőhely</th></tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table></div>'
            '<div class="hero-meta" style="margin-top:8px">A fejlécre kattintva rendezhető. Ülőhely: szektor, sor, szék a képviselői adatlapról; a miniszteri pad a 0. szektor.</div></div></section>')


def footer_html(inp: dict) -> str:
    idx = inp["idx"]
    return f"""<footer>
  <h3>Forrás és módszer</h3>
  <p>Adatok: az Országgyűlés Web API-ja (parlament.hu, XML), személyes hozzáférési kóddal, minden lekérés naplózva; a személyek azonosítása a Wikidata P4966 azonosítóján keresztül (ami megegyezik az API <span class="mono">p_azon</span> értékével). Az oldal a <span class="mono">{esc(idx["derived_at"])}</span> időpontban levezetett adatokból épült, {idx["details_cached"]} szavazás név szerinti listájával.</p>
  <p>Amit az oldal nem állít: semmit a képviselők szavazási szokásairól, a frakciófegyelemről vagy a törvényalkotás általános tempójáról — ezek számok, nem megállapítások. A „jelenlévő” fogalmának jogi értelmezése (a jelenlét = igen+nem+tartózkodott+jelen, nem szavazott) még ellenőrzendő; a többségi szabályokat az API saját „Szavazási mód” mezője adja, a jogszabályi hivatkozások a kódban VERIFY jelöléssel szerepelnek.</p>
  <p>Színek: az oldal saját jelölései, nem a pártok arculata.</p>
</footer>"""


# -- pages ------------------------------------------------------------------------------------

def freshness_html(inp: dict) -> str:
    idx = inp["idx"]
    derived_at = datetime.fromisoformat(idx["derived_at"]).astimezone(BUDAPEST)
    last_sync = datetime.fromisoformat(idx["last_sync_at"]).astimezone(BUDAPEST) if idx.get("last_sync_at") else None
    newest = max((v["ts"] for v in idx["votes"]), default=None)
    newest_dt = datetime.strptime(newest, "%Y.%m.%d.%H:%M:%S").replace(tzinfo=BUDAPEST) if newest else None
    fr = assess(sitting_days=[_date.fromisoformat(d["date"]) for d in idx["sitting_days"] if d.get("date")],
                newest_vote_at=newest_dt, last_sync_at=last_sync, now=derived_at)
    return f'<div class="fresh"><span class="hu">{esc(fr.sentence_hu)}</span><span class="en">{esc(fr.sentence_en)}</span></div>'


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
    counts_html = "".join(f'<div class="card"><b class="mono">{c}</b><span>{esc(l)}</span><span style="display:block;margin-top:2px">{esc(s)}</span></div>' for c, l, s in counts_cards)
    mode_rows = "".join(f'<tr><td>{esc(k)}</td><td class="num mono">{v}</td></tr>' for k, v in fl["by_mode"].items())
    rules_present = sorted({(v["majority"] or {}).get("rule") for v in idx["votes"] if v.get("majority")}, key=lambda r: list(Rule).index(Rule(r)) if r else 99)
    rule_buttons = '<button data-rule="all" class="on">mind</button>' + "".join(f'<button data-rule="{r}">{esc(rule_short(r))}</button>' for r in rules_present) + '<button data-rule="jelenlet">jelenlét</button>'
    result_buttons = '<button data-result="all" class="on">minden eredmény</button><button data-result="Elfogadva">elfogadva</button><button data-result="Elutasítva">elutasítva</button>'
    slim = []
    for row in idx["votes"]:
        mj = row.get("majority") or None
        slim.append({"ts": row["ts"], "slug": row["slug"], "on_date": row["on_date"], "time": row["time"], "mode": row["mode"], "kind": row["kind"],
                     "igen": row["igen"], "nem": row["nem"], "tartozkodott": row["tartozkodott"], "result_raw": row["result_raw"], "remark": row.get("remark"),
                     "motions": [{"iromany": x["iromany"], "title": x["title"], "outcome": x["outcome"], "href": x.get("href")} for x in row["motions"]],
                     "majority": ({"rule": mj["rule"], "needed": mj["needed"], "base": mj["base"], "margin": mj["margin"],
                                   "agrees_with_source": mj["agrees_with_source"]} if mj else None)})
    votes_json = json.dumps(slim, ensure_ascii=False, separators=(",", ":"), sort_keys=True).replace("</", "<\\/")
    hero_date = hu_date(hero["on_date"])
    return page_head("karzat — az Országgyűlés szavazásai, ülőhelyenként",
                     "Az Országgyűlés szavazásai a 43. ciklusban: minden szavazás a saját szükséges többségével, egy szavazás ülőhelyenként kirajzolva. Forrás: parlament.hu Web API.") + f"""
<header>
  <h1>karzat <small>az Országgyűlés szavazásai, ülőhelyenként — a szükséges többséggel együtt</small></h1>
  <p class="lede">Első oldal. Egy szavazás kirajzolva, {fl["votes"]} szavazás listázva a 43. ciklus első {fl["sitting_days"]["count"]} ülésnapjáról — mindegyik a saját oldalán, ülőhelyenként — minden döntés mellett azzal a többségi szabállyal, amelyet az Országgyűlés maga jelöl meg. Forrás: parlament.hu Web API; a személyek azonosítása Wikidatával egyeztetve.</p>
  {freshness_html(inp)}
</header>

<section class="grid">
  <div class="card">
    <h2>Egy szavazás, ülőhelyenként · <a href="szavazas/{esc(hero["slug"])}.html">{esc(hero_date)} {esc(hero["ts"][11:16])}</a></h2>
    {title_html}{meta_html}
    {chart_block(hero, inp)}
  </div>
  <div class="card">
    <h2>Eredmény és a szükséges többség</h2>
    {verdict_block(hero, inp)}
  </div>
</section>

<section class="counts">{counts_html}</section>

<section class="grid" style="margin-top:20px">
  <div class="card">
    <h2>Szavazási módok — az API megjelölése szerint</h2>
    <div class="tablewrap" style="border:0"><table><thead><tr><th>Szavazási mód</th><th class="num">db</th></tr></thead><tbody>{mode_rows}</tbody></table></div>
    <div class="hero-meta" style="margin-top:8px">A "Listás" az egyszerű többségű gépi szavazás; a többi a szükséges többséget nevezi meg. Titkos szavazásnál nincs név szerinti lista.</div>
  </div>
  <div class="card">
    <h2>Egy törvény útja — T/71</h2>
    <div class="hero-title" style="font-size:16px">A Nemzetközi Büntetőbíróság Statútumából való kilépés visszavonása — 2026. évi XV. törvény</div>
    <div class="tl"><div><b>máj. 25.</b>benyújtva (kormány)</div><div><b>máj. 26.</b>kivételes eljárás elrendelve</div><div><b>máj. 27.</b>zárószavazás</div><div><b>máj. 28.</b>kihirdetve, Magyar Közlöny 60</div></div>
    <div class="hero-meta" style="margin-top:8px">3 nap a benyújtástól a kihirdetésig. Az adatok — állapot, kihirdetés száma, MK száma, tárgyalási mód — mind az iromány-adatlapból jönnek; közlönyt nem kell böngészni.</div>
  </div>
</section>

<section class="dir">
  <div class="card">
    <h2>Szavazások — {fl["votes"]} tétel, a legfrissebb elöl · az időpontra kattintva a szavazás oldala</h2>
    <div class="filters">{rule_buttons}</div>
    <div class="filters">{result_buttons}<input id="q" type="search" placeholder="keresés a tárgyban…"><span class="n" id="n"></span></div>
    <div class="tablewrap"><table><thead><tr><th>Időpont</th><th>Tárgy</th><th>Szükséges többség</th><th class="num">igen – nem – tart.</th><th class="num">különbség</th><th>Eredmény</th></tr></thead><tbody id="rows"></tbody></table></div>
    <div class="hero-meta" style="margin-top:8px">„Szükséges”: a szabály és a küszöb / az alap. A „?” jelzés azt jelenti, hogy a számított és a forrás szerinti eredmény eltér — ilyen jelenleg {len(idx["disagreements"])} van.</div>
  </div>
</section>
{footer_html(inp)}
</div>
<script>window.__KARZAT_VOTES__ = {votes_json};</script>
<script>{JS_INDEX}</script>
</body>
</html>
"""


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

    return page_head(f'{when} — {label} · karzat', f'Az Országgyűlés {when} szavazása ülőhelyenként, a szükséges többséggel: {label}') + f"""
<header>
  <p class="crumbs"><a href="../index.html">karzat</a> › szavazások › <span class="mono">{esc(view["on_date"])} {esc(view["ts"][11:16])}</span></p>
  <h1><a href="../index.html">karzat</a> <small>{esc(when)} · {esc(view["mode"])}</small></h1>
</header>
<section class="grid">
  <div class="card">
    <h2>A szavazás · {esc(when)}</h2>
    {title_html}{meta_html}
    {chart_block(view, inp)}
  </div>
  <div class="card">
    <h2>Eredmény és a szükséges többség</h2>
    {verdict_block(view, inp)}
  </div>
</section>
{roll_call_table(view, inp)}
<nav class="pager">{pager_link(prev_ts, True)}{pager_link(next_ts, False)}</nav>
{footer_html(inp)}
</div>
<script>{JS_VOTE}</script>
</body>
</html>
"""


# -- main -------------------------------------------------------------------------------------

HERO_TS = "2026.06.15.17:20:04"


def build() -> str:
    """The index page (the name tests and --check use)."""
    return build_index(load_inputs(), HERO_TS)


def build_all(out_dir: Path, index_only: bool = False) -> dict:
    inp = load_inputs()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(build_index(inp, HERO_TS), encoding="utf-8")
    n = 0
    if not index_only:
        vd = out_dir / "szavazas"
        vd.mkdir(parents=True, exist_ok=True)
        for ts in inp["order"]:
            (vd / f'{inp["by_ts"][ts]["slug"]}.html').write_text(build_vote_page(inp, ts), encoding="utf-8")
            n += 1
    return {"index": str(out_dir / "index.html"), "vote_pages": n}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="compare a fresh index build with site/index.html; exit 1 on difference")
    ap.add_argument("--index-only", action="store_true", help="do not (re)generate the per-vote pages")
    ap.add_argument("--out", type=Path, default=SITE_DIR)
    args = ap.parse_args(argv)
    if args.check:
        page = build()
        current = SITE.read_text(encoding="utf-8") if SITE.exists() else ""
        if current != page:
            print(f"{SITE} is stale: rebuild with `python3 -m scripts.build_site`", file=sys.stderr)
            return 1
        print(f"ok: {SITE} matches a fresh build ({len(page):,} bytes)")
        return 0
    res = build_all(args.out, index_only=args.index_only)
    print(f"written {res['index']} + {res['vote_pages']} vote pages under {args.out / 'szavazas'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
