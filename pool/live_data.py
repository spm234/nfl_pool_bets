"""Live spread and result fetching for the user's assigned games.

Deliberately uses a documented, scriptable lines API (The Odds API,
https://the-odds-api.com) rather than scraping a sportsbook page or the
pool's own settlement source directly (whatever that is — see
PoolConfig.spread_source_name, which is a manual-confirmation label, not
something this module fetches). A spread fetched here is never treated as
authoritative for 10x-upset qualification on its own: it's stored as an
early estimate (is_authoritative_for_upset=0), and only confirm_spread()
below — a deliberate, separate manual step — can mark one authoritative.

Fetched results (fetch_completed_score) are different in kind from spreads:
a final score isn't a matter of interpretation the way a betting line is,
so there's no separate "confirm" step for it. The one real caveat is that
this API reports only the final score, not the score at the end of
regulation — see fetch_completed_score's docstring for what that means for
the pool's OT-tie rule.
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
SCORES_API_BASE = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/scores"


@dataclass
class SpreadEstimate:
    favorite: str  # 'home' | 'away' | 'even'
    margin: float
    source: str
    fetched_at: str


@dataclass
class GameResult:
    outcome: str  # 'home' | 'away' | 'tie'
    home_score: int
    away_score: int
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


def fetch_completed_score(
    away_team: str, home_team: str, *, api_key: Optional[str] = None, days_from: int = 3
) -> GameResult:
    """Fetches a completed game's final score from The Odds API's /scores
    endpoint and returns the settled outcome.

    Caveat: this endpoint reports only the final score (after any overtime),
    not the score at the end of regulation. The pool's rule is that a game
    tied at the end of regulation scores as a loss for any WIN/LOSS pick
    regardless of who wins in OT — that distinction can't be recovered from
    a final-score-only source. It's a non-issue when the final score is
    tied (that can only happen if regulation was also tied), but a decisive
    final score here is reported as a normal win/loss even if the game
    actually went to overtime from a regulation tie; callers should surface
    that caveat rather than treat a decisive result as unconditionally safe.
    """
    try:
        import requests
    except ImportError as e:
        raise LiveDataError(
            "The 'requests' package is required for live score fetching. "
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
            SCORES_API_BASE,
            params={"apiKey": key, "daysFrom": days_from},
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
            f"No score data found for {away_team} @ {home_team} in the last {days_from} day(s). "
            "Check team-name spelling, or the game may not have been played yet."
        )
    if not match.get("completed"):
        raise LiveDataError(f"{away_team} @ {home_team} is not marked completed yet.")

    scores = {_normalize(s["name"]): int(s["score"]) for s in match.get("scores") or []}
    if away_n not in scores or home_n not in scores:
        raise LiveDataError(
            f"Game marked completed but score data is incomplete for {away_team} @ {home_team}."
        )
    away_score, home_score = scores[away_n], scores[home_n]
    fetched_at = datetime.now(timezone.utc).isoformat()

    if away_score == home_score:
        outcome = "tie"
    elif home_score > away_score:
        outcome = "home"
    else:
        outcome = "away"

    return GameResult(
        outcome=outcome, home_score=home_score, away_score=away_score,
        source="The Odds API", fetched_at=fetched_at,
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


_SHEET_ID_RE_MARKERS = ("/d/", "/spreadsheets/")


def extract_google_sheet_id(sheet_id_or_url: str) -> str:
    """Accepts either a bare Sheet ID or a full share URL
    (https://docs.google.com/spreadsheets/d/<ID>/edit?usp=sharing) and
    returns just the ID.
    """
    s = sheet_id_or_url.strip()
    if "/d/" in s:
        s = s.split("/d/", 1)[1]
        s = s.split("/", 1)[0]
    return s


def fetch_google_sheet_csv(sheet_id_or_url: str, *, gid: Optional[str] = None, timeout: int = 15) -> str:
    """Fetches a Google Sheet's data as CSV via its public export URL —
    plain HTTP GET, no OAuth. This only works if the sheet is shared as
    "Anyone with the link can view" (or more open); a restricted sheet
    returns an HTML login/permission page instead of CSV, which this
    detects and raises on rather than silently importing garbage.

    This is a genuinely different case from the officepool4fun.com
    situation: you control this sheet's sharing setting directly, so
    there's no login-wall-workaround question — if it's shared openly,
    a plain GET is exactly what "shared with a link" is for.
    """
    try:
        import requests
    except ImportError as e:
        raise LiveDataError(
            "The 'requests' package is required for Google Sheets fetching. "
            "Install it with: pip install requests"
        ) from e

    sheet_id = extract_google_sheet_id(sheet_id_or_url)
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export"
    params = {"format": "csv"}
    if gid:
        params["gid"] = gid

    try:
        resp = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as e:
        raise LiveDataError(f"Request to Google Sheets failed: {e}") from e

    if resp.status_code != 200:
        raise LiveDataError(
            f"Google Sheets returned HTTP {resp.status_code} for sheet {sheet_id}. "
            "If this is a permissions error, share the sheet as "
            "'Anyone with the link can view' (Share → General access)."
        )
    content_type = resp.headers.get("Content-Type", "")
    text = resp.text
    if "text/csv" not in content_type or text.lstrip().startswith("<"):
        raise LiveDataError(
            f"Sheet {sheet_id} did not return CSV (got Content-Type: {content_type!r}). "
            "This usually means the sheet isn't shared publicly — set sharing to "
            "'Anyone with the link can view' (Share → General access) and try again."
        )
    return text
