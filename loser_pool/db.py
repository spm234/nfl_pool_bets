"""SQLite connection + schema initialization for the Loser Pool tool.

Deliberately a separate database file from the Office-Pool-4-Fun tool
(`pool/db.py`) — same conventions (sqlite3.Row factory, foreign keys on),
different schema, because it's a different game.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "loser_schema.sql"
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "loser_pool.db"


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.execute("INSERT OR IGNORE INTO loser_pool_config (id) VALUES (1)")
        conn.commit()
    finally:
        conn.close()


def get_or_create_owner(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM lp_owner WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO lp_owner (name) VALUES (?)", (name,))
    return cur.lastrowid


def get_or_create_entry(
    conn: sqlite3.Connection,
    owner_name: str,
    display_name: str,
    *,
    is_mine: bool = False,
    lives_per_entry: int = 2,
) -> int:
    row = conn.execute(
        "SELECT id FROM lp_entry WHERE display_name = ?", (display_name,)
    ).fetchone()
    if row:
        return row["id"]
    owner_id = get_or_create_owner(conn, owner_name)
    cur = conn.execute(
        "INSERT INTO lp_entry (owner_id, display_name, is_mine, lives_remaining) "
        "VALUES (?, ?, ?, ?)",
        (owner_id, display_name, int(is_mine), lives_per_entry),
    )
    return cur.lastrowid


def get_entry_by_name(conn: sqlite3.Connection, display_name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM lp_entry WHERE display_name = ?", (display_name,)
    ).fetchone()


def get_or_create_week(
    conn: sqlite3.Connection, season_year: int, week_number: int, *, is_playoffs: bool = False
) -> int:
    row = conn.execute(
        "SELECT id FROM lp_week WHERE season_year = ? AND week_number = ?",
        (season_year, week_number),
    ).fetchone()
    if row:
        if is_playoffs:
            conn.execute("UPDATE lp_week SET is_playoffs = 1 WHERE id = ?", (row["id"],))
        return row["id"]
    cur = conn.execute(
        "INSERT INTO lp_week (season_year, week_number, is_playoffs) VALUES (?, ?, ?)",
        (season_year, week_number, int(is_playoffs)),
    )
    return cur.lastrowid


def get_or_create_game(
    conn: sqlite3.Connection, week_id: int, away_team: str, home_team: str
) -> int:
    row = conn.execute(
        "SELECT id FROM lp_game WHERE week_id = ? AND away_team = ? AND home_team = ?",
        (week_id, away_team, home_team),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO lp_game (week_id, away_team, home_team) VALUES (?, ?, ?)",
        (week_id, away_team, home_team),
    )
    return cur.lastrowid


def most_recent_reset_week(conn: sqlite3.Connection) -> int:
    """Returns the week_number after which the most recent playoff reset
    happened, or 0 if there hasn't been one (so 'picks after week 0' means
    every pick, i.e. the normal case).
    """
    row = conn.execute(
        "SELECT MAX(reset_after_week_number) AS w FROM lp_reset_event"
    ).fetchone()
    return row["w"] if row and row["w"] is not None else 0
