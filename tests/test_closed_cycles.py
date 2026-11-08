"""The nine closed cycles are rebuilt only on the night something they are built from has changed.

They are 155,000 of the site's 162,000 pages. For as long as the nightly rebuilt all ten cycles on every
moving night, that was twelve minutes of building pages that cannot change when a vote is cast in the
tenth — and worse, because every page picked its footer fact from the whole pool, one member's vote counter
ticking over rewrote about 13,000 closed-cycle pages per changed fact, and the deploy uploaded them again.

The optimisation is only safe if the list of what a closed page depends on is complete. The impressum once
reached 3,060 pages and missed 155,739 because a partial build trusted pages it should have rebuilt; these
tests exist so that the next such dependency is found by the gate, not by a reader.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD_STATE = "4f932a3"            # 3 September 2026: a real, materially different state of every volatile input
PROBE_CYCLE = 35                 # the smallest closed cycle with roll calls: thirteen seconds to build

_TRACER = r'''
import builtins, gzip, io, json, os, pathlib, sys, hashlib
ROOT = pathlib.Path(sys.argv[1]); out = pathlib.Path(sys.argv[2]); cyc = int(sys.argv[3])
seen = set()
def note(p):
    try:
        a = pathlib.Path(os.path.abspath(os.fspath(p)))
        rel = a.relative_to(ROOT)
    except Exception:
        return
    if rel.parts and rel.parts[0] in ("data", "reference", "config"):
        seen.add(str(rel))
_open = builtins.open
def topen(f, mode="r", *a, **k):
    if "r" in mode and isinstance(f, (str, bytes, os.PathLike)): note(f)
    return _open(f, mode, *a, **k)
builtins.open = topen; io.open = topen
_gz = gzip.open
def tgz(f, mode="rb", *a, **k):
    if "r" in mode and isinstance(f, (str, bytes, os.PathLike)): note(f)
    return _gz(f, mode, *a, **k)
gzip.open = tgz
for name in ("read_text", "read_bytes"):
    orig = getattr(pathlib.Path, name)
    def wrap(self, *a, _o=orig, **k):
        note(self); return _o(self, *a, **k)
    setattr(pathlib.Path, name, wrap)
_po = pathlib.Path.open
def tpo(self, mode="r", *a, **k):
    if "r" in mode: note(self)
    return _po(self, mode, *a, **k)
pathlib.Path.open = tpo
import scripts.build_site as b
b.build_cycle(out / b.cycle_dir(cyc), cyc, light=False)
files = {str(p.relative_to(out)): hashlib.sha1(p.read_bytes()).hexdigest() for p in out.rglob("*") if p.is_file()}
print(json.dumps({"read": sorted(seen), "files": files}))
'''


def closed_build(cycle: int, derived: Path | None = None) -> dict:
    """Build one closed cycle in a fresh interpreter, tracing every file it opens under the repository."""
    with tempfile.TemporaryDirectory() as out:
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        env.pop("KARZAT_DERIVED", None)
        if derived is not None:
            env["KARZAT_DERIVED"] = str(derived)
        r = subprocess.run([sys.executable, "-c", _TRACER, str(ROOT), out, str(cycle)], cwd=ROOT, env=env,
                           capture_output=True, text=True)
        if r.returncode:
            raise AssertionError(f"the closed build failed:\n{r.stderr[-2000:]}")
        return json.loads(r.stdout.strip().splitlines()[-1])


class WhatAClosedPageIsBuiltFrom(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not (ROOT / "data" / "derived" / f"votes_index_ckl{PROBE_CYCLE}.json.gz").exists():
            raise unittest.SkipTest("the closed cycle's derived inputs are not here")

    def test_a_closed_build_opens_nothing_the_digest_does_not_account_for(self):
        """Every file it reads is either hashed into the nightly's digest, covered semantically (the open
        roster's ids, the closed-cycle facts), or proven unused by the swap test below. A new kind of input
        that fits none of those is a dependency nobody has decided about — and the night it changes, the
        closed tree would quietly keep the old version."""
        import scripts.build_site as b
        read = set(closed_build(PROBE_CYCLE)["read"])
        allowed = (set(b.CLOSED_PAGE_INPUTS) | set(b.CLOSED_PAGE_SEMANTIC) | set(b.CLOSED_PAGE_UNUSED)
                   | {str(f.relative_to(ROOT)) for f in b.closed_cycle_files()} | {"config/factions.yml"})
        unaccounted = sorted(read - allowed)
        self.assertEqual(unaccounted, [], "a closed cycle's build reads files nobody has classified — hash them "
                                          "into closed_inputs_digest, or prove them unused and list them")
        self.assertTrue(read & set(b.CLOSED_PAGE_SEMANTIC), "the trace saw nothing at all — the tracer is broken")

    def test_the_inputs_called_unused_really_are_unused(self):
        """The claim behind CLOSED_PAGE_UNUSED, re-proved: swap each of those files for its real state on
        3 September — a materially different echo index, ideal points, motions, receipt — rebuild a closed
        cycle against the swapped copy, and require every output byte to be the same."""
        import scripts.build_site as b
        probe = subprocess.run(["git", "cat-file", "-e", OLD_STATE], cwd=ROOT, capture_output=True)
        if probe.returncode:
            self.skipTest(f"{OLD_STATE} is not in this checkout (a shallow clone)")
        real = ROOT / "data" / "derived"
        with tempfile.TemporaryDirectory() as t:
            same, swapped = Path(t, "same"), Path(t, "swapped")
            for d in (same, swapped):
                d.mkdir()
                for f in real.iterdir():
                    if f.is_file():
                        (d / f.name).symlink_to(f)
            changed_inputs = 0
            for rel in b.CLOSED_PAGE_UNUSED:
                name = Path(rel).name
                old = subprocess.run(["git", "show", f"{OLD_STATE}:{rel}"], cwd=ROOT, capture_output=True)
                if old.returncode:
                    continue
                (swapped / name).unlink(missing_ok=True)
                (swapped / name).write_bytes(old.stdout)
                changed_inputs += old.stdout != (real / name).read_bytes()
            self.assertGreaterEqual(changed_inputs, 4, "the old state is too close to today to prove anything")
            a = closed_build(PROBE_CYCLE, same)
            z = closed_build(PROBE_CYCLE, swapped)
        bypass = [r for r in a["read"] + z["read"] if r.startswith("data/derived/")]
        self.assertEqual(bypass, [], "something read the real data/derived past KARZAT_DERIVED, so the swap "
                                     "proved nothing about it")
        diff = sorted(k for k in a["files"] if z["files"].get(k) != a["files"][k])
        self.assertEqual(len(a["files"]), len(z["files"]))
        self.assertEqual(diff, [], f"{len(diff)} closed-cycle files changed when an 'unused' input did")


class TheSemanticClaimsAreProvedToo(unittest.TestCase):
    """tenyek.json and mps.json are read by every closed build and are hashed only in part: the facts held by
    a closed cycle, the open roster's ids. That is a claim about how they are used, and a claim nobody checks
    is how the footer came to pull from the whole pool in the first place. So: change everything in them a
    closed page is NOT supposed to see, rebuild a closed cycle, and require it to be byte-for-byte the same."""

    def test_open_cycle_facts_and_open_member_details_do_not_reach_a_closed_page(self):
        import scripts.build_site as b
        real = ROOT / "data" / "derived"
        if not (real / f"votes_index_ckl{PROBE_CYCLE}.json.gz").exists():
            self.skipTest("the closed cycle's derived inputs are not here")
        with tempfile.TemporaryDirectory() as t:
            same, moved = Path(t, "same"), Path(t, "moved")
            for d in (same, moved):
                d.mkdir()
                for f in real.iterdir():
                    if f.is_file():
                        (d / f.name).symlink_to(f)
            facts = json.loads((real / "tenyek.json").read_text(encoding="utf-8"))
            touched = 0
            for f in facts["facts"]:
                if f not in b.closed_facts_from([f]):
                    f["hu"] = f["hu"] + " — megváltozott"
                    f["numbers"] = {k: (v + 1 if isinstance(v, int) else v) for k, v in (f.get("numbers") or {}).items()}
                    touched += 1
            facts["facts"].append({"hu": "Egy új, a nyitott ciklusról szóló tény.", "scope": "43. ciklus",
                                   "href": "", "holder_cycle": None, "numbers": {}})
            (moved / "tenyek.json").unlink()
            (moved / "tenyek.json").write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")
            mps = json.loads((real / "mps.json").read_text(encoding="utf-8"))
            for m in mps["mps"].values():
                if isinstance(m, dict):
                    m["committees"] = ["Változott Bizottság"]
                    m["name"] = (m.get("name") or "") + "*"
            (moved / "mps.json").unlink()
            (moved / "mps.json").write_text(json.dumps(mps, ensure_ascii=False), encoding="utf-8")
            self.assertGreater(touched, 3)
            a = closed_build(PROBE_CYCLE, same)
            z = closed_build(PROBE_CYCLE, moved)
        diff = sorted(k for k in a["files"] if z["files"].get(k) != a["files"][k])
        self.assertEqual(diff, [], f"{len(diff)} closed-cycle files changed although only open-cycle facts and "
                                   f"open members' details did, e.g. {diff[:3]}")


class TheFooterFactOfAClosedPage(unittest.TestCase):

    def test_a_closed_cycle_carries_only_records_held_by_a_closed_cycle(self):
        from scripts.build_site import closed_facts_from, CURRENT_CYCLE
        pool = [{"hu": "a", "holder_cycle": 38}, {"hu": "b", "holder_cycle": None},
                {"hu": "c", "holder_cycle": CURRENT_CYCLE}, {"hu": "d"}]
        self.assertEqual([f["hu"] for f in closed_facts_from(pool)], ["a"])

    def test_a_closed_page_and_an_open_page_draw_from_different_pools(self):
        import scripts.build_site as b
        closed = {f["hu"] for f in b.closed_facts()}
        self.assertTrue(closed, "no fact is held by a closed cycle; the closed pages would carry none")
        for path in (f"ckl38/szavazas/x{i}.html" for i in range(40)):
            html = b.fact_for(path, 2, closed=True)
            self.assertTrue(any(c[:30] in __import__("html").unescape(html) for c in closed), path)

    def test_the_facts_carry_their_holder_as_a_field_not_as_prose(self):
        """The scope label "38. ciklus" reads like a promise that the fact belongs to cycle 38 forever. It
        does not — the busiest sitting day is cycle 38's until the open cycle has a busier one — and parsing
        prose for a structural fact is how a label edit would silently change what closed pages show."""
        d = json.loads((ROOT / "data" / "derived" / "tenyek.json").read_text(encoding="utf-8"))
        self.assertTrue(all("holder_cycle" in f for f in d["facts"]), "a fact without a holder_cycle field")


class TheNightlysDigest(unittest.TestCase):
    """The digest must move when a closed page's input moves and stay put when only the open cycle does."""

    def setUp(self):
        import scripts.build_site as b
        import scripts.nightly as n
        self.b, self.n = b, n
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "data" / "derived").mkdir(parents=True)
        real = ROOT / "data" / "derived"
        for f in real.iterdir():
            if f.is_file():
                (self.tmp / "data" / "derived" / f.name).symlink_to(f)
        for rel in b.CLOSED_PAGE_INPUTS:
            (self.tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.tmp / rel).symlink_to(ROOT / rel)
        self.keep = (n.ROOT, b.DERIVED)
        n.ROOT, b.DERIVED = self.tmp, self.tmp / "data" / "derived"
        self.fp = n.builder_fingerprint
        n.builder_fingerprint = lambda: "fixed"
        self.base = n.closed_inputs_digest()

    def tearDown(self):
        self.n.ROOT, self.b.DERIVED = self.keep
        self.n.builder_fingerprint = self.fp
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rewrite(self, name: str, mutate) -> str:
        p = self.tmp / "data" / "derived" / name
        data = json.loads(p.read_text(encoding="utf-8"))
        mutate(data)
        p.unlink()
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return self.n.closed_inputs_digest()

    def test_it_moves_when_a_closed_record_falls(self):
        def beat_the_record(d):
            for f in d["facts"]:
                if f.get("holder_cycle") and f["holder_cycle"] != self.b.CURRENT_CYCLE:
                    f["hu"] += " (megdőlt)"
                    break
        self.assertNotEqual(self._rewrite("tenyek.json", beat_the_record), self.base)

    def test_it_stays_put_when_only_an_open_cycle_fact_moves(self):
        """Zsigó Róbert's vote count ticks every sitting night. That is exactly the change that used to
        rewrite 13,000 closed pages, and exactly the one that must no longer reach them."""
        def tick(d):
            for f in d["facts"]:
                if not f.get("holder_cycle"):
                    f["hu"] += " (+1)"
        self.assertEqual(self._rewrite("tenyek.json", tick), self.base)

    def test_it_moves_when_someone_new_joins_the_open_roster(self):
        def join(d):
            d["mps"]["zz99"] = {"name": "Új Képviselő"}
        self.assertNotEqual(self._rewrite("mps.json", join), self.base)

    def test_it_stays_put_when_an_open_members_details_change(self):
        """Only the ids reach a closed page. A member's committees or speech count changing is open-cycle
        news and must not cost a full build."""
        def detail(d):
            k = next(iter(d["mps"]))
            d["mps"][k]["note_for_the_test"] = "changed"
        self.assertEqual(self._rewrite("mps.json", detail), self.base)

    def test_it_moves_when_a_closed_cycle_file_is_rewritten(self):
        f = self.b.closed_cycle_files()[0]
        p = self.tmp / "data" / "derived" / f.name
        data = p.read_bytes()
        p.unlink()
        p.write_bytes(data + b" ")
        self.assertNotEqual(self.n.closed_inputs_digest(), self.base)


