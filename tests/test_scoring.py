from pool.scoring import (
    Game,
    default_late_pick,
    is_upset_pick,
    outcome_for_team,
    score_bet,
    spread_for_team,
)


def test_spread_for_team_favorite_is_negative():
    g = Game(favorite="home", margin=7)
    assert spread_for_team(g, "home") == -7
    assert spread_for_team(g, "away") == 7


def test_spread_for_team_pickem_is_zero():
    g = Game(favorite=None, margin=0)
    assert spread_for_team(g, "home") == 0
    assert spread_for_team(g, "away") == 0


def test_spread_for_team_no_game_or_margin():
    assert spread_for_team(None, "home") == 0
    assert spread_for_team(Game(margin=None), "home") == 0


def test_outcome_for_team():
    g = Game(outcome="home")
    assert outcome_for_team(g, "home") == "WIN"
    assert outcome_for_team(g, "away") == "LOSS"


def test_outcome_for_team_tie_and_unknown():
    assert outcome_for_team(Game(outcome="tie"), "home") == "TIE_REG"
    assert outcome_for_team(Game(outcome=None), "home") is None
    assert outcome_for_team(None, "home") is None


# --- normal win/loss ---

def test_normal_win_no_upset():
    # pick'em game, correct WIN pick
    result = score_bet(spread=0, pick="WIN", bet=30, outcome="WIN")
    assert result.delta == 30
    assert result.mult == 1


def test_normal_loss_no_upset():
    result = score_bet(spread=0, pick="WIN", bet=30, outcome="LOSS")
    assert result.delta == -30
    assert result.mult == 1


# --- min bet / all-in bet ---

def test_min_bet_win():
    result = score_bet(spread=0, pick="WIN", bet=20, outcome="WIN")
    assert result.delta == 20


def test_all_in_bet_win():
    result = score_bet(spread=0, pick="WIN", bet=150, outcome="WIN")
    assert result.delta == 150


def test_all_in_bet_loss_eliminates():
    result = score_bet(spread=0, pick="WIN", bet=150, outcome="LOSS")
    assert result.delta == -150


# --- entry under 20 points (bet must be all remaining points, i.e. < min bet) ---

def test_entry_under_20_points_all_in():
    # An entry with 15 points must bet all 15 (can't reach the 20 min).
    result = score_bet(spread=0, pick="WIN", bet=15, outcome="WIN")
    assert result.delta == 15


# --- zero-point elimination ---

def test_zero_point_bet_is_a_noop_result():
    # A bet of 0 (already eliminated) never changes points either way.
    result = score_bet(spread=0, pick="WIN", bet=0, outcome="WIN")
    assert result.delta == 0
    result = score_bet(spread=0, pick="WIN", bet=0, outcome="LOSS")
    assert result.delta == 0


# --- regulation tie with either pick ---

def test_regulation_tie_pick_tie_pays_10x():
    result = score_bet(spread=0, pick="TIE", bet=20, outcome="TIE_REG")
    assert result.delta == 200
    assert result.mult == 10


def test_regulation_tie_pick_win_loses():
    result = score_bet(spread=0, pick="WIN", bet=20, outcome="TIE_REG")
    assert result.delta == -20


def test_regulation_tie_pick_loss_also_loses():
    result = score_bet(spread=0, pick="LOSS", bet=20, outcome="TIE_REG")
    assert result.delta == -20


# --- "tie after OT": regulation tie is scored the same regardless of any later
# overtime result. The scoring API takes no OT-winner input at all, so this is
# true by construction — outcome='TIE_REG' is the only signal, matching the
# pool rule that OT winner is irrelevant for WIN/LOSS/TIE settlement.

def test_tie_after_ot_still_scores_as_regulation_tie():
    result_tie_pick = score_bet(spread=0, pick="TIE", bet=20, outcome="TIE_REG")
    result_win_pick = score_bet(spread=0, pick="WIN", bet=20, outcome="TIE_REG")
    assert result_tie_pick.delta == 200
    assert result_win_pick.delta == -20


# --- qualifying upset, both directions ---

def test_qualifying_upset_underdog_win():
    # underdog by 10, correctly picked WIN
    result = score_bet(spread=10, pick="WIN", bet=20, outcome="WIN")
    assert result.delta == 200
    assert result.mult == 10
    assert "10x upset" in result.label


def test_qualifying_upset_favorite_loss_when_enabled():
    # favorite by 10 (spread=-10), correctly picked LOSS
    result = score_bet(spread=-10, pick="LOSS", bet=20, outcome="LOSS", counts_favorite_loss=True)
    assert result.delta == 200
    assert result.mult == 10


def test_favorite_loss_does_not_qualify_when_disabled():
    result = score_bet(spread=-10, pick="LOSS", bet=20, outcome="LOSS", counts_favorite_loss=False)
    assert result.delta == 20
    assert result.mult == 1


# --- spread exactly at 10.0 vs just under ---

def test_spread_exactly_10_qualifies():
    assert is_upset_pick(10.0, "WIN") is True


def test_spread_just_under_10_does_not_qualify():
    assert is_upset_pick(9.99, "WIN") is False


def test_spread_exactly_negative_10_qualifies_for_favorite_loss():
    assert is_upset_pick(-10.0, "LOSS", counts_favorite_loss=True) is True


def test_spread_just_over_negative_10_does_not_qualify():
    assert is_upset_pick(-9.99, "LOSS", counts_favorite_loss=True) is False


# --- default late pick ---

def test_default_late_pick():
    pick = default_late_pick()
    assert pick == {"side": "home", "pick": "WIN", "bet": 20}


def test_default_late_pick_scores_as_declared():
    late = default_late_pick()
    # home team WIN, pick'em spread, home wins
    result = score_bet(spread=0, pick=late["pick"], bet=late["bet"], outcome="WIN")
    assert result.delta == 20
