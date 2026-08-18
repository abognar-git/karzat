#!/usr/bin/env python3
"""Build site/index.html — the first page — from committed inputs only.

    python3 -m scripts.build_site            # write site/index.html
    python3 -m scripts.build_site --check    # exit 1 if the committed page differs from a fresh build

Inputs (all committed, all produced by scripts/derive_first_light.py from the cache):
  data/derived/hero_vote.json    one vote in full — the seat chart and the verdict card
  data/derived/votes_index.json  every listed vote — the directory table, sitting days, sync time
  data/derived/first_light.json  the headline counts
  config/factions.yml            faction colours and left→right order

The build is deterministic: no clock is read (the freshness sentence is computed at the
derive timestamp stored in the inputs), so `--check` can guard the committed page the way
trigger-discipline guards its index.html. Everything is inline — no CDN, no fonts fetched,
one file that works from disk or from Pages.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karzat.freshness import BUDAPEST, assess  # noqa: E402
from karzat.majority import RULES, Rule  # noqa: E402

DERIVED = ROOT / "data" / "derived"
SEATING = DERIVED / "seating.json"
SITE = ROOT / "site" / "index.html"
FACTIONS = ROOT / "config" / "factions.yml"

POSITION_ORDER = ["igen", "tartozkodott", "jelen_nem_szavazott", "nem", "nem_szavazott", "bejelentett_hianyzo"]
POSITION_LABEL = {"igen": "igen", "nem": "nem", "tartozkodott": "tartózkodott", "jelen_nem_szavazott": "jelen, nem szavazott",
                  "nem_szavazott": "nem szavazott", "bejelentett_hianyzo": "előre bejelentett hiányzó"}


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


# -- seat chart ------------------------------------------------------------------------------

def hemicycle_layout(n: int, rows: int = 7, r0: float = 78.0, gap: float = 17.0) -> list[dict]:
    """Seats on concentric semicircular rows, seats per row ∝ radius, ordered left→right then inner→outer.
    Same idea as Konzol's layout (documented in reference/konzol/), tuned for 199 seats."""
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


def seat_svg_real(hero: dict, facs: list[dict], plan: dict) -> tuple[str, dict]:
    """The reconstructed chamber: every voting MP at their (sector,row,seat) coordinates.
    Returns (svg, info) where info counts what could not be placed."""
    colour = {f["id"]: f["colour"] for f in facs}
    coords = plan["coords"]; geo = plan["geometry"]
    parts, unplaced = [], []
    for m in hero["positions"]:
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
    # unplaced (no seat / unresolved): a small row at the bottom left, so nothing silently disappears
    for i, m in enumerate(unplaced):
        c = colour.get(m["faction"] or "", "#8a8a8a")
        why = "azonosítatlan név" if not m.get("mp_azon") else "nincs ülőhely a képviselői adatlapon (a mandátum azóta megszűnt)"
        parts.append(f'<g class="seat" data-pos="{m["position"]}"><title>{html.escape(m["name"])} ({html.escape(m["faction"] or "")}) — {POSITION_LABEL.get(m["position"], m["position"])} · {why}</title>{_node(-150 + i * 12, 12, m["position"], c)}</g>')
    # chrome: platform, front-bench arc, sector labels at the outer edge, aisles
    R = geo["outer_radius"] + 14
    labels = []
    for sec, (a0, a1) in geo["sector_wedges_rad"].items():
        mid = (a0 + a1) / 2
        labels.append(f'<text x="{R*math.cos(mid):.1f}" y="{-R*math.sin(mid):.1f}" class="seclabel" text-anchor="middle" dominant-baseline="middle">{sec}</text>')
        for a in (a0, a1):   # aisle ticks
            r_in, r_out = geo["r0"] - 8, geo["outer_radius"] + 6
            labels.append(f'<line x1="{r_in*math.cos(a):.1f}" y1="{-r_in*math.sin(a):.1f}" x2="{r_out*math.cos(a):.1f}" y2="{-r_out*math.sin(a):.1f}" class="aisle"/>')
    fb = geo["front_bench_radius"]; fs = geo["front_bench_span_rad"]
    a0, a1 = math.pi / 2 + fs / 2, math.pi / 2 - fs / 2
    arc = (f'<path d="M {fb*math.cos(a0):.1f} {-fb*math.sin(a0):.1f} A {fb} {fb} 0 0 1 {fb*math.cos(a1):.1f} {-fb*math.sin(a1):.1f}" class="fbarc"/>'
           f'<text x="0" y="{-fb+20:.1f}" class="seclabel" text-anchor="middle">miniszteri pad</text>')
    svg = (f'<svg viewBox="-172 -{R+12:.0f} 344 {R+30:.0f}" role="img" aria-label="Az ülésterem rekonstruált ülésrendje: {len(hero["positions"])} képviselő, frakció és szavazat szerint">'
           f'<rect x="-16" y="-7" width="32" height="8" rx="1.5" class="rostrum"/><text x="0" y="12" class="seclabel" text-anchor="middle">elnöki emelvény</text>'
           + arc + "".join(labels) + "".join(parts) + '</svg>')
    return svg, {"placed": len(hero["positions"]) - len(unplaced), "unplaced": len(unplaced), "seated_total": plan["seated"]}


