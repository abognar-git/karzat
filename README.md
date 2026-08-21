# karzat

**Every recorded vote of the Hungarian Parliament since 1990, on its own page,
drawn seat by seat, with the majority the House itself required.**
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

**The shape it takes.** A landing page of aggregates, a directory of votes, and a
per-vote screen with a seat chart and a member inspector — the arrangement a reader
of a legislature's record needs, whichever country's record it is. Everything under
it is written for this House: six recorded positions rather than three, a majority
rule that changes from vote to vote, faction membership as a history rather than a
label, and votes keyed by a timestamp because that is how the Országgyűlés keys
them.

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
(Házszabálytól eltérés) — and the API names the rule on every vote. Drawing every
vote against a single simple-majority marker would be the easy thing to do and would
be wrong about most of the interesting ones; the threshold is computed per vote and
shown with it.

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
`layout()` as the fallback for a cycle without a plan, and the story is in the git log. The generic algorithm stays as the fallback. The chart is a console, not an
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
- [x] **The console look** — a console language (dark ground, dot grid, corner-bracketed panels, mono labels, terminal footer, boot sequence); shared generated `site/assets/karzat.css` / `karzat.js`, checked like the index
- [x] **Motions on the MP pages** — the record's `<inditvanyok>` is counts only, so `iromanyok.cgi` (the current cycle's 539 irományok, submitters as "Név (Frakció)") is resolved to people with the same resolver and listed under each current-cycle MP: 974 submissions, and for every one of the 201 people the list count equals the record's "önálló" count; a parser bug that dropped every submitter of a multi-submitter bill was found and fixed on the way
- [x] **For researchers: export, citation, careers** — every table on a page has a CSV/JSON twin beside it (a vote's roll call, an MP's votes) and each cycle has `adatok/` (szavazasok, nevsorok, kepviselok, inditvanyok as CSV+JSON, with a data dictionary page); every vote, MP and career page carries a "Hivatkozás" box (plain and BibTeX, copy button); `szemely/<azon>.html` is one person across every loaded cycle, linked from each cycle's MP page
- [x] **Bill journeys** — every roll call grouped by its iromány number (amendment votes `n/k` belong to bill `n`; one number space per cycle): `iromany/<n>.html` per bill with its votes in order and the API's stage text for each, an index per cycle with prefix filters, `iromanyok.csv`; vote pages and the directory link the number to it, parlament.hu's record beside it where the list has one
- [x] **Számok** — the cycle month by month (`szamok/`): votes, the qualified-majority share of decisions, participation, dissent and agreement index per faction, as small charts with the table and `havonta.csv` beneath
- [x] **Módszer** — `modszer/`, one page generated from the code's own tables: source, who-is-who (the resolver's order and its cycle check), the majority rules with formulas and the API's labels (from `majority.RULES` and `PAYLOAD_RULES`), presence, with/against (a tie is no plurality — fixed in the site, the analytics and the loader alike), cohesion, close votes, the pages' rules (hero pick, streak, seats), downloads, and the SQLite build with three example queries; linked from every footer, from the verdict panel and the section nav
- [x] **Keresés** — `kereses/`, one box over every loaded cycle's bills, MPs and people: a static index built with the site (`kereses/index.json`, loaded on the first keystroke), accent-insensitive, ranked by exact / prefix / contains, filters by kind and cycle, `?q=` deep links
- [x] **Felszólalások** — the sitting days' speech lists (`felszolalasok.cgi`, one call per day; `python3 -m karzat sync-speeches --ckl N`; `scripts/derive_speeches.py`): who spoke on which agenda item, what kind, in what role, for how long; speakers resolved with the roll calls' resolver (ministers and the President who are not MPs stay named, unresolved). Substantive vs procedural is a named list of kinds (`normalise.SUBSTANTIVE_KINDS` — chairing, announcements and the chair's result lines are procedural, personal-involvement remarks too), and the MP record's own per-cycle count agrees with it for 187 of 189 cycle-43 MPs and 218 of 220 in cycle 42 (the pages print both where they differ). Cycle 43: 4,651 rows over 23 sitting days, 1,957 substantive; cycle 42: 36,722 rows over 192 days. SQLite v3: `speech` and `mp_committee` tables. Also found: the API's `ulesnap` dates for the older cycles are one day early against their own weekday names — corrected from the weekday, flagged per row
- [x] **Mit csinált a képviselőm** — every MP page opens with the last sitting week in numbers (névsorban / leadott / frakciója ellen / érdemi felszólalás, then the speeches by agenda item, kind, length) and a table of every sitting week; committee memberships from the record; the speeches themselves, filterable; per-MP weekly Atom channel (`kepviselo/<azon>-heti.xml`), the cycle's week in numbers (`feed/heti.xml`), `adatok/heti.csv` and `adatok/felszolalasok.csv`; a `felszolalas/` page (days, kinds, the record check) and `kepviselom/` — Ki a képviselőm? by county and OEVK, the list MPs, a name box (settlement lookup is not there: the constituency settlement lists are not loaded, and the page says so). Numbers, no prose: label form, no suffix on a numeral
- [x] **Felszólalások szövege és keresés** — `karzat sync-speech-texts --ckl N` fetches the record's text of every substantive speech (`felszolalas.cgi`, one call per speech; the payload names the MP by id where the speaker is one — it agrees with the lists' name resolution in every case), `scripts/derive_speech_texts.py` keeps them as plain paragraphs (the current cycle's file is committed; an earlier cycle's is tens of megabytes and stays local — fetch and derive it, the builder picks it up). Every substantive speech gets a page and a JSON twin (`felszolalas/<ülésnap>-<sorszám>`), every sitting day a page in the day's order grouped by agenda item; `felszolalas/kereses.html` searches the texts by word prefix (3+ letters, accents folded, terms AND-ed) over an index sharded by the token's first two letters, showing the record's own snippet; `feed/felszolalasok.xml` carries the recent speeches with their full text so a reader's filter can watch a keyword — that is the whole "topic radar": no topic classification, no summary, no server; committee sittings are not in the API and the page says so. Cycle 43: 1,957 texts, 7,328,808 characters. SQLite v4: `speech_text`
- [x] **A landing page, and one address per vote for good** — the site root is no longer the current cycle's directory but a page of its own: the chamber as it sits today drawn on the Országház floor plan with **every seat the member who holds it** — all 199 mandate-holders placed where their own record puts them, and with them the **12 nationality spokespersons** (`szoszolok.cgi` — a service I had registered and never called; they are elected by a nationality's list, sit in the back row of sectors 3–4, may speak and sit on committees, but cast no vote, so they are drawn as rings, kept out of every roster count and alignment, and their card says what they do instead). They also have a **career page** each under `szemely/` — the same address shape as an MP's, with committees and speech counts per cycle instead of votes, and a note that a cycle only appears there if the record's dated rows show it (the list itself is present-tense) — and their own kind in the search index (12 entries). The 58 seats still left as outlines are the room's spare places: it was built for 386 members, the House has had 199 since 2014 — hover or focus names them and shows the record their own page prints (leadott / frakciójával / ellene / érdemi felszólalás), a click pins the card, the name opens the page, the legend filters to one faction, and the arrow keys walk the seats; then the ten cycles since 1990 as stacked composition bars (mandates in force on each constituent sitting, read from the records' dated rows — 10 in the strip), the last sitting day with its votes and the freshness sentence, and the doors (Ki a képviselőm?, keresés, a szavak, a jelenlegi ciklus, pályaképek, módszer) as plain forms that work without JavaScript. Every cycle now lives under `ckl<N>/`, **the current one included** — before this, a vote's URL would have changed meaning at the next election, when the root switched to cycle 44; now `ckl43/szavazas/<slug>.html` is what it says it is, and the citations minted on those pages stay true
- [x] **A nap menete** — a vote page used to be an island: a decision with a chamber and a roll call, and no way to tell whether it was one vote or the fortieth of an afternoon that all went the same way. Every vote page now carries the whole sitting day as a strip — one bar per vote in the order they were taken, the margin above the axis where the decision passed and below where it did not, a tick for a quorum check that has no threshold, and the vote you are reading marked. Each bar links to its own page, and its title names the time, the subject and the difference. A day longer than 160 votes is windowed around the vote you are on and the panel says so. The data is the cycle's own index, read day by day, so it costs nothing new
- [x] **Published** — the site is live on AWS: a private S3 bucket in Frankfurt behind CloudFront with an Origin Access Control, TLS on CloudFront's certificate, `index.html` as the default root object and the bucket's 403-on-missing rewritten to a real 404 page. `scripts/deploy_aws.py` does the whole thing from a named profile — creates the bucket and the distribution if they are absent, uploads only the objects whose size or MD5 differs, sets content types and cache headers per extension, and asks for an invalidation. First push: 132,387 objects, 3.57 GB, 7.2 minutes. Storage runs about ten cents a month and the traffic sits inside CloudFront's perpetual free tier
- [x] **A mark of its own** — a favicon in the site's language: one half-ring of tiers over the Speaker's platform, white on near-black, inverted under a light UI. `FAVICON_SVG` is generated with the stylesheet and checked like it; `scripts/make_icons.py` renders the same geometry to `favicon.ico` (16/32/48/64) and a 180-pixel apple-touch-icon, so the build itself never needs Pillow
- [x] **A fact in the footer** — the boot log's last line is a fact from the corpus instead of a constant: which day the House voted most, the narrowest passage, the member with the most roll calls behind them. `scripts/derive_facts.py` computes them (never typed) into `data/derived/tenyek.json`, each with the page that proves it and the cycles it covers; **rankings are computed only over the months the listing cap never truncated**, so a record is a record and not an artefact of what survived the cut. Each page shows one, picked by a hash of its own address so the build stays reproducible, and a button rotates through the rest from a pool shipped at the site root
- [x] **„Ki a képviselőm?" takes a postcode** — the sharpest thing most people know about where they live. `scripts/pull_iranyitoszam.py` → `reference/valasztas/iranyitoszam.json`: Wikidata's P281 on Hungarian items, **joined to the annex by exact name only** (3,049 codes → 3,167 of the annex's 3,171 settlements; a town's range like 9000–9030 expanded), and Budapest by the rule the codes are built on (1XYZ → XY. kerület, all 23 districts). Four digits search as a postcode, anything else as a name; a three-digit number is answered, not ignored; a code shared by several villages (common post office) shows all of them. The caveat stays on the page: a postcode names a settlement, not a district — where the annex splits a city by streets, the address still decides. Found and fixed on the way: two annex names with a stray semicolon and three rows that were fragments of a law reference, not villages
- [x] **The Speaker's platform names who presides, and the cycle page's head can breathe** — the emelvény is not a seat in the plan: the Speaker and the deputy Speakers keep their floor seats and take the chair in turn, the clerks sit by it in rotation. The records date every office (`<tisztseg>`: elnök, alelnök, jegyző, háznagy, and the government's államtitkár rows too), so the platform is now drawn as the presidium's desk — the Speaker's place marked in the middle, the deputies at the sides in their faction colours, each a second handle for the same member (hover names them, the card says "az Országgyűlés elnöke"), the presidium of the vote's own day on a vote page and today's on the landing; nobody is moved, no seat is invented, and without data it is the bare platform. The records reach back: cycle 42's hero day shows Kövér László in the chair. And the cycle page's head — brand, tagline, a fourteen-link crumb line with a second cycle list, then the lede, all stacked — is now the term as the title ("2026. május 9. óta"), a kicker, the lede, and the cycle's pages grouped at the side under three small labels; the cycle switch lives in the top bar, once
- [x] **One chamber, every cycle** — the current cycle is drawn on the Országház's own floor plan; the earlier ones cannot be (the API gives `<ulohely>` for today only), and until now they were drawn in a different language altogether: bare dots on faint guide arcs. Now every chamber reads the same — each member sits in a seat cell of the same shape, the same glyphs carry the same six positions, the Speaker's platform and its label are there, and the seat inspector behaves identically. What differs is stated under the drawing instead of being implied by the style: "Ugyanaz a terem ugyanúgy olvasva, de a helyek kiosztása a miénk: frakciónként, azon belül szavazat szerint… ez tehát nem a valódi ülésrend." No spare seats are invented for those cycles — a cell exists only where a member sits
- [x] **A miniszteri pad** — a reader noticed the front bench was not full, and it was not: a minister need not hold a mandate, and those who do not are in none of the lists karzat reads. They do speak, though, and a speech payload names its speaker by id — so `scripts/derive_kormany.py` harvests the ids in the cycle's speech texts that are in neither the MP nor the spokesperson list, keeps the ones whose own record seats them on the bench, and takes the office from what the record of the speech prints. 5 of them (the deputy PM among them), each drawn as a faint disc, each with a page under `szemely/` listing their speeches. parlament.hu photographs only the members it lists — its portrait endpoint answers 404 for these five — so `scripts/pull_kormany_photos.py` looks them up on Wikidata and takes the Commons picture where one exists (3 of the 5), with the author and licence from the Commons API, shown in the image's title and on the page; the match counts only when the item is a human, the name agrees, and either P4966 is our own id or P39 is the office the record prints. The two without a free picture say so instead of showing a broken frame. Every portrait on the site, whatever its source, goes through the same black-and-white filter — cards included and a card that says "nem szavaz — a kormány tagja mandátum nélkül". What this cannot find is stated rather than hidden: a government member who has not spoken this cycle stays invisible, so 5 bench seats are still outlines; and a former MP's record that keeps an old seat is dropped rather than drawn (one such claim). Cycle-43 vote pages name them on their seats too — correctly as non-voters
- [x] **Every cycle since 1990** — the backfill of cycles 34–41 is in: 79,829 votes over ten cycles (1990-05-02 → 2026-08-11), 49,771 of them roll calls that name the members — none before 1998, the API's `<nev_szerint>` is empty then, so cycles 34 and 35 have the faction tallies only and their rosters come from the records (416 and 386 people); 1,602 people in all. Cycles up to 41 are built as an **archive**: light vote pages (verdict, tallies, faction bars, motions; no chamber, no per-vote roll-call table or data twins — the roll calls are in each cycle's `adatok/nevsorok.csv.gz` — gzip, 150–190 MB plain for a 1998–2010 cycle — and in the SQLite), MP pages without the full vote table; feeds, exports, cohesion, close votes and the rest as for the two live cycles. The old payloads taught the parser three more things: unescaped `"` and `&` (repaired), a header that names one more vote than its roll call (the votes cast are the base then), and cycle labels printed as "1990-94" / "1994-98" in the records. Rule vs source across the corpus: 15 disagreements in 79,829 — after two evidence-based corrections to the bases (below); before them, 272 in cycle 38 alone
- [x] **Speeches 1998–2022** — the lists of cycles 36–41 synced: 1,643 day lists, 453,818 rows, 183,796 substantive, 444,626 rows resolved to MPs (the API has no speech lists before 1998, nor an ülésnap list — cycles 34 and 35 stay without); the record agrees with the substantive count for 2,058 of 2,082 MP-cycles — after the old lists' own words for a speech joined the rule (`expozé`, `önálló indítvány indokolása`, `sürgősség indoklása`, `kivételes eljárás indoklása`: none of them occurs in cycles 42–43; before them the record disagreed for two MP-cycles in five); the rest are off by one or two. 540 of the 1,643 day lists print the day one day early — the same shift as the old ülésnap lists — while every row's own `<felszkezdete>` carries the right day, so the rows win and the header is kept as `date_listed`. The unresolved rows are ministers without a mandate, the ombudsmen, the Court's and the Audit Office's presidents, Nelson Mandela. An archive cycle gets no page per speech without the record's text (tens of thousands of near-empty pages per cycle otherwise): the rows live on the day pages with an anchor each, and the MP panels, the weekly digests, `felszolalasok.csv` and the database are filled in. The texts of these cycles are not fetched: 183,796 calls at the polite pace, about 31 hours — a decision for later
- [x] **Mennyit beszéltek — and the trap under it** — every speech row has carried its recorded length all along and nothing on the site had ever added two of them together, which is why nothing had met what happens when you do. A speech held in a joint debate is listed again under every motion the debate covered, with the same length, so summing the rows as they come overstates a cycle by 1.29× (cycle 43) to **6.68× (cycle 37: 16,559 hours against 2,480, which is 58 hours of talking per sitting day)** — and unevenly, because joint debate is not distributed evenly between the sides. `speaking_time()` counts each speech once, and the cycle's numbers page now shows the floor faction by faction beside its share of the seats: in cycle 43 the largest group holds 70% of the seats and 52% of the microphone, the smallest group in the table 3% of the seats and 12% of the microphone. Members' pages carry their own time. Four tests hold the rule — including one that fails if a future capture stops marking the copies at all, because that is the failure that would double every total in silence
- [x] **Who is this, before the numbers** — a reader landing on a member's page met a table of participation rates before they knew who they were looking at. The page now opens with a paragraph the record writes itself: faction and constituency, how many cycles and since when, the offices held (the ceremonial one-day posts filtered out by length, not by anyone's opinion of importance), the latest unbroken run of a local-government post, current committees, the most recent degree. Nothing is inferred — a clause whose field is empty is absent rather than guessed — and no value out of the record is ever inflected, because a generated Hungarian suffix on "Vasvár Város Önkormányzata" is the one thing in such a line that could be wrong. It covers all 1,602 people. The obvious alternative was to summarise the record's own biography PDF with a language model, and it was measured before it was rejected: those PDFs exist for 184 people, carry a private mobile number and a personal e-mail address in the header, and would have put the only sentence on the site that no source said in the place where an error is least detectable
- [x] **A stale stylesheet, and the three things it broke** — a reader reported that the day strip was black rectangles on a black ground and the career timeline was black text in a black box. Both drawings were correct; the stylesheet in front of them was a day old. `karzat.css` and `karzat.js` are versionless names that were being cached for a day, so new HTML met old CSS, and without the rules an SVG falls back to what SVG defaults to: fill black, size intrinsic. They now expire with the HTML they belong to — small files, and CloudFront answers the revalidation. The lesson generalises past this bug: a page and the stylesheet that draws it should not have different lifetimes
- [x] **The terminal keeps one line** — the footer printed four lines of true numbers on every page: the corpus, this page's cycle, the rule-versus-record check, the sync stamp. All four already live where they belong — the totals on the landing page, the freshness sentence above the fold, the disagreements on the method page — so the terminal keeps only the line that is different on every page and worth stopping for: a fact from the corpus, with the page that proves it
- [x] **The life path, in HTML** — the career timeline was an SVG scaled to the panel, which scaled its type with it: lane names arrived at whatever size the viewport decided, and the only way to learn what a bar meant was to hover it, which a phone cannot do. It is HTML now — the page's own type, the lane names as text, and every span carrying its name beside it. Two things that were unreadable are fixed with it: a post held term after term (six mayoral terms at one town) merges into the one span it describes instead of six labels fighting for the same pixels, and spans that genuinely overlap (a committee office during a ministry) stack on their own rows rather than on top of each other
- [x] **The pictures are ours, and they are small** — the pages had always linked a portrait where it lives rather than copying it, which is the polite thing to do and also the slow one: a 40 kB colour JPEG per face, fetched from another host, then turned grey by the browser on every paint. `scripts.pull_portraits` walks the 1,617 pictures the rosters name and caches the 1,595 that answer (22 return 404 and simply have no picture anywhere), and `scripts.make_portraits` renders each to a 192×256 grey WebP — twice the largest size any page draws it at, the tone baked in — averaging about 4 kB: 57.1 MB of colour JPEG becomes 6.3 MB. The build reads a committed manifest rather than the output directory, so the markup cannot drift from what was rendered; a person the manifest does not know keeps their original URL. CloudFront serves them for a month rather than a day. This is redistribution rather than a hotlink and parlament.hu states no terms of use for the pictures, so it stays what it was before: the owner's decision, recorded here
- [x] **The Speaker's platform in the room's own scale** — the dais drew its office-holders with a smaller token than the floor's seats, so the same person had two sizes depending on where you looked at them. Now a chair on the platform is a floor seat's own glyph, and the platform is sized from that: the deputies' arc keeps a third of a seat between neighbours, the dais is wide and tall enough to hold the arc inside its edge with a margin, and the Speaker's marked place sits forward of the arc, just larger than the mark itself
- [x] **The 400-row cap, and 17,285 votes that were never there** — mining the corpus for footer facts turned up 54 months holding exactly 400 votes, which is not how a parliament sits. `szavazasok.cgi` returns at most 400 rows per response and keeps the **latest**; its parameters take dates and not times, so a truncated month cannot be paged, offset or narrowed by hour. Re-listing those months day by day (1,644 calls, 47 minutes) recovered **17,285 votes**, and fetching each one's roll call took 17,285 more calls and four hours with nothing failing. The corpus is now **79,829 votes, 49,771 of them naming the members, 17,444,402 roll-call positions** and 0 unresolved names — cycle 34 alone went from 13,746 to 21,674 votes. The merge that folds the day-level listings back into the monthly ones keys on the vote's own timestamp, because the two overlap heavily; concatenating them counted 22,254 votes twice, which is how the first draft of this paragraph came to claim 102,083. Two invariant tests now hold the line: a vote is listed once per cycle, and the roll-call store may not name a vote the index does not list. What is still short is written down rather than smoothed over: **26 sitting days** had more than 400 votes in a single day, and for those the service has no route back — `reference/parlament/listakorlat.json` records the cap, the months it touched, what came back and what did not
- [x] **Ameddig a jegyzőkönyv elér** — a page whose subject is the data's own edge. Month by month since 1990, the height of a column is that month's votes and the bright part is how many the House recorded by name; two lanes under the axis mark the 26 permanently short days and the months containing a secret ballot. It states two things plainly that no chart of ours had: **before 1998 there is no roll call at all** — cycles 34 and 35 hold 29,818 votes and one name-level list between them, because `<nev_szerint>` is empty in those payloads, not because the fetch was lazy — and the 256 secret ballots have nothing to download by definition. The counts are the same derived indexes every cycle page is built from, so the page cannot drift from the site around it
- [x] **A Ház arcéle** — the first page that reads across the ten cycles rather than inside one, and it is deliberately asymmetric. Mandates are recorded completely since 1990, so **how many members are serving a first term** is a real series and is drawn as one: 100% in 1990, down to 15% in the 199-seat House of 2014, and **79% in 2026** — the largest turnover since the first free election. Degrees, languages and local-government service are *not* drawn that way, because they come from each person's record as it stands today: 273 of cycle 34's 416 members carry a schooling row against 383 of 386 in cycle 35, so a column chart of it would show the archive being tidied, not the Parliament changing. Those three appear once, over all 1,602 people, with a table of exactly how completely each cycle is filled in printed beside them
- [x] **Five sections of the MP record the site had never read** — the reply to `kepviselo.cgi` carries seventeen blocks and the parser was using eleven. No new call was needed: the 1,636 cached records already held **2,561 schooling rows, 2,033 languages, 9,303 asset declarations, 1,094 local-government seats** and a month of remuneration for the 205 people currently entitled to one. Each career page now draws **az életút** — the cycles sat, the offices the record dates, the local seats held before or between, and the years the degrees carry, all on one time axis, with a span the record leaves open running to the newest vote on the site rather than to the clock — and beneath it the declarations as one square per document linking the published PDF (hollow where the record says one was due and gives no link), the one month of remuneration labelled as the snapshot it is, and the degrees and languages as the record states them. `<ulohely>` and `<email>` are present in every record and empty in every record; the parser leaves them alone and the seat keeps coming from the seating plan
- [x] **A törvény útja** — `iromany.cgi` had been called exactly once, as a probe. `karzat sync-bills` now caches the current cycle's whole list and every record (539 motions, 537 records, ~5 minutes), and a motion's page draws its road: submitted, every vote the record dates as a tick that links to that vote's page, promulgated as a square with the law reference, a hair on the axis for every day the log touches, the committees assigned to it, and the complete event log — 4,779 events across the cycle, 35 promulgated laws — printed whole rather than filtered through my guess at what counts as a milestone. Current cycle only, and that is the service's limit, not a choice: `iromanyok.cgi` takes no cycle parameter (`p_ckl` was probed and changes nothing) and `p_izon` is a sequence that restarts each cycle, while the vote payloads carry the irományszám and never the izon — so the 14,056 motions the older cycles' votes point at cannot be addressed at all, and the page says so instead of implying the archive is thin
- [x] **Loose ends** — portraits: every MP page, the seat inspector, the MP index, the finder and the career pages show the parlament.hu photo (the record's `fenykep`, 195×260), loaded from there and not copied, in a black-and-white treatment (`filter: grayscale(1) contrast(1.12) brightness(.92)`), attributed on the image; the terms of use are not stated on parlament.hu, so this is the owner's decision, recorded. `bizottsagok.cgi` exists as the manual's broken cell suggested: `karzat sync-committees` (35 calls, always fresh) caches the 26 committees' records — type, chair, vice-chairs, members by id, and the next sitting's invitation date and URL when one is out; `bizottsag/` per cycle (the API's present-tense records for the current cycle, the MP records' membership histories for every cycle, subcommittees under their parent), a `feed/bizottsagok.xml` channel of announced sittings. "Ki a képviselőm?" takes a town: `reference/valasztas/oevk_telepules.json` is the electoral-district law's annex (2011. évi CCIII. tv., 2. melléklet, the text in force from 2024-12-31, njt.hu) parsed into 106 OEVK and 3,171 settlement/district names; the 23 cities and districts the annex splits by streets list every candidate and say the address decides. And the legal citations in `karzat/majority.py` were read against the consolidated Alaptörvény and House Rules on njt.hu — every VERIFY flag is gone except the API's 4/5-of-all label from 2014, for which neither text gives a basis
- [x] **Értesítések** — Atom feeds written by the builder, so a scheduled rebuild is the alert channel (`karzat/feeds.py`; `feed/` per cycle): every roll call with a dissent (`kulonvelemeny.xml`, one entry per vote listing who voted against their faction's plurality), the same per faction (`frakcio-<slug>.xml`) and per MP (`kepviselo/<azon>.xml`), and every vote (`szavazasok.xml`); a page explains them and lists them, `adatok/kulonvelemenyek.csv` is the complete record, the index and MP pages announce their feeds for autodiscovery. Independents are not a faction and are not in the channels (the CSV keeps their rows). Each channel carries the newest 100 entries but never less than the last five sitting days (a marathon day has 160+ votes). Deterministic: ids are URNs from cycle, vote time and channel, `updated` is the sync stamp, never the clock; links are absolute when `KARZAT_SITE_URL` is set for the published build and relative to the feed file otherwise. Cycle 43: 92 roll calls with a dissent, 240 dissents in all; the same numbers the MP pages and the cohesion page print, tested to agree
- [x] **Cohesion and close votes** — `karzat/analytics.py`: Rice and Hix–Noury–Roland agreement indices per faction per vote and per cycle, the faction × faction plurality-agreement matrix, MP × MP agreement within a faction (≥ 20 shared votes), all with the formulas printed on `kohezio/`; and `szoros/`, the decisions ranked by margin to their threshold, the yes-majorities a qualified threshold sank, and — a labelled counterfactual — the outcomes that would flip if every non-caster had voted with their faction's plurality; every vote page shows each faction's AI beside its bar; CSVs for all of it
- [x] **Cycle 42 pages** — the same builder, one level down (`site/ckl42/`): its own index, 2,599 vote pages and 214 MP pages, a cycle switch in the top bar and cross-links between an MP's two pages; inputs `votes_index_ckl42.json.gz` + `votes_positions_ckl42.json.gz` (deterministic gzip, 424 KB together) + `mps_ckl42.json`; the missing `kepviselo.cgi` records fetched (171 of the 214 people are not in the cycle-43 roster); factions attributed by each person's last roll call (10 switchers)
- [x] `sync-mps`: all 199 MP records; the **real seating plan** reconstructed from `<ulohely>` (`karzat/seating.py`, `scripts/derive_seating.py` → `data/derived/seating.json`) and drawn on the page — 197 of the hero vote's 199 placed, the 2 MPs whose mandates have since ended kept visible without a seat
- [x] **SQLite loader** (`karzat/load.py`, `python3 -m karzat load` / `stats`): schema v3 (v2 at the time) built from scratch from the cache — twelve seconds for two cycles then, twelve minutes for ten now — 79,829 votes, 17,444,402 roll-call positions with 0 unresolved names, 3,580 faction-history rows, 3,278 mandates across cycles, 539 bills — plus a per-vote faction-plurality table and views (`v_vote`, `v_mp_alignment`) so discipline and absence are plain SQL
- [x] **Per-vote pages**: `site/szavazas/<slug>.html` for all 259 votes — the reconstructed chamber for that vote, the verdict card, faction bars, and a sortable, filterable roll call with every MP's seat; prev/next; the directory links to them. Generated from the committed `data/derived/votes_positions.json` (git-ignored output, 0.6 s for all 259)
- [x] **MP pages**: `site/kepviselo/<p_azon>.html` for all 201 people in the roll calls plus an index — mandate, seat highlighted on the chamber, faction history and mandates back to 1990, motion counts, this cycle's participation and with/against-own-faction record with definitions on the page, the votes cast against the faction plurality, and every vote linked. Portraits shown (since **The pictures are ours** above, served from our own origin; before that, linked). Names on vote pages link here
- [x] **Cycle 42 backfilled** (2022-05-02 → 2026-05-08): 2,599 votes over 192 sitting days listed and every roll call fetched (2,445 calls at the polite pace, about 26 a minute, in one afternoon); a second Wikidata snapshot for the names; the list-level summary in `data/derived/first_light_ckl42.json`, the comparison below; both cycles in the database. What the backfill taught the vocabulary: a **seventh** roll-call state, `Ig.távol` (excused absence — 1,223 entries in 288 of cycle 42's roll calls, none in cycle 43's first 259), and how the faction rows aggregate the seven into five columns (`igtav_db` = Ig.távol + Előre bejelentett hiányzó, `nemszav_db` = Nem szav. + Jelen, nem szav.) — schema v2, one real fixture, one test

## Run it

Python 3.11 or newer (`zoneinfo`, `str | None`), `requests`; on Windows also `tzdata` (see `requirements.txt`).

```bash
python3 -m unittest discover -s tests -t .      # offline; 297 tests
python3 -m scripts.check_readme                  # every registered number in this file, recomputed (--sync rewrites)
python3 -m karzat dry-run                        # request URLs, no network
cp .env.example .env                             # then paste the token
python3 -m karzat probe                          # one call: encoding, root tag, counts, first record
python3 -m karzat sync-votes --from 2026-05-01   # month-windowed lists + one call per vote, cached
python3 -m karzat sync-mps [--refresh]           # MP list + one call per MP, then the nationality spokespersons and their records, cached (--refresh: re-fetch, the records' per-cycle counts grow)
python3 -m karzat sync-speeches --ckl 43         # one call per sitting day: the day's speech list, cached (the newest day re-fetched)
python3 -m karzat sync-speech-texts --ckl 43     # one call per substantive speech: the record's text, cached (--pace 1.5 beside another sync)
python3 -m karzat sync-committees                # the committee list and every record (~35 calls, always fresh: they carry the next sitting)
python3 -m karzat freshness --fetch              # one ulesnap call; writes data/freshness.json + the sentence
python3 -m karzat inspect data/raw/szavazas/2026-09-15T09-37-07.xml   # cached XML → JSON
python3 -m karzat fingerprint tests/fixtures/*.xml   # digest table for tests/test_golden.py
python3 -m scripts.pull_wikidata                 # re-pull the Wikidata member snapshot (deliberately)
KARZAT_SITE_URL=https://<host>/<path> python3 -m scripts.build_site   # the published build: absolute citation and feed links
python3 -m scripts.derive_first_light            # cache → data/derived/{first_light,votes_index,hero_vote}.json
python3 -m karzat load --since 2026-05-01        # cache → data/karzat.sqlite (rebuilt from scratch, git-ignored)
python3 -m karzat stats --json                   # counts + sanity queries; writes data/derived/db_summary.json
python3 -m scripts.derive_seating                # kepviselo records → data/derived/seating.json (the chamber)
python3 -m scripts.derive_mps                    # MP records + list + Wikidata → data/derived/mps.json
python3 -m scripts.derive_szoszolok              # the nationality spokespersons → data/derived/szoszolok.json (seat, nationality, committees)
python3 -m scripts.derive_kormany                # the ministerial bench's non-MP members → data/derived/kormany.json
python3 -m scripts.pull_kormany_photos           # Wikidata/Commons photographs for them (parlament.hu has none) → reference/wikidata/kormany_photos.json
python3 -m scripts.pull_iranyitoszam             # postcode → settlement (Wikidata P281 + the Budapest rule) → reference/valasztas/iranyitoszam.json
python3 -m scripts.build_site                    # data/derived + config → the landing page + one tree per cycle under site/ckl<N>/ (--check verifies the landing and the assets)
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
| parlament.hu ülésrend (seating map) | Coordinates for real seats, if `<ulohely>` maps onto a public plan | Found: the site's own felicitas query returns the 274 seat polygons; saved as `reference/parlament/patko_seats.json`, drawn by `karzat/seating.py` |
| Magyar Közlöny (magyarkozlony.hu, single PDF per issue, RSS at `/feed`) | Decrees only | Not needed for laws: `iromany.cgi` carries kihirdetés száma / MK száma / dátum |
| njt.hu (Nemzeti Jogszabálytár) | Better structured than the gazette for "what the law says" | Phase 2 candidate |
| valasztas.hu | Constituency results per MP (margin, egyéni vs. lista) | Nice-to-have enrichment |

## The model (draft)

`schema.sql` is the target; every VERIFY comment is a claim about the XML I have not
been able to check. The decisions in it that a reader might expect to have gone the
other way, and why:

| Decision | Why |
|---|---|
| one chamber, no `chamber` column | the Országgyűlés is unicameral |
| `ckl` (43) and `session_label` (tavaszi/őszi) rather than a congress number | parlament.hu numbers terms from 34 (1990); 43 = 2026– |
| a vote is keyed by `vote.ts` (idopont) and a slug, not a roll number | the API identifies a vote by timestamp and nothing else |
| seven positions, not three | abstention is a political act here and gets its own glyph; presence is recorded separately from voting; there are two kinds of excused absence |
| `faction_id` plus an `mp_faction` history | there are several factions and members move between them across cycles |
| `majority_rule` and `needed` on every vote | the rule differs from vote to vote and the source states it — this is the whole analytical point |
| `mp_mandate.kind` egyéni/lista with a constituency | a mixed electoral system |
| `bill.submitters` with `submitter_kind` kormány/képviselő | government against individual-member bills is the transparency angle |
| the directory paginates rather than capping | a sitting day can hold hundreds of votes |
| portraits shown from parlament.hu, in black and white, attributed on every image | the terms of use are not stated there — the owner's decision, recorded |

## Majority rules — the analytical core

`karzat/majority.py` gives every vote an explicit rule, base, threshold and margin. The
arithmetic is fixed ("több mint a fele" → ⌊N/2⌋+1; "kétharmada" → ⌈2N/3⌉; "négyötöde" →
⌈4N/5⌉); the legal citations were read against the consolidated texts on njt.hu on 19 August
2026 — Magyarország Alaptörvénye and the House Rules (10/2014. (II. 24.) OGY határozat, HHSZ) — and
name the paragraph read; one rule the API's vocabulary carries ("az összes képviselő 4/5-ével",
seen in 2014) has no basis I could find in either text and stays flagged.

| Rule | Base | Needed (N=199 / present 176) | Applies to (citation) |
|---|---|---|---|
| `egyszeru` | present | 100 / 89 | default — Alaptörvény 5. cikk (6); quorum 5. cikk (5) |
| `abszolut` | all MPs | 100 | miniszterelnök választás 16. cikk (4); bizalmatlansági / bizalmi szavazás 21. cikk (2)–(3); kivételes eljárás elrendelése HHSZ 62. § (1) |
| `ketharmad_jelenlevo` | present | 133 / 118 | sarkalatos törvény T) cikk (4); házszabályi rendelkezések 5. cikk (7); sürgős tárgyalás HHSZ 60. § (7) |
| `ketharmad_osszes` | all MPs | 133 | Alaptörvény módosítás S) cikk (2); alkotmánybírák 24. cikk (8); Kúria elnöke 26. cikk (3), legfőbb ügyész 29. cikk (4), alapvető jogok biztosa 30. cikk (3), ÁSZ elnöke 43. cikk (2); KE 1. forduló 11. cikk (3); zárt ülés 5. cikk (1) |
| `negyotod_jelenlevo` | present | 160 / 141 | Házszabálytól eltérés HHSZ 65. § (1) |
| `negyotod_osszes` | all MPs | 160 | the API's own label, 2014; no basis found (VERIFY) |
| `relativ` | candidates | — | KE 2. forduló 11. cikk (4): the most valid votes; not decidable from a yes/no tally |

Two modelling choices, both deliberate and both flagged in every verdict: **abstainers are
present** (tartózkodás counts against a motion under every present-based rule), and when the
source does not state the number present the module falls back to yes+no+abstain and says so
(`present_assumed`). What "present" is, the record decided once eight cycles were in: the base
of the present-based majorities is the **votes cast** — igen + nem + tartózkodott, the API's own
"Összes szavazat" — and MPs registered "Jelen, nem szav." are not in it. Counting them in
contradicts the House's own Elfogadva/Elutasítva 272 times in cycle 38 (2006–2010), 15 times in
39, 4 in 40, 2 in 41; counting only the votes cast, never (25,000 present-based decisions across
cycles 38–43, one plain secret ballot in 2017 aside). Likewise the base of the all-MP rules is
the mandates in force on the day, not a constant: a two-thirds-of-all secret ballot on
2014-12-01 passed with 132 yes, two-thirds of the 197 mandates then held (`normalise.SeatsInForce`,
from the records' election rows; falls back to 386 / 199). Which rule applies is decided by, in order: an explicit statement in the
payload (vocabulary table to be filled from real XML), keyword evidence in the vote subject or
bill title (each hit reported with the matched text), and only then the *default* of simple
majority — recorded as such, so the site can never quietly show a default as a fact.

Per Wikidata on 2026-08-18, the 199 members of cycle 43 sit in four groups — TISZA 141,
Fidesz 44, KDNP 8, Mi Hazánk 6 — which, if the API confirms it, puts one group above the
133-seat line on its own. That is why this module comes before any pixel of UI.

## The first page

`site/index.html` plus two generated files under `site/assets/` (`karzat.css`,
`karzat.js`; no CDN, no fonts fetched, one external link type: parlament.hu bill pages),
Hungarian-first. The look is a console, deliberately: a near-black ground with a faint
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
resolver turns those into people; 974 submissions across the current cycle's 539
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
big — over a gigabyte of HTML, JSON and CSV, because every roll call is a page with two data twins and every MP page
lists every vote — and git-ignored like the cycle-43 pages.

## The database

`schema.sql` is the contract and `karzat/load.py` the only writer. `python3 -m karzat load
--since 1990-01-01` rebuilds `data/karzat.sqlite` from the raw cache — all ten cycles, about
twelve minutes and 3.5 GB (two cycles took twelve seconds) — atomically, and runs a
foreign-key check at the end; nothing is ever edited in place. Loaded today: 1,636 MPs (217 with a seat in their record — the 199
sitting, and one former MP whose record still carries a leftover seat — 1,602 with a Wikidata
QID, plus 1,437 rows for former MPs — every cached `kepviselo.cgi` record loads, and the
cache now holds everyone since 1990 for the name resolver), 3,580 faction-history rows and
3,278 mandates back to 1990, 1,858 sitting days,
79,829 votes of which 49,771 carry a roll call — 17,444,402 positions, 0 unresolved names — 50,335
motions, 539 bills, and 299,811 rows of `vote_faction_majority` (each faction's plurality
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
consistency check that matters is in `stats`: 15 votes where the computed threshold and the
recorded result disagree — now across ten cycles and 49,771 roll calls, not 259 (each of the
15 is marked on its page and listed in the cycle's directory; the base corrections that took
cycle 38 from 272 to 2 are in the method note). Numbers here
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

A site can pulse "live" while its ingest has been dead for weeks, and nothing on the
page would say so. This one cannot: `karzat/freshness.py` takes the sitting days known from `ulesnap.cgi`, the newest
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
- **Six positions, explicit majority rule, faction history** — the things a three-state, single-threshold model cannot express, fixed in the schema rather than in the UI.
- **Members are joined by `p_azon`, never by name.** The roll call prints names + faction; names are unique in the current list and resolve exactly, honorifics included; former MPs resolve through Wikidata aliases (P4966 = `p_azon`) or stay explicitly unresolved.
- **Raw XML is the source of truth on disk; SQLite is derived** and can be rebuilt. This is the same
  "generated, not typed" discipline as the assay repos.
- **Working assumption for the front end: static site + JSON, built by a scheduled job** (the token held as a secret, never on disk), published as static files. No server, no auth. Whether the visual language goes for an animated terminal or takes a calmer line is still open.
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
days: 258 decisions and 1 quorum check, 3 of them secret ballots. With every roll
call in hand (the secret ballots have none), the majority module's verdict agrees with the Országgyűlés's own
Elfogadva/Elutasítva on 259 of 259 — 0 disagreements — which is the first evidence
that the "present" interpretation is at least not contradicted by the record. 190 were plain
"Listás" votes; 47 needed two-thirds of those present, 7 two-thirds of all 199, 6
more than half of all 199, 5 four-fifths of those present — 67 qualified-majority
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
quorum checks (10 of them inquorate, "Határozatképtelen"), 20 secret ballots; 556
qualified-majority votes, 22% of decisions; 28 exceptional-procedure orders and 13 urgent
ones in four years; 406 interpellation answers accepted; 5 departures from the House
rules. Cycle 43 so far: 259 votes over 23 sitting days — 11.3 per sitting day — 67
qualified-majority votes, 26% of decisions; 5 exceptional-procedure orders and 10 urgent ones
in the first three months. The mode vocabulary is identical across both cycles (the
four-fifths-of-all form appears only in 2014). What these counts mean — a busier or a more
rushed parliament, a larger or a smaller share of two-thirds legislation — is exactly what
this repository does not yet claim; both cycles' roll calls are in the database, and
cycles 34–41 are downloading, for whoever wants to ask properly.

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
site/              index.html — the first page, generated, guarded by --check and tests/test_site.py · assets/karzat.css, karzat.js — the shared look and the boot sequence, generated and checked the same way · per cycle (root = current, ckl<N>/ = earlier): szavazas/ (one page per vote + .json/.csv) · kepviselo/ (one per MP + .json/.csv, index) · iromany/ (one per bill, index) · kohezio/ · szoros/ · szamok/ · felszolalas/ · kepviselom/ · feed/ (Atom channels + their page) · adatok/ (tables + data dictionary) · site-wide: szemely/ (careers) · modszer/ · kereses/ (all generated, git-ignored)
tests/             offline tests: client/XML · normaliser on real payloads · majority arithmetic · freshness sentences · golden fingerprints · README gate; fixtures/ (real W-API captures + one synthetic)
schema.sql         normalised store (SQLite), v3 (speeches and committee memberships; v2 added the seven roll-call states) — built by karzat/load.py into data/karzat.sqlite (git-ignored)
config/factions.yml faction ids/colours/order (verified spellings), the six positions, majority rules with their API labels
reference/         parlament-webapi/ (manual v2.5, PDF + text) · wikidata/ (member snapshot + README) · valasztas/ (constituency annex, postcodes) · parlament/ (the listing cap)
data/raw/          git-ignored XML cache · data/derived/ committed summaries the README is gated on
```

## Every motion, not only the ones that were voted on

Until this point a motion had a page only if the House had held a recorded vote on it, and for the oversight
instruments that produced a rule worth stating as a set rather than a tendency: **the site published an
interpellation if and only if the member rejected the minister's answer**, because only a rejection is put to a
vote. 53 records carry `képviselő elutasította a választ` in their log. The registry holds
537 full motion records for cycle 43 — the listing names 539 and the difference is one
number under which three different motions run, of which the service returns one — and 408 of
them had no page at all.

Each `<esemeny>` in a record carries an `ülésnap/sorszám` reference into the speech corpus. All 3,606 of
them resolve to a row, and 1,644 resolve to a speech whose text the site already holds; the rest are the
chair's procedural announcements, which the record does not print as speeches, so nothing is missing. That makes
242 of the 399 oversight motions carry the exchange as it was spoken — 738
turns, 1,331,648 characters, question and answer both, nothing summarised.

I sorted the turns by the transcript's own sitting day and sequence rather than by the record's event date,
because most multi-turn exchanges happen inside a single day: the date is not an order. The existing event log
sorts by `(date, event text)`, which alphabetises a follow-up ahead of the question it follows. Three exchanges
legitimately open before the question — on `A/155` the member announced on sitting day 6 that he would not accept
a substitute answerer and the question was put on day 8 — and a rule that forced the question to the top would
hide exactly that.

A turn is any event whose reference resolves to a held text, labelled with the record's own words, rather than a
whitelist of the Hungarian phrases that name an exchange. The whitelist finds ten fewer turns, and four of them
are the member refusing a substitute answerer.

The panel is on the oversight pages only, and the pages say why. The same rule applied to legislation would add
48 records and 906 turns, but a question's two-to-five references *are* the question,
while a bill's are a slice of a multi-day debate that already lives on the speeches' own pages.

Four fields were being discarded in derivation, because `prop_map` keeps `@ertek` and drops `@href`. The costly
one is `Címzett`: filled on 399 of the 399 oversight instruments and on none of the
legislative ones, every value an office rather than a person, and nothing had ever read it. `Irományszöveg`'s
value is the literal label "szöveges PDF", so anything rendering it as a link would have emitted
`href="szöveges PDF"`; only its href is the document.

A page never says a field is missing when that kind of motion cannot have it. The generated "the record does not
carry" line is gated on what at least half of the motions of the same kind do carry, which silences it on every
oversight page — otherwise each of them would have ended with a sentence regretting the absence of a committee
and a promulgation, which questions do not have. This is the same trap as the 12%-filled panel I rejected
earlier, and it took a reviewer to see that I had walked back into it.

I do not badge the status. Every answered interpellation reads `elfogadva`, including all 53 where
the record's own log says the member rejected the answer, so a coloured badge would be the one genuine lie on
the page.

## The passages that turn up in more than one mouth

The transcript is 60,703 speeches across four cycles, and `scripts/derive_echo.py` finds every run of thirty
or more words that appears verbatim in speeches by two different members: 1,253 of them. Finding them was the
easy half. Separating what they mean is the work, and it cannot be automated, because three quite different
things look identical to a matcher.

The first thing it found was a **template**: the Legislative Committee's report, read aloud by three members on
three different days, seventy-four identical words, because the wording is the committee's and they were taking
turns at the same job. At a ten-word window the whole top of the list was politeness formulas and the chair
announcing who was taking the chair — which is why the threshold is thirty, a measured number rather than a
chosen one. The second is **quotation**, the commonest reason two members share a passage: one is reading the
other back to them, usually to disagree. Only the third is a line being carried, and the page cannot tell you
which is which.

So it does not try. It records what is measurable — who said it first, who repeated it, how many days later,
whether they share a faction — and prints the passage. 741 of the repetitions are inside a single faction and
days apart; 344 cross factions. Neither number is added to the other and neither is given a name, because the
record cannot support one. A test asserts that the site's own prose never characterises a repetition; the
transcript it quotes may say anything, and does.

One figure I cannot explain and am therefore not interpreting: cycle 41 has 329 of its 437 repetitions inside a
single faction against 56 across factions, where cycle 42 is nearly even at 245 and 201. The share of speeches
that resolve to a faction is level across the cycles — 85 to 95 per cent inside the repetitions themselves — so
it is not an artefact of who could be identified. It is a real difference, and that is where the measurement
stops.

## Two records of the same fact, made to answer to each other

The House writes down a member's faction twice and never reconciles the two. Their own record carries dated
faction rows; every roll-call name list labels them independently at every vote. Where both exist, a claim about
a change of faction is checkable rather than merely sourced, which is the only kind of claim worth publishing.

There are 298 changes of faction inside a mandate since 1990, by 255 people, across
261 mandates. Those are three answers to three different questions and the inventory I started
from had merged two of them: its "261 switches" is the count of person-mandates, not of changes.
A change between mandates is not a switch at all — counting every adjacent pair of rows gives 985, because a
member returned across eight elections has seven boundaries and no switches.

135 of them can be tested against the name lists, and the second half of the inventory's claim —
that all of them agree — is not true either. 106 agree from the first vote after the change.
14 show the old faction on the day of the change itself and the new one
afterwards. 9 keep the old label for months: seven of them are the LMP members
who left on 12 February 2013, whom the lists went on calling LMP for another 366 days. And
6 name a faction neither record row mentions. Every one of the
15 is printed on the page with the member's
name on it, because a reconciliation nobody can audit is worth nothing.

Three ways I measured it wrongly first, each recorded in the code because each is a trap. Counting row-adjacency
rather than changes within a mandate. Leaving the window after a change open, so a member who went A to B to C
read as a disagreement at A to B. And grouping by the record's cycle field while testing against whichever
cycle's ballots I happened to be iterating, which put a 2013 departure against 2018 votes and looked exactly like
a year-long discrepancy — the same shape as the real one, which is why it took a third look to separate them.

## Watching the source that everything rests on

This project has one dependency and it is somebody else's server. parliament.hu publishes no changelog, its
payloads carry no version, and a record can be amended after the fact with no notice to anyone. Until now the
cache made that invisible by construction: a refresh overwrote the stored bytes, so an amendment upstream would
have been absorbed in silence and a number on a page would have moved with nothing anywhere to explain it. That
is the failure the whole freshness contract exists to prevent, arriving through the one door nobody was watching.

`WebApi.fetch` now compares before it writes. A refresh whose bytes differ keeps the superseded payload and
appends a line naming the service, the parameters, and both hashes — the token never among them, which a test
asserts. It costs one hash of something already in memory, and it turns every refresh the nightly already
performs into a check of the source against itself.

`scripts/watch_source.py` is what makes refreshes happen on purpose, and it is deliberately narrow and
deliberately slow. Narrow, because only records that should never change prove anything: a committee's
membership and a running cycle's motion change legitimately and constantly, but the detail of a vote held in
2019 is a closed historical fact, and if it moves that is news. Slow, because 120 records a night against 79,829
cached vote details is a two-year sweep — the right pace for an integrity check running on a server that is not
mine. Records go in rotation, least recently checked first, so coverage grows evenly instead of re-asking the
same thousand.

Twenty-five records asked so far, nothing rewritten. The interesting number is the one that is not zero, and it
may be years before there is one; the point is that if it happens, the old bytes will still be here.

## A receipt for every number

The site's rule is that a number is generated rather than typed, and the README gate enforces it. But
"generated" answers *how*, not *from what*. A reader looking at a count of votes has no way to tell which
payloads produced it, or whether the code that counted them is the code in front of them.

`scripts/derive_receipt.py` pins the three things that have to agree. The corpus: 148304 cached
payloads, 2.75 GB, their content hashes folded in sorted order into one root, per service and
then over the services. The code: the commit, and whether the working tree was clean — a dirty tree is recorded
as dirty rather than quietly attributed to the commit it sits on, because that is exactly the case where
somebody trying to reproduce a figure fails and cannot see why. And each derived file's own hash, so
`--verify` can check the receipt against the disk without re-deriving anything.

Content, never modification time. A refetch returning identical bytes must not look like a change and one
returning different bytes must, which is the same property the source watcher needs and the reason both exist.
The sensitivity is tested rather than assumed: adding an eight-byte comment to one payload out of
148304 moves the root, and restoring the file restores it.

What the receipt does not claim is that a given number came from a given payload. Tracking that would mean
instrumenting every read in every derivation. The honest cheaper answer is the one here — this figure came from
this corpus state under this code — and both halves are checkable, which is more than most published statistics
offer. It costs 41 seconds a night.

## The whole corpus in three files

The site publishes a CSV per cycle per table, which is right for a spreadsheet and wrong for a question that
spans 1990 to 2026: nine downloads and a join before you start. `scripts/derive_parquet.py` writes the same data
in one piece — 79829 votes, 17444402 cast positions, and
3263 member-cycles.

The size is the surprising part. Seventeen million positions come to
2.7 MB and all three files together to 4.3 MB, because the columns
that dominate the row count — member, faction, position — are low cardinality and dictionary encoding is close
to free on them. The entire named voting record of the Hungarian Parliament since 1990 is a smaller download
than a photograph.

It is also seekable, in row groups rather than one block, so a question can pull only what answers it over
ordinary HTTP range requests. That matters less at four megabytes than I expected it to, and it is the
groundwork for running the queries in the browser rather than on a server there is none of.

Two things are asserted rather than assumed, because both are ways a published extract can quietly disagree with
the site it came from. Every named vote's positions must sum to the tallies its own header carries — checked
across three thousand votes, since a mismatch would mean the file and the pages disagree and one of them is
wrong. And all seven positions must survive: collapsing them into yes, no and abstain is the commonest way this
data is misread, and an export that did it silently would hand the mistake to everyone who downloaded it.

## SQL in the reader's browser, with no server anywhere

The three Parquet files are 4.3 MB, which is small enough that a browser can hold the lot — so `sql/` loads a
database compiled to WebAssembly, registers the files as views, and answers whatever you type. Grouping 17.4
million cast positions by faction takes 734 ms; the harder example, a window function and two joins to find who
voted against their own faction's majority, takes 94. Nothing is sent anywhere, because there is nowhere to send
it: the query runs on the reader's machine against static objects on a CDN.

The runtime is vendored rather than loaded from a public CDN, and that is the decision worth explaining. This
site sets no cookie, runs no analytics and calls no third party, so a visit is known to nobody but the access
log this project keeps for thirty days. A single `<script src="https://cdn…">` would undo that quietly, for
every visitor. So `scripts/fetch_duckdb.py` pulls the six files once, rewrites their imports to relative paths,
and records every SHA-256 in a lock file; a refetch that does not match is an error rather than a warning,
because this is executable code served to readers. The 35 MB is git-ignored — a clone rebuilds it, and the page
says the runtime is not installed rather than half-working.

I red-teamed the page before shipping it, which found a third defect the tests would not have. Values in the
result table were escaped and column names were not, so `SELECT 1 AS "<img src=x onerror=…>"` put a live element
in the header and ran the handler — verified in a browser, not reasoned about. The reach is the reader's own:
they type the query, the origin holds no cookie and no session, and nothing reads a query from the URL. It is
still a known injection and it is fixed, with a test that asserts every interpolation into `innerHTML` goes
through the escaper rather than asserting the one payload.

The same pass established three things that are *not* wrong, which is worth writing down because I would
otherwise have assumed the first two and been unable to say so. The WebAssembly sandbox has no filesystem, so
`read_csv_auto('/etc/passwd')` returns "no files found". A query naming a remote URL is refused — but by the
other origin's CORS policy rather than by anything here, so a permissively-configured target would be fetched by
the reader's browser, and that is a property of the engine rather than a hole in the page. And a runaway query
had no brake at all: a cross join over 17.4 million rows sits in the worker until the tab is closed. There is a
cancel now; it terminates the worker, because nothing else reliably stops it.

Two more defects on the way, both of which only a real browser would have shown. The deploy had no content type for
`.wasm`, so the runtime would have gone out as `application/octet-stream`, which CloudFront does not compress —
34 MB on the wire instead of 6. And the wasm path was relative: it is fetched by the worker rather than by the
page, so `../assets/…` resolved against the wrong base, became `/assets/assets/…` and 404ed, surfacing only as a
WebAssembly compile error that named no URL. Every path in the loader is absolute now, and a test greps for a
relative one.

## Licence

Four different things live in this repository, and only one of them is mine to license.

**The code** — everything under `karzat/`, `scripts/`, `tests/`, and the templates inside
`build_site.py` — is MIT, in `LICENSE`. Take it, fork it, point it at another parliament's
API; that is what it is for.

**The record** is not mine and never becomes mine. Votes, members, motions, committees and
the shorthand record belong to the Országgyűlés and are reached through the W-API under a
registered token. Nothing in this repository grants you a right to them: if you want the
same data, register for your own token at parlament.hu. Attribute the House, not me.

**The derivation** — the shape I gave that record: the schema in `schema.sql`, the summary
files in `data/derived/`, and the per-cycle tables the site publishes under `adatok/` as
JSON and CSV — is the part I actually made, and it is [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Use the tables, cite karzat and parlament.hu. Where a derived file merely restates the
record verbatim there is nothing for me to license, and I claim nothing over it.

**The pictures** are the loose end, and the honest answer is that I do not know. The
portraits come from the House's own `fenykep` endpoint; parlament.hu states no terms of use
for them that I could find, and I did not receive an answer to asking. The site serves a
resized, greyscale copy from its own origin — a copy, not a hotlink, which is the more
exposed of the two positions — and every one of them is captioned with its source. The
image files are not in this repository (`site/assets/portre/` is git-ignored); a clone
rebuilds them from the cache or falls back to loading them from parlament.hu directly. If
the House objects, one constant in `build_site.py` turns every copy back into a link.
The handful of government members the House does not photograph are taken from Wikimedia
Commons instead, under whatever each file's own licence is, with author and licence printed
on the image and on the page.

The W-API manual in `reference/parlament-webapi/` is the House's own PDF, kept here because
the repository's claims about the API should be checkable against the document they came
from. The Wikidata snapshots in `reference/wikidata/` are CC0 from source.
