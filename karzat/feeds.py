"""Atom feeds — the alert channel of a static site.

A scheduled rebuild is the notification: every sync rewrites these files, and a feed reader
polls them. Nothing here is computed differently from the pages — the feeds print the same
dissent records the MP pages, the cohesion page and `adatok/kulonvelemenyek.csv` print, and
carry the same caveats in their subtitle: a tie is no faction plurality (nobody is against it),
a quorum check is presence (nobody is with or against anything), positions that are not a cast
vote are participation, not alignment.

Feeds per cycle (site/<cycle>/feed/):
  kulonvelemeny.xml            one entry per roll call with at least one dissent, newest first
  frakcio-<slug>.xml           the same, for votes where a member of that faction dissented
  szavazasok.xml               every roll call of the cycle, newest first (the plain "new votes" channel)
Per MP (site/<cycle>/kepviselo/<azon>.xml): that person's dissents, newest first.

Atom 1.0 (RFC 4287). Every <id> is a URN built from cycle and vote timestamp, so it never
changes between builds; every <updated> is the sync stamp or the vote's own time — never the
wall clock, so a rebuild from the same inputs is byte-identical. Links are absolute when
KARZAT_SITE_URL was set at build time (set it for the published build) and otherwise relative to
the feed file itself, which every reader resolves against the retrieval URI (RFC 3986 §5.1.3) —
no xml:base is needed. Independents ("független") are not a faction and are not in the dissent
channels; the MP pages' own numbers for them stay where they are, labelled.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any
from xml.sax.saxutils import escape as _x

from .freshness import BUDAPEST

ATOM_NS = "http://www.w3.org/2005/Atom"
MIN_ENTRIES = 100          # a feed is the recent past: at least this many entries …
RECENT_DAYS = 5            # … and every entry of the newest five sitting days, whichever is more (a marathon day has 160+ votes)
MAX_ENTRIES = 600          # hard ceiling on the file
INDEPENDENT = "független"  # not a faction: independents have no faction plurality to dissent from — they are not in the dissent channels

POS_HU = {"igen": "igen", "nem": "nem", "tartozkodott": "tartózkodott"}


def slug(text: str) -> str:
    """'Mi Hazánk' → 'mi-hazank'; ASCII, lower, hyphens; safe as a file name."""
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t or "x"


def rfc3339(ts: str) -> str:
    """API timestamp '2026.06.15.17:20:04' (Budapest local) → '2026-06-15T17:20:04+02:00'."""
    return datetime.strptime(ts, "%Y.%m.%d.%H:%M:%S").replace(tzinfo=BUDAPEST).isoformat()


def rfc3339_iso(iso: str | None, fallback: str) -> str:
    """An ISO stamp with offset (the sync state) normalised to RFC 3339 with seconds; `fallback` when absent."""
    if not iso:
        return fallback
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BUDAPEST)                 # a naive stamp is Budapest time, on every machine
        return dt.astimezone(BUDAPEST).replace(microsecond=0).isoformat()
    except ValueError:
        return fallback


def recent(items: list[dict], date_of) -> list[dict]:
    """The newest MIN_ENTRIES items, extended to cover the newest RECENT_DAYS distinct dates entirely (items must be newest
    first), never more than MAX_ENTRIES."""
    days: list[str] = []
    out: list[dict] = []
    for it in items:
        d = date_of(it)
        if d not in days:
            days.append(d)
        if len(out) >= MIN_ENTRIES and len(days) > RECENT_DAYS:
            break
        out.append(it)
        if len(out) >= MAX_ENTRIES:
            break
    return out


def a(text: Any) -> str:
    """Attribute-safe text."""
    return _x(str("" if text is None else text), {'"': "&quot;"})


# -- data shape ---------------------------------------------------------------------------------
#
# vote (from the builder's view):  {ts, slug, on_date, time, iromany, title, igen, nem, tartozkodott,
#                                   result, rule_label, needed, base, margin, kind}
# dissent:                          {azon, name, faction, position, plurality}
# The builder assembles `events` = [{"vote": vote, "dissents": [dissent, ...]}, ...] newest first.


def _entry_title(vote: dict, dissents: list[dict]) -> str:
    by_f: dict[str, int] = {}
    for d in dissents:
        by_f[d["faction"]] = by_f.get(d["faction"], 0) + 1
    facs = ", ".join(f"{f} {n}" for f, n in sorted(by_f.items(), key=lambda kv: (-kv[1], kv[0])))
    n = len(dissents)
    subj = " ".join(x for x in [vote.get("iromany") or "", vote.get("title") or ""] if x).strip() or "Szavazás"
    return f'{vote["on_date"]} {vote.get("time") or ""} · {subj} — {n} különvélemény ({facs})'.replace("  ", " ")


def _vote_line(vote: dict) -> str:
    parts = [f'{vote.get("igen")} – {vote.get("nem")} – {vote.get("tartozkodott")}']
    if vote.get("rule_label"):
        parts.append(vote["rule_label"])
    if vote.get("needed") is not None and vote.get("base") is not None:
        parts.append(f'szükséges {vote["needed"]} / {vote["base"]}')
    if vote.get("margin") is not None:
        parts.append(f'különbség {"+" if vote["margin"] >= 0 else ""}{vote["margin"]}')
    if vote.get("result"):
        parts.append(vote["result"])
    return " · ".join(parts)


def _content_html(vote: dict, dissents: list[dict], root: str, cycle_dir: str, mp_links: bool = True) -> str:
    """The entry body: the vote's line, then the dissenters (name · faction · own vote vs the faction's plurality)."""
    rows = "".join(
        f'<li>{"<a href=" + chr(34) + root + cycle_dir + "kepviselo/" + a(d["azon"]) + ".html" + chr(34) + ">" if (mp_links and d.get("azon")) else ""}'
        f'{_x(d["name"])}{"</a>" if (mp_links and d.get("azon")) else ""} ({_x(d["faction"])}): '
        f'{_x(POS_HU.get(d["position"], d["position"]))}, a frakció többsége {_x(POS_HU.get(d["plurality"], d["plurality"]))}</li>'
        for d in dissents)
    return (f'<p><a href="{a(root + cycle_dir)}szavazas/{a(vote["slug"])}.html">{_x(vote["on_date"])} {_x(vote.get("time") or "")}</a> · {_x(_vote_line(vote))}</p>'
            f'<ul>{rows}</ul>')


def atom(feed_id: str, title: str, subtitle: str, self_href: str, alt_href: str, updated: str, entries: list[str],
         author: str = "karzat") -> str:
    """Assemble an Atom document. `entries` are already-rendered <entry> strings. Hrefs are either absolute or
    relative to the feed file itself, so no xml:base is needed — every reader resolves against the retrieval URI."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<feed xmlns="{ATOM_NS}" xml:lang="hu">\n'
        f'  <id>{_x(feed_id)}</id>\n'
        f'  <title>{_x(title)}</title>\n'
        f'  <subtitle>{_x(subtitle)}</subtitle>\n'
        f'  <link rel="self" type="application/atom+xml" href="{a(self_href)}"/>\n'
        f'  <link rel="alternate" type="text/html" href="{a(alt_href)}"/>\n'
        f'  <updated>{_x(updated)}</updated>\n'
        f'  <author><name>{_x(author)}</name></author>\n'
        f'  <generator>karzat</generator>\n'
        + "".join(entries) +
        '</feed>\n')


