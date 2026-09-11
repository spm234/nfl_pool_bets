-- Loser Pool schema (separate DB from the Office-Pool-4-Fun tool — this is a
-- different game with different rules, not a variant of it).
--
-- THE GAME: each week, every entry picks one team they believe will LOSE.
-- If that team loses, the entry survives. If it wins (or, per
-- tie_treated_as below, ties), the entry burns a life. Each entry starts
-- with `lives_per_entry` lives (2, per the $30-buy-in-includes-a-buyback
-- rule); a second bust eliminates the entry for good. A team can only be
-- picked once per entry per "usage phase" — see reset_event below for what
-- resets that.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS loser_pool_config (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    buy_in                  REAL    NOT NULL DEFAULT 30,
    lives_per_entry         INTEGER NOT NULL DEFAULT 2,
    -- Rare-case assumption (see README "Open assumptions"): does a tie count
    -- as a survived pick (push) or a bust? Flip via config-set once you've
    -- confirmed the actual rule with the pool operator — it's rare enough
    -- that it's fine to leave as an assumption until it comes up for real.
    tie_treated_as          TEXT    NOT NULL DEFAULT 'bust' CHECK (tie_treated_as IN ('bust','survive')),
    -- Standard NFL single-game margin std dev, used to convert a point
    -- spread into a win probability via the normal CDF. ~13.5-14 is the
    -- commonly cited value; override if you have a better-calibrated number.
    margin_std_dev          REAL    NOT NULL DEFAULT 13.86,
    -- Elo home-field-advantage constant (in Elo points), used only for
    -- weeks/games with no market spread available yet (i.e. projecting
    -- ahead of what The Odds API currently lists). ~48 is the commonly
    -- cited FiveThirtyEight NFL value.
    elo_home_advantage      REAL    NOT NULL DEFAULT 48.0,
    elo_k_factor            REAL    NOT NULL DEFAULT 20.0,
    elo_initial_rating      REAL    NOT NULL DEFAULT 1500.0
);

CREATE TABLE IF NOT EXISTS lp_owner (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS lp_entry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER NOT NULL REFERENCES lp_owner(id),
    display_name    TEXT NOT NULL UNIQUE,
    is_mine         INTEGER NOT NULL DEFAULT 0,
    lives_remaining INTEGER NOT NULL DEFAULT 2,
    eliminated      INTEGER NOT NULL DEFAULT 0,
    eliminated_week INTEGER
);

CREATE TABLE IF NOT EXISTS lp_week (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    season_year     INTEGER NOT NULL,
    week_number     INTEGER NOT NULL,
    is_playoffs     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(season_year, week_number)
);

CREATE TABLE IF NOT EXISTS lp_game (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    week_id         INTEGER NOT NULL REFERENCES lp_week(id),
    away_team       TEXT NOT NULL,
    home_team       TEXT NOT NULL,
    -- Market spread, when known (favorite/margin), and its source. NULL
    -- favorite + margin 0 means "no market line yet — project from Elo."
    favorite        TEXT CHECK (favorite IN ('home','away') OR favorite IS NULL),
    spread_margin   REAL NOT NULL DEFAULT 0,
    spread_source   TEXT,
    outcome         TEXT CHECK (outcome IN ('home','away','tie') OR outcome IS NULL),
    home_score      INTEGER,
    away_score      INTEGER,
    UNIQUE(week_id, away_team, home_team)
);

-- Elo rating for a team AS OF the start of a given week (i.e. incorporating
-- every result strictly before that week). Row for (team, season, week 1)
-- is the preseason rating; each later week's row is written by
-- spread_model.apply_elo_update after that prior week's results are in.
CREATE TABLE IF NOT EXISTS lp_team_rating (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    team            TEXT NOT NULL,
    season_year     INTEGER NOT NULL,
    week_number     INTEGER NOT NULL,
    rating          REAL NOT NULL,
    UNIQUE(team, season_year, week_number)
);

CREATE TABLE IF NOT EXISTS lp_pick (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id        INTEGER NOT NULL REFERENCES lp_entry(id),
    week_id         INTEGER NOT NULL REFERENCES lp_week(id),
    team_picked     TEXT NOT NULL,
    -- 'pending' until the game is settled; then 'survived' (team lost, or
    -- tied and tie_treated_as='survive') or 'busted' (team won, or tied
    -- and tie_treated_as='bust').
    result          TEXT NOT NULL DEFAULT 'pending' CHECK (result IN ('pending','survived','busted')),
    -- Which life this pick consumed if it busted (1 or 2). NULL otherwise.
    life_lost       INTEGER,
    UNIQUE(entry_id, week_id)
);

-- One row per playoff reset the operator has actually triggered ("all teams
-- reset" per the rules). Team-reuse checks only look at picks made AFTER
-- the most recent reset's week_number (picks at/before it no longer count
-- as "used"). Expected to have 0 or 1 rows in practice.
CREATE TABLE IF NOT EXISTS lp_reset_event (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    reset_after_week_number INTEGER NOT NULL,
    note                    TEXT,
    created_at              TEXT NOT NULL
);
