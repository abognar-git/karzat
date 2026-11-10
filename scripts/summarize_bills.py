"""A plain-language summary of every bill of the current cycle, written from the bill's own text.

    python3 -m scripts.summarize_bills                 # every törvényjavaslat with no summary for its current text
    python3 -m scripts.summarize_bills T/961 T/930     # just these
    python3 -m scripts.summarize_bills --force T/961   # rewrite even if the text has not changed
    python3 -m scripts.summarize_bills --refine        # re-check and fix the ones held for review
    python3 -m scripts.summarize_bills --pages         # no model: look every cited page up again

The title of a bill says almost nothing; the text is a 14-page PDF a reader will not open. So each bill page
carries a short, factual account of what the bill does, written by Claude from the submitted text, every
claim tied to the paragraph and page it comes from, and one sentence of the submitter's own reasoning quoted
word for word and labelled as theirs.

Three things keep it honest, and each is checked rather than asked for:
  * the quote must appear in the reasoning verbatim (whitespace, hyphens and quotation marks aside) — checked
    here, in Python;
  * every page is looked up here, never taken from the model: the page where the cited § (or annex, or a treaty
    article inside an annex) starts; a change that cites the reasoning instead of the bill is sent back;
  * a second, separate pass reads the text and the summary and lists anything the text does not support,
    or any word that judges rather than reports. Up to two retries with those problems fed back; a summary that still
    fails is kept out of the site and listed as needing review.

A bill too long to read whole (the budget, a 1,000-page treaty) is read without its annexes and says so.

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
MAX_CHARS = 400_000          # beyond this a bill is read without its annexes (and says so), never truncated silently
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
  "7–9. §", egy kihirdetett szerződésnél "1. melléklet 5. cikk") és az oldalt, ahol az áll. Ne a módosított
  régi törvény paragrafusszámát, hanem a javaslatét. A "mi változik" pontok a javaslat rendelkezéseiből
  szóljanak; ami csak az indokolásban áll, az ne legyen köztük.
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
- rossz forráshivatkozás: a megadott paragrafus (vagy melléklet, cikk) nem az, amelyben az állítás áll, vagy az
  állítás csak az indokolásban szerepel, a javaslat rendelkezéseiben nem (az oldalszámokat a program állítja be,
  azokat ne ellenőrizd);
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


FOOTER = re.compile(r"^\s*\d{1,4}\s*$")
HEAD = re.compile(r"^\s*(\d+)\.\s*§(?!-)(.*)$")
ANNEX = re.compile(r"^\s*(\d+)\.\s*melléklet\s+a\s+20\d\d\.\s*évi\s*[.…\s]+törvényhez", re.I)   # the bill's own annex, not a quoted one
CIKK = re.compile(r"^\s*(\d+)\.\s*cikk\b", re.I)
REASONS = re.compile(r"^\s*(?:(?:általános|részletes)\s+)?indokolás\s*$", re.I)


def qnorm(s: str) -> str:
    """Text as the quote check compares it: no page-number footers, no quotation marks, no hyphens (a hyphen at a
    line break and a hyphen in a compound look the same to pdftotext), whitespace collapsed."""
    s = "\n".join(l for l in s.replace("\xad", "").split("\n") if not FOOTER.match(l))
    s = re.sub(r"-\s*\n\s*", "", s)
    s = re.sub(r",,|''|[„”“\"«»‚’'\-–]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def indokolas_at(pages: list[str]) -> tuple[int, int] | None:
    """(page, line) where the submitter's reasoning starts: the first line that is only "Indokolás", "Általános
    indokolás" or "Részletes indokolás", in any case. Everything before it is the bill itself."""
    for i, pg in enumerate(pages[1:], 2):
        for j, line in enumerate(pg.split("\n")):
            if REASONS.match(line):
                return i, j
    return None


def indokolas_page(pages: list[str]) -> int | None:
    at = indokolas_at(pages)
    return at[0] if at else None


