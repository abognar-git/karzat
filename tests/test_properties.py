"""Property-based tests: the shapes I did not think of.

Every defect this project has shipped belongs to one class. Not a wrong formula, not a misread rule — a data
shape nobody imagined. A NULL in the first row of a result, so a column of numbers was judged to be text. A
DECIMAL that Arrow hands over as its unscaled integer, so 1.5 printed as 15. A field that usually holds one
bill number and, on 28,106 rows, holds two joined with " · ". A per-cycle file that is simply absent, and a
fallback that quietly substituted the current cycle's data for a parliament that sat in 1990.

Every one of those shipped with the whole suite green, because an example test checks the case its author
thought of, and the case its author thought of was never the problem.

So these tests do not name inputs. They describe the shape of a valid input and state something that must be
true of every one of them, and Hypothesis invents a few hundred — reaching first for the empty string, the
single element, the zero, the maximum, the duplicate, the out-of-order. When one breaks, it shrinks the
failure to the smallest input that still breaks it, which is the difference between knowing there is a bug
and knowing what the bug is.

Two rules I set myself while writing these, both learned the hard way in one afternoon:

  **A property that cannot fail is worse than no property.** It reads as coverage and proves nothing. Where a
  generator could drift into never producing the interesting case, there is an explicit assertion that it
  does — see the `_saw` counters.

  **The first failures will be mine.** Writing these against `karzat.majority` by hand, two properties broke
  and both were my own: one a stale message in the harness, one a rule about an empty House that the real
  code never asks. That is the normal experience and it is the point — the tool makes you say precisely what
  you believe, and half of what you believe turns out to be imprecise.
"""

from __future__ import annotations

import math
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hypothesis import HealthCheck, assume, given, settings
    from hypothesis import strategies as st
except ImportError:                                            # pragma: no cover - the suite still runs
    raise unittest.SkipTest("hypothesis is not installed — pip install -r requirements-dev.txt")

from karzat import analytics, xmlutil
from karzat.api import cache_key
from karzat.majority import Rule, Tally, base_of, evaluate, needed, needed_for_base
# At module level, not in a class body: a name bound inside a class becomes an unbound method, so
# `cut(t, n)` would pass the TestCase as the first argument. It cost one run to remember.
from scripts.build_site import cut, esc, file_size, hu_dec, hu_num
from scripts.derive_parquet import first_bill

# Deterministic and quick enough to sit in the ordinary suite; a seed means a failure found on CI can be
# reproduced here rather than described.
SETTINGS = settings(max_examples=300, deadline=None,
                    suppress_health_check=[HealthCheck.too_slow])


# ── the shapes ────────────────────────────────────────────────────────────────────────────────────────────

DATES = st.dates(min_value=date(1990, 1, 1), max_value=date(2035, 12, 31))
# The API's timestamp format is `ÉÉÉÉ.HH.NN.ÓÓ:PP:MM` — seconds, no finer. Hypothesis found that immediately
# and shrank it to 2000-01-01 00:00:00.000001, the smallest datetime in range with a microsecond to lose. The
# generator says so rather than the round-trip quietly rounding: a strategy that hides a limitation is how a
# green test comes to mean nothing.
TIMES = st.datetimes(min_value=datetime(1990, 1, 1), max_value=datetime(2035, 12, 31, 23, 59, 59)) \
    .map(lambda t: t.replace(microsecond=0))


@st.composite
def tallies(draw, seats=None):
    """A vote's arithmetic, in the proportions the House can actually produce.

    `seats` spans the real chambers (199 today, 386 before 2014) and the degenerate 0 and 1, because a
    threshold computed against an empty base is exactly the kind of input nobody writes an example for.
    """
    s = seats if seats is not None else draw(st.sampled_from([0, 1, 2, 199, 386]))
    present = draw(st.integers(min_value=0, max_value=max(0, s)))
    yes = draw(st.integers(min_value=0, max_value=present))
    no = draw(st.integers(min_value=0, max_value=present - yes))
    return Tally(yes=yes, no=no, abstain=present - yes - no,
                 not_voting=draw(st.integers(min_value=0, max_value=max(0, s - present))),
                 present=present, seats=s)


class DatesAndSlugsRoundTrip(unittest.TestCase):
    """Every date the API speaks must survive a trip through our formatting and back.

    The site's addresses are built from timestamps — `szavazas/2026-05-09T10-45-47.html` — so a slug that
    does not reverse is a page nobody can link to, and a date that does not reverse is a vote filed on the
    wrong day. Neither would raise anything; both would just be quietly wrong.
    """

    @given(DATES)
    @SETTINGS
    def test_a_date_survives_being_written_and_read(self, d):
        self.assertEqual(xmlutil.parse_date(xmlutil.fmt_date(d)), d)

    @given(TIMES)
    @SETTINGS
    def test_a_timestamp_survives_being_written_and_read(self, t):
        self.assertEqual(xmlutil.parse_ts(xmlutil.fmt_ts(t)), t)

    @given(st.integers(min_value=1, max_value=999_999))
    @SETTINGS
    def test_the_format_carries_seconds_and_says_so(self, us):
        """The resolution the round-trip does not keep, asserted rather than assumed. If the API ever starts
        sending fractions of a second, this fails and the parser is told about it deliberately."""
        t = datetime(2026, 5, 9, 10, 45, 47, us)
        self.assertEqual(xmlutil.parse_ts(xmlutil.fmt_ts(t)), t.replace(microsecond=0))

    @given(TIMES)
    @SETTINGS
    def test_a_timestamp_survives_becoming_a_url_and_coming_back(self, t):
        ts = xmlutil.fmt_ts(t)
        slug = xmlutil.ts_to_slug(ts)
        self.assertEqual(xmlutil.slug_to_ts(slug), ts)
        self.assertNotIn(":", slug, "a colon in a slug is not safe in every filesystem or URL")
        self.assertNotIn(" ", slug)


