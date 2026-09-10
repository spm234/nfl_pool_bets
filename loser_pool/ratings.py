"""DB-aware glue around spread_model.py: looks up team ratings, picks
market-spread-vs-Elo per game, and rolls Elo ratings forward after results.
"""
from __future__ import annotations

import sqlite3
from typing import Optional, Tuple

from .config import LoserPoolConfig
from .spread_model import apply_elo_update, win_prob_from_elo, win_prob_from_spread


def get_team_rating(
    conn: sqlite3.Connection,
    team: str,
    season_year: int,
    week_number: int,
    cfg: Optional[LoserPoolConfig] = None,
) -> float:
    """Rating for `team` entering `week_number`. Falls back to the most
    recent earlier week's rating for that team/season if this week has no
    row yet (e.g. a bye week never got an explicit carry-forward row), and
    finally to the configured flat initial rating if there's no history at
    all — the deliberate no-preseason-source starting point (see README).
    """
    row = conn.execute(
        "SELECT rating FROM lp_team_rating WHERE team = ? AND season_year = ? "
        "AND week_number <= ? ORDER BY week_number DESC LIMIT 1",
        (team, season_year, week_number),
    ).fetchone()
    if row:
        return row["rating"]
    return (cfg or LoserPoolConfig.load(conn)).elo_initial_rating


def game_win_probabilities(
    conn: sqlite3.Connection,
    game_row: sqlite3.Row,
    season_year: int,
    week_number: int,
    cfg: Optional[LoserPoolConfig] = None,
) -> Tuple[float, float]:
    """(p_home_win, p_away_win) for one game row from lp_game. Uses the
    market spread (favorite/spread_margin) if one is recorded, else
    projects from Elo ratings.
    """
    cfg = cfg or LoserPoolConfig.load(conn)
    has_market_spread = game_row["favorite"] is not None or game_row["spread_margin"] not in (0, 0.0)
    if has_market_spread:
        return win_prob_from_spread(game_row["spread_margin"], game_row["favorite"], cfg.margin_std_dev)

    home_rating = get_team_rating(conn, game_row["home_team"], season_year, week_number, cfg)
    away_rating = get_team_rating(conn, game_row["away_team"], season_year, week_number, cfg)
    return win_prob_from_elo(home_rating, away_rating, cfg.elo_home_advantage)


def apply_elo_after_result(
    conn: sqlite3.Connection,
    season_year: int,
    week_number: int,
    away_team: str,
    home_team: str,
    cfg: Optional[LoserPoolConfig] = None,
) -> None:
    """Reads the settled score for this game and writes both teams' updated
    Elo ratings as of NEXT week. No-ops if the game has no recorded score
    yet (nothing to update from) — call this after record_game_result sets
    home_score/away_score, not before.
    """
    cfg = cfg or LoserPoolConfig.load(conn)
    week_id_row = conn.execute(
        "SELECT id FROM lp_week WHERE season_year = ? AND week_number = ?",
        (season_year, week_number),
    ).fetchone()
    if week_id_row is None:
        return
    game = conn.execute(
        "SELECT home_score, away_score FROM lp_game WHERE week_id = ? AND away_team = ? AND home_team = ?",
        (week_id_row["id"], away_team, home_team),
    ).fetchone()
    if game is None or game["home_score"] is None or game["away_score"] is None:
        return

    home_rating = get_team_rating(conn, home_team, season_year, week_number, cfg)
    away_rating = get_team_rating(conn, away_team, season_year, week_number, cfg)
    result = apply_elo_update(
        home_rating,
        away_rating,
        game["home_score"],
        game["away_score"],
        home_advantage=cfg.elo_home_advantage,
        k_factor=cfg.elo_k_factor,
    )
    next_week = week_number + 1
    for team, rating in ((home_team, result.new_home_rating), (away_team, result.new_away_rating)):
        conn.execute(
            """
            INSERT INTO lp_team_rating (team, season_year, week_number, rating)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(team, season_year, week_number) DO UPDATE SET rating = excluded.rating
            """,
            (team, season_year, next_week, rating),
        )
    conn.commit()
