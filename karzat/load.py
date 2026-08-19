"""Load the cache into SQLite — schema.sql is the contract, normalise.py the mapping.

    python3 -m karzat load [--db data/karzat.sqlite]     # rebuild from data/raw/*
    python3 -m karzat stats                                # counts and a few sanity queries

The database is derived: rebuilt from scratch every time (a temp file swapped in on success),
never edited by hand, git-ignored. Every table is filled through the loader methods below,
which take parsed payloads (dicts) so tests can feed them fixtures instead of the cache.

Beyond the schema tables, the loader fills one derived table:
  vote_faction_majority — for each vote and faction, the faction's plurality position and its
                          share, so "did this MP vote with their faction" is a join, not code.
And it defines a few views (v_vote, v_mp_alignment) for the analytics the README promises.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .majority import RULES, Rule
from .normalise import (
    NameResolver,
    cycle_of_date as _cycle_of_date,
    record_histories,
    parse_iromany,
    parse_iromanyok,
    parse_kepviselo,
    parse_kepviselok,
    parse_szavazas,
    parse_felszolalas,
    parse_felszolalasok,
    parse_szavazasok,
    parse_ulesnap,
    seats_for,
)
from .xmlutil import parse_xml, to_dict, ts_to_slug

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schema.sql"
DEFAULT_DB = ROOT / "data" / "karzat.sqlite"

VIEWS = """
CREATE VIEW IF NOT EXISTS v_vote AS
SELECT v.ts, v.slug, v.on_date, v.kind, v.mode, v.secret, v.result_raw, v.passed,
       v.igen, v.nem, v.tartozkodott, v.present, v.majority_rule, v.needed, v.margin,
       m.iromany, m.title, m.outcome, m.submitter_kind
FROM vote v LEFT JOIN vote_motion m ON m.vote_ts = v.ts;

CREATE VIEW IF NOT EXISTS v_mp_alignment AS
SELECT p.mp_azon, mp.name, p.faction_id,
       COUNT(*)                                                     AS votes_in_roll,
       SUM(CASE WHEN p.position IN ('igen','nem','tartozkodott') THEN 1 ELSE 0 END)   AS cast_votes,
       SUM(CASE WHEN p.position = 'tartozkodott' THEN 1 ELSE 0 END)                    AS abstained,
       SUM(CASE WHEN p.position IN ('nem_szavazott','bejelentett_hianyzo','igazoltan_tavol') THEN 1 ELSE 0 END) AS absent,
       SUM(CASE WHEN p.position IN ('igen','nem','tartozkodott') AND p.position = fm.majority_position THEN 1 ELSE 0 END) AS with_faction,
       SUM(CASE WHEN p.position IN ('igen','nem','tartozkodott') AND p.position <> fm.majority_position THEN 1 ELSE 0 END) AS against_faction
