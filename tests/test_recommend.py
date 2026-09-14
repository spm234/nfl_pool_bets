import pytest

from pool.recommend import (
    build_weekly_recommendations,
    choose_pick,
    kelly_fraction,
    recommend_bet_size,
)
from pool.simulation import SimAssumptions


# --- kelly_fraction ---

def test_kelly_fraction_evenmoney_matches_2p_minus_1():
    assert kelly_fraction(0.71, 1.0) == pytest.approx(2 * 0.71 - 1)
    assert kelly_fraction(0.55, 1.0) == pytest.approx(2 * 0.55 - 1)


def test_kelly_fraction_below_50pct_evenmoney_is_zero_not_negative():
    # No edge (or a negative one) at even money -> bet nothing, never short.
    assert kelly_fraction(0.4, 1.0) == 0.0


def test_kelly_fraction_upset_bet_uses_full_multiplier_as_b():
    # p=0.19, mult=10: (10*0.19 - 0.81)/10
    assert kelly_fraction(0.19, 10.0) == pytest.approx((10 * 0.19 - 0.81) / 10)


def test_kelly_fraction_clamped_to_one():
    assert kelly_fraction(0.99, 1.0) <= 1.0


# --- recommend_bet_size: the actual fix — bigger edge means bigger stake ---

def test_recommend_bet_size_scales_with_win_probability():
    small_edge = recommend_bet_size(150, min_bet=20, win_probability=0.55, multiplier=1.0, aggression=50)
    big_edge = recommend_bet_size(150, min_bet=20, win_probability=0.71, multiplier=1.0, aggression=50)
    assert big_edge["bet"] > small_edge["bet"]
    assert big_edge["fraction"] > small_edge["fraction"]


def test_recommend_bet_size_no_edge_floors_at_min_bet():
    no_edge = recommend_bet_size(150, min_bet=20, win_probability=0.50, multiplier=1.0, aggression=50)
    assert no_edge["fraction"] == 0
    assert no_edge["bet"] == 20  # floored at min_bet even with zero Kelly edge


def test_recommend_bet_size_never_exceeds_full_kelly():
    for aggression in (0, 50, 100):
        size = recommend_bet_size(150, min_bet=20, win_probability=0.71, multiplier=1.0, aggression=aggression)
        assert size["fraction"] <= size["full_kelly"]


def test_recommend_bet_size_aggression_scales_within_kelly():
    conservative = recommend_bet_size(300, min_bet=20, win_probability=0.71, multiplier=1.0, aggression=0)
    aggressive = recommend_bet_size(300, min_bet=20, win_probability=0.71, multiplier=1.0, aggression=100)
    assert conservative["bet"] < aggressive["bet"]


def test_recommend_bet_size_respects_stack_cap():
    huge = recommend_bet_size(20, min_bet=20, win_probability=0.99, multiplier=10.0, aggression=100)
    assert huge["bet"] <= 20


# --- choose_pick (unchanged behavior, still covered) ---

def test_choose_pick_pickem_defaults_win_on_true_tie():
    choice = choose_pick(0)
    assert choice.pick == "WIN"
    assert choice.ev_per_point == choice.other_pick_ev_per_point


def test_choose_pick_moderate_underdog_below_upset_threshold_prefers_loss():
    choice = choose_pick(5, upset_threshold=10)
    assert choice.pick == "LOSS"
    assert choice.multiplier == 1.0


def test_choose_pick_big_underdog_qualifying_upset_prefers_loss_over_win_here():
    choice = choose_pick(12, upset_threshold=10, upset_multiplier=10)
    assert choice.pick == "LOSS"
    assert choice.is_upset is False
    assert choice.multiplier == 1.0


def test_choose_pick_big_favorite_prefers_win_over_upset_fade():
    choice = choose_pick(-12, upset_threshold=10, upset_multiplier=10, counts_favorite_loss=True)
    assert choice.pick == "WIN"
    assert choice.is_upset is False
    assert choice.multiplier == 1.0


