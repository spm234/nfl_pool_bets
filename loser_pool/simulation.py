"""Monte Carlo season-survival simulation for one entry.

Deliberately scoped to a single entry's own survival odds, not a full
field-wide simulation (who else is left, split-pot odds) — same
"deliberately thin" call the Office-Pool-4-Fun tool makes for its own
simulation layer (see README): modeling the whole field's pick behavior
would require assumptions about field size and pick tendencies that aren't
knowable this far out, whereas "how likely am I to survive with 2 lives if
I follow this plan" only depends on data this tool already has (schedule +
win-probability model).

The picking POLICY simulated here is fixed and simple: greedily take the
best remaining (highest loss-probability) team each week, recomputed after
each week's team is removed from availability. Loss probabilities
themselves are NOT re-randomized week to week in this simulation (they're
the model's point estimates) — only whether the picked team actually wins
or loses that week is randomized, once per Monte Carlo run.
"""
from __future__ import annotations

import random
import sqlite3
from typing import List, Optional

from .config import LoserPoolConfig
from .matchups import week_options_for_entry


def _greedy_p_lose_sequence(
    conn: sqlite3.Connection,
    season_year: int,
    week_numbers: List[int],
    exclude_teams: set,
    cfg: LoserPoolConfig,
) -> List[float]:
    """Loss probability for each week under a fixed "always take the best
    still-available team" policy. A week with zero available teams (bye
    weeks aside, this really only happens if every team is already used)
    contributes nothing — there's no pick to make, so no risk that week.
    """
    remaining_excluded = set(exclude_teams)
    sequence = []
    for week_number in sorted(week_numbers):
        options = week_options_for_entry(conn, season_year, [week_number], remaining_excluded, cfg).get(
            week_number, []
        )
        if not options:
            continue
        best = max(options, key=lambda o: o.p_lose)
        sequence.append(best.p_lose)
        remaining_excluded.add(best.team)
    return sequence


def simulate_entry_survival(
    conn: sqlite3.Connection,
    season_year: int,
    future_week_numbers: List[int],
    *,
    exclude_teams: set,
    lives_remaining: int,
    cfg: Optional[LoserPoolConfig] = None,
    runs: int = 2000,
    first_week_p_lose: Optional[float] = None,
    seed: Optional[int] = None,
) -> float:
    """P(entry has lives_remaining > 0 after every week in
    [first_week_p_lose's week, if given] + future_week_numbers), under the
    greedy pick policy for future weeks. `exclude_teams` should already
    include whatever team is being evaluated for the current week (the
    caller — optimizer.recommend_week — is responsible for that), so it's
    correctly excluded from the greedy future plan.
    """
    cfg = cfg or LoserPoolConfig.load(conn)
    p_lose_sequence = list(_greedy_p_lose_sequence(conn, season_year, future_week_numbers, exclude_teams, cfg))
    if first_week_p_lose is not None:
        p_lose_sequence = [first_week_p_lose] + p_lose_sequence

    if not p_lose_sequence:
        return 1.0 if lives_remaining > 0 else 0.0

    rng = random.Random(seed)
    survived_count = 0
    for _ in range(runs):
        lives = lives_remaining
        for p_lose in p_lose_sequence:
            if rng.random() >= p_lose:  # team WON -> bad pick -> burn a life
                lives -= 1
                if lives <= 0:
                    break
        if lives > 0:
            survived_count += 1
    return survived_count / runs
