# karzat

**Konzol, rebuilt for the Országgyűlés: every recorded vote of the Hungarian
Parliament, drawn seat by seat, with the majority rule that actually applied.**
The parlament.hu Web API token arrived on 18 August 2026. By the end of that day the
whole of cycle 43 to date — 259 votes with their roll calls — was cached and
normalised, and the first page was built: one vote drawn seat by seat with the rule
that applied, and every vote listed with its threshold. Open `site/index.html`.

<sub>**How to read this.** The first section is the whole idea in one page. After
it come the data sources, the model, the analytical core, what the first real
payloads showed, and the list of what is still unverified. Every number in this file
is recomputed by `scripts/check_readme.py`; nothing below is a result about
Hungarian politics yet — it is a description of data and of code.</sub>

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
Magyar Közlöny — the official gazette — is *not* needed: the bill payload carries
the promulgation itself ("Állapot: kihirdetve", "Kihirdetés száma", "MK száma",
"Kihirdetés dátuma" — verified on T/71), so "what became law" comes from the same
API. The gazette would only matter for decrees, which are out of scope.

**Hungary is not a two-party House, and the interesting bits are exactly where the
US model breaks.** One chamber, 199 seats. Six recorded states, not three: *igen /
nem / tartózkodott / jelen, nem szavazott / nem szavazott / előre bejelentett
hiányzó* — abstention is a real political act here and gets its own glyph, and the
roll call distinguishes present-not-voting from absent from announced-absent. Four
factions this cycle, and MPs move between them across cycles, so faction membership
is a history. Votes are identified by a timestamp, not a roll number. And the
majority rule is the story: simple, absolute, two-thirds of those present
(sarkalatos laws), two-thirds of all 199 (Alaptörvény), four-fifths of those present
(Házszabálytól eltérés) — and the API names the rule on every vote. Konzol shows
every vote against a simple-majority marker; that is the single defect this project
was built not to inherit.

**The seat chart is the chamber.** The MP record carries `<ulohely szektor sor szek>` —
sector, row and seat — and parlament.hu draws a seat map on every MP page from a query
of its own (`patko-queries/kepviselo-helye-apatkoban`) that returns all 274 seats of the
képviselőházi ülésterem with their outlines: the ministerial *patkó* as sector 0 (22 seats —
two straight arms of eight along the centre aisle, joined by an arc of six), sectors 1 and 6
as straight blocks whose "rows" are columns nearest the aisle first, sectors 2–5 as
concentric arcs, six rows everywhere, the Speaker's platform in the mouth of the horseshoe.
That table is in `reference/parlament/patko_seats.json` with its provenance, and
`karzat/seating.py` lays every MP on their real seat: 199 of 199 match, 75 seats stay
empty and are drawn as outlines, the glyphs are sized from the plan's own pitch. Nothing
about the room is estimated any more — only the scale is a drawing choice, and the
orientation parlament.hu describes (government to the Speaker's right) is what the plan
shows. Before that table turned up I estimated the geometry twice — first with sector
wedges proportional to their widest row (cramped, overlapping), then with a uniform seat
pitch and a semicircular front bench (tidy, and still wrong in shape); both are kept in
`layout()` as the fallback for a cycle without a plan, and the story is in the git log. Konzol's algorithm stays as the fallback. The chart is a console, not an
illustration: hover, tap or tab to a seat and an inspector under the chart fills — name,
faction, mandate, seat, this vote's position, the cycle record (cast, with / against own
faction) and the last twenty roll calls up to that vote as a streak strip; click pins it, Esc
releases. Seats where the MP voted against their faction's plurality wear a thin white ring,
counted in the legend; the roll-call filters dim the seats they exclude, and hovering a table
row lights its seat. All of it is build-time JSON in the page (no network), and without JS the
chart is what it was: seats, rings and titles. **For researchers**, three things that
compound: every table a page shows has a CSV/JSON twin next to it — a vote's roll call as
`<slug>.csv` / `.json`, an MP's votes as `<azon>.csv` / `.json` — and each cycle has an
`adatok/` page with the whole cycle as four tables (szavazasok, nevsorok, kepviselok,
inditvanyok; CSV is UTF-8 with a BOM, comma-separated) and a data dictionary that names
every column, so nothing has to be reverse-engineered; every vote, MP and career page
has a "Hivatkozás" box with a plain citation and a BibTeX entry (copy button) that names
the sync date, so a number quoted from here can be traced to a build; and
`szemely/<azon>.html` is one person across every loaded cycle — participation and
agreement per cycle side by side, faction switches marked, then the record's faction,
mandate and motion history — linked from each cycle's MP page and listed at
`szemely/index.html`. All of it is generated from the same inputs as the pages, by
`karzat/export.py` and the builder, so a downloaded table and the page it came from can
never disagree. Two more pages per cycle are counts with their formulas named:
`kohezio/` (Rice index |igen − nem| / (igen + nem); Hix–Noury–Roland agreement index
(max − (Σ − max)/2) / Σ over igen–nem–tartózkodott; per faction per vote and cast-weighted per
cycle; the faction × faction share of votes with the same plurality; within each faction the
MP pairs that agree least, on ≥ 20 shared cast votes; "független" is flagged as not a faction)
and `szoros/` (every decision's margin to the threshold the source names, the closest hundred;
the qualified thresholds that sank a yes-majority; and, labelled as a computed assumption, the
outcomes that would flip if every MP in the roll call who cast nothing had voted with their
faction's plurality — presence-based thresholds growing with them). Each vote page shows each
faction's agreement index beside its bar. Nothing here scales, weights or predicts; the one
counterfactual says so in its heading. Long tables — the vote directory, roll calls,
the MP index, an MP's votes and motions — show 25 rows at a time with a pager (prev / next /
page numbers / "mind" for everything) that composes with the filters, the search and the
sorting; the whole table is still in the page, so without JS nothing is missing. Where a list
spans years — cycle 42's directory, an MP's votes over a term — a year filter sits above it,
and each index links the other cycle in words, not just in the top bar's switch.

