-- Office-Pool-4-Fun strategy console schema.
--
-- Three layers, kept deliberately separate (per the project's design goal):
--   1. Raw observations  -> wager_observation, game (posted results), market_snapshot
--   2. Inferred data      -> wager_inference (reconstructed picks/bets from point deltas)
--   3. Recommendations    -> not persisted; computed on demand by the simulation layer
--
-- NOTE ON A BUG FOUND IN THE REFERENCE PROTOTYPE:
-- The browser prototype's `fieldAssignments[week][name] = {away, home}` only recorded
-- which GAME an entry was assigned to, not which SIDE (home/away team) of that game the
-- entry actually held. Its field-reconstruction code then computed `side` with
-- `assignments[name].home === home`, which is always true by construction (both sides of
-- that comparison come from the same object), so every field entry was silently treated as
-- having been assigned the home team. That's wrong whenever two entries are assigned to
-- opposite sides of the same game. This schema fixes that by making `assigned_side`
-- an explicit, required column on `assignment` for every entry (mine and field).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS pool_config (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    start_points            REAL    NOT NULL DEFAULT 150,
    min_bet                 REAL    NOT NULL DEFAULT 20,
    entry_fee               REAL    NOT NULL DEFAULT 30,
    payouts_json            TEXT    NOT NULL DEFAULT '[40,18,10,9,7,6,4,3,2,1]',
    upset_spread_threshold  REAL    NOT NULL DEFAULT 10.0,
    upset_multiplier        REAL    NOT NULL DEFAULT 10.0,
    tie_multiplier          REAL    NOT NULL DEFAULT 10.0,
    late_pick_default_bet   REAL    NOT NULL DEFAULT 20,
    late_pick_default_side  TEXT    NOT NULL DEFAULT 'home',
    late_pick_default_pick  TEXT    NOT NULL DEFAULT 'WIN',
    spread_source_name      TEXT    NOT NULL DEFAULT 'Cleveland Plain Dealer (Thursday)',
    -- Open assumption (see README): does betting the FAVORITE to LOSE against a
    -- qualifying spread also count as a paying "upset", or is it underdog-WIN only?
    -- Config-driven so it can be flipped without touching code once confirmed.
    upset_counts_favorite_loss INTEGER NOT NULL DEFAULT 1,
    -- Modeled field size for the simulator when no better estimate is available.
    default_field_size         INTEGER NOT NULL DEFAULT 107
);

CREATE TABLE IF NOT EXISTS owner (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE
);

-- display_name is pool-unique (the pool's own standings identify entries by
-- name), not just unique per owner -- so two entries can never collide even
-- if imported before their owner grouping is known.
CREATE TABLE IF NOT EXISTS entry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER NOT NULL REFERENCES owner(id),
    display_name    TEXT NOT NULL UNIQUE,
    is_mine         INTEGER NOT NULL DEFAULT 0,
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS week (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    season_year     INTEGER NOT NULL,
    week_number     INTEGER NOT NULL,
    UNIQUE(season_year, week_number)
);

CREATE TABLE IF NOT EXISTS game (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    week_id         INTEGER NOT NULL REFERENCES week(id),
    away_team       TEXT NOT NULL,
    home_team       TEXT NOT NULL,
    favorite        TEXT CHECK (favorite IN ('home','away') OR favorite IS NULL),
    spread_margin   REAL NOT NULL DEFAULT 0,
    outcome         TEXT CHECK (outcome IN ('home','away','tie') OR outcome IS NULL),
    UNIQUE(week_id, away_team, home_team)
);

CREATE TABLE IF NOT EXISTS assignment (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id        INTEGER NOT NULL REFERENCES entry(id),
    game_id         INTEGER NOT NULL REFERENCES game(id),
    assigned_side   TEXT NOT NULL CHECK (assigned_side IN ('home','away')),
    UNIQUE(entry_id, game_id)
);

-- Raw, source-tagged spread observations. Never treated as authoritative for
-- 10x-upset qualification unless is_authoritative_for_upset = 1.
CREATE TABLE IF NOT EXISTS market_snapshot (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id                     INTEGER NOT NULL REFERENCES game(id),
    fetched_at                  TEXT NOT NULL,
    favorite                    TEXT CHECK (favorite IN ('home','away','even')),
    margin                      REAL NOT NULL,
    source                      TEXT NOT NULL,
    -- Always 0 for a fetched/scraped line. Only a manual confirmation against
    -- the Thursday Cleveland Plain Dealer (or whatever spread_source_name is
    -- configured) should ever set this to 1 — never trust a scraped line for
    -- 10x-upset qualification without this flag.
    is_authoritative_for_upset  INTEGER NOT NULL DEFAULT 0,
    notes                       TEXT
);

-- Raw posted standings: (entry, week) -> points, exactly the shape of the
-- manual paste box in the prototype ("Name, Points" per week). This is the
-- source of truth for point deltas used by reconstruction; it does not
-- require a game/assignment to exist yet.
CREATE TABLE IF NOT EXISTS entry_week_points (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id    INTEGER NOT NULL REFERENCES entry(id),
    week_id     INTEGER NOT NULL REFERENCES week(id),
    points      REAL NOT NULL,
    UNIQUE(entry_id, week_id)
);

-- Raw observations: what was actually declared, for entries where the pick
-- and bet are directly known (mine) rather than inferred from a point delta.
CREATE TABLE IF NOT EXISTS wager_observation (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id   INTEGER NOT NULL REFERENCES assignment(id),
    declared_pick   TEXT CHECK (declared_pick IN ('WIN','LOSS','TIE') OR declared_pick IS NULL),
    declared_bet    REAL,
    is_late_default INTEGER NOT NULL DEFAULT 0,
    UNIQUE(assignment_id)
);

-- Inferred layer: candidate (pick, bet) hypotheses reconstructed from a point
-- delta when the actual pick/bet wasn't declared (field entries).
CREATE TABLE IF NOT EXISTS wager_inference (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id       INTEGER NOT NULL REFERENCES assignment(id),
    inferred_pick       TEXT NOT NULL CHECK (inferred_pick IN ('WIN','LOSS','TIE')),
    inferred_bet        REAL NOT NULL,
    confidence_rank     INTEGER NOT NULL,
    label               TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    UNIQUE(assignment_id, inferred_pick)
);
