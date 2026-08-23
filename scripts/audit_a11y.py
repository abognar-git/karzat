#!/usr/bin/env python3
"""Run axe-core over one page of every kind the site builds, in a real browser, and weigh what each costs.

    python3 -m scripts.audit_a11y                  # desktop and mobile, print the verdict
    python3 -m scripts.audit_a11y --viewport mobile
    python3 -m scripts.audit_a11y --kind floor,vote
    python3 -m scripts.audit_a11y --json out.json  # keep the full report

The site is 241,635 files. Nobody audits that, and nobody needs to: it is 31 templates with the record poured
through them, so one page of each kind covers every line of markup the builder can emit. The registry below
is that sample, and a template with no entry is a template nobody is checking — `tests/test_a11y.py` fails
when a `build_*_page` function appears with no page kind naming it, because the quiet way for this gate to
stop working is for the site to grow past it.

**What is a failure.** Any axe violation of `critical` or `serious` impact, on any sampled page, in either
viewport. `moderate` and `minor` are printed and do not stop the build — not because they do not matter, but
because a gate that fails on everything gets switched off, and these are the two levels where axe's
best-practice rules disagree with reasonable markup often enough to be argued with. They are counted, and the
count is in the README, so letting them drift is a visible act rather than a silent one.

**What is not measured.** axe finds perhaps a third to a half of what a person with a screen reader would
find in ten minutes. It cannot tell whether an alt text is *true*, whether a heading describes what follows,
or whether a table makes sense read aloud. It will not catch a page that is technically perfect and
incomprehensible. What it does catch is the whole class of defect that arrives by accident during a
refactor — the unlabelled control, the contrast that drifted, the heading level skipped — and that is the
class a gate is for.

**Weight.** The performance half comes from the same browser session rather than a second tool, because for
a site with no framework, no analytics and no third-party anything, the only numbers that mean something are
the bytes over the wire, the request count, the DOM size, and whether anything blocks the first paint. A
composite score would average those into one figure that hides which of them moved. The budgets are per page
kind and deliberately loose — they exist to catch a page that doubles, not to shave kilobytes.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
AXE = ROOT / "reference" / "axe" / "axe.min.js"
DRIVER = ROOT / "scripts" / "audit_pages.js"

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
)

VIEWPORTS = {
    "desktop": {"width": 1280, "height": 900, "mobile": False},
    # 390×844 is an iPhone 14; the layout bugs this project has shipped were all found at this width, because
    # it is the one where a four-column grid has to decide what to do.
    "mobile": {"width": 390, "height": 844, "mobile": True},
}

# kind -> a path under site/, or a callable that finds one. The builder emits 31 templates; every one of them
# is named here, and a new build_*_page with no entry fails the test rather than going unaudited.
def _first(rel: str, prefix: str = "", skip: tuple[str, ...] = ("index.html",)):
    def find() -> str | None:
        d = SITE / rel
        if not d.is_dir():
            return None
        for f in sorted(os.listdir(d)):
            if f.endswith(".html") and f not in skip and f.startswith(prefix):
                return f"{rel}/{f}"
        return None
    return find


PAGE_KINDS: dict[str, object] = {
    "landing": "index.html",
    "404": "404.html",
    "cycle index": "ckl43/index.html",
    "vote": _first("ckl43/szavazas"),
    "mp": _first("ckl43/kepviselo"),
    "mp index": "ckl43/kepviselo/index.html",
    "bill": _first("ckl43/iromany"),
    "bill index": "ckl43/iromany/index.html",
    "speech": _first("ckl43/felszolalas", skip=("index.html", "kereses.html")),
    "day": _first("ckl43/felszolalas", prefix="nap"),
    "speeches": "ckl43/felszolalas/index.html",
    "speech search": "ckl43/felszolalas/kereses.html",
    "floor": "ckl43/beszedido/index.html",
    "floor year": "ckl43/beszedido/2026.html",
    "cohesion": "ckl43/kohezio/index.html",
    "close votes": "ckl43/szoros/index.html",
    "numbers": "ckl43/szamok/index.html",
    "data": "ckl43/adatok/index.html",
    "committee": _first("ckl43/bizottsag"),
    "committee index": "ckl43/bizottsag/index.html",
    "feed": "ckl43/feed/index.html",
    "kepviselom": "ckl43/kepviselom/index.html",
    "echo": "ckl42/visszhang/index.html",
    "report": "riport/index.html",
    "search": "kereses/index.html",
    "method": "modszer/index.html",
    "profile": "arcel/index.html",
    "coverage": "lefedettseg/index.html",
    "person": _first("szemely"),
    "person index": "szemely/index.html",
    "spokesperson": "szemely/004O.html",
    "government member": "szemely/v076.html",
    "switches": "frakciovaltas/index.html",
    # The builders branch on inp["closed"] and inp["archive"], and 14 of them render differently on the far
    # side of that branch — which no cycle-43 page ever reaches. Two serious violations lived there: the
    # fallback chamber kept the role="img" that the current-cycle chart had already had fixed, and a
    # scrolling faction box in the closed-cycle branch sat three lines below a .tablewrap that did get its
    # tabindex. "One page of each kind covers every line of markup the builder can emit" was not true.
    "cycle index (closed)": "ckl42/index.html",
    "cycle index (archive)": "ckl39/index.html",
    "cycle index (biggest)": "ckl34/index.html",   # 21,674 votes; weighed, not rule-checked
    "vote (closed)": _first("ckl42/szavazas"),
    "mp (archive)": _first("ckl39/kepviselo"),
}

# kind -> (max transferred bytes, max DOM nodes). Loose on purpose: these catch a page that doubles, not a
# page that grew. The heavy ones are heavy for a reason the page states — a roll call is 199 names, the
# person index is every human the record has ever named — and the budget records that rather than hiding it.

# kind -> the builder it samples. Named rather than matched: "spokesperson" contains "person", so a
# substring test quietly reported that build_spokesperson_page was covered by the MP career page, and let
# build_kormany_page — a template with its own layout — go unsampled altogether.
COVERS: dict[str, str] = {
    "landing": "build_landing", "404": "build_404", "cycle index": "build_index",
    "vote": "build_vote_page", "mp": "build_mp_page", "mp index": "build_mp_index",
    "bill": "build_bill_page", "bill index": "build_bill_index",
    "speech": "build_speech_page", "day": "build_day_page", "speeches": "build_speeches_page",
    "speech search": "build_speech_search_page",
    "floor": "build_floor_page", "floor year": "build_floor_year_page",
    "cohesion": "build_cohesion_page", "close votes": "build_close_page",
    "numbers": "build_numbers_page", "data": "build_data_page",
    "committee": "build_committee_page", "committee index": "build_committee_index",
    "feed": "build_feed_page", "kepviselom": "build_kepviselom_page", "echo": "build_echo_page",
    "report": "build_report_page", "search": "build_search_page", "method": "build_method_page",
    "profile": "build_profile_page", "coverage": "build_coverage_page",
    "person": "build_person_page", "person index": "build_person_index",
    "spokesperson": "build_spokesperson_page", "government member": "build_kormany_page",
    "switches": "build_switch_page",
    "cycle index (closed)": "build_index", "cycle index (archive)": "build_index",
    "cycle index (biggest)": "build_index",
    "vote (closed)": "build_vote_page", "mp (archive)": "build_mp_page",
}

# Sampled for weight but not for rules: the same template as a kind already checked, only much bigger, where
# axe would spend three minutes to repeat a verdict it has already given.
WEIGHT_ONLY = {"cycle index (biggest)"}

# Every kind not listed below measured under 78 kB and 790 elements. 1.4 MB of headroom on that was
# decoration; this is about 2.5x, which is a real ceiling and still not a nuisance.
BUDGET_DEFAULT = (200_000, 3_000)
BUDGETS: dict[str, tuple[int, int]] = {
    # Measured over the wire as CloudFront serves it — gzipped — and set at roughly 1.6x what each page
    # weighs today. Loose enough that ordinary growth is not an event, tight enough that a page which
    # doubles is. The uncompressed figures are two to ten times these and are what a reader never receives.
    "cycle index (archive)": (1_800_000, 200_000),   # ckl39: every vote of a four-year parliament, one page
    "cycle index (biggest)": (2_400_000, 360_000),   # ckl34, 21,674 votes — weighed only, see WEIGHT_ONLY
    "cycle index (closed)": (700_000, 55_000),
    "person index": (300_000, 27_000),               # every human the record has ever named
    "echo": (260_000, 12_000),
    "cycle index": (260_000, 9_000),
    "bill index": (200_000, 9_000),
    "mp": (220_000, 8_000),
    "mp index": (420_000, 6_000),                    # 50 portraits, and they are 3 kB each
    "kepviselom": (480_000, 6_000),
    "vote": (240_000, 7_000), "vote (closed)": (240_000, 7_000),
    "floor year": (200_000, 9_000),
    "switches": (160_000, 7_000),
    "coverage": (160_000, 5_000),
    "landing": (220_000, 5_000),                     # the chamber, every seat drawn
    "mp (archive)": (180_000, 4_000),
    "close votes": (140_000, 4_000),
}

# Rules whose impact axe calls moderate but whose absence is structural rather than arguable: a page with no
# <main>, no <h1>, or content outside every landmark is not a matter of taste. Deleting <main> produced two
# soft lines for 83 elements outside any region and shipped.
ESCALATE = {"landmark-one-main", "page-has-heading-one", "region"}

# colour-contrast judges hundreds of nodes on a real page; the observed minimum across 33 kinds is in the
# dozens. A floor of 20 is three orders of magnitude clear of a working run and impossible for a stub.
CONTRAST_FLOOR = 20
# What colour-contrast still abstains on after the decorative background is neutralised, and why — measured
# on the worst page (kepviselom at 390px): elmPartiallyObscured 158, shortTextContent 60, nonBmp 1, against
# 192 nodes judged. The first is the sticky topbar overlapping content that has scrolled under it, which is a
# property of measuring a viewport rather than of the page. None of it is a second blind spot; the gradient
# was the only one. Printed above this many so that a new opaque background announces itself the day it lands.
INCOMPLETE_NOISE = 60

# What reference/axe.lock pins. A run by a different engine is a different gate, and this notices.
def _axe_version() -> str | None:
    lock = ROOT / "reference" / "axe.lock"
    if not lock.exists():
        return None
    import re as _re
    m = _re.search(r"axe-core ([\d.]+)", lock.read_text(encoding="utf-8"))
    return m.group(1) if m else None


AXE_VERSION = _axe_version()


BLOCKING = 2          # one stylesheet, one favicon-ish link; a third means something started blocking paint


def chrome_path() -> str | None:
    for c in CHROME_CANDIDATES:
        if Path(c).exists() and os.access(c, os.X_OK):
            return c
    return shutil.which("google-chrome") or shutil.which("chromium")


def resolve(kinds: list[str] | None, origin: str) -> tuple[list[dict], list[str]]:
    """The sample: one existing page per kind, and the kinds that have no page built."""
    pages, missing = [], []
    for kind, spec in PAGE_KINDS.items():
        if kinds and kind not in kinds:
            continue
        rel = spec() if callable(spec) else spec
        if not rel or not (SITE / rel).exists():
            missing.append(kind)
            continue
        pages.append({"kind": kind, "path": rel, "url": f"{origin}/{rel}",
                      "weightOnly": kind in WEIGHT_ONLY})
    return pages, missing


class Origin:
    """A throwaway HTTP server over site/, because file:// audits the wrong site.

    The pages address their own assets absolutely — `/assets/portre/<id>.webp` — which is right on a server
    and points at the filesystem root under a file:// URL. Auditing that way produced 200-odd phantom
    ERR_FILE_NOT_FOUND on the pages that carry portraits, and would have hidden a real one among them. It
    also gets the MIME types right, which is what decides whether a stylesheet applies at all."""

    def __init__(self) -> None:
        import functools
        import http.server
        import socketserver
        import threading
        class Quiet(http.server.SimpleHTTPRequestHandler):
            """Serves site/ the way CloudFront does, which is the only way the byte figures mean anything.

            The distribution compresses text, so a reader downloads the gzip of a page and not the page. An
            uncompressed local server reported the archive cycle index at 12.35 MB against the 1.2 MB that
            actually crosses a wire — a budget set from that number would have been measuring a file nobody
            receives. Repetitive table markup is exactly what compresses best, so the gap is largest on the
            pages the budget most needs to be right about."""

            def log_message(self, *a, **k):                             # the audit's output is the report
                pass

            def send_head(self):
                path = self.translate_path(self.path)
                ctype = self.guess_type(path)
                if (not Path(path).is_file() or "gzip" not in self.headers.get("Accept-Encoding", "")
                        or not (ctype.startswith("text/") or ctype in
                                ("application/javascript", "application/json", "image/svg+xml"))):
                    return super().send_head()
                import gzip as _gz
                body = _gz.compress(Path(path).read_bytes(), 6)
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                import io
                return io.BytesIO(body)

        handler = functools.partial(Quiet, directory=str(SITE))
        self.httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def run(pages: list[dict], viewport: str, chrome: str) -> dict:
    job = {"chrome": chrome, "axe": str(AXE), "pages": pages, "viewport": VIEWPORTS[viewport],
           "motionCheck": viewport == "desktop"}
    p = subprocess.run(["node", str(DRIVER)], input=json.dumps(job), capture_output=True, text=True)
    if not p.stdout.strip():
        return {"error": (p.stderr or "the driver printed nothing").strip()[:2000], "pages": []}
    return json.loads(p.stdout)


def judge(report: dict, viewport: str) -> tuple[list[str], list[str]]:
    """Hard failures and soft notes, in the order a reader of the output would want them."""
    hard, soft = [], []
    if report.get("error") is not None:
        return [f"[{viewport}] the audit could not run: {report['error']}"], []
    # Auditing nothing used to report success: `--kind nosuchkind` printed "0/0 clean" and exited 0. An empty
    # verdict is the one result a gate must never give.
    if not report.get("pages"):
        return [f"[{viewport}] the audit judged no pages at all"], []
    for pg in report.get("pages", []):
        where = f"[{viewport}] {pg['kind']} ({pg['path']})"
        if pg.get("error"):
            hard.append(f"{where}: {pg['error']}")
            continue
        for v in pg.get("violations", []):
            line = f"{where}: {v['impact']} · {v['id']} · {v['count']}× — {v['help']}"
            detail = "".join(f"\n        {n['target']}" for n in v["nodes"])
            bad = v["impact"] in ("critical", "serious") or v["id"] in ESCALATE
            (hard if bad else soft).append(line + detail)
        # Proof the engine ran, rather than an empty violations list that could mean anything. A stubbed axe,
        # an injection that silently failed, or a page that seizes window.axe all report zero violations and
        # zero passes; a page that was really audited judges hundreds of nodes for contrast alone.
        if not pg.get("weightOnly") and (pg.get("contrastPasses") or 0) < CONTRAST_FLOOR:
            hard.append(f"{where}: colour-contrast judged only {pg.get('contrastPasses', 0)} nodes — "
                        f"the rule did not really run (floor {CONTRAST_FLOOR})")
        if pg.get("axeVersion") and AXE_VERSION and pg["axeVersion"] != AXE_VERSION:
            hard.append(f"{where}: axe {pg['axeVersion']} ran, but {AXE_VERSION} is what reference/axe.lock pins")
        for e in pg.get("errors", []):
            hard.append(f"{where}: the page threw — {e[:160]}")
        # After the gradient is neutralised the residue is genuine — short text, non-BMP glyphs — and small.
        # It is printed so that a new opaque background announces itself the day it lands rather than
        # silently switching the rule off again.
        for v in pg.get("incomplete", []):
            if v["id"] == "color-contrast" and v["count"] > INCOMPLETE_NOISE:
                soft.append(f"{where}: colour-contrast could not judge {v['count']} nodes")
        if pg.get("scrollW", 0) > pg.get("clientW", 0) + 1:
            hard.append(f"{where}: the page scrolls sideways ({pg['scrollW']}px in {pg['clientW']}px)")
        if pg.get("lang") != "hu":
            hard.append(f"{where}: <html lang> is {pg.get('lang')!r}, not 'hu'")
        if not (pg.get("title") or "").strip():
            hard.append(f"{where}: no <title>")
        max_b, max_n = BUDGETS.get(pg["kind"], BUDGET_DEFAULT)
        if pg["net"]["bytes"] > max_b:
            hard.append(f"{where}: {pg['net']['bytes']:,} bytes over the wire, budget {max_b:,}")
        if pg["nodes"] > max_n:
            hard.append(f"{where}: {pg['nodes']:,} DOM nodes, budget {max_n:,}")
        if pg.get("blocking", 0) > BLOCKING:
            soft.append(f"{where}: {pg['blocking']} render-blocking resources in <head>")
        for e in pg["net"]["failed"]:
            if "net::ERR_ABORTED" not in e:
                soft.append(f"{where}: a request failed — {e}")
    # The pages above are audited under reduced motion, which is the right state to measure and also the one
    # where the boot script does nothing at all. So one page is loaded the way most readers get it: if that
    # script throws halfway, every panel stays at opacity 0 and the result is a blank page that passes every
    # rule there is, because nothing is left on it to fail.
    m = report.get("motion")
    if m and not m.get("error"):
        if m["motion"]["faded"]:
            hard.append(f"[{viewport}] {m['kind']}: {m['motion']['faded']} of {m['motion']['total']} panels "
                        f"never faded in — {', '.join(m['motion']['worst'])}")
        for e in m.get("errors", []):
            hard.append(f"[{viewport}] {m['kind']}: the page threw — {e[:160]}")
    return hard, soft


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--viewport", default="desktop,mobile", help="desktop, mobile, or both")
    ap.add_argument("--kind", help="comma-separated page kinds; default every one")
    ap.add_argument("--json", type=Path, help="write the full report here")
    ap.add_argument("--write", action="store_true",
                    help="record the coverage figures in data/derived/a11y.json for the README to quote")
    a = ap.parse_args(argv)

    chrome = chrome_path()
    if not chrome:
        print("no Chrome or Chromium found — the audit needs a browser with layout, and says so rather than\n"
              "reporting a pass it did not earn. Install one, or run this where one exists.")
        return 2
    if not AXE.exists():
        print(f"{AXE} is missing — run: python3 -m scripts.fetch_axe")
        return 2

    kinds = [k.strip() for k in a.kind.split(",")] if a.kind else None
    origin = Origin()
    try:
        pages, missing = resolve(kinds, origin.url)
        if missing:
            print(f"not built, so not audited: {', '.join(missing)}")
        if not pages:
            print("\nno page matched — nothing was audited, which is not the same as nothing being wrong.")
            return 1
        print(f"{len(pages)} page kinds · {chrome.rsplit('/', 1)[-1]} · served over http")

        all_hard, all_soft, full = [], [], {}
        for vp in [v.strip() for v in a.viewport.split(",")]:
            report = run(pages, vp, chrome)
            full[vp] = report
            hard, soft = judge(report, vp)
            all_hard += hard
            all_soft += soft
            ok = sum(1 for p in report.get("pages", []) if not p.get("error") and not p.get("violations"))
            print(f"  {vp:<8} {ok}/{len(report.get('pages', []))} clean"
                  + (f" · {report.get('browser', '')}" if report.get("browser") else ""))
    finally:
        origin.close()

    if a.json:
        a.json.write_text(json.dumps(full, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  written {a.json}")

    if all_soft:
        print(f"\n{len(all_soft)} moderate/minor, not gating:")
        for s in sorted(set(all_soft)):
            print(f"  {s}")
    if a.write:
        # The README quotes what this covers, and a figure a document states about itself is a figure the
        # document should not be told — the search page said 43.8% for a run that never shipped, and a test
        # pinned the drifted value. So the gate writes down what it actually did, and check_readme reads that.
        import re as _re
        pat = _re.compile(r'\b(?:src|srcset|data-src|poster)\s*=\s*"(https?://[^"]+)"')
        html = off = 0
        for f in SITE.rglob("*.html"):
            html += 1
            off += sum(1 for u in pat.findall(f.read_text(encoding="utf-8", errors="replace"))
                       if "wikimedia.org" not in u and "wikipedia.org" not in u)
        lock = ROOT / "reference" / "axe.lock"
        m = _re.search(r"axe-core ([\d.]+)", lock.read_text(encoding="utf-8")) if lock.exists() else None
        out = ROOT / "data" / "derived" / "a11y.json"
        out.write_text(json.dumps({"kinds": len(PAGE_KINDS), "html": html, "offsite": off,
                                   "axe": m.group(1) if m else None,
                                   "viewports": sorted(VIEWPORTS),
                                   "serious": len(all_hard), "soft": len(set(all_soft))},
                                  ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"  written {out}")

    if all_hard:
        print(f"\n{len(all_hard)} FAILING — critical/serious, or over budget:")
        for h in sorted(set(all_hard)):
            print(f"  {h}")
        return 1
    print("\nno critical or serious violation on any page kind, in either viewport, and every page inside its budget.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
