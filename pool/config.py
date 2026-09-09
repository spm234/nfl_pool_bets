"""Config-driven pool rules, loaded from/saved to the pool_config table.

Nothing about this season's specifics (payouts, thresholds, entry fee) should
be hard-coded in the scoring/simulation/reconstruction logic — it all flows
through this object instead.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import List


@dataclass
class PoolConfig:
    start_points: float = 150
    min_bet: float = 20
    entry_fee: float = 30
    payouts: List[float] = field(
        default_factory=lambda: [40, 18, 10, 9, 7, 6, 4, 3, 2, 1]
    )
    upset_spread_threshold: float = 10.0
    upset_multiplier: float = 10.0
    tie_multiplier: float = 10.0
    late_pick_default_bet: float = 20
    late_pick_default_side: str = "home"
    late_pick_default_pick: str = "WIN"
    spread_source_name: str = "Cleveland Plain Dealer (Thursday)"
    upset_counts_favorite_loss: bool = True
    default_field_size: int = 107

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> "PoolConfig":
        row = conn.execute("SELECT * FROM pool_config WHERE id = 1").fetchone()
        if row is None:
            return cls()
        return cls(
            start_points=row["start_points"],
            min_bet=row["min_bet"],
            entry_fee=row["entry_fee"],
            payouts=json.loads(row["payouts_json"]),
            upset_spread_threshold=row["upset_spread_threshold"],
            upset_multiplier=row["upset_multiplier"],
            tie_multiplier=row["tie_multiplier"],
            late_pick_default_bet=row["late_pick_default_bet"],
            late_pick_default_side=row["late_pick_default_side"],
            late_pick_default_pick=row["late_pick_default_pick"],
            spread_source_name=row["spread_source_name"],
            upset_counts_favorite_loss=bool(row["upset_counts_favorite_loss"]),
            default_field_size=row["default_field_size"],
        )

    def save(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            INSERT INTO pool_config (
                id, start_points, min_bet, entry_fee, payouts_json,
                upset_spread_threshold, upset_multiplier, tie_multiplier,
                late_pick_default_bet, late_pick_default_side, late_pick_default_pick,
                spread_source_name, upset_counts_favorite_loss, default_field_size
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                start_points=excluded.start_points,
                min_bet=excluded.min_bet,
                entry_fee=excluded.entry_fee,
                payouts_json=excluded.payouts_json,
                upset_spread_threshold=excluded.upset_spread_threshold,
                upset_multiplier=excluded.upset_multiplier,
                tie_multiplier=excluded.tie_multiplier,
                late_pick_default_bet=excluded.late_pick_default_bet,
                late_pick_default_side=excluded.late_pick_default_side,
                late_pick_default_pick=excluded.late_pick_default_pick,
                spread_source_name=excluded.spread_source_name,
                upset_counts_favorite_loss=excluded.upset_counts_favorite_loss,
                default_field_size=excluded.default_field_size
            """,
            (
                self.start_points,
                self.min_bet,
                self.entry_fee,
                json.dumps(self.payouts),
                self.upset_spread_threshold,
                self.upset_multiplier,
                self.tie_multiplier,
                self.late_pick_default_bet,
                self.late_pick_default_side,
                self.late_pick_default_pick,
                self.spread_source_name,
                int(self.upset_counts_favorite_loss),
                self.default_field_size,
            ),
        )
        conn.commit()
