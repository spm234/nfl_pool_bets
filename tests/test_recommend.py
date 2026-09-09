from pool.recommend import build_weekly_recommendations, recommend_bet_sizes
from pool.simulation import SimAssumptions


def test_recommend_bet_sizes_conservative_vs_aggressive():
    conservative = recommend_bet_sizes(200, min_bet=20, aggression=0)
    aggressive = recommend_bet_sizes(200, min_bet=20, aggression=100)
    assert conservative["normal"] < aggressive["normal"]
    assert conservative["upset"] < aggressive["upset"]


def test_recommend_bet_sizes_respects_min_and_max():
    tiny = recommend_bet_sizes(15, min_bet=20, aggression=0)
    assert tiny["normal"] == 15  # can't bet more than held
    huge = recommend_bet_sizes(1000, min_bet=20, aggression=100)
    assert huge["upset"] <= 1000


def test_build_weekly_recommendations_shapes_output():
    entries = [
        {"name": "My Entry 1", "current_points": 150, "is_upset_opportunity": False},
        {"name": "My Entry 2", "current_points": 300, "is_upset_opportunity": True},
        {"name": "My Entry 3", "current_points": 40, "is_upset_opportunity": False},
    ]
    assumptions = SimAssumptions(weeks_remaining=5, field_size=50, runs=200, min_bet=20)
    recs = build_weekly_recommendations(
        entries, assumptions, [40, 18, 10, 9, 7, 6, 4, 3, 2, 1], 30, seed=1
    )
    assert len(recs) == 3
    assert recs[1].is_upset_opportunity is True
    assert "upset" in recs[1].recommended_pick_reasoning
    assert recs[0].recommended_bet <= entries[0]["current_points"]
    assert 0 <= recs[0].sim_result.p_top10 <= 1
