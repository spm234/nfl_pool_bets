"""Live spread/result fetching for the Loser Pool tool.

Reuses pool.live_data's fetch functions as-is (they're generic — just take
team names and return a spread/score, not tied to the Office-Pool-4-Fun
schema) rather than duplicating the HTTP/parsing logic, and only adds the
persistence glue for this tool's own tables.

clevanalytics.com (both the survivor-optimizer page and its season-long
spreads page) is blocked by this environment's network egress proxy, so
this module can't reach it — see README. The Odds API covers real market
spreads for the near-term slate; ratings.py's Elo model fills in for weeks
further out than that.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import List, Optional

from pool.live_data import LiveDataError, fetch_completed_score, fetch_spread_estimate

from . import importer, picks
from .ratings import apply_elo_after_result

__all__ = [
    "LiveDataError",
    "fetch_completed_score",
    "fetch_spread_estimate",
    "fetch_and_save_spread",
    "fetch_and_save_result",
    "ResultSyncOutcome",
    "sync_pending_results",
]


@dataclass
class ResultSyncOutcome:
    season_year: int
    week_number: int
    away_team: str
    home_team: str
    outcome: Optional[str] = None
    error: Optional[str] = None


def fetch_and_save_spread(
    conn: sqlite3.Connection,
    season_year: int,
    week_number: int,
    away_team: str,
    home_team: str,
    *,
    api_key: Optional[str] = None,
):
    estimate = fetch_spread_estimate(away_team, home_team, api_key=api_key)
    favorite = None if estimate.favorite == "even" else estimate.favorite
    importer.record_game_result(
        conn, season_year, week_number, away_team, home_team,
        favorite=favorite, margin=estimate.margin, spread_source=estimate.source,
    )
    return estimate


def fetch_and_save_result(
    conn: sqlite3.Connection,
    season_year: int,
    week_number: int,
    away_team: str,
    home_team: str,
    *,
    api_key: Optional[str] = None,
    days_from: int = 3,
):
    result = fetch_completed_score(away_team, home_team, api_key=api_key, days_from=days_from)
    importer.record_game_result(
        conn, season_year, week_number, away_team, home_team,
        outcome=result.outcome, home_score=result.home_score, away_score=result.away_score,
    )
    apply_elo_after_result(conn, season_year, week_number, away_team, home_team)
    return result


def sync_pending_results(
    conn: sqlite3.Connection, *, api_key: Optional[str] = None, days_from: int = 3
) -> List[ResultSyncOutcome]:
    """Fetches and records outcomes for every lp_game missing one, across
    every season/week in one pass — this is how "yesterday's games" get
    picked up without having to know which week they belong to (same idea
    as pool.live_data.sync_pending_results). After recording new outcomes,
    settles every week that got at least one — so `status` (lives
    remaining / eliminated, this tool's standings) reflects the result
    immediately instead of needing a separate fetch-result + settle-week
    per game. A game that hasn't finished yet is skipped, not an error.
    """
    games = conn.execute(
        """
        SELECT g.away_team, g.home_team, w.season_year, w.week_number
        FROM lp_game g
        JOIN lp_week w ON w.id = g.week_id
        WHERE g.outcome IS NULL
        ORDER BY w.season_year, w.week_number, g.id
        """
    ).fetchall()

    out: List[ResultSyncOutcome] = []
    settled_weeks = set()
    for g in games:
        away, home = g["away_team"], g["home_team"]
        season_year, week_number = g["season_year"], g["week_number"]
        try:
            result = fetch_and_save_result(
                conn, season_year, week_number, away, home, api_key=api_key, days_from=days_from
            )
        except LiveDataError as e:
            out.append(ResultSyncOutcome(season_year, week_number, away, home, error=str(e)))
            continue
        out.append(ResultSyncOutcome(season_year, week_number, away, home, outcome=result.outcome))
        settled_weeks.add((season_year, week_number))

    for season_year, week_number in sorted(settled_weeks):
        picks.settle_week(conn, season_year, week_number)

    return out
