"""Weekly pick/bet-size recommendation, ported from buildRecommendationWidget
in the JS prototype, combined with the Monte Carlo simulator for the
probability/payout outputs Phase 5 asks for.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .simulation import EntryPolicy, SimAssumptions, SimResult, run_simulation


def recommend_bet_sizes(current_points: float, min_bet: float, aggression: float = 50) -> dict:
    """aggression: 0 (conservative) .. 100 (aggressive)."""
    frac_normal = 0.15 + (0.6 - 0.15) * (aggression / 100)
    frac_upset = 0.3 + (0.9 - 0.3) * (aggression / 100)
    floor = min(min_bet, current_points)
    bet_normal = max(floor, min(current_points, round(current_points * frac_normal / 10) * 10))
    bet_upset = max(floor, min(current_points, round(current_points * frac_upset / 10) * 10))
    return {"normal": bet_normal, "upset": bet_upset, "frac_normal": frac_normal, "frac_upset": frac_upset}


@dataclass
class EntryRecommendation:
    entry_name: str
    current_points: float
    is_upset_opportunity: bool
    recommended_bet: float
    recommended_pick_reasoning: str
    sim_result: SimResult


def build_weekly_recommendations(
    entries: List[dict],
    assumptions: SimAssumptions,
    payouts: List[float],
    entry_fee: float,
    *,
    aggression: float = 50,
    seed: Optional[int] = None,
) -> List[EntryRecommendation]:
    """entries: list of {'name', 'current_points', 'is_upset_opportunity'}."""
    policies = []
    sizes = []
    for e in entries:
        s = recommend_bet_sizes(e["current_points"], assumptions.min_bet, aggression)
        sizes.append(s)
        bet = s["upset"] if e["is_upset_opportunity"] else s["normal"]
        frac = bet / e["current_points"] if e["current_points"] else 0
        policies.append(EntryPolicy(bet_fraction=frac, take_upset=True))

    starts = [e["current_points"] for e in entries]
    sim_results = run_simulation(assumptions, policies, starts, payouts, entry_fee, seed=seed)

    out = []
    for e, s, res in zip(entries, sizes, sim_results):
        if e["is_upset_opportunity"]:
            reasoning = (
                f"This qualifies as a 10x-upset opportunity — recommended bet is sized "
                f"more aggressively ({s['frac_upset']*100:.0f}% of stack) to reflect the "
                f"payout skew, assuming the win probability the simulation is using "
                f"({assumptions.p_upset_win:.0%}) holds."
            )
            bet = s["upset"]
        else:
            reasoning = (
                f"Normal week — recommended bet is {s['frac_normal']*100:.0f}% of current "
                f"stack, rounded to the nearest 10, floored at the pool minimum."
            )
            bet = s["normal"]
        out.append(
            EntryRecommendation(
                entry_name=e["name"],
                current_points=e["current_points"],
                is_upset_opportunity=e["is_upset_opportunity"],
                recommended_bet=bet,
                recommended_pick_reasoning=reasoning,
                sim_result=res,
            )
        )
    return out