def _run(cands: list[tuple[int, int]]) -> dict[int, int]:
    """The longest run of numbers that only ever grows, in reading order: {number: page}."""
    best = [(1, -1)] * len(cands)
    for b in range(len(cands)):
        for a in range(b):
            if cands[a][0] < cands[b][0] and best[a][0] + 1 > best[b][0]:
                best[b] = (best[a][0] + 1, a)
    out: dict[int, int] = {}
    if cands:
        k = max(range(len(cands)), key=lambda x: best[x][0])
        while k >= 0:
            out[cands[k][0]] = cands[k][1]
            k = best[k][1]
    return out


def _heading(rest: str) -> bool:
    """What follows "5. §" on a heading line: nothing, a bracketed title, a capital, or "(1)" and a capital —
    never "(1) bekezdésében", which is a reference that wrapped to the start of a line."""
    rest = rest.strip()
    if not rest or rest[0] == "[" or rest[0].isupper():
        return True
    m = re.match(r"\(\s*1\s*\)\s*(\S)", rest)
    return bool(m) and not m.group(1).islower()


def units(pages: list[str]) -> dict:
    """Where each of the bill's own units starts: its §s (or, in an amendment of the Fundamental Law, its
    articles), its annexes, and the articles inside each annex.

    A § number at the start of a line is not enough — the text of an amended law is quoted inside the bill, and a
    quoted "69. § (5a) és (5b) bekezdését" can wrap to the start of a line. Only a line shaped like a heading
    counts (see _heading), and of those the bill's own are the longest
    run of numbers that only ever grows, in reading order; a quoted heading breaks the run and is left out."""
    end = indokolas_at(pages) or (len(pages) + 1, 0)
    heads, arts, annex, cikk, cur = [], [], {}, {}, None
    for i, pg in enumerate(pages, 1):
        for j, line in enumerate(pg.split("\n")):
            if (i, j) >= end:
                break
            m = ANNEX.match(line)
            if m:
                cur = int(m.group(1))
                annex.setdefault(cur, i)
                continue
            if cur is None:
                m = HEAD.match(line)
                if m and _heading(m.group(2)):
                    heads.append((int(m.group(1)), i))
                m = CIKK.match(line)
                if m and _heading(line[m.end():]):
                    arts.append((int(m.group(1)), i))
            else:
                m = CIKK.match(line)
                if m:
                    cikk.setdefault((cur, int(m.group(1))), i)
    return {"par": _run(heads), "art": {} if heads else _run(arts), "annex": annex, "cikk": cikk}


def _between(n: int, found: dict[int, int]) -> int | None:
    """A unit whose heading the PDF's text layer lost ("S. §" for "5. §", or no text at all) is placed only when
    the units either side of it start on the same page."""
    if n in found:
        return found[n]
    lo = max((k for k in found if k < n), default=None)
    hi = min((k for k in found if k > n), default=None)
    return found[lo] if lo is not None and hi is not None and found[lo] == found[hi] else None


def cite_page(cite: str, u: dict) -> int | None:
    """The page where the first unit a citation names starts — "2. § (3)", "4–13. §", "1. melléklet 3. cikk",
    "6. cikk". None when it cannot be found, and the link opens the PDF at its start rather than at a guess."""
    cite = cite or ""
    m = re.match(r"\s*(\d+)\.\s*melléklet\b[\s,]*(?:(\d+)\.\s*cikk\b)?", cite, re.I)
    if m:
        a = int(m.group(1))
        return u["cikk"].get((a, int(m.group(2)))) if m.group(2) else u["annex"].get(a)
    m = re.match(r"\s*(\d+)\.?\s*(?:[–-]\s*\d+\.)?\s*(§|cikk\b)", cite, re.I)
    if not m:
        return None
    n = int(m.group(1))
    if m.group(2) == "§":
        return _between(n, u["par"])
    return _between(n, u["art"]) if u["art"] else u["cikk"].get((1, n))