def seat_svg(hero: dict, facs: list[dict]) -> str:
    order = {f["id"]: f["sort_order"] for f in facs}
    colour = {f["id"]: f["colour"] for f in facs}
    pos_rank = {p: i for i, p in enumerate(POSITION_ORDER)}
    members = sorted(hero["positions"], key=lambda p: (order.get(p["faction"] or "", 999), pos_rank.get(p["position"], 9), p["name"]))
    seats = hemicycle_layout(len(members))
    parts = []
    for seat, m in zip(seats, members):
        c = colour.get(m["faction"] or "", "#8a8a8a")
        pos = m["position"]
        title = html.escape(f'{m["name"]} ({m["faction"]}) — {POSITION_LABEL.get(pos, pos)}')
        cx, cy = seat["cx"], seat["cy"]
        if pos == "igen":
            body = f'<circle cx="{cx}" cy="{cy}" r="5.2" fill="{c}"/>'
        elif pos == "nem":
            body = f'<circle cx="{cx}" cy="{cy}" r="4.6" fill="none" stroke="{c}" stroke-width="1.6"/>'
        elif pos == "tartozkodott":
            body = (f'<circle cx="{cx}" cy="{cy}" r="4.6" fill="none" stroke="{c}" stroke-width="1.6"/>'
                    f'<path d="M {cx-4.6} {cy} A 4.6 4.6 0 0 1 {cx+4.6} {cy} Z" fill="{c}"/>')
        elif pos == "jelen_nem_szavazott":
            body = f'<circle cx="{cx}" cy="{cy}" r="4.6" fill="none" stroke="{c}" stroke-width="1.6" stroke-dasharray="2.4 2"/>'
        elif pos == "nem_szavazott":
            body = f'<circle cx="{cx}" cy="{cy}" r="4.6" fill="{c}" fill-opacity="0.22" stroke="{c}" stroke-opacity="0.35" stroke-width="1"/>'
        else:  # bejelentett_hianyzo
            body = f'<circle cx="{cx}" cy="{cy}" r="2.2" fill="{c}" fill-opacity="0.35"/>'
        parts.append(f'<g class="seat" data-pos="{pos}" data-f="{html.escape(m["faction"] or "")}"><title>{title}</title>{body}</g>')
    return ('<svg viewBox="-200 -200 400 214" role="img" aria-label="Ülésrend-diagram: 199 képviselő, frakció és szavazat szerint">'
            '<rect x="-14" y="-6" width="28" height="7" rx="1.5" class="rostrum"/>' + "".join(parts) + '</svg>')


# -- helpers ----------------------------------------------------------------------------------

