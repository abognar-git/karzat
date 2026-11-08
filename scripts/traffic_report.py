"""Who actually reads the site: a weekly report from CloudFront's own access logs.

    python3 -m scripts.traffic_report --profile karzat              # the last seven days
    python3 -m scripts.traffic_report --profile karzat --days 30    # as far back as the logs go (they expire at 30)

scripts/traffic.py counts requests, robot and not; this counts readers. No cookie, no third party: the access logs CloudFront already writes to s3://karzat-logs/cf/ are
the only source. Of 234,076 requests on the first two logged days 97% were crawlers — PerplexityBot alone
125,512 — so a request count says nothing about readers. Since the counter (JS_COUNT in build_site.py) went live a
reader is a browser whose page actually ran and asked for /assets/olvaso.txt; before it, a reader was a browser
that fetched a page and the site's stylesheet or script — a guess that let disguised programs through, and the
report says which of the two it used.

Our own visits are left out by address prefix, listed one per line in data/traffic/self.txt (not committed):
the in-app browser that checks every deploy runs on the same network as the owner's phone and once looked
like the site's keenest reader.

The report (Markdown) and its numbers (JSON) are written to data/traffic/, which stays out of git — they are
aggregates, but they are the owner's, not the repository's. Addresses never leave the run.
"""

from __future__ import annotations

import argparse
import collections
import gzip
import html
import json
import re
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "traffic"
SITE = ROOT / "site"
BUCKET, PREFIX = "karzat-logs", "cf/"
OWN_HOSTS = ("ogykarzat.hu", "ogykarzat.com", "orszaggyuleskarzat.hu")
BOT = re.compile(r"bot|crawl|spider|slurp|fetch|curl|python|wget|go-http|headless|monitor|uptime|preview|scan|java/"
                 r"|okhttp|httpclient|lighthouse|bytespider|gpt|claude|perplexity|ahrefs|semrush|petal|yandex|bing"
                 r"|facebookexternalhit|whatsapp|telegram|discord|skype|axios|node-fetch|libwww|zgrab|masscan", re.I)
ASSET = re.compile(r"/assets/karzat\.(css|js)$")
BEACON = "/assets/olvaso.txt"     # what JS_COUNT requests
HEAVY = 150                      # pages per visitor per window; past this it is a program
PROBE = re.compile(r"/\.|\.env|config|wp-|\.php|/api/|/admin|/cgi|actuator|server-status|debug/|/v2/|/-/", re.I)   # scanners looking for leaked secrets


def unq(s: str) -> str:
    return urllib.parse.unquote(urllib.parse.unquote(s or "-"))


def is_page(stem: str) -> bool:
    return stem.endswith("/") or stem.endswith(".html")


def read_logs(sess, since: date) -> list[dict]:
    s3 = sess.client("s3")
    rows: list[dict] = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=PREFIX):
        for o in page.get("Contents", []):
            if o["LastModified"].date() < since:
                continue
            body = gzip.decompress(s3.get_object(Bucket=BUCKET, Key=o["Key"])["Body"].read()).decode("utf-8", "replace")
            fields: list[str] = []
            for line in body.splitlines():
                if line.startswith("#Fields:"):
                    fields = line.split()[1:]
                elif line and not line.startswith("#"):
                    r = dict(zip(fields, line.split("\t")))
                    if r.get("date", "") >= since.isoformat():
                        rows.append(r)
    return rows


def title_of(stem: str) -> str:
    """The page's own <title>, from the built site, so the report names pages the way a reader sees them."""
    p = SITE / stem.lstrip("/")
    if stem.endswith("/"):
        p = p / "index.html"
    try:
        m = re.search(r"<title>([^<]*)", p.read_text(encoding="utf-8")[:4000])
        return html.unescape(m.group(1)).replace(" · karzat", "").replace("karzat — ", "").strip() if m else stem
    except OSError:
        return stem