def quote_page(q: str, pages: list[str]) -> int | None:
    """The page where the quote starts — in the reasoning if it stands there, since a preamble often says the
    same words — or None if the text does not hold it word for word."""
    joined, starts = "", []
    for pg in pages:
        starts.append(len(joined))
        joined += qnorm(pg) + " "
    q = qnorm(q)
    if not q:
        return None
    hits, at = [], joined.find(q)
    while at >= 0:
        hits.append(max(i for i, st in enumerate(starts, 1) if st <= at))
        at = joined.find(q, at + 1)
    if not hits:
        return None
    ind = indokolas_page(pages)
    return next((h for h in hits if ind and h >= ind), hits[0])


def set_pages(s: dict, pages: list[str]) -> dict:
    """Every page in a summary is looked up, never taken from the model."""
    u = units(pages)
    for item in (s.get("valtozik") or []) + ([s["mikortol"]] if s.get("mikortol") else []):
        item["oldal"] = cite_page(item.get("paragrafus") or "", u)
    if s.get("idezet"):
        s["idezet"]["oldal"] = quote_page(s["idezet"].get("szoveg") or "", pages)
    return s


def mechanical_problems(s: dict, pages: list[str]) -> list[str]:
    """What Python can check by itself: the quote is verbatim and from the reasoning, and every change cites the
    bill's own § or annex — not its reasoning."""
    probs = []
    q = s.get("idezet")
    if q:
        pg = quote_page(q.get("szoveg") or "", pages)
        ind = indokolas_page(pages)
        if pg is None:
            probs.append(f"az idézet nem szó szerint áll a szövegben: {q['szoveg'][:80]}")
        elif ind and pg < ind:
            probs.append("az idézet nem a benyújtó indokolásából való, hanem a törvény szövegéből")
    for item in (s.get("valtozik") or []) + ([s["mikortol"]] if s.get("mikortol") else []):
        cite = item.get("paragrafus") or ""
        if "indokol" in cite.casefold():
            probs.append(f"a „{cite}” pont forrása az indokolás: a változások a javaslat paragrafusaiból vagy "
                         f"mellékletéből szóljanak; ami csak az indokolásban áll, maradjon ki")
        elif not re.match(r"\s*\d+\.?\s*(?:[–-]\s*\d+\.)?\s*(§|melléklet|cikk\b)", cite, re.I):
            probs.append(f"a „{cite}” nem a javaslat egy paragrafusa, cikke vagy melléklete")
    return probs


def for_checker(s: dict) -> str:
    """The summary as the checker reads it: without the pages, which Python sets and the checker must not judge."""
    t = json.loads(json.dumps(s))
    for item in (t.get("valtozik") or []) + ([t["mikortol"]] if t.get("mikortol") else []) + ([t["idezet"]] if t.get("idezet") else []):
        item.pop("oldal", None)
    return json.dumps(t, ensure_ascii=False, indent=1)


def trimmed(pages: list[str]) -> tuple[list[str], tuple[int, int] | None]:
    """A bill too long to read whole (the budget's tables, a treaty's 1,000 pages) is read without its annexes:
    its own §s and its reasoning stay, the annex pages are blanked so every page keeps its number."""
    ind = indokolas_page(pages) or len(pages) + 1
    first = min(units(pages)["annex"].values(), default=None)
    if not first or first >= ind:
        return pages, None
    return [("" if first <= i < ind else p) for i, p in enumerate(pages, 1)], (first, ind - 1)


def where_prompt(pages: list[str], cut: tuple[int, int] | None) -> str:
    ind = indokolas_page(pages)
    w = (f"A benyújtó indokolása a {ind}. oldalon kezdődik; az idézetet onnan vedd, betűre pontosan, ne a törvény "
         f"szövegéből vagy a bevezető bekezdésekből." if ind
         else "Ebben a szövegben nincs indokolás fejezet: az idezet legyen null.")
    if cut:
        w += (f"\nA {cut[0]}–{cut[1]}. oldal (a mellékletek) terjedelme miatt nincs a szövegben. A mellékletek "
              f"tartalmáról csak azt írd, amit a paragrafusok mondanak róluk; a „valtozik” pontok a paragrafusokból szóljanak.")
    return w


