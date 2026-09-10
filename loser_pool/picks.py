"""Recording picks, settling results, and the lives/elimination bookkeeping
that makes this a "2 lives" pool rather than a plain single-strike one.

Team reuse rule: once an entry has picked a team, that team is unavailable
to them again — regardless of whether the pick survived or busted — until
(if ever) a playoff reset happens. This matches "no more bringing back
teams" from the rules.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Set

from . import db
from .config import LoserPoolConfig


class PickError(ValueError):
    pass


def used_teams(conn: sqlite3.Connection, entry_id: int, season_year: int) -> Set[str]:
    reset_week = db.most_recent_reset_week(conn)
    rows = conn.execute(
        """
        SELECT DISTINCT p.team_picked
        FROM lp_pick p
        JOIN lp_week w ON w.id = p.week_id
        WHERE p.entry_id = ? AND w.season_year = ? AND w.week_number > ?
        """,
        (entry_id, season_year, reset_week),
    ).fetchall()
    return {r["team_picked"] for r in rows}


def _find_game_for_team(conn: sqlite3.Connection, week_id: int, team: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM lp_game WHERE week_id = ? AND (away_team = ? OR home_team = ?)",
        (week_id, team, team),
    ).fetchone()


def record_pick(
    conn: sqlite3.Connection,
    season_year: int,
    week_number: int,
    entry_display_name: str,
    team: str,
    *,
    owner_name: Optional[str] = None,
    is_mine: bool = False,
    lives_per_entry: Optional[int] = None,
) -> int:
    """Records `team` as the entry's pick-to-lose for this week. Raises
    PickError (not a generic exception) for any rule violation, so callers
    (CLI included) can show a clean message instead of a traceback:
    entry already eliminated, team already used since the last reset, no
    game for that team in this week's schedule, or a pick already settled
    for this entry/week (a still-pending pick can be corrected freely).
    """
    cfg = LoserPoolConfig.load(conn)
    entry_id = db.get_or_create_entry(
        conn, owner_name=owner_name or entry_display_name, display_name=entry_display_name,
        is_mine=is_mine, lives_per_entry=lives_per_entry or cfg.lives_per_entry,
    )
    entry = conn.execute("SELECT * FROM lp_entry WHERE id = ?", (entry_id,)).fetchone()
    if entry["eliminated"]:
        raise PickError(f"{entry_display_name} is already eliminated (week {entry['eliminated_week']}).")

    week_id = db.get_or_create_week(conn, season_year, week_number)
    game = _find_game_for_team(conn, week_id, team)
    if game is None:
        raise PickError(f"No game found for '{team}' in season {season_year} week {week_number}.")

    if team in used_teams(conn, entry_id, season_year):
        raise PickError(f"{entry_display_name} has already used {team} this cycle.")

    existing = conn.execute(
        "SELECT id, result FROM lp_pick WHERE entry_id = ? AND week_id = ?", (entry_id, week_id)
    ).fetchone()
    if existing and existing["result"] != "pending":
        raise PickError(
            f"{entry_display_name}'s week {week_number} pick is already settled as "
            f"'{existing['result']}' — can't change it."
        )

    if existing:
        conn.execute("UPDATE lp_pick SET team_picked = ? WHERE id = ?", (team, existing["id"]))
        pick_id = existing["id"]
    else:
        cur = conn.execute(
            "INSERT INTO lp_pick (entry_id, week_id, team_picked) VALUES (?, ?, ?)",
            (entry_id, week_id, team),
        )
        pick_id = cur.lastrowid
    conn.commit()
    return pick_id


@dataclass
class SettleResult:
    entry_name: str
    team_picked: str
    outcome: str  # 'survived' | 'busted'
    lives_remaining: int
    eliminated: bool


def settle_week(
    conn: sqlite3.Connection, season_year: int, week_number: int, cfg: Optional[LoserPoolConfig] = None
) -> List[SettleResult]:
    """Settles every still-pending pick for this week whose game has a
    recorded outcome. Picks whose game hasn't been decided yet are left
    pending (safe to call repeatedly as results trickle in).
    """
    cfg = cfg or LoserPoolConfig.load(conn)
    week_id = db.get_or_create_week(conn, season_year, week_number)
    pending = conn.execute(
        """
        SELECT p.id AS pick_id, p.entry_id, p.team_picked, e.display_name, e.lives_remaining
        FROM lp_pick p
        JOIN lp_entry e ON e.id = p.entry_id
        WHERE p.week_id = ? AND p.result = 'pending'
        """,
        (week_id,),
    ).fetchall()

    results: List[SettleResult] = []
    for row in pending:
        game = _find_game_for_team(conn, week_id, row["team_picked"])
        if game is None or game["outcome"] is None:
            continue

        if game["outcome"] == "tie":
            busted = cfg.tie_treated_as == "bust"
        else:
            picked_side = "home" if game["home_team"] == row["team_picked"] else "away"
            busted = game["outcome"] == picked_side  # picked team's side won -> bad for a loser pick

        outcome = "busted" if busted else "survived"
        lives_remaining = row["lives_remaining"]
        life_lost = None
        if busted:
            life_lost = cfg.lives_per_entry - lives_remaining + 1
            lives_remaining -= 1

        eliminated = lives_remaining <= 0
        conn.execute(
            "UPDATE lp_pick SET result = ?, life_lost = ? WHERE id = ?",
            (outcome, life_lost, row["pick_id"]),
        )
        conn.execute(
            "UPDATE lp_entry SET lives_remaining = ?, eliminated = ?, eliminated_week = ? WHERE id = ?",
            (
                lives_remaining,
                int(eliminated),
                week_number if eliminated else None,
                row["entry_id"],
            ),
        )
        results.append(
            SettleResult(
                entry_name=row["display_name"],
                team_picked=row["team_picked"],
                outcome=outcome,
                lives_remaining=lives_remaining,
                eliminated=eliminated,
            )
        )

    conn.commit()
    return results


def trigger_playoff_reset(conn: sqlite3.Connection, reset_after_week_number: int, note: str = "") -> int:
    """Records that team-reuse restrictions no longer apply to picks made
    after `reset_after_week_number` — i.e. every surviving entry's used-team
    list goes back to empty starting the following week. Does NOT touch
    lives_remaining or eliminated status; that's a separate rule ("still no
    winner") the caller/operator judges, this just flips the team pool.
    """
    cur = conn.execute(
        "INSERT INTO lp_reset_event (reset_after_week_number, note, created_at) VALUES (?, ?, ?)",
        (reset_after_week_number, note, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return cur.lastrowid
