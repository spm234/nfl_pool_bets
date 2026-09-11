from pool.recommend import build_weekly_recommendations, choose_pick, recommend_bet_sizes
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


# --- choose_pick: the actual fix — must not default to WIN ---

def test_choose_pick_pickem_defaults_win_on_true_tie():
    # No informational edge either way; WIN is a defensible tie-break, not
    # a hardcoded default — both EVs are genuinely equal here.
    choice = choose_pick(0)
    assert choice.pick == "WIN"
    assert choice.ev_per_point == choice.other_pick_ev_per_point


def test_choose_pick_moderate_underdog_below_upset_threshold_prefers_loss():
    # Below the upset threshold there's no 10x bonus on offer either way,
    # so the correct move is to back whichever outcome is more likely —
    # here the team is a 5-point underdog, so LOSS (fade them) is +EV and
    # WIN is -EV. The old always-WIN default would get this backwards.
    choice = choose_pick(5, upset_threshold=10)
    assert choice.pick == "LOSS"
    assert choice.ev_per_point > 0
    assert choice.other_pick_ev_per_point < 0


def test_choose_pick_big_underdog_qualifying_upset_prefers_loss_over_win_here():
    # A 12-point underdog: WIN qualifies for the 10x bonus but at ~12% model
    # win probability; LOSS is a boring ~88%-likely 1x bet. The boring bet
    # has higher raw EV per point in this model — this is exactly the kind
    # of case the naive "always WIN" default got wrong.
    choice = choose_pick(12, upset_threshold=10, upset_multiplier=10)
    assert choice.pick == "LOSS"
    assert choice.is_upset is False


def test_choose_pick_big_favorite_prefers_win_over_upset_fade():
    # Mirror image: team favored by 12. Betting them to WIN (~88% likely,
    # 1x) beats fading them for the LOSS-side 10x upset bonus (~12% likely).
    choice = choose_pick(-12, upset_threshold=10, upset_multiplier=10, counts_favorite_loss=True)
    assert choice.pick == "WIN"
    assert choice.is_upset is False


def test_choose_pick_favorite_loss_upset_disabled_strongly_prefers_win():
    # With counts_favorite_loss=False, betting a big favorite to LOSE gets
    # no bonus at all (mult=1 on a low-probability outcome, deep negative
    # EV) instead of the enabled case's still-positive-but-lower upset EV.
    # WIN's own EV is unaffected either way — only the rejected LOSS
    # alternative's EV changes.
    enabled = choose_pick(-12, upset_threshold=10, upset_multiplier=10, counts_favorite_loss=True)
    disabled = choose_pick(-12, upset_threshold=10, upset_multiplier=10, counts_favorite_loss=False)
    assert disabled.pick == "WIN" and enabled.pick == "WIN"
    assert enabled.ev_per_point == disabled.ev_per_point  # WIN's own EV is unaffected
    assert disabled.other_pick_ev_per_point < enabled.other_pick_ev_per_point  # LOSS looks worse


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
    # The regression this whole fix is about: a set of entries with
    # different spreads should not all come back recommending WIN.
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
