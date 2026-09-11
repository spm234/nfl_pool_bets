"""Import schedule and initial power-rating data for the Loser Pool tool.

Same paste-friendly shape as pool/importer.py: comma- or tab-separated
lines, no CSV library required for the simple cases.
"""
from __future__ import annotations

import sqlite3
from typing import List, Tuple

from . import db
from .config import LoserPoolConfig

_UNSET = object()


def _split_line(line: str) -> List[str]:
    parts = line.split("\t") if "\t" in line else line.split(",")
    return [p.strip() for p in parts if p.strip()]


def parse_schedule_paste(text: str) -> List[Tuple[str, str]]:
    """'Away, Home' per line -> [(away, home), ...]. Blank lines and lines
    that don't split into at least 2 fields are skipped.
    """
    out = []
    for line in text.splitlines():
        parts = _split_line(line)
        if len(parts) < 2:
            continue
        out.append((parts[0], parts[1]))
    return out


def import_schedule(
    conn: sqlite3.Connection,
    season_year: int,
    week_number: int,
    text: str,
    *,
    is_playoffs: bool = False,
) -> int:
    week_id = db.get_or_create_week(conn, season_year, week_number, is_playoffs=is_playoffs)
    count = 0
    for away, home in parse_schedule_paste(text):
        db.get_or_create_game(conn, week_id, away, home)
        count += 1
    conn.commit()
    return count


def parse_name_value_paste(text: str) -> List[Tuple[str, float]]:
    out = []
    for line in text.splitlines():
        parts = _split_line(line)
        if len(parts) < 2:
            continue
        try:
            value = float(parts[1])
        except ValueError:
            continue
        out.append((parts[0], value))
    return out


def import_elo_ratings(
    conn: sqlite3.Connection, season_year: int, week_number: int, text: str
) -> int:
    """'Team, Rating' per line — literal Elo ratings (typically ~1300-1700),
    written as each team's rating entering the given week.
    """
    count = 0
    for team, rating in parse_name_value_paste(text):
        conn.execute(
            """
            INSERT INTO lp_team_rating (team, season_year, week_number, rating)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(team, season_year, week_number) DO UPDATE SET rating = excluded.rating
            """,
            (team, season_year, week_number, rating),
        )
        count += 1
    conn.commit()
    return count


def import_win_totals(
    conn: sqlite3.Connection,
    season_year: int,
    week_number: int,
    text: str,
    *,
    config: LoserPoolConfig | None = None,
) -> int:
    """'Team, Projected Win Total' per line (e.g. from a sportsbook's season
    win-total market, 0-17) — converted to an Elo rating via the standard
    heuristic of ~25 Elo points per game of expected record above/below a
    .500 (8.5-win) season. This is the intended way to seed preseason
    ratings when you have a win-totals source handy but not raw Elo
    numbers; see README for why this project can't fetch one itself.
    """
    cfg = config or LoserPoolConfig.load(conn)
    count = 0
    for team, win_total in parse_name_value_paste(text):
        rating = cfg.elo_initial_rating + (win_total - 8.5) * 25.0
        conn.execute(
            """
            INSERT INTO lp_team_rating (team, season_year, week_number, rating)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(team, season_year, week_number) DO UPDATE SET rating = excluded.rating
            """,
            (team, season_year, week_number, rating),
        )
        count += 1
    conn.commit()
    return count


def record_game_result(
    conn: sqlite3.Connection,
    season_year: int,
    week_number: int,
    away_team: str,
    home_team: str,
    *,
    favorite=_UNSET,
    margin=_UNSET,
    spread_source=_UNSET,
    outcome=_UNSET,
    home_score=_UNSET,
    away_score=_UNSET,
) -> int:
    """Partial update, same pattern as pool/importer.py's record_game_result
    — an argument left at its default keeps the game's current value rather
    than clobbering it, since spread and outcome are typically recorded at
    different times.
    """
    week_id = db.get_or_create_week(conn, season_year, week_number)
    game_id = db.get_or_create_game(conn, week_id, away_team, home_team)
    current = conn.execute(
        "SELECT favorite, spread_margin, spread_source, outcome, home_score, away_score "
        "FROM lp_game WHERE id = ?",
        (game_id,),
    ).fetchone()
    new_favorite = current["favorite"] if favorite is _UNSET else favorite
    new_margin = current["spread_margin"] if margin is _UNSET else margin
    new_source = current["spread_source"] if spread_source is _UNSET else spread_source
    new_outcome = current["outcome"] if outcome is _UNSET else outcome
    new_home_score = current["home_score"] if home_score is _UNSET else home_score
    new_away_score = current["away_score"] if away_score is _UNSET else away_score
    conn.execute(
        """
        UPDATE lp_game
        SET favorite = ?, spread_margin = ?, spread_source = ?, outcome = ?,
            home_score = ?, away_score = ?
        WHERE id = ?
        """,
        (new_favorite, new_margin, new_source, new_outcome, new_home_score, new_away_score, game_id),
    )
    conn.commit()
    return game_id
