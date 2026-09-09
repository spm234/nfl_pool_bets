from pool.reconstruction import reconstruct_candidates


def test_no_outcome_returns_empty():
    assert reconstruct_candidates(150, 170, 0, None, 20) == []


def test_no_points_returns_empty():
    assert reconstruct_candidates(None, 170, 0, "WIN", 20) == []
    assert reconstruct_candidates(150, None, 0, "WIN", 20) == []


def test_reconstructs_normal_win():
    # 150 -> 180 with pick'em spread and outcome WIN: only WIN@30 fits cleanly.
    cands = reconstruct_candidates(150, 180, 0, "WIN", 20)
    picks = {c.pick: c.bet for c in cands}
    assert picks.get("WIN") == 30


def test_reconstructs_normal_loss():
    # 150 -> 130 with outcome LOSS: WIN@20 (incorrect, lost the bet) fits.
    cands = reconstruct_candidates(150, 130, 0, "LOSS", 20)
    picks = {c.pick: c.bet for c in cands}
    assert picks.get("WIN") == 20


def test_reconstructs_qualifying_upset_win():
    # underdog by 10, 150 -> 350 (delta 200) via WIN at 20x10=200 -> bet 20
    cands = reconstruct_candidates(150, 350, 10, "WIN", 20)
    picks = {c.pick: c.bet for c in cands}
    assert picks.get("WIN") == 20


def test_reconstructs_regulation_tie_pick():
    # 150 -> 350, delta 200, TIE_REG => bet = 200/10 = 20
    cands = reconstruct_candidates(150, 350, 0, "TIE_REG", 20)
    picks = {c.pick: c.bet for c in cands}
    assert picks.get("TIE") == 20


def test_bet_must_respect_min_and_max():
    # delta of 5 (bet=5) is below min bet of 20 and should be excluded.
    cands = reconstruct_candidates(150, 155, 0, "WIN", 20)
    assert all(c.bet >= 20 for c in cands)


def test_bet_cannot_exceed_prev_points():
    # A hypothesis requiring a bet larger than points held entering the week is invalid.
    cands = reconstruct_candidates(20, 220, 0, "WIN", 20)  # would require bet=200 > 20
    assert cands == []


def test_non_integer_bet_excluded():
    # delta of 15 with a qualifying 10x upset would require a bet of 1.5 -> excluded.
    cands = reconstruct_candidates(150, 165, 10, "WIN", 20)
    assert all(c.pick != "WIN" or c.bet != 1 for c in cands)


def test_sort_prefers_round_tens_and_pushes_tie_last():
    # 150 -> 130: WIN incorrect (bet 20, round bonus) and LOSS correct (bet 20)
    # both fit; TIE incorrect also fits with bet 20. All round-tens, so TIE
    # should still sort last.
    cands = reconstruct_candidates(150, 130, 0, "LOSS", 20)
    assert cands[-1].pick == "TIE" or "TIE" not in {c.pick for c in cands}
