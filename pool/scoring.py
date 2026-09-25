"""Scoring engine, ported 1:1 from pool-strategy-console.html's <script> block.

Mirrors: spreadForTeam, outcomeForTeam, isUpsetPick, scoreBet.
Do not re-derive these rules from the pool description; this is a direct port.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

Side = Literal["home", "away"]
Pick = Literal["WIN", "LOSS", "TIE"]
Outcome = Literal["WIN", "LOSS", "TIE_REG"]


@dataclass
class Game:
    """A single week's game. favorite/outcome use 'home'/'away'; outcome may be 'tie'."""

    favorite: Optional[Side] = None
    margin: Optional[float] = 0
    outcome: Optional[Literal["home", "away", "tie"]] = None


@dataclass
class ScoreResult:
    delta: float
    mult: float
    label: str


def spread_for_team(game: Optional[Game], side: Side) -> float:
    """Positive = team is underdog by N, negative = team favored by N, 0 = pick'em."""
    if game is None or game.margin is None:
        return 0
    if game.margin == 0:
        return 0
    is_fav = game.favorite == side
    return -game.margin if is_fav else game.margin


def outcome_for_team(game: Optional[Game], side: Side) -> Optional[Outcome]:
    if game is None or not game.outcome:
        return None
    if game.outcome == "tie":
        return "TIE_REG"
    return "WIN" if game.outcome == side else "LOSS"


def is_upset_pick(
    spread: float,
    pick: Pick,
    *,
    upset_threshold: float = 10.0,
    counts_favorite_loss: bool = True,
) -> bool:
    """A qualifying 10x upset: underdog (spread>0) betting WIN, or — if the pool
    counts it — favorite (spread<0) betting LOSS. `counts_favorite_loss` is the
    open assumption flagged in the starter prompt: confirm against a settled
    week before trusting it either way (config-driven, see pool_config table).
    """
    if abs(spread) < upset_threshold:
        return False
    if spread > 0 and pick == "WIN":
        return True
    if spread < 0 and pick == "LOSS" and counts_favorite_loss:
        return True
    return False


def score_bet(
    spread: float,
    pick: Pick,
    bet: float,
    outcome: Optional[Outcome],
    *,
    upset_threshold: float = 10.0,
    upset_multiplier: float = 10.0,
    tie_multiplier: float = 10.0,
    counts_favorite_loss: bool = True,
) -> Optional[ScoreResult]:
    """Returns None if outcome is unknown (game not yet resolved)."""
    if outcome is None:
        return None

    if outcome == "TIE_REG":
        if pick == "TIE":
            return ScoreResult(bet * tie_multiplier, tie_multiplier, "Tie correct (10x)")
        return ScoreResult(-bet, 1, f"{pick} — game tied at regulation, loses")

    if pick == "TIE":
        return ScoreResult(-bet, 1, "Picked tie — incorrect")

    correct = pick == outcome
    if not correct:
        return ScoreResult(-bet, 1, f"{pick} — incorrect")

    upset = is_upset_pick(
        spread,
        pick,
        upset_threshold=upset_threshold,
        counts_favorite_loss=counts_favorite_loss,
    )
    mult = upset_multiplier if upset else 1
    label = f"{pick} correct" + (" — qualifying 10x upset" if upset else "")
    return ScoreResult(bet * mult, mult, label)


def win_prob_from_spread(margin_for_team: float) -> float:
    """margin_for_team: negative = team favored by |m|, positive = underdog by m."""
    team_margin = -margin_for_team
    return 1 / (1 + 10 ** (-team_margin / 14))


def default_late_pick(*, side: Side = "home", pick: Pick = "WIN", bet: float = 20) -> dict:
    """Missed picks default to a home-team WIN for 20 points."""
    return {"side": side, "pick": pick, "bet": bet}


def infer_outcome_from_points(
    assigned_side: Side, pick: Pick, points_before: float, points_after: float
) -> Optional[Literal["home", "away", "tie"]]:
    """Back-solves a game's real outcome from one entry's own declared pick
    and the point delta between two posted-standings snapshots — the
    fallback path for when live/manual result recording (fetch-results /
    fetch-result, which need THE_ODDS_API_KEY) isn't available, but the
    pool operator's own posted totals already reflect the result. The
    operator's numbers are ground truth either way; this just reads the
    result back out of them instead of requiring a second, API-dependent
    source that may never get configured.

    Only unambiguous cases resolve: a correct TIE pick implies 'tie'; a
    correct or incorrect WIN/LOSS pick implies a decisive side (delta's
    sign alone says correct/incorrect, regardless of any upset multiplier,
    since a multiplier is always positive). An incorrect TIE pick, or a
    delta of exactly 0, doesn't reveal enough and returns None.

    Caveat: a WIN/LOSS pick that lost because the game was actually tied
    at regulation (the pool's rule: any WIN/LOSS pick loses on a
    regulation tie, same as a decisive loss) is indistinguishable from a
    decisive loss by delta alone for THIS entry — both score identically
    for them — but if this gets recorded as a decisive side and a
    DIFFERENT entry on the same game had picked the other side, that
    entry's own correct/incorrect would come out wrong. Regulation ties
    are rare enough that this is an acceptable fallback, not a substitute
    for a real recorded result once one becomes available (a later
    fetch-result/record-result always take precedence — this never
    overwrites an outcome that's already recorded).
    """
    delta = points_after - points_before
    if delta == 0:
        return None
    correct = delta > 0
    if pick == "TIE":
        return "tie" if correct else None
    other_side: Side = "home" if assigned_side == "away" else "away"
    if pick == "WIN":
        return assigned_side if correct else other_side
    if pick == "LOSS":
        return other_side if correct else assigned_side
    return None


def moneyline_to_win_probability(moneyline: float) -> float:
    """American moneyline odds -> implied win probability (includes the
    book's vig, so this slightly overstates true probability on both sides
    of a game — a modeling approximation, not a precise figure).
    """
    if moneyline < 0:
        return -moneyline / (-moneyline + 100)
    return 100 / (moneyline + 100)