## Status

- [x] Konzol reverse-engineered; teardown and notes in `reference/konzol/`
- [x] W-API manual v2.5 obtained and transcribed; endpoints and parameters encoded in `karzat/api.py`
- [x] Client + CLI + offline tests (`python3 -m unittest discover -s tests -t .`)
- [x] Draft schema (`schema.sql`) and faction/majority config (`config/factions.yml`) — both marked VERIFY throughout
- [x] Majority-rule module (`karzat/majority.py`): six rules with citations, explicit present/seats bases, classifier with provenance; 23 tests
- [x] Wikidata identity spine (`scripts/pull_wikidata.py` → `reference/wikidata/members_ckl43.json`): 199 members, four groups, and the P4966 ↔ `p_azon` crosswalk for 197 of them; to verify against the API
- [x] Freshness contract (`karzat/freshness.py` + `python3 -m karzat freshness`): status and a bilingual sentence from sitting days, newest vote, last sync; 17 tests
- [x] Harness: golden fingerprints of every fixture (`tests/test_golden.py`, `python3 -m karzat fingerprint`) and the README gate (`scripts/check_readme.py`, run by the suite) — the numbers in this file are recomputed, not typed
- [x] Access token received 18 August 2026 (personal; `.env`, git-ignored)
- [x] First live calls: MP list, four months of vote lists, five vote details, sitting days, one MP, the bill list, one bill — read, fixtured (`tests/fixtures/real_*`), normalised (`karzat/normalise.py`) and gated
- [x] All 259 cycle-43 vote details synced (254 calls); every computed verdict agrees with the recorded result
- [x] **First page** — `scripts/build_site.py` → `site/index.html`, deterministic from committed `data/derived/*` (`--check` and `tests/test_site.py` guard it): the T/51 seat chart + verdict, headline counts, mode table, T/71 timeline, the 259-vote directory with filters
- [x] **The console look** — restyled in Konzol's visual language (dark ground, dot grid, corner-bracketed panels, mono labels, terminal footer, boot sequence); shared generated `site/assets/karzat.css` / `karzat.js`, checked like the index
- [x] **Motions on the MP pages** — the record's `<inditvanyok>` is counts only, so `iromanyok.cgi` (the current cycle's 507 irományok, submitters as "Név (Frakció)") is resolved to people with the same resolver and listed under each current-cycle MP: 941 submissions, and for every one of the 201 people the list count equals the record's "önálló" count; a parser bug that dropped every submitter of a multi-submitter bill was found and fixed on the way
- [x] **For researchers: export, citation, careers** — every table on a page has a CSV/JSON twin beside it (a vote's roll call, an MP's votes) and each cycle has `adatok/` (szavazasok, nevsorok, kepviselok, inditvanyok as CSV+JSON, with a data dictionary page); every vote, MP and career page carries a "Hivatkozás" box (plain and BibTeX, copy button); `szemely/<azon>.html` is one person across every loaded cycle, linked from each cycle's MP page
- [x] **Bill journeys** — every roll call grouped by its iromány number (amendment votes `n/k` belong to bill `n`; one number space per cycle): `iromany/<n>.html` per bill with its votes in order and the API's stage text for each, an index per cycle with prefix filters, `iromanyok.csv`; vote pages and the directory link the number to it, parlament.hu's record beside it where the list has one
- [x] **Számok** — the cycle month by month (`szamok/`): votes, the qualified-majority share of decisions, participation, dissent and agreement index per faction, as small charts with the table and `havonta.csv` beneath
- [x] **Módszer** — `modszer/`, one page generated from the code's own tables: source, who-is-who (the resolver's order and its cycle check), the majority rules with formulas and the API's labels (from `majority.RULES` and `PAYLOAD_RULES`), presence, with/against (a tie is no plurality — fixed in the site, the analytics and the loader alike), cohesion, close votes, the pages' rules (hero pick, streak, seats), downloads, and the SQLite build with three example queries; linked from every footer, from the verdict panel and the section nav
- [x] **Cohesion and close votes** — `karzat/analytics.py`: Rice and Hix–Noury–Roland agreement indices per faction per vote and per cycle, the faction × faction plurality-agreement matrix, MP × MP agreement within a faction (≥ 20 shared votes), all with the formulas printed on `kohezio/`; and `szoros/`, the decisions ranked by margin to their threshold, the yes-majorities a qualified threshold sank, and — a labelled counterfactual — the outcomes that would flip if every non-caster had voted with their faction's plurality; every vote page shows each faction's AI beside its bar; CSVs for all of it
- [x] **Cycle 42 pages** — the same builder, one level down (`site/ckl42/`): its own index, 2,599 vote pages and 214 MP pages, a cycle switch in the top bar and cross-links between an MP's two pages; inputs `votes_index_ckl42.json.gz` + `votes_positions_ckl42.json.gz` (deterministic gzip, 424 KB together) + `mps_ckl42.json`; the missing `kepviselo.cgi` records fetched (171 of the 214 people are not in the cycle-43 roster); factions attributed by each person's last roll call (10 switchers)
- [x] `sync-mps`: all 199 MP records; the **real seating plan** reconstructed from `<ulohely>` (`karzat/seating.py`, `scripts/derive_seating.py` → `data/derived/seating.json`) and drawn on the page — 197 of the hero vote's 199 placed, the 2 MPs whose mandates have since ended kept visible without a seat
- [x] **SQLite loader** (`karzat/load.py`, `python3 -m karzat load` / `stats`): schema v2 built from scratch from the cache in about twelve seconds for two cycles — 2,858 votes, 563,216 roll-call positions with 0 unresolved names, 1,015 faction-history rows, 980 mandates across cycles, 507 bills — plus a per-vote faction-plurality table and views (`v_vote`, `v_mp_alignment`) so discipline and absence are plain SQL
- [x] **Per-vote pages**: `site/szavazas/<slug>.html` for all 259 votes — the reconstructed chamber for that vote, the verdict card, faction bars, and a sortable, filterable roll call with every MP's seat; prev/next; the directory links to them. Generated from the committed `data/derived/votes_positions.json` (git-ignored output, 0.6 s for all 259)
- [x] **MP pages**: `site/kepviselo/<p_azon>.html` for all 201 people in the roll calls plus an index — mandate, seat highlighted on the chamber, faction history and mandates back to 1990, motion counts, this cycle's participation and with/against-own-faction record with definitions on the page, the votes cast against the faction plurality, and every vote linked. Portraits linked, not embedded (licence unverified). Names on vote pages link here
- [x] **Cycle 42 backfilled** (2022-05-02 → 2026-05-08): 2,599 votes over 192 sitting days listed and every roll call fetched (2,445 calls at the polite pace, about 26 a minute, in one afternoon); a second Wikidata snapshot for the names; the list-level summary in `data/derived/first_light_ckl42.json`, the comparison below; both cycles in the database. What the backfill taught the vocabulary: a **seventh** roll-call state, `Ig.távol` (excused absence — 1,223 entries in 288 of cycle 42's roll calls, none in cycle 43's first 259), and how the faction rows aggregate the seven into five columns (`igtav_db` = Ig.távol + Előre bejelentett hiányzó, `nemszav_db` = Nem szav. + Jelen, nem szav.) — schema v2, one real fixture, one test

