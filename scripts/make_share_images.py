"""The picture a link shows when it is shared: 1200×630 PNGs for Facebook, Messenger, X and the rest.

    python3 -m scripts.make_share_images            # the site card + every vote of the current cycle
    python3 -m scripts.make_share_images --force    # redraw all, not only what changed

A link to ogykarzat.hu used to arrive on Facebook as a bare address — no picture, no title — and in Hungary
that is where most reading starts. Every page now names a picture (page_head's og:image): a vote of the
current cycle names its own, everything else the site card.

The vote card is the vote page's own chamber, the same SVG and the same stylesheet, drawn by a headless
browser — Facebook will not take an SVG, and redrawing the chamber in Pillow would be a second drawing of the
same thing that could drift from the first. Only the current cycle: it is what gets shared, and 468 cards
take under a minute where 160,000 would take a night. Each card is redrawn only when what it shows changes
(a content hash per card, kept in data/share_images.json).
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SITE = ROOT / "site"
MANIFEST = ROOT / "data" / "share_images.json"
W, H = 1200, 630
SITE_CARD = "assets/megosztas.png"

STYLE = """
html,body{width:1200px;height:630px;margin:0;overflow:hidden;background:var(--bg)}
.sc-card{position:relative;display:grid;grid-template-columns:600px 1fr;gap:36px;padding:52px 56px 0;height:630px;box-sizing:border-box}
.sc-k{display:flex;align-items:center;gap:14px;font:500 20px var(--mono);letter-spacing:.35em;color:var(--white)}
.sc-k i{display:inline-block;width:13px;height:13px;background:var(--white)}
.sc-meta{margin-top:30px;font:17px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--dim)}
.sc-t{margin:14px 0 0;font:400 33px/1.28 var(--sans);color:var(--white);text-wrap:balance;
   display:-webkit-box;-webkit-line-clamp:5;-webkit-box-orient:vertical;overflow:hidden}
.sc-res{margin-top:26px;display:flex;align-items:baseline;gap:22px}
.sc-res b{font:600 40px var(--sans)}.sc-res b.ok{color:var(--ok)}.sc-res b.no{color:var(--bad)}.sc-res b.mid{color:var(--text)}
.sc-res span{font:23px var(--mono);color:var(--text)}
.sc-need{margin-top:10px;font:18px var(--mono);color:var(--dim)}
.sc-right{display:flex;align-items:center}
.sc-right .chart{width:100%}.sc-right svg{width:100%;height:auto}
.sc-foot{position:absolute;left:56px;right:56px;bottom:34px;display:flex;justify-content:space-between;
      font:16px var(--mono);letter-spacing:.08em;color:var(--dim3)}
"""


def card_html(css: str, *, meta: str, title: str, result: tuple[str, str] | None, counts: str, need: str, chart: str) -> str:
    res = (f'<div class="sc-res"><b class="{result[0]}">{html.escape(result[1])}</b><span>{html.escape(counts)}</span></div>' if result else
           f'<div class="sc-res"><span>{html.escape(counts)}</span></div>')
    return (f'<!doctype html><html lang="hu"><head><meta charset="utf-8">'
            f'<style>{css}{STYLE}</style></head><body><div class="sc-card"><div class="sc-left">'
            f'<div class="sc-k"><i></i>KARZAT</div><div class="sc-meta">{html.escape(meta)}</div><div class="sc-t">{html.escape(title)}</div>'
            f'{res}<div class="sc-need">{html.escape(need)}</div></div>'
            f'<div class="sc-right"><div class="chart">{chart}</div></div>'
            f'<div class="sc-foot"><span>ogykarzat.hu</span><span>forrás: az Országgyűlés adatai</span></div></div></body></html>')


def svg_of(chart_block_html: str) -> str:
    m = re.search(r"<svg\b.*?</svg>", chart_block_html, re.S)
    return re.sub(r"<title>.*?</title>", "", m.group(0), flags=re.S) if m else ""   # tooltips are 100 kB of nothing in a picture


def cards() -> list[tuple[Path, str]]:
    """(output png, card html) for the site card and every current-cycle vote."""
    from scripts.build_site import (CURRENT_CYCLE, build_assets, chart_block, cycle_dir, hu_date, hu_num, load_inputs, pick_hero,
                                    rule_short, site_totals, vote_view)
    css = build_assets()["karzat.css"]               # inline: a page set from memory may not load a file:// sheet
    inp = load_inputs(CURRENT_CYCLE)
    out: list[tuple[Path, str]] = []
    tot = site_totals()
    hero = vote_view(inp, pick_hero(inp))
    out.append((SITE / SITE_CARD, card_html(
        css, meta=f"az Országgyűlés · 1990 óta · {len(tot['cycles'])} ciklus",
        title="Ki hogyan szavazott az Országgyűlésben — minden szavazás, név szerint",
        result=None, counts=f"{hu_num(tot['votes'])} szavazás · {hu_num(tot['people'])} képviselő",
        need="a saját szükséges többségével, frakciónként és ülésrend szerint", chart=svg_of(chart_block(hero, inp)))))
    for ts in inp["order"]:
        v = vote_view(inp, ts)
        mo = (v.get("motions") or [None])[0]
        title = (mo.get("title") if mo else None) or ("Jelenlét megállapítása" if v["kind"] == "jelenlet" else v.get("remark") or "Szavazás")
        meta = f'{hu_date(v["on_date"])} {v["time"]}' + (f' · {mo["iromany"]}' if mo and mo.get("iromany") else "")
        result = ("ok", "Elfogadva") if v.get("passed") is True else ("no", "Elutasítva") if v.get("passed") is False else ("mid", v.get("result_raw") or "")
        m = v.get("majority")
        need = f'{rule_short(m["rule"])} · kellett {m["needed"]} / {m["base"]}' if m else ""
        out.append((SITE / cycle_dir(CURRENT_CYCLE) / "szavazas" / f'{v["slug"]}.png', card_html(
            css, meta=meta, title=title, result=result, need=need,
            counts=f'{v["igen"]} igen · {v["nem"]} nem · {v["tartozkodott"]} tart.', chart=svg_of(chart_block(v, inp)))))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="redraw every card")
    args = ap.parse_args(argv)
    t0 = time.time()
    done = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() and not args.force else {}
    todo = []
    for png, page in cards():
        key, h = str(png.relative_to(SITE)), hashlib.sha256(page.encode("utf-8")).hexdigest()[:16]
        if done.get(key) != h or not png.exists():
            todo.append((png, page, key, h))
    if todo:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            from scripts.audit_a11y import chrome_path      # the Chrome the browser audit already drives
            browser = pw.chromium.launch(executable_path=chrome_path())
            tab = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            for png, page, key, h in todo:
                tab.set_content(page, wait_until="load")
                # Two frames before the picture: taken at once, the screenshot carried tiles of the PREVIOUS card —
                # the site card came out with a vote card's footer. A stale frame is a wrong picture, not a slow one.
                tab.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
                tab.wait_for_timeout(50)
                png.parent.mkdir(parents=True, exist_ok=True)
                tab.screenshot(path=str(png), type="png", clip={"x": 0, "y": 0, "width": W, "height": H})
                done[key] = h
            browser.close()
    MANIFEST.write_text(json.dumps(done, indent=0, sort_keys=True) + "\n", encoding="utf-8")
    print(f"share images: {len(todo)} drawn, {len(done) - len(todo)} unchanged, {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
