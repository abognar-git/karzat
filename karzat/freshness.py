"""The freshness contract: what the site is allowed to say about how current its data is.

A site that pulses "live" while its ingest has been dead for weeks is worse than one that says
plainly when it last looked, because the first kind of wrong is invisible. This module
exists so that karzat can never do that. It takes three facts and produces a status and a
sentence, and the sentence is what the page shows — not a badge.

Facts (all injectable, all optional):
  sitting_days    the sitting days of the current cycle as known from ulesnap.cgi
  newest_vote_at  timestamp of the newest vote held locally (max idopont in the cache/DB)
  last_sync_at    when the last sync run finished successfully
  now             the moment of rendering (injected for tests and for static builds)

Rules of the contract:
  1. Never say "live", "élő", "real-time" or any synonym. The data is a scheduled snapshot.
  2. Always name the date of the newest vote held ("Votes through 21 July 2026").
  3. Missing data is counted in *sitting days*, not calendar days: if Parliament sat after
     the newest vote we hold, say on which days ("Parliament sat on 22 and 23 July; those
     votes are not here yet"). Recess is not staleness.
  4. Always state the age of the last sync; past `stale_after` say the sync has not run.
  5. When the sitting-day list is unavailable, say that it cannot tell — do not imply currency.

Status vocabulary (for styling only; the sentence carries the meaning):
  current   newest vote is from the latest sitting day that has occurred, sync not stale
  behind    at least one sitting day has occurred after the newest vote we hold
  stale     sync older than stale_after (may combine with behind)
  unknown   no sitting-day information; currency cannot be asserted
  empty     no votes held at all
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

BUDAPEST = ZoneInfo("Europe/Budapest")      # vote timestamps are Budapest local time; so is everything here


def now_budapest() -> datetime:
    return datetime.now(BUDAPEST)


STALE_AFTER_DEFAULT = timedelta(hours=48)   # a daily job that misses two runs is a problem

_HU_MONTHS = ("január", "február", "március", "április", "május", "június",
              "július", "augusztus", "szeptember", "október", "november", "december")
_EN_MONTHS = ("January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December")

FORBIDDEN_WORDS = ("live", "élő", "real-time", "realtime", "valós idej")


def fmt_date_hu(d: date) -> str:
    """2026. július 21."""
    return f"{d.year}. {_HU_MONTHS[d.month - 1]} {d.day}."


def fmt_date_en(d: date) -> str:
    """21 July 2026"""
    return f"{d.day} {_EN_MONTHS[d.month - 1]} {d.year}"


def _hu_day_en(n: int) -> str:
    """Day number with the temporal suffix (-án/-én) by vowel harmony: 1-jén, 2-án, 3-án, 4-én, …"""
    if n == 1:
        return "1-jén"                                     # elsején
    if n == 2:
        return "2-án"                                      # másodikán — but 12-én, 22-én (…kettedikén)
    last = n % 10
    if last == 0:
        return f"{n}-én" if n == 10 else f"{n}-án"        # tizedikén; huszadikán, harmincadikán
    return f"{n}-{'án' if last in (3, 6, 8) else 'én'}"   # harmadikán, hatodikán, nyolcadikán; the rest -én


def _hu_day_ig(n: int) -> str:
    return "1-jéig" if n == 1 else f"{n}-ig"


def fmt_date_hu_ig(d: date) -> str:
    """2026. július 21-ig"""
    return f"{d.year}. {_HU_MONTHS[d.month - 1]} {_hu_day_ig(d.day)}"


def fmt_date_hu_en(d: date) -> str:
    """2026. július 22-én"""
    return f"{d.year}. {_HU_MONTHS[d.month - 1]} {_hu_day_en(d.day)}"


def _fmt_days_hu(days: list[date]) -> str:
    """Days with the temporal suffix: '2026. július 22-én és 23-án' — full dates across months."""
    if len(days) == 1:
        return fmt_date_hu_en(days[0])
    if all(d.year == days[0].year and d.month == days[0].month for d in days):
        nums = [_hu_day_en(d.day) for d in days]
        return f"{days[0].year}. {_HU_MONTHS[days[0].month - 1]} {', '.join(nums[:-1])} és {nums[-1]}"
    return ", ".join(fmt_date_hu_en(d) for d in days[:-1]) + f" és {fmt_date_hu_en(days[-1])}"


def _fmt_days_en(days: list[date]) -> str:
    if len(days) == 1:
        return fmt_date_en(days[0])
    if all(d.year == days[0].year and d.month == days[0].month for d in days):
        nums = [str(d.day) for d in days]
        return f"{', '.join(nums[:-1])} and {nums[-1]} {_EN_MONTHS[days[0].month - 1]} {days[0].year}"
    return ", ".join(fmt_date_en(d) for d in days[:-1]) + f" and {fmt_date_en(days[-1])}"


def _age_en(delta: timedelta) -> str:
    s = int(delta.total_seconds())
    if s < 0:
        return "in the future"
    if s < 90 * 60:
        m = max(1, s // 60)
        return f"{m} minute{'s' if m != 1 else ''} ago"
    h = s // 3600
    if h < 48:
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = s // 86400
    return f"{d} day{'s' if d != 1 else ''} ago"


def _age_hu(delta: timedelta) -> str:
    s = int(delta.total_seconds())
    if s < 0:
        return "a jövőben"
    if s < 90 * 60:
        return f"{max(1, s // 60)} perce"
    h = s // 3600
    if h < 48:
        return f"{h} órája"
    return f"{s // 86400} napja"


@dataclass(frozen=True)
class Freshness:
    status: str                       # current | behind | stale | unknown | empty
    newest_vote_at: datetime | None
    newest_vote_day: date | None
    latest_sitting_day: date | None   # latest sitting day that has occurred (<= now)
    missed_sitting_days: tuple[date, ...]   # sitting days after newest_vote_day, up to now
    scheduled_sitting_days: tuple[date, ...] # sitting days after now (announced, not missed)
    last_sync_at: datetime | None
    sync_age: timedelta | None
    sync_stale: bool
    sentence_en: str
    sentence_hu: str
    computed_at: datetime

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["newest_vote_at"] = self.newest_vote_at.isoformat() if self.newest_vote_at else None
        d["newest_vote_day"] = self.newest_vote_day.isoformat() if self.newest_vote_day else None
        d["latest_sitting_day"] = self.latest_sitting_day.isoformat() if self.latest_sitting_day else None
        d["missed_sitting_days"] = [x.isoformat() for x in self.missed_sitting_days]
        d["scheduled_sitting_days"] = [x.isoformat() for x in self.scheduled_sitting_days]
        d["last_sync_at"] = self.last_sync_at.isoformat() if self.last_sync_at else None
        d["sync_age_seconds"] = int(self.sync_age.total_seconds()) if self.sync_age is not None else None
        del d["sync_age"]
        d["computed_at"] = self.computed_at.isoformat()
        return d


def assess(*, sitting_days: Iterable[date] | None, newest_vote_at: datetime | None,
           last_sync_at: datetime | None, now: datetime,
           stale_after: timedelta = STALE_AFTER_DEFAULT, absolute_sync: bool = False) -> Freshness:
    """Compute the freshness verdict. Pure; safe to call at build time and in tests.

    absolute_sync=True prints the sync as a Budapest timestamp instead of an age ("15 perce"):
    the age is right on a page rebuilt at every sync and wrong on a static page read later.
    """
    today = now.date()
    days = sorted(set(sitting_days)) if sitting_days is not None else None
    occurred = [d for d in days if d <= today] if days is not None else None
    scheduled = tuple(d for d in days if d > today) if days is not None else ()
    latest_sitting = occurred[-1] if occurred else None
    newest_day = newest_vote_at.date() if newest_vote_at else None

    sync_age = (now - last_sync_at) if last_sync_at else None
    sync_stale = sync_age is None or sync_age > stale_after

    missed: tuple[date, ...] = ()
    if occurred is not None and newest_day is not None:
        missed = tuple(d for d in occurred if d > newest_day)

    if newest_vote_at is None:
        status = "empty"
    elif not occurred:
        # A list was fetched and holds no day that has happened — an empty list, or a cycle whose first
        # sitting is still ahead. `missed` is then empty for want of anything to compare, and the verdict
        # used to come out "current": the site asserting currency on the strength of knowing nothing.
        # "unknown" is what the docstring at the top of this file already reserves for exactly that state,
        # and the difference between the two is the difference between measuring and assuming.
        status = "unknown"
    elif days is None:
        status = "unknown"
    elif missed:
        status = "behind"
    elif sync_stale:
        status = "stale"
    else:
        status = "current"
    if status == "behind" and sync_stale:
        status = "behind"     # behind dominates; the sentence still mentions the stale sync

    stamp = last_sync_at.astimezone(BUDAPEST) if (absolute_sync and last_sync_at) else None
    en = _sentence_en(status, newest_day, missed, scheduled, latest_sitting, sync_age, sync_stale, stamp)
    hu = _sentence_hu(status, newest_day, missed, scheduled, latest_sitting, sync_age, sync_stale, stamp)
    for w in FORBIDDEN_WORDS:
        assert w not in en.casefold() and w not in hu.casefold(), f"forbidden word in sentence: {w}"
    return Freshness(status, newest_vote_at, newest_day, latest_sitting, missed, scheduled,
                     last_sync_at, sync_age, sync_stale, en, hu, now)


def _sentence_en(status, newest_day, missed, scheduled, latest_sitting, sync_age, sync_stale, stamp=None) -> str:
    parts: list[str] = []
    if status == "empty":
        parts.append("No votes are held yet.")
    else:
        parts.append(f"Votes through {fmt_date_en(newest_day)}.")
        if status == "unknown":
            parts.append("Whether Parliament has sat since cannot be told: the sitting-day list is unavailable.")
        elif missed:
            n = len(missed)
            parts.append(
                f"Parliament sat on {_fmt_days_en(list(missed))}; the votes from "
                f"{'that day are' if n == 1 else f'those {n} sitting days are'} not here yet."
            )
        elif latest_sitting is not None:
            parts.append(f"That was the most recent sitting day.")
        if scheduled:
            parts.append(f"Next sitting day announced: {fmt_date_en(scheduled[0])}.")
    if sync_age is None:
        parts.append("The sync has never completed.")
    elif stamp is not None:
        parts.append(f"Synced {fmt_date_en(stamp.date())} {stamp:%H:%M} Budapest time." + (" The sync has not run on schedule." if sync_stale else ""))
    else:
        parts.append(f"Synced {_age_en(sync_age)}." + (" The sync has not run on schedule." if sync_stale else ""))
    return " ".join(parts)


def _sentence_hu(status, newest_day, missed, scheduled, latest_sitting, sync_age, sync_stale, stamp=None) -> str:
    parts: list[str] = []
    if status == "empty":
        parts.append("Még nincs betöltött szavazás.")
    else:
        parts.append(f"Szavazások {fmt_date_hu_ig(newest_day)}.")
        if status == "unknown":
            parts.append("Hogy ülésezett-e azóta az Országgyűlés, nem állapítható meg: az ülésnapok listája nem érhető el.")
        elif missed:
            n = len(missed)
            parts.append(
                f"Az Országgyűlés ülésezett {_fmt_days_hu(list(missed))}; "
                f"{'annak a napnak' if n == 1 else f'annak a {n} ülésnapnak'} a szavazásai még hiányoznak."
            )
        elif latest_sitting is not None:
            parts.append("Ez volt a legutóbbi ülésnap.")
        if scheduled:
            parts.append(f"Következő bejelentett ülésnap: {fmt_date_hu(scheduled[0])}")
    if sync_age is None:
        parts.append("A szinkronizálás még sosem futott le.")
    elif stamp is not None:
        parts.append(f"Frissítve: {fmt_date_hu(stamp.date())} {stamp:%H:%M} (budapesti idő)." + (" A frissítés nem futott le a menetrend szerint." if sync_stale else ""))
    else:
        parts.append(f"Frissítve {_age_hu(sync_age)}." + (" A frissítés nem futott le a menetrend szerint." if sync_stale else ""))
    return " ".join(parts)


# -- adapters ---------------------------------------------------------------------------

def sitting_days_from_ulesnap(payload: Any) -> list[date]:
    """Pull dates out of a to_dict()'d ulesnap.cgi payload.

    The manual only says the payload has <ulesnap> elements with "ülésnap adatai"; the date
    field's name is unknown until a real response is seen (VERIFY). This adapter therefore
    accepts any attribute or child whose value looks like a Hungarian date (ÉÉÉÉ.HH.NN, with or
    without a trailing dot) under an element named ulesnap, and returns the distinct dates.
    Tighten it once the real key is known.
    """
    import re
    from .xmlutil import as_list

    found: set[date] = set()
    date_re = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})\.?$")

    def harvest(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str):
                    m = date_re.match(v.strip())
                    if m:
                        found.add(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
                else:
                    harvest(v)
        elif isinstance(node, list):
            for x in node:
                harvest(x)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "ulesnap":
                    for item in as_list(v):
                        harvest(item)
                else:
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(payload)
    return sorted(found)
