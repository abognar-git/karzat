"""A plain-language summary of every bill of the current cycle, written from the bill's own text.

    python3 -m scripts.summarize_bills                 # every törvényjavaslat with no summary for its current text
    python3 -m scripts.summarize_bills T/961 T/930     # just these
    python3 -m scripts.summarize_bills --force T/961   # rewrite even if the text has not changed

The title of a bill says almost nothing; the text is a 14-page PDF a reader will not open. So each bill page
carries a short, factual account of what the bill does, written by Claude from the submitted text, every
claim tied to the paragraph and page it comes from, and one sentence of the submitter's own reasoning quoted
word for word and labelled as theirs.

Three things keep it honest, and each is checked rather than asked for:
  * the quote must appear in the text verbatim (whitespace aside) — checked here, in Python;
  * every cited page must exist and the cited § number must appear on it — checked here;
  * a second, separate pass reads the text and the summary and lists anything the text does not support,
    or any word that judges rather than reports. One retry with those problems fed back; a summary that still
    fails is kept out of the site and listed as needing review.

The model runs through the local Claude Code CLI (`claude -p`), on the owner's subscription: no API key is
stored anywhere. A summary is redone only when the text's hash changes. PDFs are cached under data/raw/irom/
(git-ignored) and fetched two seconds apart.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORDS = ROOT / "data" / "derived" / "iromany_records.json"
OUT = ROOT / "data" / "derived" / "osszefoglalok.json"
CACHE = ROOT / "data" / "raw" / "irom"
MAX_CHARS = 400_000          # beyond this a bill (the budget) is summarised by hand, not truncated silently
UA = "karzat (ogykarzat.hu)"

SYSTEM = """Az Országgyűlés egy törvényjavaslatának szövegét kapod, oldalakra bontva ("=== N. oldal ===").
Írj róla rövid, közérthető, tényszerű összefoglalót egy átlagos magyar olvasónak.

Szabályok — ezek kötelezőek:
- Csak azt írd le, ami a szövegben áll. Semmit ne találj ki, ne következtess, ne egészíts ki saját tudásból.
- Semmilyen értékelés: ne írd, hogy valami fontos, vitatott, szigorú, kedvező, hátrányos, jelentős stb.
  A célt csak a benyújtó szavaival, idézetben add meg.
- Közérthető magyarság: rövid mondatok, hétköznapi szavak. Ha jogi szakszó kell, egyszer magyarázd meg
  zárójelben. Rövidítést első előfordulásnál írd ki.
- Minden "mi változik" pontnál add meg a forrást: a törvényjavaslat saját paragrafusát (pl. "4. §" vagy
  "7–9. §") és az oldalt, ahol az áll. Ne a módosított régi törvény paragrafusszámát, hanem a javaslatét.
- Az idézet a benyújtó indokolásából szó szerint vett, legfeljebb 40 szavas részlet legyen, betűre pontosan.
  Ha nincs indokolás, az idézet legyen null.
- Ha valamit a szöveg nem mond meg (pl. a hatálybalépést), azt a mezőt hagyd null-on.

Mezők:
- lede: 2–4 mondat arról, miről szól a javaslat egésze.
- kiket_erint: egy mondat, kiket érint közvetlenül (szervezetek, emberek csoportjai), a szöveg alapján.
- valtozik: 3–7 pont, a legfontosabb konkrét változások; mindegyik 1–2 mondat, számokkal, határidőkkel,
  ahogy a szöveg megadja.
- mikortol: a hatálybalépés a záró rendelkezések szerint.
- idezet: a benyújtó indokolásából egy mondat, amely a javaslat célját mondja."""

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["lede", "kiket_erint", "valtozik", "mikortol", "idezet"],
    "properties": {
        "lede": {"type": "string"},
        "kiket_erint": {"type": ["string", "null"]},
        "valtozik": {"type": "array", "minItems": 1, "maxItems": 8, "items": {
            "type": "object", "additionalProperties": False, "required": ["szoveg", "paragrafus", "oldal"],
            "properties": {"szoveg": {"type": "string"}, "paragrafus": {"type": "string"}, "oldal": {"type": "integer"}}}},
        "mikortol": {"type": ["object", "null"], "additionalProperties": False, "required": ["szoveg", "paragrafus", "oldal"],
                     "properties": {"szoveg": {"type": "string"}, "paragrafus": {"type": "string"}, "oldal": {"type": "integer"}}},
        "idezet": {"type": ["object", "null"], "additionalProperties": False, "required": ["szoveg", "hely", "oldal"],
                   "properties": {"szoveg": {"type": "string"}, "hely": {"type": "string"}, "oldal": {"type": "integer"}}},
    },
}

CHECK_SYSTEM = """Egy törvényjavaslat szövegét és a róla írt összefoglalót kapod. Ellenőrizd szigorúan.
Hibának számít:
- olyan állítás, amelyet a szöveg nem támaszt alá, vagy amely pontatlan (szám, határidő, szereplő);
- rossz forráshivatkozás: a megadott paragrafus (vagy cikk) nem az, amelyben az állítás áll, vagy a megadott
  oldalon nem kezdődik az a paragrafus. Az oldal mindig annak az oldalnak a száma, ahol a hivatkozott (több
  esetén az első) paragrafus vagy cikk KEZDŐDIK; ha az állítás ugyanannak a paragrafusnak egy későbbi oldalán
  áll, az nem hiba;
