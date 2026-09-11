"""Weekly pick/bet-size recommendation, ported from buildRecommendationWidget
in the JS prototype, combined with the Monte Carlo simulator for the
probability/payout outputs Phase 5 asks for.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .scoring import Pick, is_upset_pick, win_prob_from_spread
from .simulation import EntryPolicy, SimAssumptions, SimResult, run_simulation


@dataclass
class PickChoice:
    pick: Pick  # 'WIN' or 'LOSS' — never defaults to WIN without comparing
    win_probability: float  # model-estimated probability THIS pick is correct
    is_upset: bool
    multiplier: float  # 1 for a normal pick, upset_multiplier for a qualifying upset
    ev_per_point: float
    other_pick_ev_per_point: float  # EV of the pick not chosen, for transparency


def choose_pick(
    spread: float,
    *,
    upset_threshold: float = 10.0,
    upset_multiplier: float = 10.0,
    counts_favorite_loss: bool = True,
) -> PickChoice:
    """Compares betting WIN vs LOSS on the assigned team and returns
    whichever has higher expected value per point wagered — it does not
    default to WIN. Win probability comes from a logistic model driven by
    the spread (scoring.win_prob_from_spread): a model assumption, not a
    guarantee, same as the rest of the simulator.

    This compares linear expected points only. It does NOT account for how
    variance interacts with the pool's top-10 payout structure — a
    long-shot upset can be worth more than its raw EV suggests if you're
    chasing 1st place late in the season, and worth less if you're just
    trying to survive near the bottom. Treat this as one input, not the
    final word — the simulated P(1st)/P(top10) figures alongside it are
    the closer approximation of tournament strategy.

    TIE is deliberately excluded from the comparison: real NFL games end
    in a regulation tie so rarely that betting TIE straight up is almost
    never defensible, even at 10x, and there's no principled way to
    estimate its probability from a spread.
    """
    p_team_win = win_prob_from_spread(spread)

    def ev(pick: Pick, p_correct: float) -> float:
        upset = is_upset_pick(
            spread, pick, upset_threshold=upset_threshold, counts_favorite_loss=counts_favorite_loss
        )
        mult = upset_multiplier if upset else 1
        return p_correct * mult - (1 - p_correct)

    win_ev = ev("WIN", p_team_win)
    loss_ev = ev("LOSS", 1 - p_team_win)

    if win_ev >= loss_ev:
        is_upset = is_upset_pick(spread, "WIN", upset_threshold=upset_threshold, counts_favorite_loss=counts_favorite_loss)
        return PickChoice(
            pick="WIN", win_probability=p_team_win, is_upset=is_upset,
            multiplier=upset_multiplier if is_upset else 1.0,
            ev_per_point=win_ev, other_pick_ev_per_point=loss_ev,
        )
    is_upset = is_upset_pick(spread, "LOSS", upset_threshold=upset_threshold, counts_favorite_loss=counts_favorite_loss)
    return PickChoice(
        pick="LOSS", win_probability=1 - p_team_win, is_upset=is_upset,
        multiplier=upset_multiplier if is_upset else 1.0,
        ev_per_point=loss_ev, other_pick_ev_per_point=win_ev,
    )


def kelly_fraction(win_probability: float, multiplier: float) -> float:
    """Full Kelly fraction for a bet shaped like this pool's actual payout
    (see scoring.score_bet): win and the stake grows by bet*multiplier,
    lose and the stake is gone — never a fraction returned regardless of
    outcome. Clamped to [0, 1]; a probability too low to justify betting
    at all recommends betting nothing, never a negative (short) stake.
    """
    if multiplier <= 0:
        return 0.0
    edge = win_probability * multiplier - (1 - win_probability)
    return max(0.0, min(1.0, edge / multiplier))


def recommend_bet_size(
    current_points: float, min_bet: float, win_probability: float, multiplier: float, aggression: float = 50
) -> dict:
    """Stake scales continuously with the actual edge (win probability and
    payout multiplier), not just a flat percentage — a 71%-likely pick and
    a 55%-likely pick should not get the same stake, and previously did.

    aggression (0..100) scales how much of full Kelly to actually bet:
    0 -> 10% of Kelly (very conservative), 100 -> 75% of Kelly. Never full
    Kelly — that's theoretically optimal for long-run compounding growth
    of a repeatedly-reinvested bankroll, not appropriate for a short,
    finite, ranked tournament with a top-10 payout structure.
    """
    full_kelly = kelly_fraction(win_probability, multiplier)
    kelly_scale = 0.10 + (0.75 - 0.10) * (aggression / 100)
    fraction = full_kelly * kelly_scale
    floor = min(min_bet, current_points)
    bet = max(floor, min(current_points, round(current_points * fraction / 10) * 10))
    return {"bet": bet, "fraction": fraction, "full_kelly": full_kelly}


@dataclass
class EntryRecommendation:
    entry_name: str
    current_points: float
    recommended_pick: Pick
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
    upset_threshold: float = 10.0,
    upset_multiplier: float = 10.0,
    counts_favorite_loss: bool = True,
    seed: Optional[int] = None,
) -> List[EntryRecommendation]:
    """entries: list of {'name', 'current_points', 'spread'}. spread follows
    scoring.spread_for_team's convention: positive = the entry's assigned
    team is an underdog by N, negative = favored by N, 0 = pick'em.

    The pick (WIN or LOSS) is chosen per entry by choose_pick — it is never
    assumed to be WIN. The stake is Kelly-derived from that same pick's
    actual win probability and payout multiplier, and the simulated
    P(1st)/P(top10)/expected payout below uses that real probability for
    the immediate week too (via EntryPolicy's first_week_* override) —
    only weeks beyond this one fall back to the simulator's generic
    calibrated assumptions, since future matchups aren't known yet.
    """
    policies = []
    sizes = []
    choices = []
    for e in entries:
        choice = choose_pick(
            e["spread"], upset_threshold=upset_threshold,
            upset_multiplier=upset_multiplier, counts_favorite_loss=counts_favorite_loss,
        )
        choices.append(choice)
        size = recommend_bet_size(
            e["current_points"], assumptions.min_bet, choice.win_probability, choice.multiplier, aggression
        )
        sizes.append(size)
        frac = size["bet"] / e["current_points"] if e["current_points"] else 0
        policies.append(
            EntryPolicy(
                bet_fraction=frac, take_upset=True,
                first_week_win_prob=choice.win_probability,
                first_week_mult=choice.multiplier,
                first_week_bet_fraction=frac,
            )
        )

    starts = [e["current_points"] for e in entries]
    sim_results = run_simulation(assumptions, policies, starts, payouts, entry_fee, seed=seed)

    out = []
    for e, size, choice, res in zip(entries, sizes, choices, sim_results):
        other_pick = "LOSS" if choice.pick == "WIN" else "WIN"
        reasoning = (
            f"Recommended pick: {choice.pick}"
            + (" — qualifying 10x upset." if choice.is_upset else ".")
            + f" Model win probability {choice.win_probability:.0%}; stake sized at "
            f"{size['fraction']*100:.0f}% of stack ({size['full_kelly']*100:.0f}% would be full "
            f"Kelly at this edge, scaled down by the aggression setting). "
            f"{choice.pick} EV {choice.ev_per_point:+.2f}/pt vs {other_pick} "
            f"{choice.other_pick_ev_per_point:+.2f}/pt — compares linear point EV only, not full "
            f"tournament payout strategy."
        )
        out.append(
            EntryRecommendation(
                entry_name=e["name"],
                current_points=e["current_points"],
                recommended_pick=choice.pick,
                is_upset_opportunity=choice.is_upset,
                recommended_bet=size["bet"],
                recommended_pick_reasoning=reasoning,
                sim_result=res,
            )
        )
    return out
