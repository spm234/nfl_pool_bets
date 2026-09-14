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
from typing import Optional

from pool.live_data import LiveDataError, fetch_completed_score, fetch_spread_estimate

from . import importer
from .ratings import apply_elo_after_result

__all__ = [
    "LiveDataError",
    "fetch_completed_score",
    "fetch_spread_estimate",
    "fetch_and_save_spread",
    "fetch_and_save_result",
]


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
):
    result = fetch_completed_score(away_team, home_team, api_key=api_key)
    importer.record_game_result(
        conn, season_year, week_number, away_team, home_team,
        outcome=result.outcome, home_score=result.home_score, away_score=result.away_score,
    )
    apply_elo_after_result(conn, season_year, week_number, away_team, home_team)
    return result