- értékelő, minősítő szó vagy következtetés, amely nem a szövegből jön;
- az idézet nem szó szerinti, vagy nem a benyújtó indokolásából való.
Apró stílusbeli dolgot ne jelezz. Ha nincs hiba, a problems lista legyen üres."""

CHECK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["problems"],
                "properties": {"problems": {"type": "array", "items": {"type": "string"}}}}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("­", "").replace("-\n", "")).strip()


def fetch_text(rec: dict) -> list[str]:
    """The bill's pages, from the cached PDF (fetched once, politely)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    num = rec["szam_parsed"]["number"]
    pdf = CACHE / f"{num:05d}.pdf"
    if not pdf.exists():
        req = urllib.request.Request(rec["text_pdf_url"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r:
            pdf.write_bytes(r.read())
        time.sleep(2)
    txt = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    return txt.split("\f")


def paged(pages: list[str]) -> str:
    return "\n".join(f"=== {i} oldal ===\n{p}" for i, p in enumerate(pages, 1) if p.strip())


def ask(system: str, schema: dict, prompt: str, model: str) -> dict:
    """One headless Claude Code call, no tools, structured output."""
    cmd = ["claude", "-p", "--model", model, "--tools", "", "--output-format", "json", "--no-session-persistence",
           "--system-prompt", system, "--json-schema", json.dumps(schema, ensure_ascii=False)]
    for attempt in range(3):
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=str(CACHE), timeout=1800)
        if r.returncode == 0:
            out = json.loads(r.stdout)
            res = out.get("structured_output") or out.get("result")
            if isinstance(res, str):
                res = json.loads(res)
            if isinstance(res, dict):
                return res
        time.sleep(20 * (attempt + 1))
    raise RuntimeError(f"claude -p failed: {r.stderr[-400:] or r.stdout[-400:]}")


def mechanical_problems(s: dict, pages: list[str]) -> list[str]:
    """What Python can check by itself: the quote is verbatim, every cited page exists and names the §."""
    probs = []
    full = norm("\n".join(pages))
    q = s.get("idezet")
    if q:
        if norm(q["szoveg"]).strip("„”\"") not in full:
            probs.append(f"az idézet nem szó szerint áll a szövegben: {q['szoveg'][:80]}")
    for item in (s.get("valtozik") or []) + ([s["mikortol"]] if s.get("mikortol") else []):
        pg = item.get("oldal")
        if not isinstance(pg, int) or not 1 <= pg <= len(pages):
            probs.append(f"nem létező oldal: {pg} ({item.get('paragrafus')})")
            continue
        first = re.match(r"\d+", item.get("paragrafus") or "")
        if first:
            window = norm("\n".join(pages[max(0, pg - 2):pg + 1]))       # a § can start on the page before
            if not re.search(rf"\b{first.group(0)}\.\s*(§|cikk\b)", window, re.I):
                probs.append(f"a {item['paragrafus']} nem a {pg}. oldal környékén áll")
    return probs


def own_page(par: str, pages: list[str], ind: int | None) -> int | None:
    """The page where the bill's own § with this number starts — a heading at the start of a line, before the
    reasoning begins, never a quoted § of the law being amended (those open with „). A treaty's articles
    ("1. cikk", "5. CIKK") count the same way: in a bill that promulgates one, they are what a claim cites."""
    m = re.match(r"\d+", par or "")
    if not m:
        return None
    last = (ind - 1) if ind else len(pages)
    for i, pg in enumerate(pages[:last], 1):
        if re.search(rf"^\s*{m.group(0)}\.\s*(§(?!-)|cikk\b)", pg, re.M | re.I):
            return i
    return None


def fix_pages(s: dict, pages: list[str], ind: int | None) -> dict:
    """Re-point every change at the page where its first § really starts. Pure lookup, no model."""
    for item in (s.get("valtozik") or []) + ([s["mikortol"]] if s.get("mikortol") else []):
        pg = own_page(item.get("paragrafus") or "", pages, ind)
        if pg:
            item["oldal"] = pg
    return s


def indokolas_page(pages: list[str]) -> int | None:
    return next((i for i, pg in enumerate(pages, 1) if re.search(r"^\s*Általános indokolás\s*$", pg, re.M)), None)


def refine(rec: dict, old: dict, model: str) -> dict:
    """A summary flagged for review gets its pages fixed by lookup, then the same check-and-fix loop again,
    starting from what it already says rather than from scratch."""
    pages = fetch_text(rec)
    text = paged(pages)
    ind = indokolas_page(pages)
    s = fix_pages(json.loads(json.dumps(old["summary"])), pages, ind)
    where = (f"A benyújtó indokolása a {ind}. oldalon kezdődik; az idézetet onnan vedd, betűre pontosan." if ind
             else "Ebben a szövegben nincs „Általános indokolás” fejezet: az idezet legyen null.")
    probs: list[str] = []
    for round_ in range(3):
        probs = mechanical_problems(s, pages)
        chk = ask(CHECK_SYSTEM, CHECK_SCHEMA, f"SZÖVEG:\n{text}\n\nÖSSZEFOGLALÓ:\n{json.dumps(s, ensure_ascii=False, indent=1)}", model)
        probs += chk.get("problems") or []
        if not probs or round_ == 2:
            break
        s = ask(SYSTEM, SCHEMA, f"A törvényjavaslat: {rec['szam']} — {rec.get('title')}\n{where}\n"
                f"Ha egy szabálynak feltétele vagy kivétele van, azt is írd le.\n\nEz az összefoglaló készült róla:\n"
                f"{json.dumps(s, ensure_ascii=False, indent=1)}\n\nJavítsd ki ezeket a hibákat, a többit hagyd meg:\n- "
                + "\n- ".join(probs) + f"\n\n{text}", model)
        s = fix_pages(s, pages, ind)
    return {**old, "made": date.today().isoformat(), "model": model, "status": "ok" if not probs else "review",
            "problems": probs, "summary": s, "refined": True}


def summarise(rec: dict, model: str) -> dict:
    pages = fetch_text(rec)
    text = paged(pages)
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    base = {"szam": rec["szam"], "text_hash": h, "pages": len(pages), "model": model,
            "made": date.today().isoformat(), "source": rec["text_pdf_url"]}
    if len(text) > MAX_CHARS:
        return {**base, "status": "too_long", "chars": len(text)}
    ind = next((i for i, pg in enumerate(pages, 1) if re.search(r"^\s*Általános indokolás\s*$", pg, re.M)), None)
    where = (f"A benyújtó indokolása a {ind}. oldalon kezdődik („Általános indokolás”); az idézetet onnan vagy a "
             f"„Részletes indokolás” részből vedd, ne a törvény szövegéből vagy a bevezető bekezdésekből."
             if ind else "Ebben a szövegben nincs „Általános indokolás” fejezet: az idezet legyen null.")
    prompt = f"A törvényjavaslat: {rec['szam']} — {rec.get('title')}\n{where}\nHa egy szabálynak feltétele vagy kivétele van, azt is írd le.\n\n{text}"
    s = ask(SYSTEM, SCHEMA, prompt, model)
    probs: list[str] = []
    for round_ in range(3):
        probs = mechanical_problems(s, pages)
        chk = ask(CHECK_SYSTEM, CHECK_SCHEMA, f"SZÖVEG:\n{text}\n\nÖSSZEFOGLALÓ:\n{json.dumps(s, ensure_ascii=False, indent=1)}", model)
        probs += chk.get("problems") or []
        if not probs or round_ == 2:
            break
        s = ask(SYSTEM, SCHEMA, prompt + "\n\nEgy korábbi változatban ezek a hibák voltak, kerüld el őket:\n- "
                + "\n- ".join(probs), model)
    return {**base, "status": "ok" if not probs else "review", "problems": probs, "summary": s}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("only", nargs="*", help="iromány numbers, e.g. T/961")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--model", default="opus")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--refine", action="store_true", help="re-check the summaries flagged for review, fixing pages first")
    args = ap.parse_args(argv)
    d = json.loads(RECORDS.read_text(encoding="utf-8"))
    start = "2026-05-09"
    recs = [r for r in d["records"].values() if r.get("main_type") == "törvényjavaslat" and r.get("text_pdf_url")
            and (r.get("submitted_on") or "") >= start]
    if args.only:
        recs = [r for r in recs if r["szam"] in set(args.only)]
    done = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    if args.refine:
        todo = [r for r in recs if done.get(r["szam"], {}).get("status") == "review"]
    else:
        todo = [r for r in recs if args.force or done.get(r["szam"], {}).get("status") not in ("ok", "too_long", "review")]
    print(f"{len(recs)} bills, {len(todo)} to write", flush=True)
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {(ex.submit(refine, r, done[r["szam"]], args.model) if args.refine else ex.submit(summarise, r, args.model)): r["szam"] for r in sorted(todo, key=lambda r: r["szam_parsed"]["number"], reverse=True)}
        for i, f in enumerate(cf.as_completed(futs), 1):
            szam = futs[f]
            try:
                res = f.result()
            except Exception as e:                                  # one bad bill must not lose the others
                res = {"szam": szam, "status": "error", "error": str(e)[:300]}
            done[szam] = res
            OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            print(f"[{i}/{len(todo)}] {szam}: {res['status']}" + (f" — {len(res.get('problems') or [])} gond" if res.get("problems") else "")
                  + f" · {(time.time() - t0) / 60:.0f} perc", flush=True)
    st = {}
    for v in done.values():
        st[v["status"]] = st.get(v["status"], 0) + 1
    print("kész:", st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