def hu_date(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{y}. {['január','február','március','április','május','június','július','augusztus','szeptember','október','november','december'][int(m)-1]} {int(d)}."


def rule_short(rule: str | None) -> str:
    return {"egyszeru": "egyszerű", "abszolut": "abszolút (összes ½)", "ketharmad_jelenlevo": "⅔ jelenlévők",
            "ketharmad_osszes": "⅔ összes", "negyotod_jelenlevo": "⅘ jelenlévők", "negyotod_osszes": "⅘ összes",
            "relativ": "relatív"}.get(rule or "", "—")


def esc(s) -> str:
    return html.escape("" if s is None else str(s))


# -- page --------------------------------------------------------------------------------------

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1a2230;--muted:#5b6675;--line:#dde2e9;--accent:#2563eb;--ok:#15803d;--okbg:#e8f7ee;--warn:#b91c1c;--warnbg:#fde8e8;--chip:#eef1f5;--rostrum:#c9d0da}
@media (prefers-color-scheme:dark){:root{--bg:#10141c;--card:#161b26;--ink:#e6ebf2;--muted:#93a0b4;--line:#242c3a;--accent:#5b8def;--ok:#4ade80;--okbg:#12281c;--warn:#fca5a5;--warnbg:#2a1416;--chip:#1c2330;--rostrum:#2c3545}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 72px}
header h1{margin:0;font-size:34px;font-weight:600;letter-spacing:-.02em}header h1 small{font-weight:400;color:var(--muted);font-size:16px;margin-left:10px}
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
tr:last-child td{border-bottom:0}
td.num{text-align:right;white-space:nowrap}td.ts{white-space:nowrap;color:var(--muted)}
td .sub{color:var(--muted);font-size:12px;display:block}
.tl{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:8px;font-size:12.5px}.tl div{border-left:2px solid var(--accent);padding-left:8px}.tl b{display:block}
footer{margin-top:36px;color:var(--muted);font-size:12.5px;line-height:1.6}
footer h3{font-size:12px;text-transform:uppercase;letter-spacing:.08em;margin:18px 0 6px;color:var(--muted)}
"""

JS = """
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
      var need = m ? (short(m.rule) + ' <span class="sub mono">' + m.needed + ' / ' + m.base + (m.present_basis && m.present_basis.indexOf('list') === 0 ? ' · lista' : '') + '</span>') : (v.kind === 'jelenlet' ? '—' : '—');
      var res = v.result_raw === 'Elfogadva' ? '<span class="badge ok">Elfogadva</span>' : v.result_raw === 'Elutasítva' ? '<span class="badge no">Elutasítva</span>' : '<span class="badge mid">' + esc(v.result_raw) + '</span>';
      var margin = m && m.margin != null ? (m.margin >= 0 ? '+' : '') + m.margin : '';
      var flag = m && m.agrees_with_source === false ? ' <span class="badge no" title="a számított és a forrás szerinti eredmény eltér">?</span>' : '';
      out.push('<tr><td class="ts mono">' + esc(v.on_date) + ' ' + esc(v.time) + '</td><td>' + subj + '</td><td>' + need + '</td>' +
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


def build() -> str:
    hero = json.loads((DERIVED / "hero_vote.json").read_text(encoding="utf-8"))
    idx = json.loads((DERIVED / "votes_index.json").read_text(encoding="utf-8"))
    plan = json.loads(SEATING.read_text(encoding="utf-8")) if SEATING.exists() else None
    fl = json.loads((DERIVED / "first_light.json").read_text(encoding="utf-8"))
    facs = factions()
    fac_by_id = {f["id"]: f for f in facs}

    # freshness at the derive timestamp (deterministic)
    derived_at = datetime.fromisoformat(idx["derived_at"]).astimezone(BUDAPEST)
    last_sync = datetime.fromisoformat(idx["last_sync_at"]).astimezone(BUDAPEST) if idx.get("last_sync_at") else None
    newest = max((v["ts"] for v in idx["votes"]), default=None)
    newest_dt = datetime.strptime(newest, "%Y.%m.%d.%H:%M:%S").replace(tzinfo=BUDAPEST) if newest else None
    from datetime import date as _date
    fr = assess(sitting_days=[_date.fromisoformat(d["date"]) for d in idx["sitting_days"] if d.get("date")],
                newest_vote_at=newest_dt, last_sync_at=last_sync, now=derived_at)

    if plan:
        chart_svg, info = seat_svg_real(hero, facs, plan)
        geo = plan["geometry"]
        chart_note = (f'Az ülésrend a képviselői adatlapok szektor/sor/szék adataiból rekonstruálva ({info["placed"]} képviselő a helyén'
                      + (f', {info["unplaced"]} balra lent, ülőhely-adat nélkül' if info["unplaced"] else '') + '), az elnöki emelvény felől nézve: '
                      f'balra az ellenzék ({", ".join(str(x) for x in geo["sector_order_left_to_right"][:2])}. szektor), jobbra a kormányoldal; '
                      'a 0. szektor a miniszteri pad. Becsült: a szektorok szélessége (a legszélesebb sorral arányos), a sorok távolsága és a székek balról jobbra sorrendje.')
    else:
        chart_svg = seat_svg(hero, facs)
        chart_note = "Sorrend: frakció (a beállított bal–jobb sorrendben), azon belül szavazat. A helyek nem a valódi ülésrend."
    m = hero["majority"] or {}
    mo = hero["motions"][0] if hero["motions"] else {}
    rule_info = RULES[Rule(m["rule"])] if m else None
    counts = hero["position_counts"]
    total_pos = sum(counts.values()) or 1

    # faction stacks (igen / tart / nem / other)
    fb = []
    for t in hero["faction_tallies"]:
        f = fac_by_id.get(t["faction"], {"colour": "#8a8a8a", "name": t["faction"]})
        n = t["osszesen"] or 1
        seg = lambda k, op: f'<i style="width:{100*(t[k] or 0)/n:.1f}%;background:{f["colour"]};opacity:{op}" title="{esc(t["faction"])} {POSITION_LABEL.get(k,k)}: {t[k]}"></i>'
        rest = n - (t["igen"] or 0) - (t["tartozkodott"] or 0) - (t["nem"] or 0)
        fb.append(f'<div class="row"><span>{esc(t["faction"])}</span><span class="stack">{seg("igen",1)}{seg("tartozkodott",.55)}{seg("nem",.25)}'
                  f'<i style="width:{100*rest/n:.1f}%;background:transparent" title="{esc(t["faction"])} nem szavazott / hiányzó: {rest}"></i></span>'
                  f'<span class="mono" style="text-align:right">{t["igen"]} – {t["nem"]} – {t["tartozkodott"]}</span></div>')

    legend_f = "".join(f'<span class="f"><i style="background:{f["colour"]}"></i>{esc(f["id"])} <span class="mono">{esc(hero["faction_tallies"][i]["osszesen"] if i < len(hero["faction_tallies"]) else "")}</span></span>'
                       for i, f in enumerate([fac_by_id.get(t["faction"], {"colour":"#8a8a8a","id":t["faction"]}) for t in hero["faction_tallies"]]))
    legend_p = ('<span class="f"><svg width="14" height="14" viewBox="-7 -7 14 14"><circle r="5" fill="currentColor"/></svg>igen</span>'
                '<span class="f"><svg width="14" height="14" viewBox="-7 -7 14 14"><circle r="4.6" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M-4.6 0 A4.6 4.6 0 0 1 4.6 0Z" fill="currentColor"/></svg>tartózkodott</span>'
                '<span class="f"><svg width="14" height="14" viewBox="-7 -7 14 14"><circle r="4.6" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>nem</span>'
                '<span class="f"><svg width="14" height="14" viewBox="-7 -7 14 14"><circle r="4.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.4 2"/></svg>jelen, nem szavazott</span>'
                '<span class="f"><svg width="14" height="14" viewBox="-7 -7 14 14"><circle r="4.6" fill="currentColor" fill-opacity=".22" stroke="currentColor" stroke-opacity=".35"/></svg>nem szavazott</span>'
                '<span class="f"><svg width="14" height="14" viewBox="-7 -7 14 14"><circle r="2.2" fill="currentColor" fill-opacity=".35"/></svg>előre bejelentett hiányzó</span>')

    counts_cards = [
        (fl["votes"], "szavazás", f'{hu_date(fl["window"]["from"])} – {hu_date(fl["window"]["to"])}'),
        (fl["sitting_days"]["count"], "ülésnap", " · ".join(f'{k}: {v}' for k, v in fl["sitting_days"]["by_session"].items())),
        (fl["qualified_majority_votes"], "minősített többségű szavazás", f'{fl["decisions"]} döntésből'),
        (fl["kiveteles_votes"], "kivételes eljárás elrendelése", f'{fl["surgos_votes"]} sürgős tárgyalás'),
        (fl["hazszabalytol_elteres_votes"], "Házszabálytól eltérés", f'{fl["interpellation_answers"]} interpellációs válasz elfogadva'),
        (fl["mps"]["total"], "képviselő", " · ".join(f'{k}: {v}' for k, v in fl["mps"]["by_faction"].items())),
    ]
    counts_html = "".join(f'<div class="card"><b class="mono">{c}</b><span>{esc(l)}</span><span style="display:block;margin-top:2px">{esc(s)}</span></div>' for c, l, s in counts_cards)

    modes = fl["by_mode"]
    mode_rows = "".join(f'<tr><td>{esc(k)}</td><td class="num mono">{v}</td></tr>' for k, v in modes.items())

    rules_present = sorted({(v["majority"] or {}).get("rule") for v in idx["votes"] if v.get("majority")}, key=lambda r: list(Rule).index(Rule(r)) if r else 99)
    rule_buttons = '<button data-rule="all" class="on">mind</button>' + "".join(f'<button data-rule="{r}">{esc(rule_short(r))}</button>' for r in rules_present) + '<button data-rule="jelenlet">jelenlét</button>'
    result_buttons = '<button data-result="all" class="on">minden eredmény</button><button data-result="Elfogadva">elfogadva</button><button data-result="Elutasítva">elutasítva</button>'

    slim = []
    for row in idx["votes"]:
        mj = row.get("majority") or None
        slim.append({"ts": row["ts"], "on_date": row["on_date"], "time": row["time"], "mode": row["mode"], "kind": row["kind"],
                     "igen": row["igen"], "nem": row["nem"], "tartozkodott": row["tartozkodott"], "result_raw": row["result_raw"],
                     "remark": row.get("remark"),
                     "motions": [{"iromany": x["iromany"], "title": x["title"], "outcome": x["outcome"], "href": x.get("href")} for x in row["motions"]],
                     "majority": ({"rule": mj["rule"], "needed": mj["needed"], "base": mj["base"], "margin": mj["margin"],
                                   "agrees_with_source": mj["agrees_with_source"], "present_basis": mj.get("present_basis")} if mj else None)})
    votes_json = json.dumps(slim, ensure_ascii=False, separators=(",", ":"), sort_keys=True).replace("</", "<\\/")

    fresh_hu, fresh_en = fr.sentence_hu, fr.sentence_en
    hero_date = hu_date(hero["on_date"])
    verdict_ok = m.get("agrees_with_source")
    passed_badge = '<span class="badge ok">Elfogadva</span>' if hero["passed"] else '<span class="badge no">Elutasítva</span>'
    share = round(100 * hero["igen"] / m["base"], 1) if m else 0
    need_pct = round(100 * m["needed"] / m["base"], 1) if m else 50

    return f"""<!doctype html>
<html lang="hu">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>karzat — az Országgyűlés szavazásai, ülőhelyenként</title>
<meta name="description" content="Az Országgyűlés szavazásai a 43. ciklusban: minden szavazás a saját szükséges többségével, egy szavazás ülőhelyenként kirajzolva. Forrás: parlament.hu Web API.">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>karzat <small>az Országgyűlés szavazásai, ülőhelyenként — a szükséges többséggel együtt</small></h1>
  <p class="lede">Első oldal. Egy szavazás kirajzolva, {fl["votes"]} szavazás listázva a 43. ciklus első {fl["sitting_days"]["count"]} ülésnapjáról, minden döntés mellett azzal a többségi szabállyal, amelyet az Országgyűlés maga jelöl meg. Forrás: parlament.hu Web API; a személyek azonosítása Wikidatával egyeztetve.</p>
  <div class="fresh"><span class="hu">{esc(fresh_hu)}</span><span class="en">{esc(fresh_en)}</span></div>
</header>

<section class="grid">
  <div class="card">
    <h2>Egy szavazás, ülőhelyenként · {esc(hero_date)} {esc(hero["ts"][11:16])}</h2>
    <div class="hero-title">{('<a href="' + esc(mo.get("href")) + '" target="_blank" rel="noopener">' + esc(mo.get("iromany")) + '</a> ' if mo.get("href") else esc(mo.get("iromany", "")) + " ")}{esc(mo.get("title", ""))}</div>
    <div class="hero-meta">{esc(mo.get("outcome", ""))} · benyújtó: {esc(", ".join(mo.get("submitters", [])))}</div>
    <div class="chart">{chart_svg}</div>
    <div class="legend">{legend_f}</div>
    <div class="legend">{legend_p}</div>
    <div class="hero-meta" style="margin-top:8px">{chart_note}</div>
  </div>
  <div class="card">
    <h2>Eredmény és a szükséges többség</h2>
    <div class="tally"><div><b class="mono">{hero["igen"]}</b><span>igen</span></div><div><b class="mono">{hero["nem"]}</b><span>nem</span></div><div><b class="mono">{hero["tartozkodott"]}</b><span>tartózkodott</span></div></div>
    <div class="bar"><i style="width:{share}%"></i><em style="left:{need_pct}%"></em></div>
    <div class="hero-meta mono">{hero["igen"]} igen · szükséges {m.get("needed")} a {m.get("base")}-ból ({esc(rule_info.label_hu if rule_info else "")}) · különbség {("+" if (m.get("margin") or 0) >= 0 else "") + str(m.get("margin"))}</div>
    <dl class="verdict">
      <dt>Szavazási mód</dt><dd>{esc(hero["mode"])} <span class="hero-meta">(az API saját megjelölése)</span></dd>
      <dt>Szabály</dt><dd>{esc(rule_info.label_hu if rule_info else "—")} — {esc(rule_info.formula if rule_info else "")}</dd>
      <dt>Alap</dt><dd class="mono">{m.get("base")} {("képviselő" if rule_info and rule_info.base == "seats" else "jelenlévő")}{" · jelenlét = igen+nem+tartózkodott+jelen, nem szavazott" if rule_info and rule_info.base == "present" else ""}</dd>
      <dt>Eredmény</dt><dd>{passed_badge} <span class="hero-meta">forrás szerint · számítás {"egyezik" if verdict_ok else "eltér"}</span></dd>
      <dt>Nem szavazott</dt><dd class="mono">{counts.get("nem_szavazott", 0)} · jelen, nem szavazott {counts.get("jelen_nem_szavazott", 0)} · előre bejelentett hiányzó {counts.get("bejelentett_hianyzo", 0)}</dd>
    </dl>
    <div class="fbars">{"".join(fb)}</div>
    <div class="hero-meta" style="margin-top:10px">Frakciónként: igen (teli), tartózkodott (fél), nem (halvány), a maradék nem szavazott vagy hiányzott. Számok: igen – nem – tartózkodott.</div>
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
    <h2>Szavazások — {fl["votes"]} tétel, a legfrissebb elöl</h2>
    <div class="filters">{rule_buttons}</div>
    <div class="filters">{result_buttons}<input id="q" type="search" placeholder="keresés a tárgyban…"><span class="n" id="n"></span></div>
    <div class="tablewrap"><table><thead><tr><th>Időpont</th><th>Tárgy</th><th>Szükséges többség</th><th class="num">igen – nem – tart.</th><th class="num">különbség</th><th>Eredmény</th></tr></thead><tbody id="rows"></tbody></table></div>
    <div class="hero-meta" style="margin-top:8px">„Szükséges”: a szabály és a küszöb / az alap. Ahol a név szerinti lista még nincs letöltve, az alap az „Összes szavazat” (lista); ahol megvan, a jelenlévők száma a névsorból. A „?” jelzés azt jelenti, hogy a számított és a forrás szerinti eredmény eltér — ilyen jelenleg {len(idx["disagreements"])} van.</div>
  </div>
</section>

<footer>
  <h3>Forrás és módszer</h3>
  <p>Adatok: az Országgyűlés Web API-ja (parlament.hu, XML), személyes hozzáférési kóddal, minden lekérés naplózva; a személyek azonosítása a Wikidata P4966 azonosítóján keresztül (ami megegyezik az API <span class="mono">p_azon</span> értékével). Ez az oldal a <span class="mono">{esc(idx["derived_at"])}</span> időpontban levezetett adatokból épült, {idx["details_cached"]} szavazás név szerinti listájával; a többi tétel a listás összesítésből.</p>
  <p>Amit az oldal nem állít: semmit a képviselők szavazási szokásairól, a frakciófegyelemről vagy a törvényalkotás általános tempójáról — ezek számok, nem megállapítások. A „jelenlévő” fogalmának jogi értelmezése (a jelenlét = igen+nem+tartózkodott+jelen, nem szavazott) még ellenőrzendő; a többségi szabályokat az API saját „Szavazási mód” mezője adja, a jogszabályi hivatkozások a kódban VERIFY jelöléssel szerepelnek.</p>
  <p>Színek: az oldal saját jelölései, nem a pártok arculata.</p>
</footer>
</div>
<script>window.__KARZAT_VOTES__ = {votes_json};</script>
<script>{JS}</script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="compare a fresh build with site/index.html; exit 1 on difference")
    ap.add_argument("--out", type=Path, default=SITE)
    args = ap.parse_args(argv)
    page = build()
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != page:
            print(f"{args.out} is stale: rebuild with `python3 -m scripts.build_site`", file=sys.stderr)
            return 1
        print(f"ok: {args.out} matches a fresh build ({len(page):,} bytes)")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8")
    print(f"written {args.out} ({len(page):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