def entry(entry_id: str, title: str, href: str, updated: str, content_html: str, categories: list[str] = (), summary: str | None = None) -> str:
    cats = "".join(f'    <category term="{a(c)}"/>\n' for c in categories)
    summ = f'    <summary>{_x(summary)}</summary>\n' if summary else ""
    return (
        '  <entry>\n'
        f'    <id>{_x(entry_id)}</id>\n'
        f'    <title>{_x(title)}</title>\n'
        f'    <link rel="alternate" type="text/html" href="{a(href)}"/>\n'
        f'    <updated>{_x(updated)}</updated>\n'
        f'{cats}{summ}'
        f'    <content type="html">{_x(content_html)}</content>\n'
        '  </entry>\n')


CAVEATS = ("Különvélemény: a képviselő leadott szavazata (igen, nem, tartózkodott) eltér a frakciója leadott szavazatainak "
           "többségétől ugyanazon a szavazáson. Döntetlennél nincs többség, így ellene sincs; a jelenlét megállapítása nem számít; "
           "a le nem adott állapotok részvétel, nem egyezés. Számok, nem minősítések.")


def dissent_feed(cycle: int, events: list[dict], updated: str, root: str, cycle_dir: str, self_path: str, alt_path: str,
                 faction: str | None = None, mp: dict | None = None) -> str:
    """The cycle's dissent feed, or one faction's, or one MP's. `events` newest first, already filtered.
    `root` prefixes every href: the deployed origin + '/', or the relative path from the feed file to the site
    root ('../' for a feed one level below a root-cycle page, '../../' one cycle down) — links then resolve
    against the feed's own URL in every reader, xml:base or not."""
    if mp:
        title = f'karzat · {mp["name"]} — különvéleményei · {cycle}. ciklus'
        fid = f'urn:karzat:{cycle}:kulonvelemeny:kepviselo:{mp["azon"]}'
    elif faction:
        title = f'karzat · {faction} — különvélemények · {cycle}. ciklus'
        fid = f'urn:karzat:{cycle}:kulonvelemeny:frakcio:{slug(faction)}'
    else:
        title = f'karzat · különvélemények · {cycle}. ciklus'
        fid = f'urn:karzat:{cycle}:kulonvelemeny'
    ents = []
    for ev in recent(events, lambda e: e["vote"]["on_date"]):
        v, ds = ev["vote"], ev["dissents"]
        if mp:
            d = ds[0]
            t = f'{v["on_date"]} {v.get("time") or ""} · {" ".join(x for x in [v.get("iromany") or "", v.get("title") or ""] if x).strip() or "Szavazás"} — {POS_HU.get(d["position"], d["position"])}, frakciója ({d["faction"]}) többsége {POS_HU.get(d["plurality"], d["plurality"])}'
        else:
            t = _entry_title(v, ds)
        # one id per (vote, channel): the same vote is a different entry in the cycle, a faction and an MP channel
        eid = f'urn:karzat:{cycle}:kulonvelemeny:{v["slug"]}' + (f':kepviselo:{mp["azon"]}' if mp else f':frakcio:{slug(faction)}' if faction else "")
        ents.append(entry(eid, t, f'{root}{cycle_dir}szavazas/{v["slug"]}.html', rfc3339(v["ts"]),
                          _content_html(v, ds, root, cycle_dir, mp_links=not mp),
                          categories=sorted({d["faction"] for d in ds})))
    return atom(fid, title, CAVEATS, f'{root}{cycle_dir}{self_path}', f'{root}{cycle_dir}{alt_path}', updated, ents)