class RepairIsSafeToRunTwice(unittest.TestCase):
    """The old cycles' payloads carry unescaped `"` inside attributes and bare `&` in text, and the parser
    repairs both before ElementTree sees them.

    A repair that is not idempotent is a trap: the nightly re-derives from cache, so any payload may be
    repaired more than once over its life, and a second pass that changes the text again would corrupt what
    the first pass fixed. `&amp;` must not become `&amp;amp;`.
    """

    # Both take bytes, not str: the payload is parsed from bytes so ElementTree honours the declared
    # ISO-8859-2 of the older CGIs. Passing text raised TypeError on the first run — my mistake, and the kind
    # a property test surfaces in one second because it calls the function rather than reading it.
    TEXT = st.binary(max_size=120)

    @given(TEXT)
    @SETTINGS
    def test_repairing_ampersands_twice_changes_nothing_the_second_time(self, s):
        once = xmlutil.repair_bare_ampersands(s)
        self.assertEqual(xmlutil.repair_bare_ampersands(once), once)

    @given(TEXT)
    @SETTINGS
    def test_repairing_quotes_twice_changes_nothing_the_second_time(self, s):
        once = xmlutil.repair_attribute_quotes(s)
        self.assertEqual(xmlutil.repair_attribute_quotes(once), once)

    @given(st.binary(max_size=60))
    @SETTINGS
    def test_an_entity_that_is_already_correct_is_left_alone(self, s):
        already = s.replace(b"&", b"&amp;")
        self.assertEqual(xmlutil.repair_bare_ampersands(already), already,
                         "a correctly escaped entity was escaped again")

    @given(st.text(alphabet=st.characters(blacklist_categories=("Cs", "Cc"), max_codepoint=0x24F),
                   max_size=80).filter(lambda t: "<" not in t and ">" not in t and "&" not in t))
    @SETTINGS
    def test_the_repaired_text_still_says_what_it_said(self, t):
        """Idempotence alone is not correctness, and a mutation proved it: a repair rewritten to emit
        `&amp;amp;` is still idempotent — the second pass leaves a valid entity alone — while every ampersand
        in the record silently doubles. The eight other mutations I tried were caught; this one walked past.

        So the property is the one the function actually owes: repair it, parse it as XML, and the text must
        read back exactly as it went in. That is what the repair is for."""
        import xml.etree.ElementTree as ET
        raw = f"<r>{t}&</r>".encode("utf-8")
        fixed = xmlutil.repair_bare_ampersands(raw)
        parsed = ET.fromstring(fixed)
        self.assertEqual(parsed.text or "", t + "&",
                         "the repair changed the text rather than only making it parse")


class TheThresholdIsNeverImpossible(unittest.TestCase):
    """What a vote needed to pass is the number the whole site is built to show, so it must be a number that
    could actually be reached.

    The interesting inputs are the degenerate ones — a base of nought, a base of one, a chamber of 386 rather
    than 199 — because a formula written for the ordinary case tends to produce something absurd at the edge
    and nothing complains.
    """

    _saw = {"empty": 0, "full": 0}

    @given(st.sampled_from(list(Rule)), tallies())
    @SETTINGS
    def test_a_threshold_is_never_negative_and_never_out_of_reach(self, rule, t):
        n = needed(rule, t)
        if n is None:
            self.assertEqual(rule, Rule.RELATIV, "only the plurality rule may decline to name a threshold")
            return
        b = base_of(rule, t)
        self.assertGreaterEqual(n, 0, f"{rule.name}: a negative number of votes")
        if b:
            self.assertLessEqual(n, b, f"{rule.name}: needs {n} of a base of {b}")
            self._saw["full"] += 1
        else:
            self._saw["empty"] += 1

    @given(st.sampled_from([r for r in Rule if r is not Rule.RELATIV]),
           st.integers(min_value=0, max_value=400), st.integers(min_value=0, max_value=400))
    @SETTINGS
    def test_a_bigger_chamber_never_needs_fewer_votes(self, rule, a, b):
        """Monotonicity. A threshold that fell as the House grew would be a sign-flip or an integer-division
        mistake, and both are the kind of error that looks plausible in every example anyone would write."""
        lo, hi = min(a, b), max(a, b)
        self.assertLessEqual(needed_for_base(rule, lo), needed_for_base(rule, hi),
                             f"{rule.name}: base {lo} needs more than base {hi}")

    @given(st.sampled_from(list(Rule)), st.integers(min_value=0, max_value=400))
    @SETTINGS
    def test_every_rule_the_enum_names_is_a_rule_the_arithmetic_handles(self, rule, base):
        """Totality. `needed_for_base` ends in `raise AssertionError(rule)`, so a rule added to the enum and
        not to the arithmetic fails here rather than in a nightly build over the whole corpus."""
        needed_for_base(rule, base)                            # must not raise

    @given(st.sampled_from(list(Rule)), tallies())
    @SETTINGS
    def test_the_verdict_and_the_threshold_tell_the_same_story(self, rule, t):
        """`evaluate` and `needed` are two paths to the same claim, and the site prints both side by side."""
        v = evaluate(rule, t)
        n = needed(rule, t)
        if n is None or v is None:
            return
        got = v.get("needed") if isinstance(v, dict) else getattr(v, "needed", None)
        if got is not None:
            self.assertEqual(got, n, f"{rule.name}: the verdict says {got}, the threshold says {n}")

    @classmethod
    def tearDownClass(cls):
        # A property that never meets the interesting case is a green test that proves nothing.
        if cls._saw["full"] == 0:
            raise AssertionError("no tally with a real base was ever generated — the property is vacuous")


