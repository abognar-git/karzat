# karzat

**Konzol, rebuilt for the Országgyűlés: every recorded vote of the Hungarian
Parliament, drawn seat by seat, with the majority rule that actually applied.**
Nothing is ingested yet — the parlament.hu Web API token was requested in August
2026 and this repository is the scaffold waiting for it.

<sub>**How to read this.** The first section is the whole idea in one page. After
it come the data sources, the model I intend to build, the decisions I have already
taken and — the longest part — the list of things I do not know until real payloads
arrive. Nothing below claims a result; there is no result yet.</sub>

---

## What this is, in one page

**It is a re-implementation, not a fork.** The reference is *Konzol*, Konzol
Media LLC's dark, terminal-styled congressional vote visualizer at
konzol.invalid/konzol. I took it apart in August 2026 — routes, RSC payloads,
bundles, the public JSON route, the GSAP boot/exit choreography, the hemicycle
layout algorithm — and the teardown lives in
[`reference/konzol/`](reference/konzol/konzol-teardown.html) so I do not
have to keep it in my head. What carries over is the *shape*: a landing page of
aggregates, a directory of votes, and a per-vote screen with a seat chart and a
member inspector. What does not carry over is any of the code, the two-party
assumptions, or the defects I found (see below).

**One source is enough for the core.** The Országgyűlés publishes a Web API
(`https://www.parlament.hu/cgi-bin/web-api-pub/*.cgi`, XML, personal access token,
every request logged) with exactly the twelve services a vote tracker needs: MPs,
one MP, votes in a date range, one vote (by faction and by name), bills, one bill
(with its lifecycle events and the votes taken on it), committees, nationality
spokespersons, sitting days, and speeches. The manual (v2.5) is transcribed in
[`reference/parlament-webapi/`](reference/parlament-webapi/webapi_felhasznaloi_kezikonyv_v2.5.txt).
Magyar Közlöny — the official gazette — is *not* needed for votes; it is a possible
phase-two source for "what became law" and for decrees, and only if the bill
payload turns out not to carry the promulgation reference already.

**Hungary is not a two-party House, and the interesting bits are exactly where the
US model breaks.** One chamber, 199 seats. Four vote positions, not three:
*igen / nem / tartózkodott / nem szavazott* — abstention is a real political act
here and gets its own glyph. Many factions, and MPs move between them, so faction
membership is a history. Votes are identified by a timestamp, not a roll number.
And the majority rule is the story: simple, absolute, two-thirds of those present
(sarkalatos laws), two-thirds of all 199 (Alaptörvény). Konzol shows every vote
against a simple-majority marker; that is the single defect I most want not to
inherit.

**The seat chart can be real.** The MP record carries `<ulohely>` — the actual seat
— so the diagram can be the chamber's own seating plan rather than an algorithmic
hemicycle. Konzol's algorithm (rows proportional to radius, parties poured by
angle) is documented in the teardown as the fallback.

## Status

- [x] Konzol reverse-engineered; teardown and notes in `reference/konzol/`
- [x] W-API manual v2.5 obtained and transcribed; endpoints and parameters encoded in `karzat/api.py`
- [x] Client + CLI + offline tests (`python3 -m unittest discover -s tests -t .`)
- [x] Draft schema (`schema.sql`) and faction/majority config (`config/factions.yml`) — both marked VERIFY throughout
- [x] Majority-rule module (`karzat/majority.py`): six rules with citations, explicit present/seats bases, classifier with provenance; 23 tests
- [x] Cycle-43 faction list from Wikidata (199 members, four groups) as a placeholder to verify against the API
- [x] Freshness contract (`karzat/freshness.py` + `python3 -m karzat freshness`): status and a bilingual sentence from sitting days, newest vote, last sync; 16 tests
- [x] Harness: golden fingerprints of every fixture (`tests/test_golden.py`, `python3 -m karzat fingerprint`) and the README gate (`scripts/check_readme.py`, run by the suite) — the numbers in this file are recomputed, not typed
- [ ] Access token (requested via parlament.hu/web-api-regisztracio, Aug 2026)
- [ ] First `probe` against the live API; replace synthetic test fixtures with trimmed real XML
- [ ] Ingest: `sync-mps`, `sync-votes` for ciklus 43, normalise into SQLite
- [ ] Front end

## Run it

```bash
python3 -m unittest discover -s tests -t .      # offline; 60 tests
python3 -m scripts.check_readme                  # every registered number in this file, recomputed (--sync rewrites)
python3 -m karzat dry-run                        # request URLs, no network
cp .env.example .env                             # then paste the token
python3 -m karzat probe                          # one call: encoding, root tag, counts, first record
python3 -m karzat sync-votes --from 2026-05-01   # month-windowed lists + one call per vote, cached
python3 -m karzat sync-mps                       # MP list + one call per MP, cached
python3 -m karzat freshness --fetch              # one ulesnap call; writes data/freshness.json + the sentence
python3 -m karzat inspect data/raw/szavazas/2026-09-15T09-37-07.xml   # cached XML → JSON
python3 -m karzat fingerprint tests/fixtures/*.xml   # digest table for tests/test_golden.py
```