def votes_feed(cycle: int, votes: list[dict], updated: str, root: str, cycle_dir: str, self_path: str) -> str:
    """Every vote of the cycle (roll calls, secret ballots, quorum checks — each marked), newest first: the plain
    'new votes' channel."""
    ents = []
    for v in recent(votes, lambda x: x["on_date"]):
        subj = " ".join(x for x in [v.get("iromany") or "", v.get("title") or ""] if x).strip() or ("Jelenlét megállapítása" if v.get("kind") == "jelenlet" else "Szavazás")
        mark = " · titkos szavazás" if v.get("secret") else (" · jelenlét megállapítása" if v.get("kind") == "jelenlet" and subj != "Jelenlét megállapítása" else "")
        t = f'{v["on_date"]} {v.get("time") or ""} · {subj}{mark}' + (f' — {v["result"]}' if v.get("result") else "")
        html = f'<p><a href="{a(root + cycle_dir)}szavazas/{a(v["slug"])}.html">{_x(v["on_date"])} {_x(v.get("time") or "")}</a> · {_x(_vote_line(v))}{" · titkos szavazás, névsor nélkül" if v.get("secret") else ""}</p>'
        if v.get("n_dissent"):
            html += f'<p>{v["n_dissent"]} különvélemény.</p>'
        ents.append(entry(f'urn:karzat:{cycle}:szavazas:{v["slug"]}', t, f'{root}{cycle_dir}szavazas/{v["slug"]}.html', rfc3339(v["ts"]), html))
    return atom(f'urn:karzat:{cycle}:szavazasok', f'karzat · szavazások · {cycle}. ciklus',
                "A ciklus minden szavazása, a legfrissebb elöl: tárgy, arányok, szükséges többség, eredmény; a titkos szavazás és a jelenlét megállapítása jelölve. Számok, nem minősítések.",
                f'{root}{cycle_dir}{self_path}', f'{root}{cycle_dir}index.html', updated, ents)
