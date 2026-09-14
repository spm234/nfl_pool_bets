"""Converts point spreads (or, absent a market spread, Elo ratings) into win
probabilities.

Two sources, picked per-game by the caller (see optimizer.py /
live_data.py), not blended:

1. **Market spread** — when a real spread is known (from The Odds API or a
   manual entry), win probability comes from the normal-CDF margin model:
   NFL final-score margins are approximately Normal(mean=spread, sd~13.86),
   so P(favorite covers 0, i.e. wins outright) = Phi(spread / sd). This is
   the standard sportsbook-adjacent approximation, not a proprietary model.
2. **Elo projection** — for weeks with no market spread yet (i.e. beyond
   The Odds API's near-term horizon), win probability comes from each
   team's Elo rating via the standard logistic Elo formula, with a
   home-field-advantage bonus added to the home team's rating before
   comparing. Elo ratings start flat (elo_initial_rating for every team,
   since no preseason power-rating source was available — see README) and
   only start to differentiate teams once real results are fed back in via
   apply_elo_update.

Both return the SAME shape: (p_home_win, p_away_win), which always sum to
1.0 — a small NFL tie probability (~0.5% historically) is not modeled
separately; ties are rare enough, and handled at settlement time by
config.tie_treated_as, that folding them into the binary win/lose split
here doesn't materially change recommendations.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple


def win_prob_from_spread(margin: float, favorite: Optional[str], std_dev: float) -> Tuple[float, float]:
    """margin: points the favorite is favored by (>=0). favorite: 'home',
    'away', or None/'even' for a pick'em. Returns (p_home_win, p_away_win).
    """
    if favorite not in ("home", "away", None, "even"):
        raise ValueError(f"favorite must be 'home', 'away', or None/'even', got {favorite!r}")
    if favorite in (None, "even") or margin == 0:
        signed_home_spread = 0.0
    elif favorite == "home":
        signed_home_spread = margin
    else:
        signed_home_spread = -margin

    p_home_win = _normal_cdf(signed_home_spread / std_dev)
    return p_home_win, 1.0 - p_home_win


def win_prob_from_elo(home_rating: float, away_rating: float, home_advantage: float) -> Tuple[float, float]:
    """Standard logistic Elo win-probability formula."""
    rating_diff = (home_rating + home_advantage) - away_rating
    p_home_win = 1.0 / (1.0 + 10 ** (-rating_diff / 400.0))
    return p_home_win, 1.0 - p_home_win


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass
class EloUpdateResult:
    new_home_rating: float
    new_away_rating: float


def apply_elo_update(
    home_rating: float,
    away_rating: float,
    home_score: int,
    away_score: int,
    *,
    home_advantage: float,
    k_factor: float,
) -> EloUpdateResult:
    """FiveThirtyEight-style Elo update with a margin-of-victory multiplier,
    so a 30-point win moves ratings more than a 1-point win. A tie is
    treated as actual=0.5 for both sides.

    MOV multiplier = ln(|margin| + 1) * (2.2 / (winner_elo_diff * 0.001 + 2.2))
    where winner_elo_diff is the *pre-game* rating gap in the winner's favor
    (with home-field advantage applied to whichever side is home), which
    dampens the multiplier for lopsided-on-paper blowouts (a favorite
    steamrolling an already-worse team moves ratings less than the same
    margin from a pick'em game).
    """
    margin = home_score - away_score
    rating_diff = (home_rating + home_advantage) - away_rating  # home-perspective, pre-game

    if margin > 0:
        actual_home = 1.0
        winner_diff = rating_diff
    elif margin < 0:
        actual_home = 0.0
        winner_diff = -rating_diff
    else:
        actual_home = 0.5
        winner_diff = abs(rating_diff)

    expected_home = 1.0 / (1.0 + 10 ** (-rating_diff / 400.0))

    if margin == 0:
        mov_multiplier = 1.0
    else:
        mov_multiplier = math.log(abs(margin) + 1) * (2.2 / (winner_diff * 0.001 + 2.2))

    delta = k_factor * mov_multiplier * (actual_home - expected_home)
    return EloUpdateResult(new_home_rating=home_rating + delta, new_away_rating=away_rating - delta)
