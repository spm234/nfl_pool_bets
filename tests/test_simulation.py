import random

from pool.simulation import EntryPolicy, SimAssumptions, run_simulation, simulate_one_entry


def test_simulate_one_entry_elimination_floors_at_zero():
    rng = random.Random(1)
    # Force every week to be a loss by using p_win=0.
    points = simulate_one_entry(
        150, 5, 0.5, False, p_win=0.0, p_upset_freq=0.0, p_upset_win=0.0, min_bet=20, rng=rng
    )
    assert points == 0


def test_simulate_one_entry_always_wins_grows():
    rng = random.Random(1)
    points = simulate_one_entry(
        150, 3, 0.2, False, p_win=1.0, p_upset_freq=0.0, p_upset_win=0.0, min_bet=20, rng=rng
    )
    assert points > 150


def test_simulate_one_entry_never_bets_more_than_held():
    rng = random.Random(2)
    points = simulate_one_entry(
        15, 1, 0.9, False, p_win=0.0, p_upset_freq=0.0, p_upset_win=0.0, min_bet=20, rng=rng
    )
    # started under the min bet; a loss can only cost what's held (15), not more.
    assert points == 0


def test_run_simulation_probabilities_are_bounded_and_deterministic():
    assumptions = SimAssumptions(weeks_remaining=4, field_size=20, runs=200)
    policies = [EntryPolicy(bet_fraction=0.3, take_upset=True)]
    results_a = run_simulation(assumptions, policies, [150], [40, 18, 10, 9, 7, 6, 4, 3, 2, 1], 30, seed=42)
    results_b = run_simulation(assumptions, policies, [150], [40, 18, 10, 9, 7, 6, 4, 3, 2, 1], 30, seed=42)

    assert results_a[0].p_first == results_b[0].p_first  # deterministic given seed
    assert 0 <= results_a[0].p_first <= 1
    assert 0 <= results_a[0].p_top10 <= 1
    assert results_a[0].p_first <= results_a[0].p_top3 <= results_a[0].p_top5 <= results_a[0].p_top10
    assert results_a[0].avg_payout_dollars >= 0


def test_run_simulation_higher_start_points_beats_lower_on_average():
    assumptions = SimAssumptions(weeks_remaining=3, field_size=30, runs=300)
    policies = [EntryPolicy(0.3, True), EntryPolicy(0.3, True)]
    results = run_simulation(
        assumptions, policies, [400, 20], [40, 18, 10, 9, 7, 6, 4, 3, 2, 1], 30, seed=7
    )
    assert results[0].p_top10 >= results[1].p_top10
