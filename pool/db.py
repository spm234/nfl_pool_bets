"""SQLite connection + schema initialization."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "pool.db"


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    my_entry_names: tuple[str, ...] = ("SPM", "SPM 2", "SPM 3"),
) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.execute("INSERT OR IGNORE INTO pool_config (id) VALUES (1)")
        existing = conn.execute("SELECT COUNT(*) AS n FROM entry WHERE is_mine = 1").fetchone()
        if existing["n"] == 0:
            for name in my_entry_names:
                get_or_create_entry(conn, owner_name="Me", display_name=name, is_mine=True)
        conn.commit()
    finally:
        conn.close()


def get_or_create_owner(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM owner WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO owner (name) VALUES (?)", (name,))
    return cur.lastrowid


def get_or_create_entry(
    conn: sqlite3.Connection, owner_name: str, display_name: str, *, is_mine: bool = False
) -> int:
    """display_name is treated as pool-unique (as it is in practice — the
    pool's own standings identify entries by name), so lookup ignores owner
    and matches any existing entry with this display_name first. This
    matters for the 3 pre-seeded 'my' entries: importers that only know a
    display_name (not the 'Me' owner) must still resolve to the same row
    rather than creating a duplicate under a new owner.
    """
    row = conn.execute(
        "SELECT id FROM entry WHERE display_name = ?", (display_name,)
    ).fetchone()
    if row:
        return row["id"]
    owner_id = get_or_create_owner(conn, owner_name)
    cur = conn.execute(
        "INSERT INTO entry (owner_id, display_name, is_mine) VALUES (?, ?, ?)",
        (owner_id, display_name, int(is_mine)),
    )
    return cur.lastrowid


def get_my_entry_by_name(conn: sqlite3.Connection, display_name: str) -> int:
    """Looks up one of 'my' entries (seeded by init_db) by display name.
    Falls back to creating one under owner 'Me' if it doesn't exist yet,
    so this also works against a DB that wasn't seeded via init_db.
    """
    row = conn.execute(
        "SELECT id FROM entry WHERE is_mine = 1 AND display_name = ?", (display_name,)
    ).fetchone()
    if row:
        return row["id"]
    return get_or_create_entry(conn, owner_name="Me", display_name=display_name, is_mine=True)


def get_or_create_week(conn: sqlite3.Connection, season_year: int, week_number: int) -> int:
    row = conn.execute(
        "SELECT id FROM week WHERE season_year = ? AND week_number = ?",
        (season_year, week_number),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO week (season_year, week_number) VALUES (?, ?)",
        (season_year, week_number),
    )
    return cur.lastrowid


def get_or_create_game(
    conn: sqlite3.Connection, week_id: int, away_team: str, home_team: str
) -> int:
    row = conn.execute(
        "SELECT id FROM game WHERE week_id = ? AND away_team = ? AND home_team = ?",
        (week_id, away_team, home_team),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO game (week_id, away_team, home_team) VALUES (?, ?, ?)",
        (week_id, away_team, home_team),
    )
    return cur.lastrowid


def get_or_create_assignment(
    conn: sqlite3.Connection, entry_id: int, game_id: int, assigned_side: str
) -> int:
    row = conn.execute(
        "SELECT id, assigned_side FROM assignment WHERE entry_id = ? AND game_id = ?",
        (entry_id, game_id),
    ).fetchone()
    if row:
        if row["assigned_side"] != assigned_side:
            raise ValueError(
                f"Entry {entry_id} already assigned '{row['assigned_side']}' for game "
                f"{game_id}, cannot reassign to '{assigned_side}'"
            )
        return row["id"]
    cur = conn.execute(
        "INSERT INTO assignment (entry_id, game_id, assigned_side) VALUES (?, ?, ?)",
        (entry_id, game_id, assigned_side),
    )
    return cur.lastrowid