## Run it

```bash
python3 -m unittest discover -s tests -t .      # offline; 152 tests
python3 -m scripts.check_readme                  # every registered number in this file, recomputed (--sync rewrites)
python3 -m karzat dry-run                        # request URLs, no network
cp .env.example .env                             # then paste the token
python3 -m karzat probe                          # one call: encoding, root tag, counts, first record
python3 -m karzat sync-votes --from 2026-05-01   # month-windowed lists + one call per vote, cached
python3 -m karzat sync-mps                       # MP list + one call per MP, cached
python3 -m karzat freshness --fetch              # one ulesnap call; writes data/freshness.json + the sentence
python3 -m karzat inspect data/raw/szavazas/2026-09-15T09-37-07.xml   # cached XML → JSON
python3 -m karzat fingerprint tests/fixtures/*.xml   # digest table for tests/test_golden.py
python3 -m scripts.pull_wikidata                 # re-pull the Wikidata member snapshot (deliberately)
python3 -m scripts.derive_first_light            # cache → data/derived/{first_light,votes_index,hero_vote}.json
python3 -m karzat load --since 2026-05-01        # cache → data/karzat.sqlite (rebuilt from scratch, git-ignored)
python3 -m karzat stats --json                   # counts + sanity queries; writes data/derived/db_summary.json
python3 -m scripts.derive_seating                # kepviselo records → data/derived/seating.json (the chamber)
python3 -m scripts.derive_mps                    # MP records + list + Wikidata → data/derived/mps.json
python3 -m scripts.build_site                    # data/derived + config → index + site/szavazas/*.html + site/kepviselo/*.html (--check verifies the index)
python3 -m http.server 4174 --directory site     # then open http://localhost:4174/
```