def check_and_fix(rec: dict, s: dict, pages: list[str], text: str, base_prompt: str, model: str,
                  revise_from_current: bool) -> tuple[dict, list[str]]:
    probs: list[str] = []
    for round_ in range(3):
        s = set_pages(s, pages)
        probs = mechanical_problems(s, pages)
        chk = ask(CHECK_SYSTEM, CHECK_SCHEMA, f"SZÖVEG:\n{text}\n\nÖSSZEFOGLALÓ:\n{for_checker(s)}", model)
        probs += chk.get("problems") or []
        if not probs or round_ == 2:
            break
        if revise_from_current:
            s = ask(SYSTEM, SCHEMA, f"{base_prompt}\n\nEz az összefoglaló készült róla:\n{for_checker(s)}\n\n"
                    f"Javítsd ki ezeket a hibákat, a többit hagyd meg:\n- " + "\n- ".join(probs) + f"\n\n{text}", model)
        else:
            s = ask(SYSTEM, SCHEMA, f"{base_prompt}\n\n{text}\n\nEgy korábbi változatban ezek a hibák voltak, kerüld el őket:\n- "
                    + "\n- ".join(probs), model)
    return set_pages(s, pages), probs


def refine(rec: dict, old: dict, model: str) -> dict:
    """A summary flagged for review goes through the same check-and-fix loop again, starting from what it already
    says rather than from scratch."""
    pages = fetch_text(rec)
    read, cut = trimmed(pages) if old.get("partial") else (pages, None)
    base = f"A törvényjavaslat: {rec['szam']} — {rec.get('title')}\n{where_prompt(pages, cut)}\nHa egy szabálynak feltétele vagy kivétele van, azt is írd le."
    s, probs = check_and_fix(rec, json.loads(json.dumps(old["summary"])), read, paged(read), base, model, True)
    return {**old, "made": date.today().isoformat(), "model": model, "status": "ok" if not probs else "review",
            "problems": probs, "summary": s, "refined": True}


def summarise(rec: dict, model: str) -> dict:
    pages = fetch_text(rec)
    text = paged(pages)
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    base = {"szam": rec["szam"], "text_hash": h, "pages": len(pages), "model": model,
            "made": date.today().isoformat(), "source": rec["text_pdf_url"]}
    read, cut = pages, None
    if len(text) > MAX_CHARS:
        read, cut = trimmed(pages)
        if not cut or len(paged(read)) > MAX_CHARS:
            return {**base, "status": "too_long", "chars": len(text)}
        base["partial"] = {"annex_pages": list(cut)}
    prompt = f"A törvényjavaslat: {rec['szam']} — {rec.get('title')}\n{where_prompt(pages, cut)}\nHa egy szabálynak feltétele vagy kivétele van, azt is írd le."
    s = ask(SYSTEM, SCHEMA, f"{prompt}\n\n{paged(read)}", model)
    s, probs = check_and_fix(rec, s, read, paged(read), prompt, model, False)
    return {**base, "status": "ok" if not probs else "review", "problems": probs, "summary": s}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("only", nargs="*", help="iromány numbers, e.g. T/961")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--model", default="opus")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--refine", action="store_true", help="re-check the summaries flagged for review, fixing pages first")
    ap.add_argument("--pages", action="store_true",
                    help="no model: look every page up again, and send back for review any summary Python now faults")
    args = ap.parse_args(argv)
    d = json.loads(RECORDS.read_text(encoding="utf-8"))
    start = "2026-05-09"
    recs = [r for r in d["records"].values() if r.get("main_type") == "törvényjavaslat" and r.get("text_pdf_url")
            and (r.get("submitted_on") or "") >= start]
    if args.only:
        recs = [r for r in recs if r["szam"] in set(args.only)]
    done = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    if args.pages:
        for r in recs:
            v = done.get(r["szam"])
            if not v or not v.get("summary"):
                continue
            pages = fetch_text(r)
            read = trimmed(pages)[0] if v.get("partial") else pages
            set_pages(v["summary"], read)
            probs = mechanical_problems(v["summary"], read)
            if probs and v["status"] == "ok":
                v.update(status="review", problems=probs)
                print(f"{r['szam']}: review — {'; '.join(probs)[:160]}", flush=True)
        OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return 0
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