def analyse(rows: list[dict], self_prefixes: list[str]) -> dict:
    ua = lambda r: unq(r.get("cs(User-Agent)"))
    mine = lambda r: any(r["c-ip"].startswith(p) for p in self_prefixes)
    bots = collections.Counter()
    loaded_assets: set[tuple[str, str]] = set()
    for r in rows:
        u = ua(r)
        if BOT.search(u):
            if is_page(r["cs-uri-stem"]):
                m = re.search(r"([A-Za-z][\w-]*(?:bot|crawler|spider)[\w-]*)", u, re.I)
                bots[m.group(1) if m else u[:40]] += 1
            continue
        if ASSET.search(r["cs-uri-stem"]):
            loaded_assets.add((r["c-ip"], u))
    # A page reached from another site is a person following a link even when the assets came from cache.
    referred = {(r["c-ip"], ua(r)) for r in rows if is_page(r["cs-uri-stem"]) and not BOT.search(ua(r))
                and (h := urllib.parse.urlparse(unq(r.get("cs(Referer)"))).netloc.lower()) and not h.endswith(OWN_HOSTS)}
    # whoever probed for a leaked secret is a scanner, whatever browser it claims to be
    scanners = {(r["c-ip"], ua(r)) for r in rows if PROBE.search(unq(r["cs-uri-stem"]))}
    beacons = [r for r in rows if r["cs-uri-stem"] == BEACON and not BOT.search(ua(r))]
    if beacons:
        # The counter (JS_COUNT in build_site.py): one request per page a person opened, made by the page's
        # own script. Each becomes a page view of the path it names, from the site it names.
        method = "számláló"
        views = []
        for r in beacons:
            # CloudFront writes the query percent-encoded once more ("%2F" arrives as "%252F"): one unquote takes
            # off the log's layer, parse_qs the browser's.
            raw = r.get("cs-uri-query", "-")
            q = urllib.parse.parse_qs(urllib.parse.unquote(raw) if raw != "-" else "")
            p = (q.get("p") or ["/"])[0]
            if (r["c-ip"], ua(r)) in scanners or mine(r) or not p.startswith("/"):
                continue
            views.append({**r, "cs-uri-stem": p, "cs(Referer)": "https://" + q["r"][0] + "/" if q.get("r") else "-",
                          "cs-uri-query": "-", "sc-status": "200"})
        readers = {(r["c-ip"], ua(r)) for r in views}
    else:
        method = "napló"
        readers = (loaded_assets | referred) - scanners
        views = [r for r in rows if r["cs-method"] == "GET" and r["sc-status"] in ("200", "304") and is_page(r["cs-uri-stem"])
                 and (r["c-ip"], ua(r)) in readers and not mine(r)]
    # A "browser" that reads hundreds of pages a day is a scraper wearing a browser's name: 595 pages from one
    # address on the first logged day. Counted apart, not as the site's most devoted reader.
    per = collections.Counter((r["c-ip"], ua(r)) for r in views)
    heavy = {k for k, c in per.items() if c > HEAVY}
    disguised = sum(per[k] for k in heavy)
    views = [r for r in views if (r["c-ip"], ua(r)) not in heavy]
    by_day: dict[str, set] = collections.defaultdict(set)
    views_by_day = collections.Counter()
    for r in views:
        by_day[r["date"]].add((r["c-ip"], ua(r)))
        views_by_day[r["date"]] += 1
    visitors = {(r["c-ip"], ua(r)) for r in views}
    referrers = collections.Counter()
    landings: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in views:
        host = urllib.parse.urlparse(unq(r.get("cs(Referer)"))).netloc.lower()
        if host and not host.endswith(OWN_HOSTS):
            host = re.sub(r"^(www\.|m\.|l\.|lm\.)", "", host)
            referrers[host] += 1
            landings[host][r["cs-uri-stem"]] += 1
    searches = collections.Counter()
    for r in (views if method == "napló" else [r for r in rows if (r["c-ip"], ua(r)) in readers and is_page(r["cs-uri-stem"])]):
        if r["cs-uri-stem"].startswith("/kereses") and r.get("cs-uri-query", "-") != "-":
            q = urllib.parse.parse_qs(unq(r["cs-uri-query"])).get("q")
            if q:
                searches[q[0].strip().lower()] += 1
    mobile = sum(1 for v in visitors if re.search(r"Mobile|Android|iPhone", v[1]))
    pages = [r for r in rows if r["cs-method"] == "GET" and is_page(r["cs-uri-stem"])]
    missing = collections.Counter(r["cs-uri-stem"] for r in rows if r["sc-status"] == "404" and is_page(r["cs-uri-stem"])
                                  and urllib.parse.urlparse(unq(r.get("cs(Referer)"))).netloc.lower().endswith(OWN_HOSTS)
                                  and not mine(r) and not PROBE.search(unq(r["cs-uri-stem"])))   # a broken link on our own page
    probes = sum(1 for r in rows if r["sc-status"] in ("403", "404") and PROBE.search(unq(r["cs-uri-stem"])))
    return {
        "method": method,
        "requests": len(rows),
        "page_requests": len(pages),
        "bot_page_requests": sum(bots.values()),
        "visitors": len(visitors),
        "views": len(views),
        "mobile_share": round(mobile / len(visitors), 2) if visitors else 0,
        "by_day": {d: {"visitors": len(by_day[d]), "views": views_by_day[d]} for d in sorted(by_day)},
        "top_pages": collections.Counter(r["cs-uri-stem"] for r in views).most_common(15),
        "referrers": referrers.most_common(15),
        "landings": {h: c.most_common(3) for h, c in landings.items()},
        "searches": searches.most_common(15),
        "bots": bots.most_common(8),
        "missing": missing.most_common(8),
        "disguised": {"visitors": len(heavy), "views": disguised},
        "probes": probes,
    }