Everything the client fetches is cached as raw XML under `data/raw/<service>/` with
readable names (`szavazas/2026-09-15T09-37-07.xml`, `kepviselo/mp_z004.xml`), so
re-runs cost nothing and the request log on the other side stays short. `data/raw/`
is git-ignored. Live requests are paced at ≥ 0.6 s.

## Data sources

| Source | Role | Status |
|---|---|---|
| parlament.hu Web API (`web-api-pub/*.cgi`) | Everything: MPs, votes, bills, committees, sittings, speeches | Token received 2026-08-18; UTF-8 XML; shapes in `karzat/normalise.py`; requests logged, personal, non-transferable; republishing is an intended use per the manual |
| Wikidata (CC0) + Commons | Identity spine: QIDs, P4966 = `p_azon`, P4100 group, constituency, portraits with per-file licences | Snapshot pulled 2026-08-18 into `reference/wikidata/`; re-pull deliberately |
| parlament.hu ülésrend (seating map) | Coordinates for real seats, if `<ulohely>` maps onto a public plan | Looked for; the public page is prose only, no machine-readable plan found |
| Magyar Közlöny (magyarkozlony.hu, single PDF per issue, RSS at `/feed`) | Decrees only | Not needed for laws: `iromany.cgi` carries kihirdetés száma / MK száma / dátum |
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
| `vote: yea/nay/nv` | `position:` igen / nem / tartozkodott / jelen_nem_szavazott / nem_szavazott / bejelentett_hianyzo / igazoltan_tavol | Abstention is first-class; presence is recorded separately from voting; two kinds of excused absence |
| `party: DEM/REP/IND` | `faction_id` + `mp_faction` history | N factions, MPs move |
| `thresholdLabel` (always SIMPLE) | `majority_rule` from the API's "Szavazási mód" + `needed`, per vote | The rule differs and matters — and the source states it |
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

## The first page

