-- karzat — SQLite schema (v1, after the first real payloads, 2026-08-18).
--
-- Target shape for the normalised store the front end reads from. Columns are named after
-- what the W-API actually returns (see karzat/normalise.py for the payload → record mapping);
-- the few remaining VERIFY marks are legal interpretations, not payload guesses. Raw XML stays
-- on disk; this database is derived and can be rebuilt from the cache at any time.
--
-- Design decisions:
--   * a vote is identified by its timestamp (the API's idopont), not by a roll number;
--   * six recorded positions are first-class: igen / nem / tartozkodott / jelen_nem_szavazott /
--     nem_szavazott / bejelentett_hianyzo (the API's Igen / Nem / Tart. / "Jelen, nem szav." /
--     "Nem szav." / "Előre bejelentett hiányzó");
--   * the majority rule is stored per vote from the API's own "Szavazási mód", never assumed;
--   * faction membership is a history, not a single field, because MPs move.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS ciklus (
    ckl            INTEGER PRIMARY KEY,           -- parlament.hu cycle number (43 = 2026–)
    label          TEXT NOT NULL,                 -- '2026–2030'
    started_on     TEXT,                          -- ISO date
    seats          INTEGER NOT NULL DEFAULT 199
);

CREATE TABLE IF NOT EXISTS mp (
    azon           TEXT PRIMARY KEY,              -- kepviselo p_azon (alnum, e.g. 'a011', '006E'); = Wikidata P4966
    name           TEXT NOT NULL,                 -- as the API prints it, honorific included ('Dr. Árvay Nikolett')
    seat_sector    INTEGER,                       -- <ulohely szektor sor szek/>
    seat_row       INTEGER,
    seat_no        INTEGER,
    photo_url      TEXT,                          -- parlament.hu felicitas URL (licence VERIFY)
    website        TEXT,
    county         TEXT,                          -- kepviselok.cgi vmegye ('Vas' | 'Országos lista')
    constituency_no INTEGER,                      -- kepviselok.cgi vkorzet (egyéni only)
    wikidata_qid   TEXT,                          -- from reference/wikidata (crosswalk by P4966)
    raw_json       TEXT                           -- full to_dict() of kepviselo.cgi for later columns
);

CREATE TABLE IF NOT EXISTS faction (
    id             TEXT PRIMARY KEY,              -- exactly the API's kepv_csop / frakcio string: 'TISZA', 'Fidesz', 'KDNP', 'Mi Hazánk'
    name           TEXT NOT NULL,
    colour         TEXT,                          -- from config/factions.yml
    sort_order     INTEGER                        -- left→right position in the chamber diagram
);

-- One row per stint; an MP who changes faction has several. <kepvcsop-tagsagok>
CREATE TABLE IF NOT EXISTS mp_faction (
    mp_azon        TEXT NOT NULL REFERENCES mp(azon),
    faction_id     TEXT NOT NULL REFERENCES faction(id),
    ckl            INTEGER REFERENCES ciklus(ckl),
    from_date      TEXT,
    to_date        TEXT,                          -- NULL = current
    PRIMARY KEY (mp_azon, faction_id, from_date)
);

-- <valasztasok>: individual constituency (egyéni) vs national list (országos lista)
CREATE TABLE IF NOT EXISTS mp_mandate (
    mp_azon        TEXT NOT NULL REFERENCES mp(azon),
    ckl            INTEGER NOT NULL REFERENCES ciklus(ckl),
    kind           TEXT NOT NULL CHECK (kind IN ('egyeni', 'lista', 'nemzetisegi', 'other')),
    constituency   TEXT,                          -- <valasztas valasztokerulet> e.g. 'Vas 2. OEVK'
    elected_on     TEXT,                          -- <valasztas megvalasztas_napja>
    mandate_from   TEXT,                          -- <valasztas mandatum_kezdete>
    mandate_to     TEXT,
    PRIMARY KEY (mp_azon, ckl)
);

CREATE TABLE IF NOT EXISTS sitting_day (
    ckl            INTEGER NOT NULL REFERENCES ciklus(ckl),
    nap            INTEGER NOT NULL,              -- <ulnap> number within the cycle
    on_date        TEXT NOT NULL,                 -- <datum>
    weekday        TEXT,                          -- <nap>
    session_label  TEXT,                          -- <ulszak> '2026. tavaszi' / '2026. nyári' / …
    kind           TEXT,                          -- <ulesjelleg> 'Rendes' / 'Rendkívüli'
    PRIMARY KEY (ckl, nap)
);

CREATE TABLE IF NOT EXISTS vote (
    ts             TEXT PRIMARY KEY,              -- idopont as 'ÉÉÉÉ.HH.NN.ÓÓ:PP:MM' (natural key)
    slug           TEXT NOT NULL UNIQUE,          -- '2026-09-15T09-37-07' (URL id)
    ckl            INTEGER REFERENCES ciklus(ckl),
    on_date        TEXT NOT NULL,
    sitting_nap    INTEGER,                       -- join to sitting_day by on_date
    kind           TEXT NOT NULL CHECK (kind IN ('dontes', 'jelenlet')),  -- 'Jelenlét megállapítás' is not a decision
    mode           TEXT,                          -- "Szavazási mód" verbatim: 'Listás', 'Listás a jelenlevők 2/3-ával', 'Titkos', …
    secret         INTEGER NOT NULL DEFAULT 0,    -- Titkos…: no roll call, no faction counts
    result_raw     TEXT,                          -- "Elfogadás": 'Elfogadva' / 'Elutasítva' / 'Határozatképes'
    passed         INTEGER,                       -- 1/0 from result_raw; NULL for quorum checks
    remark         TEXT,                          -- "Megjegyzés" (e.g. whom a personnel vote elected)
    igen           INTEGER,
    nem            INTEGER,
    tartozkodott   INTEGER,
    osszes_szavazat INTEGER,                      -- "Összes szavazat" = igen+nem+tartozkodott (verified on 259 votes)
    jelen_nem_szavazott INTEGER,                  -- from the roll call ("Jelen, nem szav.")
    nem_szavazott  INTEGER,                       -- from the roll call / totals row nemszav_db
    bejelentett_hianyzo INTEGER,                  -- totals row igtav_db ("Előre bejelentett hiányzó")
    present        INTEGER,                       -- igen+nem+tartozkodott+jelen_nem_szavazott — interpretation, VERIFY legally
    present_basis  TEXT,                          -- how `present` was derived
    -- karzat/majority.py: Rule enum, classification provenance, and the derived threshold
    majority_rule  TEXT CHECK (majority_rule IN ('egyszeru', 'abszolut', 'ketharmad_jelenlevo',
                                                 'ketharmad_osszes', 'negyotod_jelenlevo', 'relativ')),
    majority_source TEXT CHECK (majority_source IN ('payload', 'keyword', 'default')),  -- how the rule was decided
    majority_evidence TEXT,                       -- payload string or matched keyword, for the reader to disagree with
    needed         INTEGER,                       -- votes needed under majority_rule (majority.needed(); NULL for relativ)
    margin         INTEGER,                       -- igen − needed
    present_assumed INTEGER,                      -- 1 if `present` was NULL and yes+no+abstain was used as the base
    raw_json       TEXT
);

CREATE INDEX IF NOT EXISTS vote_on_date ON vote(on_date);

-- <nev_szerint>: one row per MP per vote
CREATE TABLE IF NOT EXISTS vote_position (
    vote_ts        TEXT NOT NULL REFERENCES vote(ts),
    mp_azon        TEXT REFERENCES mp(azon),      -- resolved from 'Név (Frakció)' via kepviselok.cgi (names unique) + Wikidata aliases; NULL if unresolved
    mp_name        TEXT NOT NULL,                 -- as printed, kept for auditing the mapping
    faction_id     TEXT,                          -- faction at the moment of the vote (from <kepvcsop_szerint> or history)
    position       TEXT NOT NULL CHECK (position IN ('igen', 'nem', 'tartozkodott', 'jelen_nem_szavazott',
                                                     'nem_szavazott', 'bejelentett_hianyzo')),
    PRIMARY KEY (vote_ts, mp_name)
);

CREATE INDEX IF NOT EXISTS vote_position_mp ON vote_position(mp_azon);

-- <kepvcsop_szerint>: faction tallies as the API prints them (cross-check against positions)
CREATE TABLE IF NOT EXISTS vote_faction_tally (
    vote_ts        TEXT NOT NULL REFERENCES vote(ts),
    faction_id     TEXT NOT NULL,
    igen           INTEGER, nem INTEGER, tartozkodott INTEGER, nem_szavazott INTEGER, bejelentett_hianyzo INTEGER, osszesen INTEGER,
    PRIMARY KEY (vote_ts, faction_id)
);

CREATE TABLE IF NOT EXISTS bill (
    izon           TEXT PRIMARY KEY,              -- iromany p_izon (= the number in szam for cycle 43)
    number         TEXT,                          -- irományszám 'T/71', 'H/84', 'S/3', 'I/…'; amendments are 'N/M'
    kind           TEXT,                          -- letter of the szam (T/H/S/I/B/…) or 'amendment'
    type           TEXT,                          -- "Típus" ('törvényjavaslat nemzetközi szerződésről', …)
    main_type      TEXT,                          -- "Főtípus" ('törvényjavaslat', …)
    nature         TEXT,                          -- "Jelleg" ('új', …)
    title          TEXT NOT NULL,
    status_type    TEXT,                          -- <allapottipus> 'folyamatban' / 'lezárt'
    status         TEXT,                          -- "Állapot" ('kihirdetve', …)
    submitted_on   TEXT,                          -- "Benyújtva"
    procedure_mode TEXT,                          -- "Tárgyalási mód" ('kivételes tárgyalásban', …)
    procedure_kind TEXT CHECK (procedure_kind IN ('normal', 'surgos', 'kiveteles', NULL)),
    submitters     TEXT,                          -- "Benyújtó(k)" verbatim
    submitter_kind TEXT CHECK (submitter_kind IN ('kormany', 'kepviselo', 'bizottsag', 'other', NULL)),
    promulgation_no TEXT,                         -- "Kihirdetés száma" ('XV')
    promulgated_as TEXT,                          -- '2026. évi XV. törvény' (törvényjavaslat only)
    mk_issue       TEXT,                          -- "MK száma" ('60') — Magyar Közlöny issue, from the API
    promulgated_on TEXT,                          -- "Kihirdetés dátuma"
    days_to_final_vote INTEGER,                   -- derived from events/votes
    days_to_promulgation INTEGER,
    text_url       TEXT,                          -- "Irományszöveg"
    raw_json       TEXT
);

CREATE TABLE IF NOT EXISTS bill_event (
    izon           TEXT NOT NULL REFERENCES bill(izon),
    seq            INTEGER NOT NULL,
    on_date        TEXT,
    text           TEXT,                          -- <esemeny><leiras> (free text: 'kivételességi javaslat elfogadva', …)
    related        TEXT,                          -- <kapcsolodik> (committee name etc.)
    speech         TEXT,                          -- <felszolalas> ref '3/43'
    PRIMARY KEY (izon, seq)
);

-- <inditvanyok> on a vote / <szavazasok> on a bill: many-to-many (one motion per vote observed)
CREATE TABLE IF NOT EXISTS vote_motion (
    vote_ts        TEXT NOT NULL REFERENCES vote(ts),
    iromany        TEXT NOT NULL,                 -- 'T/71' or amendment 'N/M'; not FK-enforced
    title          TEXT,
    outcome        TEXT,                          -- <ok>: 'önálló indítvány elfogadva', 'kivételességi javaslat elfogadva', …
    submitters     TEXT,                          -- names as printed
    submitter_ids  TEXT,                          -- p_azon list from the href, when present
    submitter_kind TEXT,
    PRIMARY KEY (vote_ts, iromany)
);

CREATE TABLE IF NOT EXISTS sync_run (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    date_from      TEXT, date_to TEXT,
    live_calls     INTEGER, cache_hits INTEGER,
    notes          TEXT
);