class TheCorpusDatabaseIsRebuiltOnlyWhenItsInputsMove(unittest.TestCase):
    """data/karzat.sqlite is a pure function of the raw payloads, the Wikidata snapshots, the schema and the
    loader's code. It feeds one step. Rebuilding it from identical inputs is nine minutes for nothing; NOT
    rebuilding it from changed inputs is the footer's facts going stale, which happened once already."""

    def setUp(self):
        import scripts.nightly as n
        self.n = n
        self.root = Path(tempfile.mkdtemp())
        for rel, body in (("data/raw/szavazas/a.xml", b"<a/>"), ("data/raw/kepviselo/mp_x.xml", b"<m/>"),
                          ("reference/wikidata/members_ckl43.json", b"{}"), ("karzat/load.py", b"# v1"),
                          ("schema.sql", b"CREATE TABLE vote(ts TEXT);")):
            f = self.root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(body)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _db(self, with_vote=True):
        import sqlite3
        con = sqlite3.connect(self.root / "data" / "karzat.sqlite")
        if with_vote:
            con.execute("CREATE TABLE vote(ts TEXT)")
        con.commit(); con.close()

    def test_a_rewrite_with_the_same_bytes_is_not_a_change(self):
        """sync-mps rewrites every member record nightly with the bytes it already had. By date that is a
        change every night and the skip would never fire."""
        d1, files = self.n.load_inputs_digest(self.root)
        self.n.save_load_sidecar(d1, files, self.root)
        f = self.root / "data/raw/kepviselo/mp_x.xml"
        f.write_bytes(f.read_bytes())
        os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 10**9))
        self.assertEqual(self.n.load_inputs_digest(self.root)[0], d1)

    def test_a_changed_payload_is_a_change(self):
        d1, files = self.n.load_inputs_digest(self.root)
        self.n.save_load_sidecar(d1, files, self.root)
        (self.root / "data/raw/szavazas/a.xml").write_bytes(b"<a v='1'/>")
        self.assertNotEqual(self.n.load_inputs_digest(self.root)[0], d1)

    def test_the_loaders_own_code_is_an_input(self):
        d1, _ = self.n.load_inputs_digest(self.root)
        (self.root / "karzat/load.py").write_bytes(b"# v2")
        self.assertNotEqual(self.n.load_inputs_digest(self.root)[0], d1)

    def test_current_means_built_from_these_inputs_and_actually_a_database(self):
        d1, files = self.n.load_inputs_digest(self.root)
        self.assertFalse(self.n.corpus_db_is_current(d1, self.root), "no database at all")
        (self.root / "data" / "karzat.sqlite").write_bytes(b"")
        self.n.save_load_sidecar(d1, files, self.root)
        self.assertFalse(self.n.corpus_db_is_current(d1, self.root), "a zero-byte file is not a database")
        (self.root / "data" / "karzat.sqlite").unlink()
        self._db(with_vote=False)
        self.assertFalse(self.n.corpus_db_is_current(d1, self.root), "no vote table, nothing derive_facts can use")
        (self.root / "data" / "karzat.sqlite").unlink()
        self._db()
        self.assertTrue(self.n.corpus_db_is_current(d1, self.root))
        self.assertFalse(self.n.corpus_db_is_current("other", self.root), "built from different inputs")

    def test_a_failed_load_does_not_record_itself_as_current(self):
        src = (ROOT / "scripts" / "nightly.py").read_text(encoding="utf-8")
        block = src[src.index('if step[:3] == ["-m", "karzat", "load"]:'):]
        block = block[:block.index("continue\n        if run(py + step")]
        self.assertIn("derive_failed.append", block)
        self.assertLess(block.index("derive_failed.append"), block.index("save_load_sidecar"),
                        "the sidecar would be written whether or not the build succeeded")
        self.assertIn("elif not dry:", block)


class TheResolverRemembersPerCycle(unittest.TestCase):
    """Memoised on (label, cycle) because the date reaches the decision only through its cycle. Proved on
    the real corpus by rebuilding the database with and without the memo and comparing all 17 tables,
    17,483,486 voting positions included: identical. This pins the key."""

    def test_two_dates_in_one_cycle_share_an_answer_and_two_cycles_do_not(self):
        from karzat.normalise import NameResolver
        calls = []
        r = NameResolver([{"name": "Kiss János", "faction": "TISZA", "p_azon": "k1"}], None,
                         {"k1": {"name": "Kiss János", "factions": [("2026-", "TISZA")]}})
        orig = r._resolve
        r._resolve = lambda label, on_date=None: (calls.append(on_date), orig(label, on_date))[1]
        r.resolve("Kiss János (TISZA)", "2026-06-01")
        r.resolve("Kiss János (TISZA)", "2026-10-01")
        self.assertEqual(len(calls), 1, "a second date in the same cycle was resolved again")
        r.resolve("Kiss János (TISZA)", "2015-06-01")
        self.assertEqual(len(calls), 2, "another cycle must be resolved on its own")


if __name__ == "__main__":
    unittest.main()