`site/index.html` plus two generated files under `site/assets/` (`karzat.css`,
`karzat.js`; no CDN, no fonts fetched, one external link type: parlament.hu bill pages),
Hungarian-first with the freshness sentence in both languages. The look is Konzol's,
deliberately — the console I studied before starting: a near-black ground with a faint
24 px dot grid, zinc-bordered panels with 16 px corner brackets, tiny upper-case
monospace labels, big light-weight numbers, a 48 px top bar (brand square, breadcrumb,
cycle, and the sync time — never a "live" pulse, because nothing here is live), and a
footer that is a terminal: five lines that describe the corpus first and the page's cycle
second — how many cycles and votes and roll calls and people the site holds, then this
cycle's numbers, the one verification worth saying (the computed result agrees with the
record on every vote, or the count that disagrees), that the seat plan exists only for the
current cycle, and the sync time — followed by a two-paragraph colophon in plain words
("Honnan" / "Mit nem"). The lines are computed at build time from every cycle's inputs, so
they grow as cycles are loaded and never say more than is loaded; an earlier version listed
API endpoints and called itself a boot log, which read as a machine talking about itself.
The boot choreography — panels settling in, mono labels scrambling into place, numbers
counting up to exactly what is printed, the log typing itself — is vanilla JS on
`data-kz-*` attributes; the whole sequence is skipped under `prefers-reduced-motion` and
in a hidden tab, and without JS the pages are complete, the vote directory included (its
259 rows are rendered at build time; the script only filters). Dark only, like the
original, with the label greys lifted above the reference's zinc-500 so 9–11 px text
clears 4.5:1 on the black. The freshness sentence prints the sync as a Budapest
timestamp, not "15 perce": a static page is read later than it is built. Top: the freshness contract's
sentence. Hero: T/51, the 16th
amendment of the Alaptörvény, 15 June 2026 — 199 seats drawn by faction and by the
recorded positions (six occur in cycle 43; the seventh, excused absence, is drawn only where
it occurs), with the verdict card: mode as the API states it, rule, base 199,
needed 133, 135 igen, margin +2, and "számítás egyezik" because the computed verdict
agrees with the recorded result. Then the headline counts, the mode table, T/71's four
days, and the directory: all 259 votes, newest first, each with its rule, threshold /
base, tally, margin and result, filterable by rule and result and searchable by subject.
The seats are the chamber's own: every MP at their sector / row / seat from the API,
seen from the Speaker's platform, opposition left and government right, ministers on the
front bench, the empty seats faint; what is estimated (uniform seat width, row spacing,
the direction of seat numbers) the footer says, in one line. Every vote in the directory has its own page of the same
shape — chamber, verdict, faction bars — plus the full roll call as a table you can sort
and filter by faction and position, each MP with their seat, and links to the previous
and next vote. Secret ballots say plainly that there is no roll call; the quorum check
says it is not a decision. Every MP has a page too: mandate and seat (highlighted on the
chamber), faction history and mandates as far back as the API records them, motion counts
per cycle, and this cycle's voting record — participation, and whether each cast vote
matched the plurality of the MP's own faction — the definition in one line next to the
numbers, the votes cast against the faction listed, and every vote linked. These are
counts, not verdicts, and the pages say so — briefly: after a pass that cut every API
field name, formula and method caveat out of the panels (they live in the footer's two short
paragraphs and here), the pages carry labels and numbers, and little else. The MP record's `<inditvanyok>` block is
counts per cycle only — önálló / nem önálló — so the pages also list *what* the MP
submitted this cycle: `iromanyok.cgi` names submitters as "Név (Frakció)" and the same
resolver turns those into people; 941 submissions across the current cycle's 507
irományok, and for every one of the 201 people the list count equals the record's
"önálló" count (a test keeps it so). Amendments ("nem önálló") are not in that list, and
neither is any earlier cycle — the closed-cycle panel says so in one line. Finding this exposed a parser bug:
`parse_iromanyok` kept one `<benyujto>` and dropped all of them when a bill had several.

Built by `scripts/build_site.py` from committed inputs only, with no clock read, so a
clean checkout reproduces the index byte for byte (`--check`, `tests/test_site.py`) and
regenerates the 259 vote pages from `data/derived/votes_positions.json` — a compact store
of every roll call (p_azon, faction, position) that is committed so the pages never
depend on the raw cache.

**Cycle 42 has the same pages one level down**, under `site/ckl42/`: its own index (all
2,599 votes in the directory, the 15th Alaptörvény amendment's final vote — 140–21–0,
needed 133 — as the hero), 2,599 vote pages and 214 MP pages, built by the same functions
with a `cycle` argument; the top bar switches cycles and an MP who sat in both has each
page linking to the other. Two things a closed cycle cannot have, and the pages say so:
the API publishes seats present-tense only (of 173 former MPs' records exactly one still
carries a seat — a leftover, not history), so cycle-42 chamber views are the by-faction
fallback layout, not an ülésrend, and there is no seat column; and instead of the freshness
sentence the index says the cycle is closed (2022-05-02 – 2026-05-08) and prints the sync
stamp. The roster is everyone in the cycle's roll calls — 214 people with the replacements,
each under the faction printed at their last roll call, which is how the LMP group's
dissolution and the Jobbik departures show up (10 people end the cycle in a different group
than they began it). Inputs: `votes_index_ckl42.json.gz` and `votes_positions_ckl42.json.gz`
(gzip with mtime zeroed, so a re-derive from the same cache is byte-identical; 424 KB
together instead of 16 MB), `mps_ckl42.json` (171 of the 214 are not in the cycle-43 roster and needed their own
`kepviselo.cgi` record — six had come in for the name resolver, the rest in one afternoon at
the polite pace), and `factions_ckl42` in
`config/factions.yml` with the eleven groups the roll calls print. The generated tree is
big — about half a gigabyte of HTML, because every roll call is a page and every MP page
lists every vote — and git-ignored like the cycle-43 pages.