def report(a: dict, since: date, until: date) -> str:
    n = lambda x: f"{x:,}".replace(",", " ")
    out = [f"# karzat — látogatók, {since.isoformat()} – {until.isoformat()}", ""]
    out.append(f"**{n(a['visitors'])} olvasó, {n(a['views'])} oldalmegtekintés.** "
               f"Mobilon: {round(100 * a['mobile_share'])}%. Az összes {n(a['requests'])} lekérésből "
               f"{n(a['bot_page_requests'])} oldal-lekérés volt gépé (kereső- és MI-robotok).")
    if a["method"] == "számláló":
        out += ["", "Olvasó: böngésző, amely ténylegesen megnyitott és megjelenített egy oldalt — az oldal saját számlálója jelezte "
                "(süti nélkül), és nem robotnak mondja magát. Egy olvasó egy cím és böngésző párosa a héten; aki telefonról és gépről "
                "is jött, kettőnek számít. A saját látogatásaink nincsenek benne.", ""]
    else:
        out += ["", "Olvasó: böngésző, amely az oldalt és a stílusát vagy szkriptjét is letöltötte, és nem robotnak mondja magát. "
                "A visszatérő olvasót, akinek a böngészője már tárolja ezeket, nem mindig látja — ez alsó becslés, és a böngészőnek "
                "álcázott programokat sem szűri ki mind. A saját látogatásaink nincsenek benne.", ""]
    out += ["## Naponta", "", "| nap | olvasó | megtekintés |", "|---|---:|---:|"]
    out += [f"| {d} | {v['visitors']} | {v['views']} |" for d, v in a["by_day"].items()]
    out += ["", "## Mit néztek", "", "| oldal | megtekintés |", "|---|---:|"]
    out += [f"| [{title_of(p)}](https://ogykarzat.hu{p}) | {c} |" for p, c in a["top_pages"]]
    out += ["", "## Honnan jöttek", ""]
    if a["referrers"]:
        out += ["| oldal | látogatás | ide érkeztek |", "|---|---:|---|"]
        out += [f"| {h} | {c} | {', '.join(title_of(p) for p, _ in a['landings'][h])} |" for h, c in a["referrers"]]
        out += ["", "A többiek közvetlenül jöttek: beírt cím, könyvjelző, alkalmazásból megnyitott link — ezek nem mondják meg a forrásukat."]
    else:
        out.append("Senki nem érkezett más oldalról.")
    if a["searches"]:
        out += ["", "## Mit kerestek", ""] + [f"- {q} ({c})" for q, c in a["searches"]]
    if a["missing"]:
        out += ["", "## Törött linkek a saját oldalainkon", ""] + [f"- `{p}` ({c})" for p, c in a["missing"]]
    out += ["", "## Robotok (oldal-lekérés)", ""] + [f"- {b}: {n(c)}" for b, c in a["bots"]]
    if a["disguised"]["visitors"]:
        out.append(f"- böngészőnek álcázott letöltő: {a['disguised']['visitors']} cím, {n(a['disguised']['views'])} oldal (nincs az olvasók között)")
    if a["probes"]:
        out.append(f"- titkokat kereső szkennerek (.git, .aws, config…): {n(a['probes'])} próbálkozás, mind elutasítva")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default="karzat")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--sns-topic", default="", help="also send the report to this SNS topic")
    args = ap.parse_args(argv)
    import boto3
    sess = boto3.Session(profile_name=args.profile)
    until = datetime.now(timezone.utc).date()
    since = until - timedelta(days=args.days - 1)
    self_file = OUT / "self.txt"
    self_prefixes = [l.strip() for l in self_file.read_text().splitlines() if l.strip() and not l.startswith("#")] if self_file.exists() else []
    rows = read_logs(sess, since)
    if not rows:
        print("no log lines in that window", file=sys.stderr)
        return 1
    since = min(date.fromisoformat(r["date"]) for r in rows)          # the logs may not reach back that far
    a = analyse(rows, self_prefixes)
    text = report(a, since, until)
    OUT.mkdir(parents=True, exist_ok=True)
    stem = f"{until.isocalendar()[0]}-W{until.isocalendar()[1]:02d}"
    (OUT / f"{stem}.md").write_text(text, encoding="utf-8")
    (OUT / f"{stem}.json").write_text(json.dumps({"since": since.isoformat(), "until": until.isoformat(), **a}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(text)
    if args.sns_topic:
        sess.client("sns", region_name="eu-central-1").publish(
            TopicArn=args.sns_topic, Subject=f"karzat: heti látogatók ({a['visitors']} olvasó)"[:100], Message=text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