class AnIndexStaysInsideItsOwnRange(unittest.TestCase):
    """Cohesion indices are ratios, and a ratio outside [0, 1] means the denominator went somewhere it should
    not have. Both formulas divide by a count that can be zero.
    """

    COUNTS = st.integers(min_value=0, max_value=200)

    @given(COUNTS, COUNTS)
    @SETTINGS
    def test_rice_is_a_fraction(self, yes, no):
        r = analytics.rice(yes, no)
        if r is None:
            self.assertEqual(yes + no, 0, "Rice declined to answer on a non-empty vote")
            return
        self.assertGreaterEqual(r, 0.0)
        self.assertLessEqual(r, 1.0)

    @given(COUNTS, COUNTS, COUNTS)
    @SETTINGS
    def test_agreement_is_a_fraction(self, yes, no, abstain):
        a = analytics.agreement_index(yes, no, abstain)
        if a is None:
            self.assertEqual(yes + no + abstain, 0, "the index declined to answer on a non-empty vote")
            return
        self.assertGreaterEqual(a, 0.0)
        self.assertLessEqual(a, 1.0)

    @given(COUNTS, COUNTS)
    @SETTINGS
    def test_rice_does_not_care_which_side_is_called_yes(self, yes, no):
        """|yes − no| / (yes + no) is symmetric by construction, and a Rice index that changed when the two
        columns were swapped would mean the absolute value had been dropped somewhere."""
        self.assertEqual(analytics.rice(yes, no), analytics.rice(no, yes))

    @given(COUNTS)
    @SETTINGS
    def test_a_unanimous_faction_scores_one(self, n):
        assume(n > 0)
        self.assertAlmostEqual(analytics.rice(n, 0), 1.0)
        self.assertAlmostEqual(analytics.agreement_index(n, 0, 0), 1.0)





class WhatIsPrintedIsWhatIsMeant(unittest.TestCase):
    """The formatters stand between a number in the record and a number on a reader's screen, and that is
    exactly where this project's worst defect of the day lived: a DECIMAL arriving as its unscaled integer, so
    1,5 was printed as 15. Nothing raised. The table simply held a different number from the corpus.

    So each of these asserts the reverse direction — that what is printed reads back as what went in.
    """

    @given(st.floats(min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False),
           st.integers(min_value=0, max_value=6))
    @SETTINGS
    def test_a_decimal_reads_back_as_the_number_it_was(self, x, places):
        """Hungarian writes the decimal comma. Reading the string back must give the rounded input — a
        formatter that dropped a digit, or that used a comma the parser then read as a thousands separator,
        fails here."""
        printed = hu_dec(x, places)
        self.assertNotIn(".", printed, "a full stop in a Hungarian decimal")
        back = float(printed.replace(",", "."))
        self.assertAlmostEqual(back, round(x, places), places=max(0, places - 1))

    @given(st.integers(min_value=-10**12, max_value=10**12))
    @SETTINGS
    def test_a_thousands_separator_never_changes_the_value(self, n):
        printed = hu_num(n)
        self.assertEqual(int(printed.replace("\u00a0", "").replace("-", "-")), n)

    @given(st.integers(min_value=0, max_value=10**11))
    @SETTINGS
    def test_a_file_size_is_never_a_size_nothing_has(self, n):
        """A 33 kB table printed "0,0 MB", which is a size no file has. Any non-empty file must print a
        non-zero figure, whatever unit it lands in."""
        printed = file_size(n)
        if n >= 1000:
            digits = printed.replace("\u00a0", "").split()[0].replace(",", ".")
            self.assertGreater(float(digits), 0.0, f"{n} bytes printed as {printed!r}")

    @given(st.text(max_size=300), st.integers(min_value=1, max_value=120))
    @SETTINGS
    def test_shortening_says_that_it_shortened(self, t, n):
        """A label cut without a mark reads as the whole value. The bar chart's 34-character truncation did
        exactly that until a reader saw a motion title simply stop."""
        out = cut(t, n)
        if out != t.strip():
            self.assertTrue(out.endswith("…"), f"{t.strip()[:40]!r} was shortened without a mark")

    @given(st.text(max_size=200))
    @SETTINGS
    def test_escaping_leaves_nothing_that_could_open_a_tag(self, t):
        """Total, not nearly total. A red-team pass found column names reaching innerHTML unescaped and
        running a handler; this asserts the escaper itself has no gap for any string at all."""
        out = esc(t)
        for ch in ("<", ">"):
            self.assertNotIn(ch, out, f"{ch!r} survived escaping")
        import html as _html
        self.assertEqual(_html.unescape(out), t, "escaping lost or altered the text")


class TheExportKeepsEveryRowItWasGiven(unittest.TestCase):
    """Conservation. Two of today's defects were losses in exactly this shape — a vote's motions reduced to
    `motions[0]`, and a speech's several bill numbers joined into one unmatched string — and both were
    invisible because the surviving value was always present and always plausible.
    """

    @given(st.lists(st.from_regex(r"[THBK]/\d{1,5}", fullmatch=True), min_size=1, max_size=5))
    @SETTINGS
    def test_the_first_bill_is_one_bill(self, bills):
        """`first_bill` exists because a reference can hold several joined with " · ". Whatever it returns
        must be a single number a join can match — never the joined string it was handed."""
        joined = " · ".join(bills)
        got = first_bill(joined)
        self.assertNotIn(" · ", got or "", "the joined string was returned whole")
        self.assertEqual(got, bills[0])

    @given(st.sampled_from(["", "   ", None]))
    @SETTINGS
    def test_an_absent_bill_is_absent_rather_than_empty(self, v):
        """An empty string in a key column joins to nothing and reads as a value. None says "not recorded"."""
        self.assertIsNone(first_bill(v))