## The database

`schema.sql` is the contract and `karzat/load.py` the only writer. `python3 -m karzat load
--since 2022-05-01` rebuilds `data/karzat.sqlite` from the raw cache — both cycles, about
twelve seconds — atomically, and runs a foreign-key check at the end; nothing is ever
edited in place. Loaded today: 372 MPs (200 with a seat in their record — the 199
sitting, and one former MP whose record still carries a leftover seat — 370 with a Wikidata
QID, plus 173 rows for former MPs the roll calls name, full records now that cycle 42
needed them), 1,015 faction-history rows and 980 mandates back to 1990, 215 sitting days,
2,858 votes of which 2,835 carry a roll call — 563,216 positions, 0 unresolved names — 2,746
motions, 507 bills, and 27,343 rows of `vote_faction_majority` (each faction's plurality
position per vote). The zero is earned, not assumed, twice over. First, six former MPs
resolved to nobody until their own `kepviselo.cgi` records were fetched, because the roll
call prints the API's spelling ("Z. Kárpát Dániel", "Czunyiné Dr. Bertalan Judit", "Szabó
Timea") where Wikidata's label differs — so every cached record feeds the resolver, and the
API's spelling wins. Second, and worse, a zero can hide a wrong person: cycle 42's "Dr. Kiss
János (Fidesz)" resolved for a while to the 2026 TISZA MP Kiss János, because a bare-name
fallback against today's list ran before anything else. The resolver now goes exact label →
the API's own record spelling → bare name, and any candidate must not contradict the printed
faction in the record's faction history for the vote's cycle; two candidates it cannot tell
apart stay unresolved. A cross-check that every attribution agrees with the person's own
record is now a test, for both cycles. Two
views make the analytics the README promised into queries: `v_vote` (vote + motion + rule)
and `v_mp_alignment` (per MP: cast, abstained, absent, with / against own faction). The
consistency check that matters is in `stats`: 0 votes where the computed threshold and the
recorded result disagree — now across two cycles and 2,835 roll calls, not 259. Numbers here
come from `data/derived/db_summary.json`, written by `python3 -m karzat stats --json`.

## The identity spine

`reference/wikidata/members_ckl43.json` is a dated snapshot of Wikidata's view of the
chamber, pulled by `scripts/pull_wikidata.py` (one SPARQL query, plus Commons lookups for
image licences). It hedges two things the W-API may or may not give me — per-MP ids in the
roll and reusable portraits — and it turned out to carry more than a hedge: **P4966
"Hungarian National Assembly ID" is the W-API's `p_azon`** (its formatter URL ends in
`kepv_adat?p_azon=$1`), so the crosswalk between the two worlds exists before the token does.
On the first pull: 201 statements, 199 current members and 2 ended (two Fidesz seats changed
hands in July and August), 197 of the 199 with a P4966 id — the two without are the
replacements seated on 10 August, whom Wikidata has not caught up with — 106 with a
constituency (P768; exactly the number of single-member districts, so that is the egyéni set)
and 49 with a Commons portrait, under a mix of licences that has to be honoured per file.
Group comes from the P4100 qualifier, not from P102 party membership, which lags. All of it
is to be verified against `kepviselok.cgi`; the numbers in this paragraph are gated by
`scripts/check_readme.py` on the snapshot itself.

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

## Decisions

- **Votes are keyed by timestamp**, URL slug `YYYY-MM-DDTHH-MM-SS`; no invented roll numbers.
- **Six positions, explicit majority rule, faction history** — the things Konzol gets wrong or cannot express, fixed in the schema rather than in the UI.
- **Members are joined by `p_azon`, never by name.** The roll call prints names + faction; names are unique in the current list and resolve exactly, honorifics included; former MPs resolve through Wikidata aliases (P4966 = `p_azon`) or stay explicitly unresolved.
- **Raw XML is the source of truth on disk; SQLite is derived** and can be rebuilt. This is the same
  "generated, not typed" discipline as the assay repos.
