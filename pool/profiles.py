"""Thin competitor profiles built from resolved weeks' top reconstruction
candidate per entry. Deliberately additive/approximate: with only a few
weeks of real data this is low-confidence, and it's fine to leave thin until
there's more history to learn from (per the starter prompt's Phase 3 scope).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass
class CompetitorProfile:
    entry_id: int
    entry_name: str
    weeks_observed: int
    avg_bet_fraction: Optional[float]
    favorite_bias: Optional[float]  # -1 favorite-leaning .. +1 underdog-leaning
    upset_take_rate: Optional[float]  # fraction of qualifying-upset weeks taken


def build_competitor_profiles(conn: sqlite3.Connection) -> list[CompetitorProfile]:
    entries = conn.execute(
        "SELECT id, display_name FROM entry WHERE is_mine = 0"
    ).fetchall()

    profiles = []
    for e in entries:
        # Top-confidence inference per assignment (rank 0) is treated as the
        # best guess of what actually happened, for profiling purposes only.
        rows = conn.execute(
            """
            SELECT wi.inferred_pick, wi.inferred_bet, ewp_cur.points AS cur_points,
                   g.favorite, g.spread_margin, a.assigned_side
            FROM wager_inference wi
            JOIN assignment a ON a.id = wi.assignment_id
            JOIN game g ON g.id = a.game_id
            JOIN entry_week_points ewp_cur ON ewp_cur.entry_id = a.entry_id AND ewp_cur.week_id = g.week_id
            WHERE wi.confidence_rank = 0 AND a.entry_id = ?
            """,
            (e["id"],),
        ).fetchall()

        if not rows:
            profiles.append(
                CompetitorProfile(
                    entry_id=e["id"],
                    entry_name=e["display_name"],
                    weeks_observed=0,
                    avg_bet_fraction=None,
                    favorite_bias=None,
                    upset_take_rate=None,
                )
            )
            continue

        bet_fractions = []
        favorite_leaning = []
        upset_qualifying = 0
        upset_taken = 0
        for r in rows:
            side = r["assigned_side"]
            margin = r["spread_margin"] or 0
            is_fav = r["favorite"] == side
            spread = -margin if is_fav else margin
            if r["cur_points"] and r["cur_points"] > 0:
                # bet fraction relative to the resulting stack is a rough proxy
                # (true "points held entering the week" isn't retained here).
                bet_fractions.append(min(1.0, (r["inferred_bet"] or 0) / max(r["cur_points"], 1)))
            favorite_leaning.append(1 if spread > 0 else (-1 if spread < 0 else 0))
            if abs(spread) >= 10:
                upset_qualifying += 1
                if (spread > 0 and r["inferred_pick"] == "WIN") or (
                    spread < 0 and r["inferred_pick"] == "LOSS"
                ):
                    upset_taken += 1

        profiles.append(
            CompetitorProfile(
                entry_id=e["id"],
                entry_name=e["display_name"],
                weeks_observed=len(rows),
                avg_bet_fraction=sum(bet_fractions) / len(bet_fractions) if bet_fractions else None,
                favorite_bias=sum(favorite_leaning) / len(favorite_leaning) if favorite_leaning else None,
                upset_take_rate=(upset_taken / upset_qualifying) if upset_qualifying else None,
            )
        )
    return profiles
