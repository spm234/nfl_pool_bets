"""Read/derive layer: turns raw + inferred tables into timelines and
reconstructions. Nothing here is persisted as a "recommendation" — that's
computed on demand by the simulation layer (Phase 5).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from .config import PoolConfig
from .reconstruction import Candidate, reconstruct_candidates
from .scoring import Game, ScoreResult, outcome_for_team, score_bet, spread_for_team


@dataclass
class TimelineRow:
    week_number: int
    away_team: str
    home_team: str
    assigned_side: str
    pick: Optional[str]
    bet: Optional[float]
    points_before: float
    points_after: Optional[float]
    result: Optional[ScoreResult]


@dataclass
class EntryTimeline:
    rows: List[TimelineRow]
    current_points: float
    eliminated: bool


def _game_from_row(row: sqlite3.Row) -> Game:
    return Game(favorite=row["favorite"], margin=row["spread_margin"], outcome=row["outcome"])


def compute_my_entry_timeline(
    conn: sqlite3.Connection, entry_id: int, config: PoolConfig
) -> EntryTimeline:
    rows = conn.execute(
        """
        SELECT w.week_number, g.away_team, g.home_team, g.favorite, g.spread_margin, g.outcome,
               a.assigned_side, wo.declared_pick, wo.declared_bet
        FROM assignment a
        JOIN game g ON g.id = a.game_id
        JOIN week w ON w.id = g.week_id
        LEFT JOIN wager_observation wo ON wo.assignment_id = a.id
        WHERE a.entry_id = ?
        ORDER BY w.week_number ASC
        """,
        (entry_id,),
    ).fetchall()

    points = config.start_points
    out_rows: List[TimelineRow] = []
    for r in rows:
        game = _game_from_row(r)
        side = r["assigned_side"]
        spread = spread_for_team(game, side)
        outcome = outcome_for_team(game, side)
        pick = r["declared_pick"]
        bet = r["declared_bet"]
        result = None
        before = points
        after: Optional[float] = None
        if pick is not None and bet is not None:
            result = score_bet(
                spread,
                pick,
                bet,
                outcome,
                upset_threshold=config.upset_spread_threshold,
                upset_multiplier=config.upset_multiplier,
                tie_multiplier=config.tie_multiplier,
                counts_favorite_loss=config.upset_counts_favorite_loss,
            )
            if result is not None:
                points += result.delta
                after = points
        out_rows.append(
            TimelineRow(
                week_number=r["week_number"],
                away_team=r["away_team"],
                home_team=r["home_team"],
                assigned_side=side,
                pick=pick,
                bet=bet,
                points_before=before,
                points_after=after,
                result=result,
            )
        )
    return EntryTimeline(rows=out_rows, current_points=points, eliminated=points <= 0)


@dataclass
class FieldReconRow:
    entry_id: int
    entry_name: str
    away_team: str
    home_team: str
    assigned_side: str
    prev_points: Optional[float]
    current_points: Optional[float]
    delta: Optional[float]
    candidates: List[Candidate]


def compute_field_reconstruction(
    conn: sqlite3.Connection, season_year: int, week_number: int, config: PoolConfig
) -> List[FieldReconRow]:
    week_row = conn.execute(
        "SELECT id FROM week WHERE season_year = ? AND week_number = ?",
        (season_year, week_number),
    ).fetchone()
    if week_row is None:
        return []
    week_id = week_row["id"]

    prev_week_row = conn.execute(
        """
        SELECT id, week_number FROM week
        WHERE season_year = ? AND week_number < ?
        ORDER BY week_number DESC LIMIT 1
        """,
        (season_year, week_number),
    ).fetchone()

    assignments = conn.execute(
        """
        SELECT a.id AS assignment_id, a.entry_id, e.display_name, a.assigned_side,
               g.away_team, g.home_team, g.favorite, g.spread_margin, g.outcome
        FROM assignment a
        JOIN entry e ON e.id = a.entry_id
        JOIN game g ON g.id = a.game_id
        WHERE g.week_id = ? AND e.is_mine = 0
        ORDER BY e.display_name
        """,
        (week_id,),
    ).fetchall()

    out: List[FieldReconRow] = []
    for r in assignments:
        cur_row = conn.execute(
            "SELECT points FROM entry_week_points WHERE entry_id = ? AND week_id = ?",
            (r["entry_id"], week_id),
        ).fetchone()
        cur_points = cur_row["points"] if cur_row else None

        prev_points = None
        if prev_week_row is not None:
            prev_row = conn.execute(
                "SELECT points FROM entry_week_points WHERE entry_id = ? AND week_id = ?",
                (r["entry_id"], prev_week_row["id"]),
            ).fetchone()
            prev_points = prev_row["points"] if prev_row else None
        elif week_number == 1:
            prev_points = config.start_points

        game = _game_from_row(r)
        side = r["assigned_side"]
        spread = spread_for_team(game, side)
        outcome = outcome_for_team(game, side)

        candidates = reconstruct_candidates(
            prev_points,
            cur_points,
            spread,
            outcome,
            config.min_bet,
            upset_threshold=config.upset_spread_threshold,
            upset_multiplier=config.upset_multiplier,
            tie_multiplier=config.tie_multiplier,
            counts_favorite_loss=config.upset_counts_favorite_loss,
        )

        if candidates:
            now = datetime.now(timezone.utc).isoformat()
            for rank, c in enumerate(candidates):
                conn.execute(
                    """
                    INSERT INTO wager_inference
                        (assignment_id, inferred_pick, inferred_bet, confidence_rank, label, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(assignment_id, inferred_pick) DO UPDATE SET
                        inferred_bet = excluded.inferred_bet,
                        confidence_rank = excluded.confidence_rank,
                        label = excluded.label,
                        created_at = excluded.created_at
                    """,
                    (r["assignment_id"], c.pick, c.bet, rank, c.label, now),
                )
            conn.commit()

        out.append(
            FieldReconRow(
                entry_id=r["entry_id"],
                entry_name=r["display_name"],
                away_team=r["away_team"],
                home_team=r["home_team"],
                assigned_side=side,
                prev_points=prev_points,
                current_points=cur_points,
                delta=(cur_points - prev_points) if (cur_points is not None and prev_points is not None) else None,
                candidates=candidates,
            )
        )
    return out


@dataclass
class ScenarioEntry:
    name: str
    is_mine: bool
    away_team: str
    home_team: str
    assigned_side: str
    points_entering_week: float


@dataclass
class ScenarioGame:
    away_team: str
    home_team: str
    favorite: Optional[str]
    margin: float
    outcome: Optional[str]


@dataclass
class ScenarioData:
    games: List[ScenarioGame]
    entries: List[ScenarioEntry]


def get_scenario_projection_data(
    conn: sqlite3.Connection, season_year: int, week_number: int, config: PoolConfig
) -> ScenarioData:
    """Everyone (mine + field) with an assignment this week, plus the games
    they're tied to — the raw material for a client-side 'what if this game
    goes this way' standings projector. points_entering_week is each entry's
    standing as of the end of the *previous* week (or start_points for week
    1), matching what a pre-game hypothetical should be based on.
    """
    week_row = conn.execute(
        "SELECT id FROM week WHERE season_year = ? AND week_number = ?",
        (season_year, week_number),
    ).fetchone()
    if week_row is None:
        return ScenarioData(games=[], entries=[])
    week_id = week_row["id"]

    prev_week_row = conn.execute(
        """
        SELECT id FROM week WHERE season_year = ? AND week_number < ?
        ORDER BY week_number DESC LIMIT 1
        """,
        (season_year, week_number),
    ).fetchone()

    assignments = conn.execute(
        """
        SELECT e.display_name, e.is_mine, e.id AS entry_id, a.assigned_side,
               g.away_team, g.home_team, g.favorite, g.spread_margin, g.outcome
        FROM assignment a
        JOIN entry e ON e.id = a.entry_id
        JOIN game g ON g.id = a.game_id
        WHERE g.week_id = ?
        ORDER BY e.is_mine DESC, e.display_name
        """,
        (week_id,),
    ).fetchall()

    games: dict = {}
    entries: List[ScenarioEntry] = []
    for r in assignments:
        key = (r["away_team"], r["home_team"])
        if key not in games:
            games[key] = ScenarioGame(
                away_team=r["away_team"], home_team=r["home_team"],
                favorite=r["favorite"], margin=r["spread_margin"] or 0, outcome=r["outcome"],
            )

        points = None
        if prev_week_row is not None:
            prev_row = conn.execute(
                "SELECT points FROM entry_week_points WHERE entry_id = ? AND week_id = ?",
                (r["entry_id"], prev_week_row["id"]),
            ).fetchone()
            points = prev_row["points"] if prev_row else None
        elif week_number == 1:
            points = config.start_points
        if points is None:
            continue  # no known starting point for this entry — skip rather than guess

        entries.append(
            ScenarioEntry(
                name=r["display_name"], is_mine=bool(r["is_mine"]),
                away_team=r["away_team"], home_team=r["home_team"],
                assigned_side=r["assigned_side"], points_entering_week=points,
            )
        )

    return ScenarioData(games=list(games.values()), entries=entries)
