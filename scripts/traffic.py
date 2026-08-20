"""What the server logged, counted — and nothing about who.

    python3 -m scripts.traffic                 # the last 7 days
    python3 -m scripts.traffic --days 30
    python3 -m scripts.traffic --no-write      # print, write nothing

The site carries no tracker: no analytics script, no cookie, no third party, no consent banner, because there is
nothing to consent to. What exists is the thing a web server produces anyway — CloudFront's access log, delivered
to s3://karzat-logs/cf/ and expired by a lifecycle rule after 30 days.

**The client IP never leaves this process.** It is read, used to count distinct sources, and dropped; the file
this writes holds counts and no address, no user-agent string tied to a request, nothing that identifies a reader.
That is the whole reason to aggregate here rather than to query the raw log whenever a number is wanted.

**These are requests, not people.** A static site of 240,000 pages is catnip to crawlers, and on the first full
day the distribution served 37,842 requests to a site nobody had been told about yet. So every figure is split:
what identified itself as a robot, and what did not. "Did not identify itself as a robot" is not the same as "is
a person" — it is the honest ceiling, and it is labelled as such rather than called a visitor count.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "derived" / "traffic.json"
BUCKET, PREFIX = "karzat-logs", "cf/"

# Matched against the user agent, longest-standing names first. A robot that does not say so lands in the other
# bucket, which is why that bucket is never called "people".
BOTS = [
    ("Googlebot", r"Googlebot|Google-InspectionTool|Storebot-Google"),
    ("Bingbot", r"bingbot|BingPreview|adidxbot"),
    ("GPTBot", r"GPTBot"),
    ("ClaudeBot", r"ClaudeBot|Claude-Web|anthropic-ai"),
    ("CCBot", r"CCBot"),
    ("PerplexityBot", r"PerplexityBot"),
    ("Applebot", r"Applebot"),
    ("YandexBot", r"YandexBot"),
    ("DuckDuckBot", r"DuckDuckBot|DuckAssistBot"),
    ("SeznamBot", r"SeznamBot"),
    ("AhrefsBot", r"AhrefsBot"),
    ("SemrushBot", r"SemrushBot"),
    ("MJ12bot", r"MJ12bot"),
    ("Bytespider", r"Bytespider"),
    ("Meta", r"meta-externalagent|facebookexternalhit"),
    ("Twitter", r"Twitterbot"),
    ("feed reader", r"Feedly|Inoreader|NewsBlur|Miniflux|FreshRSS|Tiny Tiny RSS"),
    ("monitor", r"UptimeRobot|Pingdom|StatusCake|curl/|wget|python-requests|okhttp|Go-http-client"),
    ("other robot", r"[Bb]ot\b|[Ss]pider|[Cc]rawler|scrapy|HeadlessChrome"),
]
BOTS = [(name, re.compile(pat)) for name, pat in BOTS]


def classify(ua: str) -> str | None:
    for name, pat in BOTS:
        if pat.search(ua):
            return name
    return None


def parse_log(raw: bytes) -> list[dict]:
    """CloudFront's standard log: two '#' lines then tab-separated fields named by the second one."""
    text = gzip.decompress(raw).decode("utf-8", "replace")
    fields, rows = None, []
    for line in text.splitlines():
        if line.startswith("#Fields:"):
            fields = line.split(":", 1)[1].split()
            continue
        if line.startswith("#") or not line.strip() or not fields:
            continue
        parts = line.split("\t")
        if len(parts) < len(fields):
            continue
        rows.append(dict(zip(fields, parts)))
    return rows


def unquote(s: str) -> str:
    return re.sub(r"%([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), s or "").replace("+", " ")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--profile", default="karzat")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    import boto3

    s3 = boto3.Session(profile_name=a.profile, region_name="eu-central-1").client("s3")
    since = datetime.now(timezone.utc) - timedelta(days=a.days)

    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=PREFIX):
        for o in page.get("Contents") or []:
            if o["LastModified"] >= since:
                keys.append(o["Key"])
    if not keys:
        print(f"no log files under s3://{BUCKET}/{PREFIX} in the last {a.days} days.")
        print("CloudFront delivers them within a few hours of the requests; logging was switched on 2026-08-20.")
        return 0

    by_day: dict[str, Counter] = {}
    bots, paths, referrers, status, agents_seen = Counter(), Counter(), Counter(), Counter(), Counter()
    sources: set[str] = set()          # distinct client IPs, counted then discarded — never written out
    total = bot_hits = bytes_out = 0

    for k in keys:
        for r in parse_log(s3.get_object(Bucket=BUCKET, Key=k)["Body"].read()):
            ua = unquote(r.get("cs(User-Agent)", ""))
            day = r.get("date", "")
            kind = classify(ua)
            total += 1
            bytes_out += int(r.get("sc-bytes") or 0)
            d = by_day.setdefault(day, Counter())
            d["requests"] += 1
            if kind:
                bots[kind] += 1
                bot_hits += 1
                d["robot"] += 1
            else:
                d["not_a_robot"] += 1
                sources.add(r.get("c-ip", ""))
                path = r.get("cs-uri-stem", "")
                if not path.startswith("/assets/"):
                    paths[path] += 1
                ref = unquote(r.get("cs(Referer)", "-"))
                if ref and ref != "-":
                    referrers[re.sub(r"^https?://([^/]+).*$", r"\1", ref)] += 1
            status[r.get("sc-status", "")] += 1
            agents_seen[(kind or "nem nevezi magát robotnak")] += 1

    out = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "window_days": a.days, "log_files": len(keys),
        "requests": total, "robot_requests": bot_hits, "other_requests": total - bot_hits,
        "distinct_sources_not_robot": len(sources),      # a count, never the addresses
        "bytes": bytes_out,
        "by_day": {d: dict(c) for d, c in sorted(by_day.items())},
        "robots": dict(bots.most_common()),
        "top_pages_not_robot": dict(paths.most_common(25)),
        "referrers_not_robot": dict(referrers.most_common(15)),
        "status": dict(sorted(status.items())),
        "note": ("A számok kérések, nem emberek. A „nem robot” csoport azt jelenti, hogy a kérés user agentje nem "
                 "nevezte magát robotnak — ez felső becslés, nem látogatószám. A nyers napló IP-címet tartalmaz, "
                 "ez az összesítés nem: 30 nap után a napló magától törlődik."),
    }
    if not a.no_write:
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    pct = (100 * bot_hits / total) if total else 0
    print(f"{len(keys)} log files · {total:,} requests · {bytes_out / 1e6:,.0f} MB")
    print(f"  robots: {bot_hits:,} ({pct:.0f}%) · not identifying as one: {total - bot_hits:,} "
          f"from {len(sources):,} distinct sources")
    for name, n in bots.most_common(8):
        print(f"    {n:8,}  {name}")
    if paths:
        print("  most-requested pages, robots excluded:")
        for p, n in paths.most_common(8):
            print(f"    {n:8,}  {p}")
    if referrers:
        print("  where they came from:")
        for rf, n in referrers.most_common(6):
            print(f"    {n:8,}  {rf}")
    if not a.no_write:
        print(f"written {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