- **Working assumption for the front end: static site + JSON, built by a scheduled job** (a GitHub Action holding the token as a secret), published on Pages like the pyrite explorer. No Vercel, no Cloudflare, no auth. Whether the visual language mirrors Konzol's GSAP terminal or takes a calmer line is still open.
- **No gazette scraping.** Promulgation comes from the API; decrees are out of scope.

## What the first payloads answered (18 August 2026, 15 calls)

1. **Encoding:** UTF-8, declared. No HTML inside fields; submitter links are URL-encoded JSON in an `href` attribute that carries the MP's `p_azon`.
2. **`szavazas.cgi`:** `<tulajdonsagok>` is a list of `<tulajdonsag nev ertek>` pairs and **names the majority rule** in "Szavazási mód" (`Listás`, `Listás a jelenlevők 2/3-ával`, `Listás az összes képviselő 2/3-ával`, `Listás az összes képviselő felével`, `Listás a jelenlevők 4/5-ével`, `Titkos…`, `Jelenlét megállapítás`), the counts, "Összes szavazat" (= igen+nem+tartózkodott on all 259 votes) and "Elfogadás" (`Elfogadva` / `Elutasítva` / `Határozatképes`). `<nev_szerint>` carries **names + faction, not ids**; positions are `Igen`, `Nem`, `Tart.`, `Jelen, nem szav.`, `Nem szav.`, `Előre bejelentett hiányzó`. `<kepvcsop_szerint>` gives per-faction counts including `igtav_db` (announced absence) and a totals row. Secret ballots have neither.
3. **`iromany.cgi`:** carries "Állapot: kihirdetve", "Kihirdetés száma", "MK száma", "Kihirdetés dátuma", "Benyújtva", "Tárgyalási mód" (e.g. `kivételes tárgyalásban`), the submitter, dated free-text events, the votes taken on it, and the committee with the HHSZ paragraph it acted under (`62. § (5) bekezdés` for the exceptional procedure).
4. **`kepviselo.cgi`:** `<ulohely szektor sor szek>`; faction history across cycles with dates; elections with constituency ("Vas 2. OEVK") and mandate dates; motion and speech counts per cycle; pay, asset declarations, education. **All 197 Wikidata P4966 ids resolve; the two missing ids are `006E` (Hortay Olivér) and `006F` (Palóc André).** Names are unique across the 199.
5. **Sizes:** vote list ≈ 120 KB per month, vote detail ≈ 21 KB, MP list 80 KB, MP record 24 KB, bill list 419 KB (507 bills). No throttling seen at 0.6 s pacing.
6. **Factions** are spelled `TISZA`, `Fidesz`, `KDNP`, `Mi Hazánk` — the config ids were right.
7. **Qualified-majority parts are voted separately:** the motion outcome reads "…minősített többséget igénylő része elfogadva" / "…egyszerű többséget igénylő része elfogadva".
8. **Sitting days:** `<ulesnap>` has `datum`, `ulnap`, `nap`, `ulszak` ("2026. tavaszi", "2026. nyári"), `ulesjelleg` (`Rendes` / `Rendkívüli`).

## Still unverified

