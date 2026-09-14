import math

from loser_pool.spread_model import (
    apply_elo_update,
    win_prob_from_elo,
    win_prob_from_spread,
)


def test_win_prob_from_spread_pickem_is_fifty_fifty():
    p_home, p_away = win_prob_from_spread(0, None, 13.86)
    assert math.isclose(p_home, 0.5)
    assert math.isclose(p_away, 0.5)


def test_win_prob_from_spread_home_favorite():
    p_home, p_away = win_prob_from_spread(7, "home", 13.86)
    assert p_home > 0.5
    assert math.isclose(p_home + p_away, 1.0)


def test_win_prob_from_spread_symmetric_home_away():
    p_home_fav, _ = win_prob_from_spread(10, "home", 13.86)
    p_home_dog, _ = win_prob_from_spread(10, "away", 13.86)
    assert math.isclose(p_home_fav, 1 - p_home_dog)


def test_win_prob_from_spread_big_favorite_approaches_one():
    p_home, _ = win_prob_from_spread(28, "home", 13.86)
    assert p_home > 0.97


def test_win_prob_from_elo_equal_ratings_no_home_field():
    p_home, p_away = win_prob_from_elo(1500, 1500, 0)
    assert math.isclose(p_home, 0.5)
    assert math.isclose(p_away, 0.5)


def test_win_prob_from_elo_home_field_advantage_helps_home():
    p_home, _ = win_prob_from_elo(1500, 1500, 48)
    assert p_home > 0.5


def test_apply_elo_update_favorite_win_increases_rating():
    result = apply_elo_update(1600, 1400, home_score=24, away_score=10, home_advantage=0, k_factor=20)
    assert result.new_home_rating > 1600
    assert result.new_away_rating < 1400


def test_apply_elo_update_zero_sum():
    result = apply_elo_update(1550, 1450, home_score=17, away_score=20, home_advantage=0, k_factor=20)
    delta_home = result.new_home_rating - 1550
    delta_away = result.new_away_rating - 1450
    assert math.isclose(delta_home, -delta_away, abs_tol=1e-9)


def test_apply_elo_update_upset_moves_rating_more_than_expected_result():
    # Underdog (away, lower rating) wins outright -> away rating should rise
    # by more than it would for a game between evenly-matched teams.
    upset = apply_elo_update(1650, 1350, home_score=10, away_score=24, home_advantage=0, k_factor=20)
    coinflip = apply_elo_update(1500, 1500, home_score=10, away_score=24, home_advantage=0, k_factor=20)
    upset_away_gain = upset.new_away_rating - 1350
    coinflip_away_gain = coinflip.new_away_rating - 1500
    assert upset_away_gain > coinflip_away_gain


def test_apply_elo_update_tie_treated_as_half_point():
    result = apply_elo_update(1500, 1500, home_score=20, away_score=20, home_advantage=0, k_factor=20)
    assert math.isclose(result.new_home_rating, 1500, abs_tol=1e-9)
    assert math.isclose(result.new_away_rating, 1500, abs_tol=1e-9)