class TheChartReadsAResultTheSameWayWhicheverRowIsFirst(unittest.TestCase):
    """The chart's column inference ran on row zero, so a NULL there hid a chart over a result full of
    numbers. The fix was to judge a column across the result — and this asserts the consequence rather than
    the implementation: shuffling the rows must not change what the chart decides to draw.

    The inference is JavaScript, so it runs under node against generated result sets. If node is absent the
    class skips rather than passing quietly, because a silent skip is how this kind of check rots.
    """

    @classmethod
    def setUpClass(cls):
        import shutil
        if not shutil.which("node"):
            raise unittest.SkipTest("node is not available")
        from scripts.build_site import build_assets
        js = build_assets()["karzat.js"]
        start = js.index("  var box = document.getElementById('q'); if (!box) return;")
        blk = js[start:js.index("})();", js.index("  function draw(){", start))]
        cls.fns = (blk[blk.index("  function num(v){"):blk.index("  function svgEl(")]
                   + blk[blk.index("  function step(range){"):blk.index("  function draw(){")])

    def shape_of(self, rows):
        import json, subprocess
        prog = (self.fns + "\nconst R = " + json.dumps(rows) + ";\n"
                "const s = shape(R, Object.keys(R[0]));\n"
                "console.log(JSON.stringify(s ? {label: s.labelCol, value: s.valueCol,"
                " ordered: s.ordered, also: s.also} : null));")
        out = subprocess.run(["node", "-e", prog], capture_output=True, text=True, check=True).stdout
        return json.loads(out)

    @given(st.lists(st.tuples(st.text(alphabet="abcdefgáéíó ", min_size=1, max_size=8),
                              st.one_of(st.integers(min_value=-1000, max_value=10**6), st.none())),
                    min_size=2, max_size=12),
           st.randoms(use_true_random=False))
    @settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_the_order_of_the_rows_does_not_change_the_columns(self, pairs, rnd):
        rows = [{"f": a, "n": b} for a, b in pairs]
        assume(any(r["n"] is not None for r in rows))          # a column of nothing is a different question
        first = self.shape_of(rows)
        shuffled = rows[:]
        rnd.shuffle(shuffled)
        self.assertEqual(self.shape_of(shuffled) and
                         {k: v for k, v in self.shape_of(shuffled).items() if k != "ordered"},
                         first and {k: v for k, v in first.items() if k != "ordered"},
                         "the chart picks different columns depending on which row came first")

    @given(st.lists(st.integers(min_value=-10**6, max_value=10**6), min_size=2, max_size=15))
    @settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_a_column_of_numbers_is_always_seen_as_numbers(self, vals):
        """Whatever NULLs are scattered through it, and wherever they fall."""
        rows = [{"f": f"r{i}", "n": v} for i, v in enumerate(vals)]
        rows[0]["n"] = None                                    # the exact shape that hid a chart this morning
        s = self.shape_of(rows)
        self.assertIsNotNone(s, "a result with a numeric column was judged undrawable")
        self.assertEqual(s["value"], "n")
        self.assertEqual(s["label"], "f")


class TheParserSurvivesWhatTheApiActuallyDoes(unittest.TestCase):
    """Metamorphic properties over the real payloads.

    Generating a plausible parliamentary XML document from nothing is a poor use of a fuzzer: it would spend
    its time on documents the API could never send. What is worth generating is the *perturbation* — the ways
    a real payload can differ from the one captured in a fixture, all of which the record has produced at some
    point: an element repeated, an optional attribute absent, whitespace where none was, elements in another
    order, a field holding two values where it usually holds one.

    Every one of this project's parser surprises was of that kind, so the fixtures are the seed and Hypothesis
    chooses the mutation."""

    FIXTURES = sorted((ROOT / "tests" / "fixtures").glob("real_*.xml"))

    @classmethod
    def setUpClass(cls):
        if not cls.FIXTURES:
            raise unittest.SkipTest("no captured payloads to perturb")

    @given(st.integers(min_value=0, max_value=40), st.integers(min_value=0, max_value=6))
    @settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_whitespace_between_elements_never_changes_the_parse(self, which, indent):
        """XML is whitespace-insensitive between elements, and a parser that reads `.text` without stripping
        quietly disagrees. The API's own pretty-printing has changed at least once in this corpus."""
        import xml.etree.ElementTree as ET
        f = self.FIXTURES[which % len(self.FIXTURES)]
        raw = f.read_bytes()
        d1 = xmlutil.to_dict(xmlutil.parse_xml(raw))
        padded = raw.replace(b"><", b">" + b" " * indent + b"<") if indent else raw
        try:
            d2 = xmlutil.to_dict(xmlutil.parse_xml(padded))
        except ET.ParseError:
            self.fail(f"{f.name}: padding between elements broke the parse")
        self.assertEqual(_strip(d1), _strip(d2), f"{f.name}: whitespace changed the meaning")

    @given(st.integers(min_value=0, max_value=40))
    @settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_a_payload_that_needs_repair_still_parses_after_it(self, which):
        """The two repairs run on every payload. Applying them to a document that never needed them must be a
        no-op, and applying them to one that did must leave something ElementTree accepts."""
        f = self.FIXTURES[which % len(self.FIXTURES)]
        raw = f.read_bytes()
        fixed = xmlutil.repair_attribute_quotes(xmlutil.repair_bare_ampersands(raw))
        xmlutil.parse_xml(fixed)                                # must not raise
        again = xmlutil.repair_attribute_quotes(xmlutil.repair_bare_ampersands(fixed))
        self.assertEqual(again, fixed, f"{f.name}: repairing a repaired payload changed it")


