"""Majority rules of the Országgyűlés: which threshold applied, how many votes it needed,
and whether a tally clears it.

This is the analytical core the site is built around. Showing every vote against a simple-majority
marker). Here every vote gets an explicit rule, an explicit base (present MPs or all MPs), an
explicit "needed" number and a margin against it.

Legal basis — read against the consolidated texts on njt.hu on 2026-08-19: Magyarország
Alaptörvénye (njt.hu/jogszabaly/2011-4301-02-00) and the House Rules, 10/2014. (II. 24.) OGY
határozat, "HHSZ" (njt.hu/jogszabaly/2014-10-30-41). Every citation below names the paragraph
that was read; the one rule with no basis found says so.

Rules
-----
egyszeru              more than half of the MPs *present*                Alaptörvény 5. cikk (6) (default when nothing
                                                                          else applies); quorum: 5. cikk (5)
abszolut              more than half of *all* MPs                        16. cikk (4) PM election; 21. cikk (2)–(3)
                                                                          no-confidence/confidence; HHSZ 62. § (1)
                                                                          kivételes eljárás elrendelése
ketharmad_jelenlevo   at least two-thirds of the MPs *present*           T) cikk (4) sarkalatos törvény; 5. cikk (7)
                                                                          házszabályi rendelkezések; HHSZ 60. § (7)
                                                                          sürgős tárgyalás elrendelése
ketharmad_osszes      at least two-thirds of *all* MPs (133 of 199)      S) cikk (2) Alaptörvény; 24. cikk (8) AB tagok;
                                                                          26. cikk (3) Kúria elnöke, 29. cikk (4) legfőbb
                                                                          ügyész, 30. cikk (3) alapvető jogok biztosa,
                                                                          43. cikk (2) ÁSZ elnöke; 11. cikk (3) KE 1st round;
                                                                          5. cikk (1) zárt ülés
negyotod_jelenlevo    at least four-fifths of the MPs *present*          HHSZ 65. § (1) Házszabálytól eltérés (and the old
                                                                          46/1994 Házszabály 140. § (1) said the same)
negyotod_osszes       at least four-fifths of *all* MPs (160 of 199)     seen in the API's own vocabulary
                                                                          ("Listás az összes képviselő 4/5-ével", 2014);
                                                                          no basis found in the consolidated texts (VERIFY)
relativ               plurality among candidates, no yes/no threshold    11. cikk (4) KE 2nd round (the most valid votes,
                                                                          "tekintet nélkül a szavazásban részt vevők számára")

Arithmetic
----------
"több mint a fele"  -> floor(N/2) + 1
"kétharmada"        -> ceil(2N/3)      (2/3 of 199 = 132.67 -> 133)
"négyötöde"         -> ceil(4N/5)

What counts as "present" (jelen lévő) is the single most consequential modelling choice, and
the record settles it: the base is the votes cast — igen + nem + tartózkodott, the API's own
"Összes szavazat". Abstaining MPs ARE in it, so an abstention counts against a motion under
every present-based rule; MPs registered "Jelen, nem szav." are NOT (see normalise.PRESENT_POSITIONS
for the evidence: 272 contradicted outcomes in cycle 38 with them counted, none without).

The base for "all MPs" (összes képviselő) is the number of mandates in force on the day —
199 when no seat is vacant, fewer around a resignation (a two-thirds-of-all ballot on 2014-12-01
passed with 132 = ⌈2·197/3⌉); normalise.SeatsInForce counts them from the records, and falls back
to the era's 386 / 199 when it has nothing better.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

SEATS_DEFAULT = 199


class Rule(str, Enum):
    EGYSZERU = "egyszeru"
    ABSZOLUT = "abszolut"
    KETHARMAD_JELENLEVO = "ketharmad_jelenlevo"
    KETHARMAD_OSSZES = "ketharmad_osszes"
    NEGYOTOD_JELENLEVO = "negyotod_jelenlevo"
    NEGYOTOD_OSSZES = "negyotod_osszes"
    RELATIV = "relativ"


@dataclass(frozen=True)
class RuleInfo:
    rule: Rule
    label_hu: str
    label_en: str
    base: str                 # "present" | "seats" | "candidates"
    formula: str
    basis: tuple[str, ...]    # citations, each may end with "(VERIFY)"


RULES: dict[Rule, RuleInfo] = {
    Rule.EGYSZERU: RuleInfo(
        Rule.EGYSZERU, "Egyszerű többség", "Simple majority (of those present)",
        "present", "floor(present/2)+1",
        ("Alaptörvény 5. cikk (6) — a jelen lévő képviselők több mint felének szavazata, ha az Alaptörvény eltérően nem rendelkezik; 5. cikk (5) határozatképesség",),
    ),
    Rule.ABSZOLUT: RuleInfo(
        Rule.ABSZOLUT, "Abszolút többség", "Absolute majority (of all MPs)",
        "seats", "floor(seats/2)+1",
        ("Alaptörvény 16. cikk (4) — miniszterelnök megválasztása",
         "Alaptörvény 21. cikk (2)–(3) — bizalmatlansági indítvány, bizalmi szavazás",
         "HHSZ 62. § (1) — kivételes eljárás elrendelése"),
    ),
    Rule.KETHARMAD_JELENLEVO: RuleInfo(
        Rule.KETHARMAD_JELENLEVO, "Jelenlévők kétharmada", "Two-thirds of those present",
        "present", "ceil(2*present/3)",
        ("Alaptörvény T) cikk (4) — sarkalatos törvény",
         "Alaptörvény 5. cikk (7) — házszabályi rendelkezések elfogadása",
         "HHSZ 60. § (7) — sürgős tárgyalás elrendelése"),
    ),
    Rule.KETHARMAD_OSSZES: RuleInfo(
        Rule.KETHARMAD_OSSZES, "Összes képviselő kétharmada", "Two-thirds of all MPs",
        "seats", "ceil(2*seats/3)",
        ("Alaptörvény S) cikk (2) — Alaptörvény elfogadása/módosítása",
         "Alaptörvény 24. cikk (8) — alkotmánybírák",
         "Alaptörvény 26. cikk (3), 29. cikk (4), 30. cikk (3), 43. cikk (2) — Kúria elnöke, legfőbb ügyész, "
         "alapvető jogok biztosa, ÁSZ elnöke",
         "Alaptörvény 11. cikk (3) — köztársasági elnök, első forduló",
         "Alaptörvény 5. cikk (1) — zárt ülés elrendelése"),
    ),
    Rule.NEGYOTOD_JELENLEVO: RuleInfo(
        Rule.NEGYOTOD_JELENLEVO, "Jelenlévők négyötöde", "Four-fifths of those present",
        "present", "ceil(4*present/5)",
        ("HHSZ 65. § (1) — a határozati házszabályi rendelkezésektől való eltérés (a jelen lévő képviselők legalább négyötöde)",),
    ),
    Rule.NEGYOTOD_OSSZES: RuleInfo(
        Rule.NEGYOTOD_OSSZES, "Összes képviselő négyötöde", "Four-fifths of all MPs",
        "seats", "ceil(4*seats/5)",
        ("W-API 'Szavazási mód' = 'Listás az összes képviselő 4/5-ével' (observed 2014); no basis found in the consolidated Alaptörvény or House Rules texts (VERIFY)",),
    ),
    Rule.RELATIV: RuleInfo(
        Rule.RELATIV, "Relatív többség", "Plurality among candidates",
        "candidates", "—",
        ("Alaptörvény 11. cikk (4) — köztársasági elnök, második forduló: a legtöbb érvényes szavazat, a részt vevők számától függetlenül",),
    ),
}


# -- tallies and verdicts ---------------------------------------------------------------

@dataclass(frozen=True)
class Tally:
    """Counts as recorded for one vote.

    present  — jelen lévő képviselők as stated by the source; None if the source does not say,
               in which case yes+no+abstain (voting-present) is used and the verdict flags it.
    seats    — MPs holding a mandate at the time (base of the "összes" rules); default 199.
    """
    yes: int
    no: int
    abstain: int = 0
    not_voting: int = 0
    present: int | None = None
    seats: int = SEATS_DEFAULT

    def __post_init__(self) -> None:
        for name in ("yes", "no", "abstain", "not_voting", "seats"):
            v = getattr(self, name)
            if not isinstance(v, int) or v < 0:
                raise ValueError(f"{name} must be a non-negative int, got {v!r}")
        if self.present is not None:
            if self.present < self.voting_present:
                raise ValueError(
                    f"present ({self.present}) is smaller than yes+no+abstain ({self.voting_present})"
                )
            if self.present > self.seats:
                raise ValueError(f"present ({self.present}) exceeds seats ({self.seats})")
        if self.voting_present > self.seats:
            raise ValueError(f"yes+no+abstain ({self.voting_present}) exceeds seats ({self.seats})")

    @property
    def voting_present(self) -> int:
        """MPs who pressed igen/nem/tartózkodott. Abstainers are present."""
        return self.yes + self.no + self.abstain

    @property
    def effective_present(self) -> int:
        return self.present if self.present is not None else self.voting_present

    @property
    def present_is_assumed(self) -> bool:
        return self.present is None


@dataclass(frozen=True)
class Verdict:
    rule: str
    label_hu: str
    base_kind: str            # "present" | "seats" | "candidates"
    base: int | None
    needed: int | None
    yes: int
    margin: int | None        # yes - needed (positive = cleared with room)
    passed: bool | None       # None for relativ (not decidable from yes/no)
    present_assumed: bool     # True when present fell back to yes+no+abstain
    basis: tuple[str, ...]
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["basis"] = list(self.basis)
        return d


def _more_than_half(n: int) -> int:
    return n // 2 + 1


def _at_least_fraction(n: int, num: int, den: int) -> int:
    return math.ceil(n * num / den)


def base_of(rule: Rule, tally: Tally) -> int | None:
    kind = RULES[rule].base
    if kind == "present":
        return tally.effective_present
    if kind == "seats":
        return tally.seats
    return None


def needed_for_base(rule: Rule | str, base: int) -> int | None:
    """Votes in favour required under `rule` when the base (present or seats) is `base` — the one place the
    thresholds live; the site, the analytics and the loader all call this. None for relativ."""
    rule = Rule(rule)
    if rule == Rule.EGYSZERU or rule == Rule.ABSZOLUT:
        return _more_than_half(base)
    if rule in (Rule.KETHARMAD_JELENLEVO, Rule.KETHARMAD_OSSZES):
        return _at_least_fraction(base, 2, 3)
    if rule in (Rule.NEGYOTOD_JELENLEVO, Rule.NEGYOTOD_OSSZES):
        return _at_least_fraction(base, 4, 5)
    if rule == Rule.RELATIV:
        return None
    raise AssertionError(rule)


def needed(rule: Rule, tally: Tally) -> int | None:
    """Votes in favour required under `rule` for this tally (None for relativ)."""
    b = base_of(rule, tally)
    if b is None:
        return None
    return needed_for_base(rule, b)


def evaluate(rule: Rule | str, tally: Tally) -> Verdict:
    """Full verdict for one vote under one rule. Never guesses the rule — see classify()."""
    rule = Rule(rule)
    info = RULES[rule]
    b = base_of(rule, tally)
    n = needed(rule, tally)
    note = ""
    if info.base == "present" and tally.present_is_assumed:
        note = "present not stated by source; using yes+no+abstain (the votes cast — the House's own base)"
    if rule == Rule.RELATIV:
        return Verdict(rule.value, info.label_hu, info.base, None, None, tally.yes, None, None,
                       tally.present_is_assumed, info.basis,
                       "plurality vote: not decidable from a yes/no tally")
    assert n is not None and b is not None
    # `b > 0` is the whole of the empty-House case, and the rules disagreed about it: two-thirds of nobody is
    # nought, so `0 >= 0` carried the motion, while more-than-half of nobody is one, so the same empty tally
    # was rejected. Four rules passing a decision nobody voted for, two refusing it.
    #
    # This corrects nothing that was ever published. The corpus holds 31 votes with a rule and nobody present,
    # every one of them `egyszeru`, whose empty threshold is 1 — so all 31 read "Elutasítva", which is what
    # the record itself says. The four rules that would have carried them have never met an empty base.
    # The guard is here because a threshold is not made right by not being called, and because two rules
    # answering an identical tally differently is a disagreement one of them has to be losing.
    return Verdict(rule.value, info.label_hu, info.base, b, n, tally.yes, tally.yes - n,
                   b > 0 and tally.yes >= n, tally.present_is_assumed, info.basis, note)


def evaluate_all(tally: Tally) -> dict[str, Verdict]:
    """Every rule against one tally — useful for a 'what if' panel and for cross-checks."""
    return {r.value: evaluate(r, tally) for r in Rule if r != Rule.RELATIV}


# -- classification --------------------------------------------------------------------

@dataclass(frozen=True)
class Classification:
    rule: Rule
    source: str               # "payload" | "keyword" | "default"
    evidence: str = ""        # the string that decided it

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule.value, "source": self.source, "evidence": self.evidence}


def _norm(s: str | None) -> str:
    """Casefold + NFC so accented Hungarian keywords match regardless of source normal form."""
    return unicodedata.normalize("NFC", (s or "")).casefold()


# Payload vocabulary -> rule, compared as substrings after _norm() in insertion order, so the
# qualified forms must precede the bare ones. The first block is the W-API's own "Szavazási mód"
# vocabulary as observed on 2026-08-18 across 259 votes of cycle 43:
#   Listás (190) · Listás a jelenlevők 2/3-ával (47) · Listás az összes képviselő 2/3-ával (7)
#   Listás az összes képviselő felével (6) · Listás a jelenlevők 4/5-ével (5)
#   Titkos az összes képviselő 2/3-ával (2) · Titkos (1) · Jelenlét megállapítás (1, not a decision)
# "Listás" is the plain electronic vote (simple majority); "Titkos" a secret ballot (no roll call).
PAYLOAD_RULES: dict[str, Rule] = {
    "a jelenlevők 2/3-ával": Rule.KETHARMAD_JELENLEVO,
    "az összes képviselő 2/3-ával": Rule.KETHARMAD_OSSZES,
    "az összes képviselő felével": Rule.ABSZOLUT,          # "felével" = (more than) half of all MPs
    "a jelenlevők 4/5-ével": Rule.NEGYOTOD_JELENLEVO,
    "az összes képviselő 4/5-ével": Rule.NEGYOTOD_OSSZES,     # seen in 2014 lists
    "listás": Rule.EGYSZERU,
    "titkos": Rule.EGYSZERU,
    # older / prose forms kept for text sources
    "egyszerű többség": Rule.EGYSZERU,
    "abszolút többség": Rule.ABSZOLUT,
    "jelen lévő képviselők kétharmada": Rule.KETHARMAD_JELENLEVO,
    "jelenlévő képviselők kétharmada": Rule.KETHARMAD_JELENLEVO,
    "országgyűlési képviselők kétharmada": Rule.KETHARMAD_OSSZES,
    "összes képviselő kétharmada": Rule.KETHARMAD_OSSZES,
    "jelen lévő képviselők négyötöde": Rule.NEGYOTOD_JELENLEVO,
    "minősített többség": Rule.KETHARMAD_JELENLEVO,   # bare "minősített" on a bill = sarkalatos part
}

# Keyword heuristics over the vote subject / bill title, first match wins (order matters).
# These exist so the site can say *something* before the payload vocabulary is known; every
# hit is reported with its evidence so a reader can disagree.
KEYWORD_RULES: tuple[tuple[str, Rule], ...] = (
    (r"házszabálytól (?:való )?eltérés", Rule.NEGYOTOD_JELENLEVO),
    (r"alaptörvény[^,;]{0,40}módosít|módosít[^,;]{0,40}alaptörvény|alaptörvény[^,;]{0,20}elfogad", Rule.KETHARMAD_OSSZES),
    (r"alkotmánybír[áo]|kúria elnök|legfőbb ügyész|alapvető jogok biztos|állami számvevőszék elnök", Rule.KETHARMAD_OSSZES),
    (r"köztársasági elnök[^,;]{0,40}(?:választ|megválaszt)", Rule.KETHARMAD_OSSZES),
    (r"miniszterelnök[^,;]{0,40}(?:választ|megválaszt)|bizalmatlansági|bizalmi szavazás", Rule.ABSZOLUT),
    (r"kivételes eljárás", Rule.ABSZOLUT),
    (r"sürgős(?:ségi)? tárgyalás|sürgős tárgyalásb", Rule.KETHARMAD_JELENLEVO),
    (r"sarkalatos|minősített többség|kétharmad|két ?harmad|2/3", Rule.KETHARMAD_JELENLEVO),
    (r"házszabály[^,;]{0,30}(?:elfogad|módosít)", Rule.KETHARMAD_JELENLEVO),
)


def classify(*, payload_rule: str | None = None, subject: str | None = None,
             bill_title: str | None = None, vote_kind: str | None = None) -> Classification:
    """Decide the rule for a vote.

    Priority: an explicit statement in the payload (PAYLOAD_RULES) > keyword evidence in the
    subject / bill title / vote kind (KEYWORD_RULES) > default egyszerű. The default is a
    *default*, not knowledge; the verdict carries source="default" so the UI can say so.
    """
    if payload_rule:
        p = _norm(payload_rule)
        for key, rule in PAYLOAD_RULES.items():
            if key in p:
                return Classification(rule, "payload", payload_rule)
    text = " | ".join(_norm(t) for t in (vote_kind, subject, bill_title) if t)
    for pattern, rule in KEYWORD_RULES:
        m = re.search(pattern, text)
        if m:
            return Classification(rule, "keyword", m.group(0))
    return Classification(Rule.EGYSZERU, "default", "")


def describe(rule: Rule | str) -> dict[str, Any]:
    info = RULES[Rule(rule)]
    d = asdict(info)
    d["rule"] = info.rule.value
    d["basis"] = list(info.basis)
    return d