Everything the client fetches is cached as raw XML under `data/raw/<service>/` with
readable names (`szavazas/2026-09-15T09-37-07.xml`, `kepviselo/mp_z004.xml`), so
re-runs cost nothing and the request log on the other side stays short. `data/raw/`
is git-ignored. Live requests are paced at ≥ 0.6 s.

## Data sources

| Source | Role | Status |
|---|---|---|
| parlament.hu Web API (`web-api-pub/*.cgi`) | Everything in phase 1: MPs, votes, bills, committees, sittings, speeches | Token pending; requests logged, personal, non-transferable; republishing is an intended use per the manual |
| parlament.hu ülésrend (seating map) | Coordinates for real seats, if `<ulohely>` maps onto a public plan | Not yet looked at |
| Magyar Közlöny (magyarkozlony.hu, single PDF per issue, RSS at `/feed`) | Phase 2 only: promulgation and decrees | Deliberately out of scope for now |
| njt.hu (Nemzeti Jogszabálytár) | Better structured than the gazette for "what the law says" | Phase 2 candidate |
| valasztas.hu | Constituency results per MP (margin, egyéni vs. lista) | Nice-to-have enrichment |

## The model (draft)

`schema.sql` is the target; every VERIFY comment is a claim about the XML I have not
been able to check. The mapping from Konzol's model to this one:

| Konzol | karzat | Why it changes |
|---|---|---|
| `chamber` House/Senate | — (one chamber) | Országgyűlés is unicameral |
| `congress`, `session` (119-2) | `ckl` (43), `session_label` (tavaszi/őszi), `sitting_day` | parlament.hu numbers terms from 34 (1990); 43 = 2026– |
| `rollNumber` | `vote.ts` (idopont) + `slug` | The API identifies a vote by timestamp |
| `vote: yea/nay/nv` | `position: igen/nem/tartozkodott/nem_szavazott` | Abstention is first-class |
| `party: DEM/REP/IND` | `faction_id` + `mp_faction` history | N factions, MPs move |
| `thresholdLabel` (always SIMPLE) | `majority_rule` + `needed`, per vote | The rule differs and matters |
| `district` | `mp_mandate.kind` egyéni/lista + constituency | Mixed electoral system |
| `billRole` sponsor/cosponsor | `bill.submitters` + `submitter_kind` kormány/képviselő | Government vs. individual-MP bills is the transparency angle |
| `sponsoredBillHistory[20]` | derived from `<inditvanyok>` | Keep the idea, recompute from source |
| `recentPartyAlignment[20]` | derived: position vs. faction majority | Discipline is near-total on the government side; deviation and absence are the signal |
| photo from congress.gov | `mp.photo_url` from parlament.hu (licence VERIFY) | |
| 200-record directory cap | none; paginate | Hundreds of votes per sitting day |

## Majority rules — the analytical core

`karzat/majority.py` gives every vote an explicit rule, base, threshold and margin. The
arithmetic is fixed ("több mint a fele" → ⌊N/2⌋+1; "kétharmada" → ⌈2N/3⌉; "négyötöde" →
⌈4N/5⌉); the legal citations are transcribed from memory and **every one is flagged VERIFY**
in the module until it has been read against the consolidated text on njt.hu.

| Rule | Base | Needed (N=199 / present 176) | Applies to (citation, VERIFY) |
|---|---|---|---|
| `egyszeru` | present | 100 / 89 | default — Alaptörvény 5. cikk (6) |
| `abszolut` | all MPs | 100 | miniszterelnök választás 16. cikk (4); bizalmatlansági / bizalmi szavazás 21. cikk; kivételes eljárás elrendelése (HHSZ) |
| `ketharmad_jelenlevo` | present | 133 / 118 | sarkalatos törvény T) cikk (4); Házszabály 5. cikk (7); sürgős tárgyalás (HHSZ) |
| `ketharmad_osszes` | all MPs | 133 | Alaptörvény módosítás S) cikk (2); alkotmánybírák 24. cikk (8); Kúria elnöke, legfőbb ügyész, ombudsman, ÁSZ elnöke; KE 1. forduló 11. cikk (3) |
| `negyotod_jelenlevo` | present | 160 / 141 | Házszabálytól eltérés (HHSZ) |
| `relativ` | candidates | — | KE 2. forduló 11. cikk (4); not decidable from a yes/no tally |

Two modelling choices, both deliberate and both flagged in every verdict: **abstainers are
present** (tartózkodás counts against a motion under every present-based rule), and when the
source does not state the number present the module falls back to yes+no+abstain and says so
(`present_assumed`). Which rule applies is decided by, in order: an explicit statement in the
payload (vocabulary table to be filled from real XML), keyword evidence in the vote subject or
bill title (each hit reported with the matched text), and only then the *default* of simple
majority — recorded as such, so the site can never quietly show a default as a fact.

Per Wikidata on 2026-08-18, the 199 members of cycle 43 sit in four groups — TISZA 141,
Fidesz 44, KDNP 8, Mi Hazánk 6 — which, if the API confirms it, puts one group above the
133-seat line on its own. That is why this module comes before any pixel of UI.

