"""Import standings, schedules, assignments, and my own picks.

Standings paste shape matches the prototype's manual paste box exactly:
one entry per line, "Name, Points" (comma or tab separated).

Assignment import intentionally differs from the prototype: the prototype's
"Name, Away, Home" format only recorded which game an entry was in, not
which side of it — a bug (see sql/schema.sql header). Here, assignment
import takes the entry's actual assigned TEAM and resolves home/away by
matching it against that week's already-imported schedule.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import List, Optional, Tuple

from . import db


def _split_line(line: str) -> List[str]:
    if "\t" in line:
        parts = line.split("\t")
    else:
        parts = line.split(",")
    return [p.strip() for p in parts if p.strip()]


def parse_standings_paste(text: str) -> List[Tuple[str, float]]:
    """'Name, Points' per line -> [(name, points), ...]."""
    out = []
    for line in text.splitlines():
        parts = _split_line(line)
        if len(parts) < 2:
            continue
        name = parts[0]
        try:
            points = float(parts[1])
        except ValueError:
            continue
        out.append((name, points))
    return out


def import_standings(
    conn: sqlite3.Connection, season_year: int, week_number: int, text: str
) -> int:
    """Applies a standings paste for a week. Returns count of rows applied."""
    week_id = db.get_or_create_week(conn, season_year, week_number)
    count = 0
    for name, points in parse_standings_paste(text):
        entry_id = db.get_or_create_entry(conn, owner_name=name, display_name=name)
        conn.execute(
            """
            INSERT INTO entry_week_points (entry_id, week_id, points)
            VALUES (?, ?, ?)
            ON CONFLICT(entry_id, week_id) DO UPDATE SET points = excluded.points
            """,
            (entry_id, week_id, points),
        )
        count += 1
    conn.commit()
    return count


def parse_schedule_paste(text: str) -> List[Tuple[str, str]]:
    """'Away, Home' per line -> [(away, home), ...]."""
    out = []
    for line in text.splitlines():
        parts = _split_line(line)
        if len(parts) < 2:
            continue
        out.append((parts[0], parts[1]))
    return out


def import_schedule(
    conn: sqlite3.Connection, season_year: int, week_number: int, text: str
) -> int:
    week_id = db.get_or_create_week(conn, season_year, week_number)
    count = 0
    for away, home in parse_schedule_paste(text):
        db.get_or_create_game(conn, week_id, away, home)
        count += 1
    conn.commit()
    return count


@dataclass
class AssignmentImportError:
    name: str
    team: str
    reason: str


def import_assignments(
    conn: sqlite3.Connection, season_year: int, week_number: int, text: str
) -> Tuple[int, List[AssignmentImportError]]:
    """'Name, Team' per line. Team is matched against that week's schedule
    (already imported via import_schedule) to resolve home/away. Entries
    whose team can't be matched to exactly one game are reported as errors
    rather than guessed at.
    """
    week_id = db.get_or_create_week(conn, season_year, week_number)
    games = conn.execute(
        "SELECT id, away_team, home_team FROM game WHERE week_id = ?", (week_id,)
    ).fetchall()

    count = 0
    errors: List[AssignmentImportError] = []
    for line in text.splitlines():
        parts = _split_line(line)
        if len(parts) < 2:
            continue
        name, team = parts[0], parts[1]

        matches = [
            (g["id"], "away" if g["away_team"] == team else "home")
            for g in games
            if g["away_team"] == team or g["home_team"] == team
        ]
        if len(matches) != 1:
            errors.append(
                AssignmentImportError(
                    name=name,
                    team=team,
                    reason="no matching game in schedule"
                    if not matches
                    else "team matches multiple games — schedule data is ambiguous",
                )
            )
            continue

        game_id, side = matches[0]
        entry_id = db.get_or_create_entry(conn, owner_name=name, display_name=name)
        db.get_or_create_assignment(conn, entry_id, game_id, side)
        count += 1

    conn.commit()
    return count, errors


def record_game_result(
    conn: sqlite3.Connection,
    season_year: int,
    week_number: int,
    away_team: str,
    home_team: str,
    *,
    favorite: Optional[str] = None,
    margin: float = 0,
    outcome: Optional[str] = None,
) -> int:
    week_id = db.get_or_create_week(conn, season_year, week_number)
    game_id = db.get_or_create_game(conn, week_id, away_team, home_team)
    conn.execute(
        "UPDATE game SET favorite = ?, spread_margin = ?, outcome = ? WHERE id = ?",
        (favorite, margin, outcome, game_id),
    )
    conn.commit()
    return game_id


def record_my_pick(
    conn: sqlite3.Connection,
    season_year: int,
    week_number: int,
    entry_display_name: str,
    away_team: str,
    home_team: str,
    assigned_side: str,
    pick: str,
    bet: float,
    *,
    is_late_default: bool = False,
) -> int:
    week_id = db.get_or_create_week(conn, season_year, week_number)
    game_id = db.get_or_create_game(conn, week_id, away_team, home_team)
    entry_id = db.get_my_entry_by_name(conn, entry_display_name)
    assignment_id = db.get_or_create_assignment(conn, entry_id, game_id, assigned_side)
    conn.execute(
        """
        INSERT INTO wager_observation (assignment_id, declared_pick, declared_bet, is_late_default)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(assignment_id) DO UPDATE SET
            declared_pick = excluded.declared_pick,
            declared_bet = excluded.declared_bet,
            is_late_default = excluded.is_late_default
        """,
        (assignment_id, pick, bet, int(is_late_default)),
    )
    conn.commit()
    return assignment_id
