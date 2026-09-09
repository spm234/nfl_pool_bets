"""Live spread fetching for the user's assigned games.

Deliberately uses a documented, scriptable lines API (The Odds API,
https://the-odds-api.com) rather than scraping a sportsbook page or the
Cleveland Plain Dealer directly — the CPD is paywalled/subscription content
and the pool's actual 10x-qualification source, so it's a manual confirm
step (see confirm_spread below), never something this script fetches or
treats as authoritative on its own.

Every value this module returns or stores is an "early estimate": it is
never written with is_authoritative_for_upset=1.
"""
from __future__ import annotations

import os
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from . import db

ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"


@dataclass
class SpreadEstimate:
    favorite: str  # 'home' | 'away' | 'even'
    margin: float
    source: str
    fetched_at: str


class LiveDataError(Exception):
    pass


def _normalize(name: str) -> str:
    return name.strip().lower()


def fetch_spread_estimate(
    away_team: str, home_team: str, *, api_key: Optional[str] = None
) -> SpreadEstimate:
    """Fetches a current spread estimate for one game from The Odds API.

    Requires the `requests` package and an API key (from the api_key arg or
    the THE_ODDS_API_KEY environment variable). Raises LiveDataError with a
    clear message on any failure — this never silently falls back to a guess.
    """
    try:
        import requests
    except ImportError as e:
        raise LiveDataError(
            "The 'requests' package is required for live spread fetching. "
            "Install it with: pip install requests"
        ) from e

    key = api_key or os.environ.get("THE_ODDS_API_KEY")
    if not key:
        raise LiveDataError(
            "No API key found. Set THE_ODDS_API_KEY or pass api_key= explicitly. "
            "Sign up at https://the-odds-api.com for a free-tier key."
        )

    try:
        resp = requests.get(
            ODDS_API_BASE,
            params={
                "apiKey": key,
                "regions": "us",
                "markets": "spreads",
                "oddsFormat": "american",
            },
            timeout=15,
        )
        resp.raise_for_status()
        events = resp.json()
    except requests.RequestException as e:
        raise LiveDataError(f"Request to The Odds API failed: {e}") from e

    away_n, home_n = _normalize(away_team), _normalize(home_team)
    match = None
    for event in events:
        if _normalize(event.get("away_team", "")) == away_n and _normalize(
            event.get("home_team", "")
        ) == home_n:
            match = event
            break
    if match is None:
        raise LiveDataError(
            f"No current odds found for {away_team} @ {home_team}. "
            "Check team-name spelling matches The Odds API's naming, or the "
            "game may not be listed yet."
        )

    home_margins: List[float] = []
    for bookmaker in match.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market.get("key") != "spreads":
                continue
            for outcome in market.get("outcomes", []):
                if _normalize(outcome.get("name", "")) == home_n:
                    home_margins.append(float(outcome["point"]))

    if not home_margins:
        raise LiveDataError(
            f"Odds data found for {away_team} @ {home_team} but no spread market present."
        )

    median_home_point = statistics.median(home_margins)  # negative = home favored
    fetched_at = datetime.now(timezone.utc).isoformat()
    n_books = len(home_margins)

    if median_home_point == 0:
        return SpreadEstimate("even", 0, f"The Odds API, median of {n_books} books", fetched_at)
    if median_home_point < 0:
        return SpreadEstimate(
            "home", abs(median_home_point), f"The Odds API, median of {n_books} books", fetched_at
        )
    return SpreadEstimate(
        "away", median_home_point, f"The Odds API, median of {n_books} books", fetched_at
    )


def save_spread_snapshot(
    conn: sqlite3.Connection,
    season_year: int,
    week_number: int,
    away_team: str,
    home_team: str,
    estimate: SpreadEstimate,
) -> int:
    week_id = db.get_or_create_week(conn, season_year, week_number)
    game_id = db.get_or_create_game(conn, week_id, away_team, home_team)
    cur = conn.execute(
        """
        INSERT INTO market_snapshot
            (game_id, fetched_at, favorite, margin, source, is_authoritative_for_upset, notes)
        VALUES (?, ?, ?, ?, ?, 0, 'early estimate — confirm against configured spread source before trusting for 10x qualification')
        """,
        (game_id, estimate.fetched_at, estimate.favorite, estimate.margin, estimate.source),
    )
    conn.commit()
    return cur.lastrowid


def confirm_spread(
    conn: sqlite3.Connection,
    season_year: int,
    week_number: int,
    away_team: str,
    home_team: str,
    favorite: Optional[str],
    margin: float,
    source_name: str,
) -> int:
    """Records a manually-confirmed spread as authoritative and applies it to
    the game record used for scoring/upset qualification. This is the only
    path that should ever set is_authoritative_for_upset=1.
    """
    week_id = db.get_or_create_week(conn, season_year, week_number)
    game_id = db.get_or_create_game(conn, week_id, away_team, home_team)
    fetched_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO market_snapshot
            (game_id, fetched_at, favorite, margin, source, is_authoritative_for_upset, notes)
        VALUES (?, ?, ?, ?, ?, 1, 'manually confirmed for 10x-upset qualification')
        """,
        (game_id, fetched_at, favorite or "even", margin, source_name),
    )
    conn.execute(
        "UPDATE game SET favorite = ?, spread_margin = ? WHERE id = ?",
        (favorite, margin, game_id),
    )
    conn.commit()
    return game_id