FROM vote_position p
JOIN mp ON mp.azon = p.mp_azon
LEFT JOIN vote_faction_majority fm ON fm.vote_ts = p.vote_ts AND fm.faction_id = p.faction_id
GROUP BY p.mp_azon, mp.name, p.faction_id;
"""

EXTRA_TABLES = """
CREATE TABLE IF NOT EXISTS vote_faction_majority (
    vote_ts            TEXT NOT NULL REFERENCES vote(ts),
    faction_id         TEXT NOT NULL,
    majority_position  TEXT,            -- plurality among igen/nem/tartozkodott within the faction; NULL when tied (a tie is no plurality)
    majority_share     REAL,            -- share of the faction's cast votes
    cast_votes         INTEGER,
    PRIMARY KEY (vote_ts, faction_id)
);
"""


def cycle_number(label: str | None) -> int | None:
    """'2026-' or '2022-2026' -> parlament.hu ciklus number (34 = 1990–94 … 43 = 2026–)."""
    if not label:
        return None
    m = re.match(r"^(\d{4})", label.strip())
    if not m:
        return None
    y = int(m.group(1))
    return 34 + (y - 1990) // 4 if y >= 1990 else None


cycle_of_date = _cycle_of_date        # shared with the normaliser (the resolver needs it too)


def mandate_kind(constituency: str | None) -> str:
    c = (constituency or "").casefold()
    if not c:
        return "other"
    if "országos lista" in c:
        return "lista"
    if "oevk" in c or "választókerület" in c:
        return "egyeni"
    if "nemzetiségi" in c:
        return "nemzetisegi"
    return "other"


class Loader:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.execute("PRAGMA foreign_keys = OFF")   # loading order is ours; integrity checked at the end
        self.stats: Counter = Counter()

    # -- schema -----------------------------------------------------------------------------
    def create_schema(self) -> None:
        self.conn.executescript(SCHEMA.read_text(encoding="utf-8"))   # schema.sql turns foreign_keys ON …
        self.conn.executescript(EXTRA_TABLES)
        self.conn.executescript(VIEWS)
        self.conn.execute("PRAGMA foreign_keys = OFF")                # … loading order is ours; checked in integrity()
        self.conn.execute("INSERT OR IGNORE INTO ciklus(ckl, label, started_on, seats) VALUES (43, '2026–2030', '2026-05-09', 199)")

    def _ciklus(self, ckl: int | None, label: str | None = None) -> None:
        if ckl is not None:
            self.conn.execute("INSERT OR IGNORE INTO ciklus(ckl, label, seats) VALUES (?,?,?)", (ckl, label or str(ckl), 199 if ckl >= 40 else 386))

    def _faction(self, fid: str | None, name: str | None = None) -> None:
        if fid:
            self.conn.execute("INSERT OR IGNORE INTO faction(id, name) VALUES (?, ?)", (fid, name or fid))

    # -- MPs --------------------------------------------------------------------------------
    def load_kepviselok(self, payload: dict[str, Any]) -> int:
        n = 0
        for m in parse_kepviselok(payload):
            self._faction(m["faction"])
            self.conn.execute(
                "INSERT INTO mp(azon, name, photo_url, county, constituency_no) VALUES (?,?,?,?,?) "
                "ON CONFLICT(azon) DO UPDATE SET name=excluded.name, photo_url=excluded.photo_url, "
                "county=excluded.county, constituency_no=excluded.constituency_no",
                (m["p_azon"], m["name"], m["photo_url"], m["county"], m["constituency_no"]))
            n += 1
        self.stats["mp_listed"] += n
        return n

    def load_kepviselo(self, azon: str, payload: dict[str, Any], wikidata_qid: str | None = None) -> None:
        r = parse_kepviselo(payload)
        seat = r["seat"] or {}
        self.conn.execute(
            "INSERT INTO mp(azon, name, photo_url, website, seat_sector, seat_row, seat_no, wikidata_qid, raw_json) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(azon) DO UPDATE SET name=COALESCE(mp.name, excluded.name), photo_url=COALESCE(excluded.photo_url, mp.photo_url), "
            "website=excluded.website, seat_sector=excluded.seat_sector, seat_row=excluded.seat_row, seat_no=excluded.seat_no, "
            "wikidata_qid=COALESCE(excluded.wikidata_qid, mp.wikidata_qid), raw_json=excluded.raw_json",
            (azon, r["name"], r["photo_url"], r["website"], seat.get("sector"), seat.get("row"), seat.get("seat"),
             wikidata_qid, json.dumps(payload, ensure_ascii=False)))
        for f in r["factions"]:
            self._faction(f["faction"])
            ckl = cycle_number(f["ciklus"])
            self._ciklus(ckl, f["ciklus"])
            self.conn.execute("INSERT OR REPLACE INTO mp_faction(mp_azon, faction_id, ckl, from_date, to_date) VALUES (?,?,?,?,?)",
                              (azon, f["faction"], ckl, f["from"], f["to"]))
        for e in r["elections"]:
            ckl = cycle_number(e["ciklus"])
            if ckl is None:
                continue
            self._ciklus(ckl, e["ciklus"])
            self.conn.execute("INSERT OR REPLACE INTO mp_mandate(mp_azon, ckl, kind, constituency, elected_on, mandate_from, mandate_to) VALUES (?,?,?,?,?,?,?)",
                              (azon, ckl, mandate_kind(e["constituency"]), e["constituency"], e["elected_on"], e["mandate_from"], e["mandate_to"]))
        for c in r.get("committees") or []:
            if c.get("committee"):
                self.conn.execute("INSERT OR REPLACE INTO mp_committee(mp_azon, committee, subcommittee, role, from_date, to_date) VALUES (?,?,?,?,?,?)",
                                  (azon, c["committee"], c.get("subcommittee") or "", c.get("role") or "", c.get("from") or "", c.get("to")))
                self.stats["mp_committees"] += 1
        self.stats["mp_records"] += 1

    # -- speeches ---------------------------------------------------------------------------
    def load_felszolalasok(self, payload: dict[str, Any], ckl: int, resolver: "NameResolver | None") -> int:
        """One sitting day's speech list."""
        day = parse_felszolalasok(payload)
        if day["ulnap"] is None:
            return 0
        self._ciklus(ckl)
        n = 0
        for sp in day["speeches"]:
            azon = resolver.resolve(sp["speaker_label"], on_date=day["date"]) if (resolver and sp["speaker_label"]) else None
            self.conn.execute(
                "INSERT OR REPLACE INTO speech(ckl, nap, on_date, ord, seq, event, iromany, speaker_label, mp_azon, faction, kind, role, duration_s, technical) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ckl, day["ulnap"], day["date"], sp["order"], sp["seq"], sp["event"], sp["iromany"], sp["speaker_label"], azon, sp["faction"],
                 sp["kind"], sp["role"], sp["duration_s"], 1 if sp["technical"] else 0))
            n += 1
        self.stats["speeches"] += n
        return n

    def load_felszolalas(self, raw: bytes, ckl: int) -> int:
        """One speech's text."""
        d = parse_felszolalas(raw)
        if d["ulnap"] is None or d["seq"] is None:
            return 0
        self._ciklus(ckl)
        self.conn.execute(
            "INSERT OR REPLACE INTO speech_text(ckl, nap, seq, mp_azon, speaker, position, kind, duration_s, chars, body) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ckl, d["ulnap"], d["seq"], d["azon"], d["speaker"], d["position"], d["kind"], d["duration_s"], d["chars"], "\n\n".join(d["paragraphs"])))
        self.stats["speech_texts"] += 1
        return 1

    # -- sitting days -----------------------------------------------------------------------
    def load_ulesnap(self, payload: dict[str, Any], ckl: int | None = None) -> int:
        """Sitting days; the cycle comes from each day's date unless given (ulesnap.cgi is per cycle)."""
        n = 0
        for d in parse_ulesnap(payload):
            if d["ulnap"] is None or not d["date"]:
                continue
            c = ckl if ckl is not None else cycle_of_date(d["date"])
            self._ciklus(c)
            self.conn.execute("INSERT OR REPLACE INTO sitting_day(ckl, nap, on_date, weekday, session_label, kind) VALUES (?,?,?,?,?,?)",
                              (c, d["ulnap"], d["date"], d["weekday"], d["session"], d["kind"]))
            n += 1
        self.stats["sitting_days"] += n
        return n

    # -- votes ------------------------------------------------------------------------------
    def _vote_row(self, v: dict[str, Any], detail: bool) -> None:
        maj = v.get("majority") or {}
        self.conn.execute(
            "INSERT INTO vote(ts, slug, ckl, on_date, sitting_nap, kind, mode, secret, result_raw, passed, remark, igen, nem, tartozkodott, "
            "osszes_szavazat, jelen_nem_szavazott, nem_szavazott, bejelentett_hianyzo, igazoltan_tavol, present, present_basis, "
            "majority_rule, majority_source, majority_evidence, needed, margin, present_assumed, raw_json) "
            "VALUES (?,?,?,?,(SELECT nap FROM sitting_day WHERE on_date=? LIMIT 1),?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(ts) DO UPDATE SET kind=excluded.kind, mode=excluded.mode, secret=excluded.secret, result_raw=excluded.result_raw, "
            "passed=excluded.passed, remark=excluded.remark, igen=excluded.igen, nem=excluded.nem, tartozkodott=excluded.tartozkodott, "
            "osszes_szavazat=excluded.osszes_szavazat, "
            "jelen_nem_szavazott=COALESCE(excluded.jelen_nem_szavazott, vote.jelen_nem_szavazott), "
            "nem_szavazott=COALESCE(excluded.nem_szavazott, vote.nem_szavazott), "
            "bejelentett_hianyzo=COALESCE(excluded.bejelentett_hianyzo, vote.bejelentett_hianyzo), "
            "igazoltan_tavol=COALESCE(excluded.igazoltan_tavol, vote.igazoltan_tavol), "
            "present=COALESCE(excluded.present, vote.present), present_basis=COALESCE(excluded.present_basis, vote.present_basis), "
            "majority_rule=COALESCE(excluded.majority_rule, vote.majority_rule), majority_source=COALESCE(excluded.majority_source, vote.majority_source), "
            "majority_evidence=COALESCE(excluded.majority_evidence, vote.majority_evidence), needed=COALESCE(excluded.needed, vote.needed), "
            "margin=COALESCE(excluded.margin, vote.margin), present_assumed=COALESCE(excluded.present_assumed, vote.present_assumed), "
            "raw_json=COALESCE(excluded.raw_json, vote.raw_json)",
            (v["ts"], v["slug"], self._vote_ckl(v["on_date"]), v["on_date"], v["on_date"], v["kind"], v["mode"], 1 if v["secret"] else 0,
             v["result_raw"], None if v["passed"] is None else int(v["passed"]), v.get("remark"),
             v["igen"], v["nem"], v["tartozkodott"], v["osszes_szavazat"],
             # the four non-cast counts come from the roll call itself (the totals row aggregates them differently:
             # nemszav_db lumps "Jelen, nem szav." in, igtav_db lumps "Előre bejelentett hiányzó" in)
             (v.get("position_counts") or {}).get("jelen_nem_szavazott") if detail else None,
             (v.get("position_counts") or {}).get("nem_szavazott") if detail else None,
             (v.get("position_counts") or {}).get("bejelentett_hianyzo") if detail else None,
             (v.get("position_counts") or {}).get("igazoltan_tavol") if detail else None,
             v.get("present") if detail else None, v.get("present_basis") if detail else None,
             maj.get("rule"), maj.get("source"), maj.get("evidence"), maj.get("needed"), maj.get("margin"),
             (1 if maj.get("present_assumed") else 0) if maj else None,
             None))
        # motions (list and detail carry the same inditvanyok)
        self.conn.execute("DELETE FROM vote_motion WHERE vote_ts = ?", (v["ts"],))
        for m in v.get("motions", []):
            if not m.get("iromany"):
                continue
            self.conn.execute("INSERT OR REPLACE INTO vote_motion(vote_ts, iromany, title, outcome, submitters, submitter_ids, submitter_kind) VALUES (?,?,?,?,?,?,?)",
                              (v["ts"], m["iromany"], m.get("title"), m.get("outcome"), "; ".join(m.get("submitters") or []),
                               ",".join(m.get("submitter_ids") or []), m.get("submitter_kind")))

    def _vote_ckl(self, on_date: str | None) -> int | None:
        ckl = cycle_of_date(on_date)
        self._ciklus(ckl)
        return ckl

    def load_vote_list(self, payload: dict[str, Any]) -> int:
        n = 0
        for v in parse_szavazasok(payload):
            if not v["ts"]:
                continue
            # list-level majority (present = Összes szavazat) so lists without details still carry a rule
            from .majority import Tally, classify, evaluate
            if v["kind"] == "dontes" and v["igen"] is not None:
                cls = classify(payload_rule=v["mode"])
                try:
                    r = evaluate(cls.rule, Tally(yes=v["igen"], no=v["nem"] or 0, abstain=v["tartozkodott"] or 0,
                                                 present=v["osszes_szavazat"], seats=seats_for(v["on_date"])))
                    v["majority"] = {"rule": r.rule, "source": cls.source, "evidence": cls.evidence, "needed": r.needed,
                                     "margin": r.margin, "present_assumed": r.present_assumed}
                except ValueError:
                    v["majority"] = None
            self._vote_row(v, detail=False)
            n += 1
        self.stats["votes_listed"] += n
        return n

    def load_vote_detail(self, payload: dict[str, Any], resolver: NameResolver | None) -> str | None:
        v = parse_szavazas(payload, resolver=resolver)
        if not v["ts"]:
            return None
        self._vote_row(v, detail=True)
        ts = v["ts"]
        self.conn.execute("DELETE FROM vote_position WHERE vote_ts = ?", (ts,))
        self.conn.execute("DELETE FROM vote_faction_tally WHERE vote_ts = ?", (ts,))
        self.conn.execute("DELETE FROM vote_faction_majority WHERE vote_ts = ?", (ts,))
        for p in v["positions"]:
            if p.get("mp_azon"):
                # a resolved id we have no record for (a former MP): the roll call is evidence enough for a stub row
                self.conn.execute("INSERT OR IGNORE INTO mp(azon, name) VALUES (?, ?)", (p["mp_azon"], p["name"]))
            self.conn.execute("INSERT OR REPLACE INTO vote_position(vote_ts, mp_azon, mp_name, faction_id, position) VALUES (?,?,?,?,?)",
                              (ts, p.get("mp_azon"), p["name_raw"], p["faction"], p["position"]))
            self._faction(p["faction"])
        for t in v["faction_tallies"]:
            self._faction(t["faction"])
            self.conn.execute("INSERT OR REPLACE INTO vote_faction_tally(vote_ts, faction_id, igen, nem, tartozkodott, nem_szavazott, igazoltan_tavol, osszesen) VALUES (?,?,?,?,?,?,?,?)",
                              (ts, t["faction"], t["igen"], t["nem"], t["tartozkodott"], t["nem_szavazott"], t["igazoltan_tavol"], t["osszesen"]))
        # faction plurality among cast positions
        by_f: dict[str, Counter] = {}
        for p in v["positions"]:
            if p["position"] in ("igen", "nem", "tartozkodott") and p["faction"]:
                by_f.setdefault(p["faction"], Counter())[p["position"]] += 1
        for f, c in by_f.items():
            pos, n = c.most_common(1)[0]
            cast = sum(c.values())
            if sum(1 for v_ in c.values() if v_ == n) > 1:
                pos = None                                   # a tie is no plurality — the same rule the site's alignment uses
            self.conn.execute("INSERT OR REPLACE INTO vote_faction_majority(vote_ts, faction_id, majority_position, majority_share, cast_votes) VALUES (?,?,?,?,?)",
                              (ts, f, pos, n / cast if cast else None, cast))
        self.stats["votes_detailed"] += 1
        self.stats["positions"] += len(v["positions"])
        self.stats["unresolved_positions"] += sum(1 for p in v["positions"] if not p.get("mp_azon"))
        return ts

    # -- bills ------------------------------------------------------------------------------
    def _bill_key(self, izon: str, szam: str | None, title: str | None) -> str:
        """izon, or izon#n for repeated izon values with a different szam/title (A/254 × 3 in cycle 43)."""
        rows = self.conn.execute("SELECT key, number, title FROM bill WHERE izon = ? ORDER BY key", (izon,)).fetchall()
        for key, number, t in rows:
            if number == szam and (t or "") == (title or ""):
                return key
        return izon if not rows else f"{izon}#{len(rows) + 1}"

    def load_iromanyok(self, payload: dict[str, Any]) -> int:
        n = 0
        for b in parse_iromanyok(payload):
            if not b["izon"]:
                continue
            key = self._bill_key(b["izon"], b["szam"], b["title"])
            self.conn.execute(
                "INSERT INTO bill(key, izon, number, kind, title, status_type, submitters, submitter_kind) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET number=excluded.number, kind=excluded.kind, title=excluded.title, "
                "status_type=excluded.status_type, submitters=excluded.submitters, submitter_kind=excluded.submitter_kind",
                (key, b["izon"], b["szam"], b["szam_parsed"].get("kind"), b["title"] or "", b["status_type"],
                 "; ".join(b["submitters"]), b["submitter_kind"]))
            n += 1
        self.stats["bills_listed"] += n
        return n

    def load_iromany(self, payload: dict[str, Any]) -> str | None:
        b = parse_iromany(payload)
        if not b["izon"]:
            return None
        pr = b.get("promulgation") or {}
        key = self._bill_key(b["izon"], b["szam"], b["title"])
        # a detail for an izon whose list entry has the same szam but a longer/shorter title still maps to it
        same_szam = self.conn.execute("SELECT key FROM bill WHERE izon = ? AND number = ?", (b["izon"], b["szam"])).fetchone()
        if same_szam:
            key = same_szam[0]
        self.conn.execute(
            "INSERT INTO bill(key, izon, number, kind, type, main_type, nature, title, status, submitted_on, procedure_mode, procedure_kind, "
            "submitters, submitter_kind, promulgation_no, promulgated_as, mk_issue, promulgated_on, days_to_final_vote, days_to_promulgation, text_url, raw_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET type=excluded.type, main_type=excluded.main_type, nature=excluded.nature, title=excluded.title, "
            "status=excluded.status, submitted_on=excluded.submitted_on, procedure_mode=excluded.procedure_mode, procedure_kind=excluded.procedure_kind, "
            "submitters=excluded.submitters, submitter_kind=excluded.submitter_kind, promulgation_no=excluded.promulgation_no, "
            "promulgated_as=excluded.promulgated_as, mk_issue=excluded.mk_issue, promulgated_on=excluded.promulgated_on, "
            "days_to_final_vote=excluded.days_to_final_vote, days_to_promulgation=excluded.days_to_promulgation, text_url=excluded.text_url, raw_json=excluded.raw_json",
            (key, b["izon"], b["szam"], b["szam_parsed"].get("kind"), b["type"], b["main_type"], b["nature"], b["title"] or "", b["status"],
             b["submitted_on"], b["procedure_mode"], b["procedure_kind"], "; ".join(b["submitters"]), b["submitter_kind"],
             pr.get("number"), pr.get("law_ref"), pr.get("mk_issue"), pr.get("date"), b["days_submission_to_final_vote"],
             b["days_submission_to_promulgation"], b["text_url"], json.dumps(payload, ensure_ascii=False)))
        self.conn.execute("DELETE FROM bill_event WHERE bill_key = ?", (key,))
        for i, e in enumerate(b["events"], 1):
            self.conn.execute("INSERT INTO bill_event(bill_key, izon, seq, on_date, text, related, speech) VALUES (?,?,?,?,?,?,?)",
                              (key, b["izon"], i, e["date"], e["text"], e["related"], e["speech"]))
        self.stats["bills_detailed"] += 1
        return b["izon"]

    def record_sync_run(self, notes: str, date_from: str | None = None, date_to: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute("INSERT INTO sync_run(started_at, finished_at, date_from, date_to, live_calls, cache_hits, notes) VALUES (?,?,?,?,?,?,?)",
                          (now, now, date_from, date_to, 0, 0, notes))

    def integrity(self) -> list[str]:
        problems = []
        self.conn.execute("PRAGMA foreign_keys = ON")
        for row in self.conn.execute("PRAGMA foreign_key_check"):
            problems.append(f"fk: {row}")
        return problems


# -- building from the cache ---------------------------------------------------------------------

def _load_xml(path: Path) -> dict[str, Any]:
    root = parse_xml(path.read_bytes())
    return {root.tag: to_dict(root)}


def build_from_cache(cache: Path, db_path: Path, wikidata_snapshot: Path | None = None,
                     since: str | None = None) -> dict[str, Any]:
    """Rebuild db_path from the raw cache. Atomic: writes to a temp file, then replaces."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="karzat-", suffix=".sqlite", dir=str(db_path.parent))
    os.close(fd)
    conn = sqlite3.connect(tmp)
    try:
        L = Loader(conn)
        L.create_schema()
        aliases: dict[str, str] = {}
        qid_by_azon: dict[str, str] = {}
        snaps = []
        if wikidata_snapshot and wikidata_snapshot.exists():
            snaps = sorted(wikidata_snapshot.parent.glob("members_*.json"))   # every cycle snapshot alongside
        for sp in snaps:
            for m in json.loads(sp.read_text(encoding="utf-8"))["members"]:
                for oid in m.get("ogy_ids") or []:
                    qid_by_azon.setdefault(oid, m["qid"])
                if m.get("name_hu") and m.get("ogy_ids"):
                    aliases.setdefault(m["name_hu"], m["ogy_ids"][0])
        histories = record_histories(cache)                 # the API's own spelling + faction history per cycle
        mps = []
        kl = cache / "kepviselok" / "all.xml"
        if kl.exists():
            payload = _load_xml(kl)
            L.load_kepviselok(payload)
            mps = parse_kepviselok(payload)
        for p in sorted((cache / "kepviselo").glob("mp_*.xml")):
            azon = p.stem[3:]
            L.load_kepviselo(azon, _load_xml(p), qid_by_azon.get(azon))
        for p in sorted((cache / "ulesnap").glob("*.xml")):
            L.load_ulesnap(_load_xml(p))
        for p in sorted((cache / "szavazasok").glob("*.xml")):
            if since and p.stem.split("__")[0] < since:
                continue
            L.load_vote_list(_load_xml(p))
        resolver = NameResolver(mps, aliases=aliases, histories=histories) if mps else None
        for p in sorted((cache / "szavazas").glob("*.xml")):
            if since and p.stem[:10] < since:
                continue
            L.load_vote_detail(_load_xml(p), resolver)
        for p in sorted((cache / "felszolalasok").glob("ckl*_nap*.xml")):
            m = re.match(r"ckl(\d+)_nap(\d+)$", p.stem)
            if m:
                L.load_felszolalasok(_load_xml(p), int(m.group(1)), resolver)
        for p in sorted((cache / "felszolalas").glob("ckl*_nap*_f*.xml")):
            m = re.match(r"ckl(\d+)_nap(\d+)_f(\d+)$", p.stem)
            if m:
                try:
                    L.load_felszolalas(p.read_bytes(), int(m.group(1)))
                except Exception:                     # one broken payload must not take the whole build down
                    continue
        il = cache / "iromanyok" / "all.xml"
        if il.exists():
            L.load_iromanyok(_load_xml(il))
        for p in sorted((cache / "iromany").glob("iromany_*.xml")):
            L.load_iromany(_load_xml(p))
        L.record_sync_run("build_from_cache", since, None)
        problems = L.integrity()
        conn.commit()
        stats = dict(L.stats)
        stats["fk_problems"] = problems
    except BaseException:
        conn.close()
        try:
            os.unlink(tmp)                      # a failed build leaves no half-written database behind
        except OSError:
            pass
        raise
    conn.close()
    os.replace(tmp, db_path)
    return stats


def summary(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    try:
        q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
        out = {
            "mp": q("SELECT COUNT(*) FROM mp"), "mp_with_seat": q("SELECT COUNT(*) FROM mp WHERE seat_sector IS NOT NULL"),
            "mp_with_qid": q("SELECT COUNT(*) FROM mp WHERE wikidata_qid IS NOT NULL"),
            "mp_faction_rows": q("SELECT COUNT(*) FROM mp_faction"), "mp_mandates": q("SELECT COUNT(*) FROM mp_mandate"),
            "factions": q("SELECT COUNT(*) FROM faction"), "sitting_days": q("SELECT COUNT(*) FROM sitting_day"),
            "votes": q("SELECT COUNT(*) FROM vote"), "votes_with_roll_call": q("SELECT COUNT(DISTINCT vote_ts) FROM vote_position"),
            "positions": q("SELECT COUNT(*) FROM vote_position"), "positions_unresolved": q("SELECT COUNT(*) FROM vote_position WHERE mp_azon IS NULL"),
            "vote_motions": q("SELECT COUNT(*) FROM vote_motion"), "bills": q("SELECT COUNT(*) FROM bill"),
            "bills_detailed": q("SELECT COUNT(*) FROM bill WHERE raw_json IS NOT NULL"), "bill_events": q("SELECT COUNT(*) FROM bill_event"),
            "faction_majorities": q("SELECT COUNT(*) FROM vote_faction_majority"),
            "speeches": q("SELECT COUNT(*) FROM speech"), "speeches_substantive": q("SELECT COUNT(*) FROM speech WHERE technical = 0"),
            "speech_texts": q("SELECT COUNT(*) FROM speech_text"), "speech_text_chars": q("SELECT COALESCE(SUM(chars), 0) FROM speech_text"),
            "speeches_resolved": q("SELECT COUNT(*) FROM speech WHERE mp_azon IS NOT NULL"), "mp_committee_rows": q("SELECT COUNT(*) FROM mp_committee"),
            "rule_source_disagreements": q("SELECT COUNT(*) FROM vote WHERE passed IS NOT NULL AND needed IS NOT NULL AND ((igen >= needed) <> (passed = 1))"),
        }
        out["by_rule"] = dict(conn.execute("SELECT COALESCE(majority_rule,'—'), COUNT(*) FROM vote GROUP BY 1 ORDER BY 2 DESC").fetchall())
        out["alignment_top_deviators"] = conn.execute(
            "SELECT name, faction_id, cast_votes, against_faction, abstained, absent FROM v_mp_alignment "
            "WHERE cast_votes >= 20 ORDER BY against_faction DESC, name LIMIT 5").fetchall()
        return out
    finally:
        conn.close()
