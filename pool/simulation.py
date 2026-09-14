"""Monte Carlo season simulator, ported from simulateOneEntry/runSim in the
JS prototype. Approximate by design: it doesn't know real future matchups or
real competitor behavior, so treat outputs as directional, not precise.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class SimAssumptions:
    weeks_remaining: int
    field_size: int
    runs: int = 1500
    p_win: float = 0.50
    p_upset_freq: float = 0.20
    p_upset_win: float = 0.22
    field_bet_fraction: float = 0.22
    field_upset_take_rate: float = 0.25
    min_bet: float = 20
    start_points: float = 150


@dataclass
class EntryPolicy:
    bet_fraction: float = 0.30
    take_upset: bool = True
    # Optional override for the very first simulated week, so a real,
    # already-known pick (real spread -> real win probability, from
    # recommend.choose_pick) is reflected in P(1st)/P(top10)/expected
    # payout instead of the generic p_win/p_upset_freq/p_upset_win
    # assumptions used for every other, not-yet-known future week.
    first_week_win_prob: Optional[float] = None
    first_week_mult: Optional[float] = None
    first_week_bet_fraction: Optional[float] = None


@dataclass
class SimResult:
    p_first: float
    p_top3: float
    p_top5: float
    p_top10: float
    avg_payout_pct: float
    avg_payout_dollars: float


def simulate_one_entry(
    points: float,
    weeks_remaining: int,
    frac: float,
    take_upset: bool,
    *,
    p_win: float,
    p_upset_freq: float,
    p_upset_win: float,
    min_bet: float,
    rng: random.Random,
    first_week_win_prob: Optional[float] = None,
    first_week_mult: Optional[float] = None,
    first_week_bet_fraction: Optional[float] = None,
) -> float:
    for week_index in range(weeks_remaining):
        if points <= 0:
            break
        if week_index == 0 and first_week_win_prob is not None:
            week_frac = first_week_bet_fraction if first_week_bet_fraction is not None else frac
            mult = first_week_mult if first_week_mult is not None else 1.0
            bet = min_bet if min_bet <= points else points
            bet = max(bet, round(points * week_frac))
            bet = min(bet, points)
            if rng.random() < first_week_win_prob:
                points += bet * mult
            else:
                points -= bet
            if points < 0:
                points = 0
            continue
        qualifies = rng.random() < p_upset_freq
        if qualifies and take_upset:
            bet = min_bet if min_bet <= points else points
            bet = max(bet, round(points * min(frac * 1.4, 1)))
            bet = min(bet, points)
            if rng.random() < p_upset_win:
                points += bet * 10
            else:
                points -= bet
        else:
            bet = min_bet if min_bet <= points else points
            bet = max(bet, round(points * frac))
            bet = min(bet, points)
            if rng.random() < p_win:
                points += bet
            else:
                points -= bet
        if points < 0:
            points = 0
    return points


def run_simulation(
    assumptions: SimAssumptions,
    my_policies: List[EntryPolicy],
    my_start_points: List[float],
    payouts: List[float],
    entry_fee: float,
    *,
    seed: Optional[int] = None,
) -> List[SimResult]:
    rng = random.Random(seed)
    a = assumptions
    finish_counts = [
        {"first": 0, "top3": 0, "top5": 0, "top10": 0, "payout_sum": 0.0} for _ in my_policies
    ]

    for _ in range(a.runs):
        field_final = [
            simulate_one_entry(
                a.start_points,
                a.weeks_remaining,
                a.field_bet_fraction,
                rng.random() < a.field_upset_take_rate,
                p_win=a.p_win,
                p_upset_freq=a.p_upset_freq,
                p_upset_win=a.p_upset_win,
                min_bet=a.min_bet,
                rng=rng,
            )
            for _ in range(a.field_size)
        ]
        my_final = [
            simulate_one_entry(
                my_start_points[i],
                a.weeks_remaining,
                pol.bet_fraction,
                pol.take_upset,
                p_win=a.p_win,
                p_upset_freq=a.p_upset_freq,
                p_upset_win=a.p_upset_win,
                min_bet=a.min_bet,
                rng=rng,
                first_week_win_prob=pol.first_week_win_prob,
                first_week_mult=pol.first_week_mult,
                first_week_bet_fraction=pol.first_week_bet_fraction,
            )
            for i, pol in enumerate(my_policies)
        ]

        all_scores = field_final + my_final
        ranking = sorted(range(len(all_scores)), key=lambda i: -all_scores[i])
        rank_of = {idx: rank + 1 for rank, idx in enumerate(ranking)}

        for i in range(len(my_policies)):
            my_index = a.field_size + i
            rank = rank_of[my_index]
            fc = finish_counts[i]
            if rank == 1:
                fc["first"] += 1
            if rank <= 3:
                fc["top3"] += 1
            if rank <= 5:
                fc["top5"] += 1
            if rank <= 10:
                fc["top10"] += 1
            pct = payouts[rank - 1] if 1 <= rank <= len(payouts) else 0
            fc["payout_sum"] += pct

    total_pot = (a.field_size + len(my_policies)) * entry_fee
    results = []
    for fc in finish_counts:
        avg_payout_pct = fc["payout_sum"] / a.runs
        results.append(
            SimResult(
                p_first=fc["first"] / a.runs,
                p_top3=fc["top3"] / a.runs,
                p_top5=fc["top5"] / a.runs,
                p_top10=fc["top10"] / a.runs,
                avg_payout_pct=avg_payout_pct,
                avg_payout_dollars=(avg_payout_pct / 100) * total_pot,
            )
        )
    return results