1. The legal definition of "jelen lévő" for present-based majorities. The normaliser counts igen + nem + tartózkodott + "Jelen, nem szav." and records the basis on every vote; whether the Országgyűlés counts exactly that is a reading of the Házszabály I have not done.
2. The citations in `karzat/majority.py` (Alaptörvény / HHSZ paragraphs) — the API's own rule labels make them unnecessary for classification, but they are still asserted in the docs.
3. `bizottsagok.cgi` (the manual's link is broken; not called yet), and the earliest date `szavazasok.cgi` serves.
4. Whether parlament.hu portraits (`…/kepviselo-kepek/{p_azon}`) may be republished.
5. What `szektipus` `p` (21 seats at the outer ends of sectors 1–2 and 5–6, none occupied by an MP) and `s` (one seat) mean in parlament.hu's seat table — the source does not say; the sides of the horseshoe are described as the EP members' and permanent guests' places, which fits, but is not confirmed.

## First light — what the cached payloads say

Numbers here come from `data/derived/first_light.json` (regenerate with
`python3 -m scripts.derive_first_light`) and from the fixtures; the gate checks them.

Between 9 May and 11 August 2026 the Országgyűlés recorded 259 votes over 23 sitting
days: 258 decisions and 1 quorum check, 3 of them secret ballots. With all 259 roll
calls in hand, the majority module's verdict agrees with the Országgyűlés's own
Elfogadva/Elutasítva on 259 of 259 — 0 disagreements — which is the first evidence
that the "present" interpretation is at least not contradicted by the record. 190 were plain
"Listás" votes; 47 needed two-thirds of those present, 7 two-thirds of all 199, 6
more than half of all 199, 5 four-fifths of those present — 65 qualified-majority
votes, a quarter of all decisions. 5 votes ordered exceptional procedure
(*kivételes eljárás*), 10 ordered urgent procedure, 5 approved a departure from the
House rules, and 53 accepted an interpellation answer. The chamber holds 199 members:
106 elected in single-member districts and 93 from the national list. T/71 — the
bill reversing Hungary's withdrawal from the International Criminal Court — was
submitted on 25 May, voted through in exceptional procedure in 2 days, and
promulgated as 2026. évi XV. törvény in Magyar Közlöny 60 on 28 May: 3 days from
submission to promulgation.

## Two cycles side by side — counts, not conclusions

The same derive, run over the previous term (`--since 2022-05-02 --until 2026-05-08
--suffix ckl42`), gives cycle 42 as a baseline for cycle 43's first months. Cycle 42:
2,599 votes over 192 sitting days — 13.5 per sitting day — of which 2,583 decisions and 16
quorum checks (10 of them inquorate, "Határozatképtelen"), 20 secret ballots; 545
qualified-majority votes, 21% of decisions; 28 exceptional-procedure orders and 13 urgent
ones in four years; 406 interpellation answers accepted; 11 departures from the House
rules. Cycle 43 so far: 259 votes over 23 sitting days — 11.3 per sitting day — 65
qualified-majority votes, 25% of decisions; 5 exceptional-procedure orders and 10 urgent ones
in the first three months. The mode vocabulary is identical across both cycles (the
four-fifths-of-all form appears only in 2014). What these counts mean — a busier or a more
rushed parliament, a larger or a smaller share of two-thirds legislation — is exactly what
this repository does not yet claim; cycle 43's roll calls are in the database and cycle
42's are joining them as they download, for whoever wants to ask properly.

## What this does not show

The "first light" figures describe three months of one cycle from list payloads and
a handful of details; they are counts, not findings. Nothing here says anything
about how MPs vote, how disciplined factions are, or how fast laws pass in general —
one bill's 3 days is an example of a field, not a statistic. Those claims need the
per-vote roll calls ingested and the majority module run over all of them, and then
this section will say what the numbers cannot support.

## Layout

```
karzat/            api.py (W-API client, cache) · xmlutil.py · cli.py · normalise.py (payload → records) · load.py (cache → SQLite) · majority.py (rules, thresholds, classifier) · seating.py (the chamber: parlament.hu's floor plan, or the estimate) · freshness.py (what the site may say about currency) · export.py (CSV/JSON shapes + data dictionary) · analytics.py (cohesion indices, close votes) · fingerprint.py · wikidata.py
scripts/           check_readme.py — the README gate ("Generated, not typed") · pull_wikidata.py — the identity-spine snapshot · derive_first_light.py, derive_seating.py, derive_mps.py — cache → data/derived · build_site.py — data/derived → site/
site/              index.html — the first page, generated, guarded by --check and tests/test_site.py · assets/karzat.css, karzat.js — the shared look and the boot sequence, generated and checked the same way · szavazas/ — one page per vote (+ .json/.csv) · kepviselo/ — one page per MP (+ .json/.csv) + index · adatok/ — the cycle's tables and data dictionary · ckl<N>/ — the same for earlier cycles · szemely/ — one career page per person across cycles (all generated, git-ignored)
tests/             offline tests: client/XML · normaliser on real payloads · majority arithmetic · freshness sentences · golden fingerprints · README gate; fixtures/ (real W-API captures + one synthetic)
schema.sql         normalised store (SQLite), v1 after the first real payloads — built by karzat/load.py into data/karzat.sqlite (git-ignored)
config/factions.yml faction ids/colours/order (verified spellings), the six positions, majority rules with their API labels
reference/         parlament-webapi/ (manual v2.5, PDF + text) · konzol/ (teardown + notes) · wikidata/ (member snapshot + README)
data/raw/          git-ignored XML cache · data/derived/ committed summaries the README is gated on
```

## Licence

MIT. Data belongs to the Országgyűlés and is republished under the terms of its Web
API registration; attribute it.
