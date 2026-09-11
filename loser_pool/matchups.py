"""Shared helper: for a set of weeks and a set of teams an entry still has
available, what are this entry's (team, opponent, loss-probability) options
each week? Used by both optimizer.py and simulation.py — split out so
neither has to import the other.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, List

from .config import LoserPoolConfig
from .ratings import game_win_probabilities


@dataclass
class TeamWeekOption:
    week_number: int
    team: str
    opponent: str
    is_home: bool
    p_lose: float


def week_options_for_entry(
    conn: sqlite3.Connection,
    season_year: int,
    week_numbers: List[int],
    exclude_teams: set,
    cfg: LoserPoolConfig,
) -> Dict[int, List[TeamWeekOption]]:
    """{week_number: [TeamWeekOption, ...]} for every team NOT in
    exclude_teams that has a scheduled game in that week.
    """
    out: Dict[int, List[TeamWeekOption]] = {}
    for week_number in week_numbers:
        week_row = conn.execute(
            "SELECT id FROM lp_week WHERE season_year = ? AND week_number = ?",
            (season_year, week_number),
        ).fetchone()
        if week_row is None:
            out[week_number] = []
            continue
        games = conn.execute("SELECT * FROM lp_game WHERE week_id = ?", (week_row["id"],)).fetchall()
        options = []
        for game in games:
            p_home_win, p_away_win = game_win_probabilities(conn, game, season_year, week_number, cfg)
            for team, opponent, is_home, p_win in (
                (game["home_team"], game["away_team"], True, p_home_win),
                (game["away_team"], game["home_team"], False, p_away_win),
            ):
                if team in exclude_teams:
                    continue
                options.append(TeamWeekOption(week_number, team, opponent, is_home, 1.0 - p_win))
        out[week_number] = options
    return out