def test_choose_pick_favorite_loss_upset_disabled_strongly_prefers_win():
    enabled = choose_pick(-12, upset_threshold=10, upset_multiplier=10, counts_favorite_loss=True)
    disabled = choose_pick(-12, upset_threshold=10, upset_multiplier=10, counts_favorite_loss=False)
    assert disabled.pick == "WIN" and enabled.pick == "WIN"
    assert enabled.ev_per_point == disabled.ev_per_point
    assert disabled.other_pick_ev_per_point < enabled.other_pick_ev_per_point


# --- build_weekly_recommendations: end-to-end ---

def test_build_weekly_recommendations_shapes_output():
    entries = [
        {"name": "My Entry 1", "current_points": 150, "spread": 0},
        {"name": "My Entry 2", "current_points": 300, "spread": 12},
        {"name": "My Entry 3", "current_points": 40, "spread": -3},
    ]
    assumptions = SimAssumptions(weeks_remaining=5, field_size=50, runs=200, min_bet=20)
    recs = build_weekly_recommendations(
        entries, assumptions, [40, 18, 10, 9, 7, 6, 4, 3, 2, 1], 30, seed=1
    )
    assert len(recs) == 3
    assert recs[1].recommended_pick in ("WIN", "LOSS")
    assert recs[0].recommended_bet <= entries[0]["current_points"]
    assert 0 <= recs[0].sim_result.p_top10 <= 1


def test_build_weekly_recommendations_does_not_always_recommend_win():
    entries = [
        {"name": "A", "current_points": 150, "spread": 5},   # moderate dog, no bonus -> LOSS
        {"name": "B", "current_points": 150, "spread": -12}, # big favorite -> WIN
        {"name": "C", "current_points": 150, "spread": 12},  # big dog, bonus available -> LOSS
    ]
    assumptions = SimAssumptions(weeks_remaining=5, field_size=50, runs=100, min_bet=20)
    recs = build_weekly_recommendations(
        entries, assumptions, [40, 18, 10, 9, 7, 6, 4, 3, 2, 1], 30, seed=1
    )
    picks = {r.entry_name: r.recommended_pick for r in recs}
    assert picks == {"A": "LOSS", "B": "WIN", "C": "LOSS"}


def test_build_weekly_recommendations_bigger_favorite_gets_bigger_stake():
    # The user's exact complaint: a 5.5-point favorite winning should mean
    # a bigger, more confident stake than a 1.5-point favorite — and the
    # stake now actually reflects that (previously both got the same flat
    # "normal week" percentage regardless of how big the edge was).
    entries = [
        {"name": "SmallFavorite", "current_points": 150, "spread": -1.5},
        {"name": "BigFavorite", "current_points": 150, "spread": -5.5},
    ]
    assumptions = SimAssumptions(weeks_remaining=5, field_size=50, runs=100, min_bet=20)
    recs = build_weekly_recommendations(
        entries, assumptions, [40, 18, 10, 9, 7, 6, 4, 3, 2, 1], 30, seed=1
    )
    small, big = recs[0], recs[1]
    assert small.recommended_pick == "WIN" and big.recommended_pick == "WIN"
    assert big.recommended_bet > small.recommended_bet


def test_build_weekly_recommendations_bigger_favorite_gets_better_expected_payout():
    # The other half of the complaint: the simulated expected payout should
    # also be better for the more confident pick, since the simulation now
    # actually uses the real per-pick win probability for the immediate
    # week instead of a generic 50/50 assumption regardless of edge.
    # A small field keeps top-10 reachable in a single simulated week — with
    # a big field, one week's binary win/lose swing can't lift either entry
    # into the top 10 out of hundreds, saturating both at 0 and masking the
    # real difference. What matters here is that the *signal* (win
    # probability -> final points -> rank -> payout) actually flows through,
    # which a small field still exercises.
    entries = [
        {"name": "SmallFavorite", "current_points": 150, "spread": -1.5},
        {"name": "BigFavorite", "current_points": 150, "spread": -5.5},
    ]
    assumptions = SimAssumptions(weeks_remaining=1, field_size=6, runs=5000, min_bet=20)
    recs = build_weekly_recommendations(
        entries, assumptions, [40, 18, 10, 9, 7, 6, 4, 3, 2, 1], 30, seed=7
    )
    small, big = recs[0], recs[1]
    assert big.sim_result.avg_payout_dollars > small.sim_result.avg_payout_dollars
    assert big.sim_result.p_first >= small.sim_result.p_first