def _strip(v):
    """Recursively strip whitespace so two parses can be compared for meaning rather than layout."""
    if isinstance(v, dict):
        return {k: _strip(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_strip(x) for x in v]
    return v.strip() if isinstance(v, str) else v


class NothingIsLostBetweenTheRecordAndTheExport(unittest.TestCase):
    """Conservation over the corpus itself, not over invented data.

    Two of the day's defects were losses of exactly this kind and both were invisible in the output: the
    surviving value was always present and always plausible. A count on each side is the cheapest thing that
    would have caught either.
    """

    @classmethod
    def setUpClass(cls):
        cls.d = ROOT / "site" / "adatok"
        if not (cls.d / "szavazasok.parquet").exists():
            raise unittest.SkipTest("parquet not built")
        import pyarrow.parquet as pq
        # Read once. Hypothesis calls the test body a hundred times, and re-reading a 2.7 MB column set inside
        # the body turned a two-second class into three minutes — a property test that is too slow to run is a
        # property test nobody runs.
        cls.votes = pq.read_table(cls.d / "szavazasok.parquet",
                                  columns=["ciklus", "szavazas_id", "iromany_db", "datum"]).to_pydict()
        cls.links = pq.read_table(cls.d / "szavazas_iromany.parquet", columns=["ciklus"]).to_pydict()
        cls.dated = {}
        for name in ("felszolalasok", "szavazatok"):
            f = cls.d / f"{name}.parquet"
            if f.exists():
                cls.dated[name] = pq.read_table(f, columns=["ciklus", "datum"]).to_pydict()
        import collections
        cls.link_count = collections.Counter(cls.links["ciklus"])
        cls.want_count = collections.Counter()
        for c, n in zip(cls.votes["ciklus"], cls.votes["iromany_db"]):
            cls.want_count[c] += n or 0
        cls.span = {}
        for c, dt in zip(cls.votes["ciklus"], cls.votes["datum"]):
            if dt:
                lo, hi = cls.span.get(c, (dt, dt))
                cls.span[c] = (min(lo, dt), max(hi, dt))
        cls.by_cycle_dates = {}
        for name, t in cls.dated.items():
            d = collections.defaultdict(set)
            for c, dt in zip(t["ciklus"], t["datum"]):
                if dt:
                    d[c].add(dt[:4])
            cls.by_cycle_dates[name] = d

    @given(st.integers(min_value=0, max_value=9))
    @settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_every_vote_has_as_many_link_rows_as_it_says(self, nth):
        """`iromany_db` and the link table are two counts of one thing, written by different lines. A cycle at
        a time, so a failure names the parliament rather than the corpus."""
        cycles = sorted(self.want_count)
        c = cycles[nth % len(cycles)]
        self.assertEqual(self.link_count[c], self.want_count[c],
                         f"cycle {c}: the vote table counts {self.want_count[c]} motions, "
                         f"the link table has {self.link_count[c]}")

    @given(st.integers(min_value=0, max_value=9))
    @settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_a_cycle_never_holds_another_cycle_s_dates(self, nth):
        """The shape of the phantom-speech defect, asserted per cycle over every dated table at once."""
        cycles = sorted(self.span)
        c = cycles[nth % len(cycles)]
        lo, hi = self.span[c]
        for name, by_cycle in self.by_cycle_dates.items():
            bad = sorted(y for y in by_cycle.get(c, ()) if not (lo[:4] <= y <= str(int(hi[:4]) + 1)))
            self.assertEqual(bad, [], f"cycle {c} in {name}: years outside {lo[:4]}–{hi[:4]}")


class TwoDifferentRequestsNeverShareACacheFile(unittest.TestCase):
    """`cache_key` gives the hot services a readable filename built from a few named parameters and hashes
    everything else. Readable is worth a great deal when you are looking at 200,000 cached payloads — but it
    means the key ignores every parameter it was not told about, and a request that differs only in one of
    those would read the other's answer out of the cache and never touch the network.

    No call site does that today; I checked every one. The point of writing it down is that the correspondence
    between "the parameters this service is called with" and "the parameters in its key" is currently held in
    nobody's head, and the failure it would cause — a payload served for the wrong question, from disk, with
    no request made — is silent."""

    # what each keyed service is actually called with, read off the call sites in karzat/cli.py
    CALLED_WITH = {
        "szavazas":      {"p_szavdatum": st.sampled_from(["2026.05.09.10:45:47", "2026.06.15.17:20:04"])},
        "szavazasok":    {"p_datum_tol": st.sampled_from(["2026.05.01", "2026.06.01"]),
                          "p_datum_ig": st.sampled_from(["2026.05.31", "2026.06.30"])},
        "kepviselo":     {"p_azon": st.sampled_from(["a011", "n004", "b007"])},
        "iromany":       {"p_izon": st.integers(min_value=1, max_value=99)},
        "felszolalasok": {"p_ckl": st.sampled_from([42, 43]), "p_nap": st.integers(min_value=1, max_value=30)},
        "felszolalas":   {"p_ckl": st.sampled_from([42, 43]), "p_uln": st.integers(min_value=1, max_value=30),
                          "p_felsz": st.integers(min_value=0, max_value=99)},
        "ulesnap":       {"p_ckl": st.sampled_from([34, 42, 43])},
        "bizottsag":     {"p_biz": st.sampled_from(["A123", "B456"])},
    }

    @given(st.sampled_from(sorted(CALLED_WITH)), st.data())
    @SETTINGS
    def test_changing_any_parameter_changes_the_file(self, service, data):
        shape = self.CALLED_WITH[service]
        a = {k: data.draw(v) for k, v in shape.items()}
        b = {k: data.draw(v) for k, v in shape.items()}
        assume(a != b)
        self.assertNotEqual(cache_key(service, a), cache_key(service, b),
                            f"{service}: {a} and {b} share a cache file")

    def test_the_key_covers_every_parameter_the_code_passes(self):
        """The other half, and the half a generator cannot reach: a parameter added to a call site tomorrow
        and not to the key would make the property above vacuous rather than failing. So this reads the call
        sites and compares them with what the key names."""
        import re as _re
        keyed = {
            "szavazas": {"p_szavdatum"}, "szavazasok": {"p_datum_tol", "p_datum_ig"},
            "kepviselo": {"p_azon"}, "iromany": {"p_izon"},
            "felszolalasok": {"p_ckl", "p_nap"}, "felszolalas": {"p_ckl", "p_uln", "p_felsz"},
            "ulesnap": {"p_ckl"}, "bizottsag": {"p_biz"},
        }
        src = (ROOT / "karzat" / "cli.py").read_text(encoding="utf-8")
        for m in _re.finditer(r'\.fetch\(\s*["\'](\w+)["\']([^)]*)\)', src, _re.S):
            svc, rest = m.group(1), m.group(2)
            if svc not in keyed:
                continue                                       # hashed key: every parameter is in it already
            passed = {p for p in _re.findall(r"\b(p_\w+)\s*=", rest)}
            missing = passed - keyed[svc]
            self.assertEqual(missing, set(),
                             f"{svc} is called with {sorted(missing)}, which its cache key ignores — "
                             f"two different requests would share one file")


class TheSevenPositionsAreNamedEverywhereTheyAppear(unittest.TestCase):
    """The House distinguishes seven ways of not voting yes, and collapsing them into three is the commonest
    way this data is misread. The project says so on the page, in the export's dictionary and in the README.

    It also kept three copies of the table that writes them out for a reader — the exporter's, the builder's
    and the feeds' — and the feeds' held three of the seven. No dissent feed has ever carried one of the other
    four, because dissent is defined over the three cast positions, so nothing was wrong on the site. The trap
    was set for the day that definition widened: a feed printing `jelen_nem_szavazott` at its readers.

    Same shape as the chart's invented faction palette earlier the same day — a second copy of a table that
    drifted from the first. So: one table, beside the codes it names, and these assert the two cannot part."""

    def test_every_code_the_parser_can_emit_has_a_name(self):
        from karzat.normalise import POSITIONS, POSITION_LABEL
        self.assertEqual(set(POSITIONS.values()), set(POSITION_LABEL),
                         "a position the parser produces has no Hungarian name, or a name has no position")

    def test_the_printing_order_lists_each_position_once(self):
        from karzat.normalise import POSITION_LABEL, POSITION_ORDER
        self.assertEqual(sorted(POSITION_ORDER), sorted(POSITION_LABEL))
        self.assertEqual(len(POSITION_ORDER), len(set(POSITION_ORDER)), "a position is printed twice")

    def test_nobody_keeps_a_second_copy_of_the_table(self):
        """The check that would have caught the drift: three modules, one object."""
        from karzat.export import POSITION_LABEL as exported
        from karzat.feeds import POS_HU as in_feeds
        from karzat.normalise import POSITION_LABEL as canonical
        from scripts.build_site import POSITION_LABEL as on_pages
        for name, table in (("export", exported), ("feeds", in_feeds), ("build_site", on_pages)):
            self.assertIs(table, canonical, f"{name} keeps its own copy of the position names")

    @given(st.sampled_from(sorted(__import__("karzat.normalise", fromlist=["x"]).POSITION_LABEL)))
    @SETTINGS
    def test_a_name_is_never_the_code_itself(self, code):
        """A fallback of `.get(code, code)` is how an unlabelled position reaches a reader looking like a
        database column. The mark of that is an underscore in running Hungarian text.

        My first version of this also asserted the label differed from the code with its underscores turned
        to spaces, and Hypothesis produced `nem_szavazott` in a second: "nem szavazott" is simply the correct
        phrase, and the property was wrong rather than the table. Left in the comment because it is the whole
        lesson — the tool does not find your bugs first, it finds your assumptions."""
        from karzat.normalise import POSITION_LABEL
        label = POSITION_LABEL[code]
        self.assertNotIn("_", label, f"{code} is printed as an identifier rather than a phrase")
        self.assertTrue(label.strip(), f"{code} has an empty name")


class CurrencyIsClaimedOnlyOnEvidence(unittest.TestCase):
    """The freshness verdict is the one sentence on every page that makes a claim about the present, and the
    rule this project set itself is that it may never say more than the record supports.

    It said more. `missed` is the list of sitting days later than the newest vote, so when no sitting day has
    happened yet — an empty list, or a cycle whose first sitting is still ahead — `missed` was empty for want
    of anything to compare, and the verdict came out "current". The site asserting currency on the strength of
    knowing nothing. `unknown` is what this module's own docstring reserves for that state.

    A property rather than an example because the interesting inputs are combinations: a list that is empty, a
    list entirely in the future, a vote before or after the last known sitting, a sync that is fresh or stale.
    """

    @st.composite
    def _world(draw):
        from karzat.freshness import BUDAPEST
        base = datetime(2026, 8, 18, 9, 0, tzinfo=BUDAPEST)
        offsets = draw(st.lists(st.integers(min_value=-40, max_value=40), max_size=4, unique=True))
        days = draw(st.one_of(st.none(), st.just([(base.date() + timedelta(days=o)) for o in offsets])))
        newest = draw(st.one_of(st.none(),
                                st.integers(min_value=-60, max_value=10)
                                  .map(lambda o: base + timedelta(days=o))))
        sync = draw(st.one_of(st.none(),
                              st.integers(min_value=0, max_value=200)
                                .map(lambda h: base - timedelta(hours=h))))
        return days, newest, sync, base

    @given(_world())
    @SETTINGS
    def test_current_means_a_sitting_day_has_happened_and_is_covered(self, world):
        from karzat.freshness import assess
        days, newest, sync, now = world
        f = assess(sitting_days=days, newest_vote_at=newest, last_sync_at=sync, now=now)
        if f.status != "current":
            return
        self.assertIsNotNone(f.newest_vote_day, "current with no vote to be current about")
        self.assertIsNotNone(f.latest_sitting_day,
                             "current while no sitting day is known to have happened")
        self.assertGreaterEqual(f.newest_vote_day, f.latest_sitting_day,
                                "current while the newest vote predates the last sitting day")
        self.assertEqual(f.missed_sitting_days, (), "current with a sitting day missing")

    @given(_world())
    @SETTINGS
    def test_the_sentence_never_names_a_day_the_verdict_does_not_hold(self, world):
        """The two sentences are written from the verdict, so a status that changes without them changing is
        a sentence that has come loose from its own evidence."""
        from karzat.freshness import assess
        days, newest, sync, now = world
        f = assess(sitting_days=days, newest_vote_at=newest, last_sync_at=sync, now=now)
        for sentence in (f.sentence_hu, f.sentence_en):
            self.assertTrue(sentence.strip(), f"status {f.status} produced no sentence")
            if f.latest_sitting_day is None:
                self.assertNotIn("legutóbbi ülésnap", sentence,
                                 "the sentence names a most recent sitting day the verdict does not have")


class AParserNeverStopsTheRun(unittest.TestCase):
    """Totality. A derivation reads half a million rows in one pass, and a parser that raises on a shape it
    did not expect does not lose that row — it loses the night.

    `_duration_s` guarded its conversion with `str.isdigit()`, which is true of the superscript digits, and
    converted with `int()`, which rejects every one of them. `'²:30'` raised a ValueError out of the middle of
    the speech derivation. Nothing in the record has ever held a '²'; the two lines disagreeing about what a
    digit is, is the defect, and the record's future contents are not mine to promise."""

    TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",), max_codepoint=0x2FFF), max_size=24)

    @given(st.one_of(st.none(), TEXT))
    @SETTINGS
    def test_a_duration_is_a_number_or_nothing_and_never_an_exception(self, v):
        from karzat.normalise import _duration_s
        out = _duration_s(v)
        self.assertTrue(out is None or isinstance(out, int), f"{v!r} produced {out!r}")
        if isinstance(out, int):
            self.assertGreaterEqual(out, 0, f"{v!r} produced a negative duration")

    @given(st.integers(min_value=0, max_value=59), st.integers(min_value=0, max_value=59))
    @SETTINGS
    def test_minutes_and_seconds_are_read_as_minutes_and_seconds(self, m, sec):
        from karzat.normalise import _duration_s
        self.assertEqual(_duration_s(f"{m}:{sec:02d}"), m * 60 + sec)

    @given(st.integers(min_value=0, max_value=9), st.integers(min_value=0, max_value=59),
           st.integers(min_value=0, max_value=59))
    @SETTINGS
    def test_three_fields_are_hours_minutes_seconds(self, h, m, sec):
        from karzat.normalise import _duration_s
        self.assertEqual(_duration_s(f"{h}:{m:02d}:{sec:02d}"), h * 3600 + m * 60 + sec)


class AVerdictIsNeverBetterThanItsVotes(unittest.TestCase):
    """`passed` is the site's headline for a vote, and the arithmetic that produces it and the threshold
    printed beside it are two different code paths. A pass that is not a majority would be the worst
    single wrong thing this site could print."""

    @given(st.sampled_from(list(Rule)), tallies())
    @SETTINGS
    def test_a_vote_that_passed_had_more_yes_than_no(self, rule, t):
        v = evaluate(rule, t)
        passed = v.get("passed") if isinstance(v, dict) else getattr(v, "passed", None)
        if passed is not True:
            return
        self.assertGreater(t.yes, t.no,
                           f"{rule.name}: passed with {t.yes} yes against {t.no} no")

    @given(st.sampled_from(list(Rule)), tallies())
    @SETTINGS
    def test_an_empty_house_never_carries_a_verdict(self, rule, t):
        """The degenerate base, asserted at the verdict rather than at the threshold. `_more_than_half(0)` is
        1 and `_at_least_fraction(0, 2, 3)` is 0, so the rules disagree about the empty case; what matters to
        a reader is that neither can produce a decision out of nobody."""
        if base_of(rule, t) not in (0, None):
            return
        v = evaluate(rule, t)
        passed = v.get("passed") if isinstance(v, dict) else getattr(v, "passed", None)
        self.assertIsNot(passed, True, f"{rule.name}: a decision carried on an empty base")


class AnAgendaItemSplitsWithoutLosingItself(unittest.TestCase):
    """`split_event` decides what every hour on the Beszédidő pages is *about*, by partitioning a string.

    It is the whole feature resting on one `str.partition`, and the real corpus agrees with it on every one
    of its rows — which is exactly the situation where an example test proves nothing. The interesting inputs
    are the ones the House has not written yet: a title that repeats its own bill number, a stage that is
    empty, a number that occurs inside a longer number, a bill string that is only separators.

    Two things must hold whatever comes in. Nothing may be invented — every character of the stage and the
    subject has to come from the event itself. And it must never raise, because a derivation over half a
    million speeches must not be stoppable by one odd agenda item."""

    @given(st.text(max_size=40), st.one_of(st.none(), st.text(max_size=20),
                                           st.sampled_from(["T/1", "T/11", "H/2 · T/3", " · ", "", "T/1 · T/1"])))
    @SETTINGS
    def test_it_never_raises_and_never_invents(self, event, iromany):
        from karzat.analytics import split_event
        stage, subject = split_event(event, iromany)
        for part in (stage, subject):
            if part is not None:
                self.assertIn(part.strip(), event, f"{part!r} is not part of {event!r}")

    @given(st.text(alphabet="abcdef ", min_size=1, max_size=12),
           st.sampled_from(["T", "H", "B", "S", "Ü"]), st.integers(min_value=1, max_value=99999),
           st.text(alphabet="ghijkl ", min_size=1, max_size=12))
    @SETTINGS
    def test_a_well_formed_item_splits_back_into_its_two_halves(self, stage, prefix, n, title):
        """The shape the record actually uses: '<stage> <number> <title>' must come apart into exactly those."""
        from karzat.analytics import split_event
        assume(stage.strip() and title.strip())
        num = f"{prefix}/{n}"
        assume(num not in stage and num not in title)
        got_stage, got_title = split_event(f"{stage.strip()} {num} {title.strip()}", num)
        self.assertEqual(got_stage, stage.strip())
        self.assertEqual(got_title, title.strip())

    @given(st.sampled_from(["T/1", "T/12", "T/123"]), st.text(alphabet="0123456789", max_size=3))
    @SETTINGS
    def test_a_number_inside_a_longer_number_still_partitions_on_the_first_hit(self, num, tail):
        """'T/12' occurs inside 'T/123'. The partition takes the first occurrence, so the subject may begin
        with the rest of the longer number — ugly, but it is still the record's own text, and nothing is
        lost. What must not happen is a crash or a subject invented out of nothing."""
        from karzat.analytics import split_event
        event = f"Általános vita {num}{tail} valamiről"
        stage, subject = split_event(event, num)
        self.assertEqual(stage, "Általános vita")
        self.assertIn(subject, event)


class ADurationKeepsItsPlaceValue(unittest.TestCase):
    """The magnitude bug, in its own class because it is the exact shape of the worst defect of the week.

    An unreadable field was filtered out of the positional fold, and dropping a field shifts every field after
    it down a magnitude: `'1::03'` folded to 63 where the writing says 3663 — a speech of an hour recorded as
    one of a minute. No exception, no gap in the data, a plausible number that is 58 times too small. The same
    shape as a DECIMAL printed unscaled, and the same reason it would never have been noticed.

    None of the 400,086 duration fields in the cache is anything but M:SS, H:MM:SS or empty, so no published
    figure has ever been wrong. The parser was."""

    @given(st.lists(st.one_of(st.integers(min_value=0, max_value=999).map(str),
                              st.just(""), st.just("²"), st.just("-2"), st.text(alphabet="ab", max_size=2)),
                    min_size=1, max_size=4).map(":".join))
    @SETTINGS
    def test_a_readable_leading_field_never_loses_its_magnitude(self, v):
        """Policy-free: whatever an unreadable field is taken to mean, it may not be deleted. If the first
        field reads as a number, the answer is either nothing at all or at least that number's own place."""
        from karzat.normalise import _duration_s
        out = _duration_s(v)
        if out is None:
            return
        fields = [x.strip() for x in v.split(":")]
        while fields and not fields[-1]:
            fields.pop()
        while fields and not fields[0]:
            fields.pop(0)
        if not fields or not (fields[0].isascii() and fields[0].isdigit()):
            return
        self.assertGreaterEqual(out, int(fields[0]) * 60 ** (len(fields) - 1),
                                f"{v!r} -> {out}: a field was dropped and the rest slid down a magnitude")

    @given(st.integers(min_value=0, max_value=9), st.integers(min_value=0, max_value=59),
           st.integers(min_value=0, max_value=59))
    @SETTINGS
    def test_a_trailing_colon_does_not_change_the_reading(self, h, m, sec):
        """`'25:40:'` is a stray colon, not a third field; the record has produced worse."""
        from karzat.normalise import _duration_s
        self.assertEqual(_duration_s(f"{m}:{sec:02d}:"), _duration_s(f"{m}:{sec:02d}"))


class TheSupersededStoreLosesNothing(unittest.TestCase):
    """The change watcher keeps the payload the source overwrote, and it was overwriting them itself.

    The kept file was named for the record and a stamp at one-second resolution, so two rewrites of the same
    record inside one second landed on one path and the earlier payload was gone. The log would then say the
    source changed a record three times while the evidence for two of them no longer existed — this feature
    failing at precisely the job it exists to do, silently, in the direction that looks like nothing happened.

    tests/test_api.py exercises one change and asserts one file, which is true of both the broken and the
    fixed version. The property is about sequences, which is why an example never reached it."""

    @given(st.lists(st.binary(min_size=1, max_size=40).map(lambda b: b"<v>" + b + b"</v>"),
                    min_size=2, max_size=6))
    @settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_every_logged_change_still_has_its_payload_on_disk(self, bodies):
        import hashlib, json as _json, tempfile
        from karzat.api import WebApi
        d = Path(tempfile.mkdtemp())
        api = WebApi(token="x", cache_dir=d)
        svc, params = "szavazasok", {"p_datum_tol": "2026.05.01", "p_datum_ig": "2026.05.31"}
        path = api.cache_path(svc, params)
        path.parent.mkdir(parents=True, exist_ok=True)
        transitions = [(a, b) for a, b in zip(bodies, bodies[1:]) if a != b]
        for a, b in transitions:
            api._record_change(svc, params, path, a, b)
        log = d / ".superseded" / "changes.jsonl"
        if not transitions:
            return
        lines = [_json.loads(x) for x in log.read_text(encoding="utf-8").strip().splitlines()]
        kept = sorted((d / ".superseded").rglob("*.xml"))
        on_disk = {hashlib.sha256(f.read_bytes()).hexdigest() for f in kept}
        self.assertEqual(len(lines), len(transitions))
        for ln in lines:
            self.assertIn(ln["was"]["sha256"], on_disk,
                          "the log records a change whose superseded payload is gone")

    @given(st.binary(min_size=1, max_size=40).map(lambda b: b"<v>" + b + b"</v>"))
    @settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_recording_the_same_change_twice_keeps_one_file(self, body):
        """Idempotence: the nightly can re-see a transition, and a second copy of the same bytes is noise."""
        import tempfile
        from karzat.api import WebApi
        d = Path(tempfile.mkdtemp())
        api = WebApi(token="x", cache_dir=d)
        svc, params = "szavazasok", {"p_datum_tol": "2026.05.01", "p_datum_ig": "2026.05.31"}
        path = api.cache_path(svc, params)
        path.parent.mkdir(parents=True, exist_ok=True)
        api._record_change(svc, params, path, body, b"<v>new</v>")
        first = sorted((d / ".superseded").rglob("*.xml"))
        api._record_change(svc, params, path, body, b"<v>new</v>")
        self.assertEqual(len(sorted((d / ".superseded").rglob("*.xml"))), len(first),
                         "re-recording one transition left two copies of the same payload")



if __name__ == "__main__":                                     # pragma: no cover
    unittest.main()
