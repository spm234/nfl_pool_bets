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
        return PickChoice(
            pick="WIN", win_probability=p_team_win,
            is_upset=is_upset_pick(spread, "WIN", upset_threshold=upset_threshold, counts_favorite_loss=counts_favorite_loss),
            ev_per_point=win_ev, other_pick_ev_per_point=loss_ev,
        )
    return PickChoice(
        pick="LOSS", win_probability=1 - p_team_win,
        is_upset=is_upset_pick(spread, "LOSS", upset_threshold=upset_threshold, counts_favorite_loss=counts_favorite_loss),
        ev_per_point=loss_ev, other_pick_ev_per_point=win_ev,
    )


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
    assumed to be WIN.
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
        s = recommend_bet_sizes(e["current_points"], assumptions.min_bet, aggression)
        sizes.append(s)
        bet = s["upset"] if choice.is_upset else s["normal"]
        frac = bet / e["current_points"] if e["current_points"] else 0
        policies.append(EntryPolicy(bet_fraction=frac, take_upset=True))

    starts = [e["current_points"] for e in entries]
    sim_results = run_simulation(assumptions, policies, starts, payouts, entry_fee, seed=seed)

    out = []
    for e, s, choice, res in zip(entries, sizes, choices, sim_results):
        other_pick = "LOSS" if choice.pick == "WIN" else "WIN"
        ev_note = (
            f"Model win probability {choice.win_probability:.0%}; expected value "
            f"{choice.ev_per_point:+.2f}/pt on {choice.pick} vs {choice.other_pick_ev_per_point:+.2f}/pt "
            f"on {other_pick} — compares linear point EV only, not full tournament payout strategy."
        )
        if choice.is_upset:
            bet = s["upset"]
            reasoning = (
                f"Recommended pick: {choice.pick} — qualifies as a 10x-upset opportunity, "
                f"sized more aggressively ({s['frac_upset']*100:.0f}% of stack) to reflect "
                f"the payout skew. {ev_note}"
            )
        else:
            bet = s["normal"]
            reasoning = (
                f"Recommended pick: {choice.pick} — normal week, bet sized at "
                f"{s['frac_normal']*100:.0f}% of current stack. {ev_note}"
            )
        out.append(
            EntryRecommendation(
                entry_name=e["name"],
                current_points=e["current_points"],
                recommended_pick=choice.pick,
                is_upset_opportunity=choice.is_upset,
                recommended_bet=bet,
                recommended_pick_reasoning=reasoning,
                sim_result=res,
            )
        )
    return out