## Generated, not typed

Two mechanisms carried over from the sibling repos, both live before any real data exists:

- **Golden fingerprints.** Every payload in `tests/fixtures/` is pinned in
  `tests/test_golden.py` as a digest of *how this code reads it* — canonical JSON of the
  parsed structure, not the bytes. Change the parser and the test says so; regenerate with
  `python3 -m karzat fingerprint tests/fixtures/*.xml` and say why in the commit. An
  unregistered fixture fails too, so nothing is pinned by accident. Today the fixtures are
  synthetic and say so in their names; the first job after the token is to replace them with
  trimmed real payloads.
- **The README gate.** `scripts/check_readme.py` registers fragments of this file verbatim
  with their numbers as format specs, recomputes the numbers from the code (`majority`
  thresholds), the config (Wikidata seat counts and their sum) and test discovery, and fails
  on any drift or on a fragment that has stopped appearing. It runs inside the test suite.
  When derived data exists, the site's census numbers join the same list.

## The freshness contract

Konzol pulsed "LIVE_FEED_ACTIVE" while its ingest had been dead for 26 days. karzat
cannot: `karzat/freshness.py` takes the sitting days known from `ulesnap.cgi`, the newest
vote held, and the last successful sync, and produces a status and **a sentence** — the
sentence is what the page shows, in Hungarian and English. Its rules: never say "live" (a
test forbids the word); always name the date of the newest vote; count what is missing in
*sitting days*, not calendar days, so recess is not staleness ("Parliament sat on 22 and 23
July 2026; the votes from those 2 sitting days are not here yet"); always state the sync's
age and say plainly when it has not run on schedule; and when the sitting-day list is
unavailable, say that currency cannot be told rather than imply it. `sync-votes` and
`sync-mps` record `data/sync_state.json` only on success; `freshness` reads it, costs one
`ulesnap` call, and writes `data/freshness.json` for the static build.

## Decisions taken before the token arrived

- **Votes are keyed by timestamp**, URL slug `YYYY-MM-DDTHH-MM-SS`; no invented roll numbers.
- **Four positions, explicit majority rule, faction history** — the three things Konzol gets wrong or cannot express, fixed in the schema rather than in the UI.
- **Raw XML is the source of truth on disk; SQLite is derived** and can be rebuilt. This is the same
  "generated, not typed" discipline as the assay repos.
- **Working assumption for the front end: static site + JSON, built by a scheduled job** (a GitHub Action holding the token as a secret), published on Pages like the pyrite explorer. No Vercel, no Cloudflare, no auth. Whether the visual language mirrors Konzol's GSAP terminal or takes a calmer line is still open.
- **Magyar Közlöny scraping is not in phase 1.**

## What I do not know yet — verify the moment the token lands

1. XML declared encoding (UTF-8 vs. ISO-8859-2) and whether any fields contain HTML.
2. `szavazas.cgi`: does `<tulajdonsagok>` state the vote type, the result *and the majority rule*? Does `<nev_szerint>` carry `p_azon` per MP or only names (name collisions)? How do absentees appear — as `nem szavazott` or missing?
3. `iromany.cgi`: does `<tulajdonsagok>` carry the promulgation ("… évi … törvény", MK issue)? What is the `<esemenyek>` taxonomy?
4. `kepviselo.cgi` for a former MP's `p_azon` — does history work? What does `<ulohely>` look like, and is there a public seat map to digitise?
5. Earliest date `szavazasok.cgi` serves; typical payload sizes; whether any throttling appears in practice (none is documented).
6. The manual's URL for the committee *list* is a broken link; `bizottsagok.cgi` is my guess.
7. How the API spells the four cycle-43 factions Wikidata reports — `config/factions.yml` ids are placeholders until then.
8. Whether `<tulajdonsagok>` states the required majority in words (fills `PAYLOAD_RULES` in `majority.py`) or the rule must be inferred from the subject line — and how "jelen lévő" is counted in the payload's own numbers (does `nem szavazott` count as present?).
9. Whether the sarkalatos parts of a bill are voted separately ("minősített többséget igénylő rendelkezések") as the subject text suggests, so one bill yields two zárószavazás records with different rules.

## What this does not show

Nothing, yet. There is no data in this repository, no numbers, and no claim about
the Hungarian Parliament. When there is, this section will say what the numbers
cannot support.

## Layout

```
karzat/            api.py (W-API client, cache) · xmlutil.py · cli.py · majority.py (rules, thresholds, classifier) · freshness.py (what the site may say about currency) · fingerprint.py
scripts/           check_readme.py — the README gate ("Generated, not typed")
tests/             offline tests: client/XML · majority arithmetic · freshness sentences · golden fingerprints · README gate; fixtures/ (synthetic now, real later)
schema.sql         draft normalised store (SQLite)
config/factions.yml faction colours/order, position vocabulary, majority rules — placeholders
reference/         parlament-webapi/ (manual v2.5, PDF + text) · konzol/ (teardown + notes)
data/raw/          git-ignored XML cache
```

## Licence

MIT. Data belongs to the Országgyűlés and is republished under the terms of its Web
API registration; attribute it.
