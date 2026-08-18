-- karzat — draft SQLite schema (v0, pre-token).
--
-- This is the target shape for the normalised store the front end reads from. Every column
-- marked VERIFY is an assumption about the W-API payloads that has to be checked against real
-- XML in data/raw/ before it is trusted. Raw XML stays on disk; this database is derived and
-- can be rebuilt from the cache at any time.
--
-- Design decisions taken now (see README "Decisions taken before the token arrived"):
--   * a vote is identified by its timestamp (the API's idopont), not by a roll number;
--   * four vote positions are first-class: igen / nem / tartozkodott / nem_szavazott;
--   * the majority rule is stored per vote, never assumed to be "simple";
--   * faction membership is a history, not a single field, because MPs move.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS ciklus (
    ckl            INTEGER PRIMARY KEY,           -- parlament.hu cycle number (43 = 2026–)
    label          TEXT NOT NULL,                 -- '2026–2030'
    started_on     TEXT,                          -- ISO date
    seats          INTEGER NOT NULL DEFAULT 199
);

CREATE TABLE IF NOT EXISTS mp (
    azon           TEXT PRIMARY KEY,              -- kepviselo p_azon (e.g. 'z004')  VERIFY format
    name           TEXT NOT NULL,
    seat           TEXT,                          -- <ulohely>                        VERIFY format
    photo_url      TEXT,                          -- VERIFY presence + licence
    born_on        TEXT,
    website        TEXT,
    email          TEXT,
    raw_json       TEXT                           -- full to_dict() of kepviselo.cgi for later columns
);

CREATE TABLE IF NOT EXISTS faction (
    id             TEXT PRIMARY KEY,              -- short code used in kepvcsop fields (e.g. 'Fidesz')  VERIFY
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
    kind           TEXT NOT NULL CHECK (kind IN ('egyeni', 'lista', 'nemzetisegi', 'other')),  -- VERIFY vocabulary
    constituency   TEXT,                          -- e.g. 'Budapest 01. OEVK' for egyéni
    list_name      TEXT,
    PRIMARY KEY (mp_azon, ckl)
);

CREATE TABLE IF NOT EXISTS sitting_day (
    ckl            INTEGER NOT NULL REFERENCES ciklus(ckl),
    nap            INTEGER NOT NULL,              -- ulesnap number within the cycle
    on_date        TEXT NOT NULL,
    session_label  TEXT,                          -- 'tavaszi' / 'őszi' / 'rendkívüli'  VERIFY availability
    PRIMARY KEY (ckl, nap)
);

CREATE TABLE IF NOT EXISTS vote (
    ts             TEXT PRIMARY KEY,              -- idopont as 'ÉÉÉÉ.HH.NN.ÓÓ:PP:MM' (natural key)
    slug           TEXT NOT NULL UNIQUE,          -- '2026-09-15T09-37-07' (URL id)
    ckl            INTEGER REFERENCES ciklus(ckl),
    on_date        TEXT NOT NULL,
    sitting_nap    INTEGER,                       -- join to sitting_day               VERIFY derivable
    kind           TEXT,                          -- <tulajdonsagok> vote type (zárószavazás, módosító, ügyrendi, ...)  VERIFY
    subject        TEXT,                          -- what was voted on, as printed
    result         TEXT,                          -- 'elfogadva' / 'elutasitva' / ...  VERIFY vocabulary
    passed         INTEGER,                       -- 0/1 derived from result
    igen           INTEGER,
    nem            INTEGER,
    tartozkodott   INTEGER,
    nem_szavazott  INTEGER,
    present        INTEGER,                       -- jelenlévők, if the payload gives it   VERIFY
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
    mp_azon        TEXT REFERENCES mp(azon),      -- NULL until name→azon resolution is proven  VERIFY ids present
    mp_name        TEXT NOT NULL,                 -- as printed, kept for auditing the mapping
    faction_id     TEXT,                          -- faction at the moment of the vote (from <kepvcsop_szerint> or history)
    position       TEXT NOT NULL CHECK (position IN ('igen', 'nem', 'tartozkodott', 'nem_szavazott')),
    PRIMARY KEY (vote_ts, mp_name)
);

CREATE INDEX IF NOT EXISTS vote_position_mp ON vote_position(mp_azon);

-- <kepvcsop_szerint>: faction tallies as the API prints them (cross-check against positions)
CREATE TABLE IF NOT EXISTS vote_faction_tally (
    vote_ts        TEXT NOT NULL REFERENCES vote(ts),
    faction_id     TEXT NOT NULL,
    igen           INTEGER, nem INTEGER, tartozkodott INTEGER, nem_szavazott INTEGER,
    PRIMARY KEY (vote_ts, faction_id)
);

CREATE TABLE IF NOT EXISTS bill (
    izon           TEXT PRIMARY KEY,              -- iromany p_izon (internal id)
    number         TEXT,                          -- irományszám: 'T/1234', 'H/56', ...   VERIFY field name
    kind           TEXT,                          -- törvényjavaslat / határozati javaslat / ...
    title          TEXT NOT NULL,
    status         TEXT,                          -- <allapottipus>
    submitted_on   TEXT,
    submitters     TEXT,                          -- <benyujtok> joined; keep raw too
    submitter_kind TEXT CHECK (submitter_kind IN ('kormany', 'kepviselo', 'bizottsag', 'other', NULL)),  -- derived  VERIFY
    promulgated_as TEXT,                          -- '2026. évi XII. törvény'  VERIFY presence in <tulajdonsagok>
    mk_issue       TEXT,                          -- Magyar Közlöny issue ('2026/114') VERIFY presence
    raw_json       TEXT
);

CREATE TABLE IF NOT EXISTS bill_event (
    izon           TEXT NOT NULL REFERENCES bill(izon),
    seq            INTEGER NOT NULL,
    on_date        TEXT,
    kind           TEXT,                          -- <esemenyek> event type   VERIFY taxonomy
    detail         TEXT,
    PRIMARY KEY (izon, seq)
);

-- <inditvanyok> on a vote / <szavazasok> on a bill: many-to-many
CREATE TABLE IF NOT EXISTS vote_bill (
    vote_ts        TEXT NOT NULL REFERENCES vote(ts),
    izon           TEXT NOT NULL,                 -- not FK-enforced: amendments may not be listed bills
    label          TEXT,
    PRIMARY KEY (vote_ts, izon)
);

CREATE TABLE IF NOT EXISTS sync_run (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    date_from      TEXT, date_to TEXT,
    live_calls     INTEGER, cache_hits INTEGER,
    notes          TEXT
);
