"""The watchdog that does not live on the laptop.

    python3 -m scripts.watch_live                    # check the live heartbeat, print the verdict
    python3 -m scripts.watch_live --github           # …and open / update / close the stale-site issue
    python3 -m scripts.watch_live --github --test    # open and immediately close a test issue

**Why it exists.** The site's whole promise is that it never misstates its own freshness, and from
September to November it stood still for two months without anybody knowing. Every alarm it had started
on the machine that runs the nightly: the SNS alert used the same AWS credentials whose failure it was
supposed to report, and the local marker needs the laptop to be awake to write it. A pipeline cannot be
trusted to report its own death. So this runs somewhere else entirely — a scheduled GitHub Actions job —
and reads only what any reader could read: the live site.

**What it reads.** Not the page's "Frissítve" stamp: when the House does not sit, a healthy pipeline
rightly rebuilds nothing for up to `refresh_days`, so the stamp can be a week old with nothing wrong. It
reads `heartbeat.json`, which the nightly writes at the end of every run that ended well, published or
not. Its age is the time since the pipeline last finished properly.

**How it speaks.** A GitHub issue in the public repository, which notifies the owner by email, and a red
workflow run, which GitHub mails separately to whoever last edited the schedule. Two channels, neither of
which shares anything with the laptop, AWS, or the nightly.

Standard library only, so the runner installs nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

SITE = "https://ogykarzat.hu"
HEARTBEAT_URL = f"{SITE}/heartbeat.json"
LABEL = "stale-site"
UA = "karzat-freshness-watch/1.0 (+https://github.com/abognar-git/karzat)"

# The nightly runs once a day. One missed night is a closed laptop and not worth an email; a second is not
# an accident. 60 hours sits past two missed runs and short of a third.
MAX_SILENCE = timedelta(hours=60)
# Beyond the producer's own promise (refresh_days, read from the heartbeat) a day and a half of slack,
# because the forced rebuild that keeps that promise only happens on the next nightly after it expires.
PUBLISH_SLACK = timedelta(hours=36)
DEFAULT_REFRESH_DAYS = 7


def _when(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _age(td: timedelta) -> str:
    h = td.total_seconds() / 3600
    return f"{h:.0f} hours" if h < 72 else f"{h / 24:.1f} days"


def evaluate(hb: dict | None, now: datetime, site_status: int | None = 200) -> list[str]:
    """Every reason to worry, in plain words. An empty list means the site is as fresh as it promised."""
    problems: list[str] = []
    if site_status != 200:
        problems.append(f"{SITE}/ answered {site_status or 'nothing'} — the site itself is not being served.")
    if hb is None:
        problems.append("heartbeat.json could not be read from the live site, so nothing can be said about "
                        "when the pipeline last finished.")
        return problems
    checked = _when(hb.get("checked_at"))
    if checked is None:
        problems.append(f"the heartbeat carries no readable checked_at ({hb.get('checked_at')!r}).")
    elif checked - now > timedelta(hours=1):
        # A heartbeat from the future means the writer's clock is wrong, and a wrong clock is also how the
        # AWS signatures can fail — it is worth saying rather than quietly treating as fresh.
        problems.append(f"the heartbeat is dated {checked.isoformat()}, {_age(checked - now)} in the future — "
                        "the machine that writes it has a wrong clock.")
    elif now - checked > MAX_SILENCE:
        problems.append(f"the nightly last finished properly {_age(now - checked)} ago "
                        f"({checked.isoformat()}); it runs daily.")
    try:
        refresh = float(hb.get("refresh_days") or DEFAULT_REFRESH_DAYS)
    except (TypeError, ValueError):
        refresh = DEFAULT_REFRESH_DAYS
    if refresh <= 0:
        refresh = DEFAULT_REFRESH_DAYS
    published = _when(hb.get("published_at"))
    limit = timedelta(days=refresh) + PUBLISH_SLACK
    if published is None:
        problems.append("the heartbeat says the site has never been published.")
    elif now - published > limit:
        problems.append(f"the site was last published {_age(now - published)} ago ({published.isoformat()}); "
                        f"the pipeline promises at least every {refresh:g} days. The nightly is finishing but "
                        "not publishing.")
    return problems


def decide(problems: list[str], open_issue: dict | None) -> str:
    """open · update · close · none — what the issue tracker should do about it."""
    if problems:
        return "update" if open_issue else "open"
    return "close" if open_issue else "none"


def fetch_heartbeat(url: str = HEARTBEAT_URL) -> tuple[dict | None, int | None]:
    site_status = None
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{SITE}/", headers={"User-Agent": UA}),
                                    timeout=30) as r:
            site_status = r.status
    except urllib.error.HTTPError as e:
        site_status = e.code
    except Exception:
        site_status = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8")), site_status
    except Exception:
        return None, site_status


# ---- GitHub ------------------------------------------------------------------------------------------

def _gh(method: str, path: str, token: str, body: dict | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"https://api.github.com{path}", data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else None
        except ValueError:
            return e.code, raw.decode("utf-8", "replace")


def _body(problems: list[str], hb: dict | None, now: datetime) -> str:
    lines = ["The external watchdog could not confirm that ogykarzat.hu is as fresh as it promises.", ""]
    lines += [f"- {p}" for p in problems]
    lines += ["", f"Checked {now.isoformat(timespec='seconds')} from GitHub Actions, outside the machine "
              "that runs the nightly.", "",
              "Where to look first: `~/Library/Logs/karzat-sync.log` on the build machine — the head of the "
              "most recent run names the previous run's failure — and `data/last_failure.json` beside it.",
              "", "This issue closes itself on the first check that finds the site healthy again.", "",
              "<details><summary>Heartbeat as served</summary>", "", "```json",
              json.dumps(hb, ensure_ascii=False, indent=1) if hb else "(unreadable)", "```", "</details>"]
    return "\n".join(lines)


def act_on_github(problems: list[str], hb: dict | None, now: datetime, repo: str, token: str,
                  test: bool = False) -> str:
    _gh("POST", f"/repos/{repo}/labels", token,
        {"name": LABEL, "color": "b60205", "description": "the live site is not as fresh as it promises"})
    if test:
        st, issue = _gh("POST", f"/repos/{repo}/issues", token, {
            "title": "Freshness watchdog: test alert (closes itself)", "labels": [LABEL],
            "body": "A deliberate test of the alert path. If this reached your inbox, so will a real one.\n\n"
                    + _body(problems or ["(nothing is actually wrong — this is a test)"], hb, now)})
        if st == 201 and isinstance(issue, dict):
            _gh("PATCH", f"/repos/{repo}/issues/{issue['number']}", token, {"state": "closed"})
            return f"test issue #{issue['number']} opened and closed"
        raise SystemExit(f"could not open the test issue: {st} {issue}")
    st, found = _gh("GET", f"/repos/{repo}/issues?state=open&labels={LABEL}&per_page=5", token)
    open_issue = found[0] if st == 200 and isinstance(found, list) and found else None
    action = decide(problems, open_issue)
    if action == "open":
        st, issue = _gh("POST", f"/repos/{repo}/issues", token, {
            "title": "ogykarzat.hu is stale — the nightly is not finishing or not publishing",
            "labels": [LABEL], "body": _body(problems, hb, now)})
        if st != 201:
            raise SystemExit(f"could not open the issue: {st} {issue}")
        return f"opened #{issue['number']}"
    if action == "update":
        # The body is rewritten, not commented on: one email when it opens, not one a day while it stays.
        _gh("PATCH", f"/repos/{repo}/issues/{open_issue['number']}", token, {"body": _body(problems, hb, now)})
        return f"updated #{open_issue['number']}"
    if action == "close":
        _gh("POST", f"/repos/{repo}/issues/{open_issue['number']}/comments", token,
            {"body": f"Healthy again at {now.isoformat(timespec='seconds')}: the heartbeat is fresh and the "
                     "site is published within its promise. Closing."})
        _gh("PATCH", f"/repos/{repo}/issues/{open_issue['number']}", token, {"state": "closed"})
        return f"closed #{open_issue['number']}"
    return "nothing to do"


def keep_schedule_alive(repo: str, token: str, workflow: str) -> str:
    """GitHub switches off a public repository's scheduled workflows after 60 days without repository
    activity — and the nightly never pushes, so two quiet months, the exact failure this exists for, would
    disable the watchdog too. Enabling the workflow through the API on every run restarts that clock without
    a dummy commit in the history."""
    st, _ = _gh("PUT", f"/repos/{repo}/actions/workflows/{workflow}/enable", token)
    return f"schedule kept alive ({st})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--github", action="store_true", help="open, update or close the stale-site issue")
    ap.add_argument("--test", action="store_true", help="with --github: open and close a test issue")
    ap.add_argument("--url", default=HEARTBEAT_URL)
    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    hb, site_status = fetch_heartbeat(args.url)
    problems = evaluate(hb, now, site_status)
    print(json.dumps({"checked": now.isoformat(timespec="seconds"), "site_status": site_status,
                      "heartbeat": hb, "problems": problems}, ensure_ascii=False, indent=1))
    if args.github:
        repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
        if not repo or not token:
            raise SystemExit("--github needs GITHUB_REPOSITORY and GITHUB_TOKEN")
        print(act_on_github(problems, hb, now, repo, token, test=args.test))
        print(keep_schedule_alive(repo, token, os.environ.get("WATCH_WORKFLOW", "freshness-watch.yml")))
    # A red run is the second channel: GitHub mails a failed scheduled run to whoever last edited the cron.
    return 1 if problems and not args.test else 0


if __name__ == "__main__":
    sys.exit(main())
