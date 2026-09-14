"""Config-driven Loser Pool rules, loaded from/saved to loser_pool_config.

Nothing rule-specific should be hard-coded elsewhere — it all flows through
this object, same philosophy as pool/config.py.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class LoserPoolConfig:
    buy_in: float = 30.0
    lives_per_entry: int = 2
    tie_treated_as: str = "bust"  # 'bust' | 'survive'
    margin_std_dev: float = 13.86
    elo_home_advantage: float = 48.0
    elo_k_factor: float = 20.0
    elo_initial_rating: float = 1500.0

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> "LoserPoolConfig":
        row = conn.execute("SELECT * FROM loser_pool_config WHERE id = 1").fetchone()
        if row is None:
            return cls()
        return cls(
            buy_in=row["buy_in"],
            lives_per_entry=row["lives_per_entry"],
            tie_treated_as=row["tie_treated_as"],
            margin_std_dev=row["margin_std_dev"],
            elo_home_advantage=row["elo_home_advantage"],
            elo_k_factor=row["elo_k_factor"],
            elo_initial_rating=row["elo_initial_rating"],
        )

    def save(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            INSERT INTO loser_pool_config (
                id, buy_in, lives_per_entry, tie_treated_as, margin_std_dev,
                elo_home_advantage, elo_k_factor, elo_initial_rating
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                buy_in=excluded.buy_in,
                lives_per_entry=excluded.lives_per_entry,
                tie_treated_as=excluded.tie_treated_as,
                margin_std_dev=excluded.margin_std_dev,
                elo_home_advantage=excluded.elo_home_advantage,
                elo_k_factor=excluded.elo_k_factor,
                elo_initial_rating=excluded.elo_initial_rating
            """,
            (
                self.buy_in,
                self.lives_per_entry,
                self.tie_treated_as,
                self.margin_std_dev,
                self.elo_home_advantage,
                self.elo_k_factor,
                self.elo_initial_rating,
            ),
        )
        conn.commit()
