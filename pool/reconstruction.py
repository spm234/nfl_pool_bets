"""Historical reconstruction, ported from reconstructCandidates in the JS prototype.

Given a start/end point total, spread, and outcome, returns the set of
(pick, bet) hypotheses consistent with the observed point delta.

Note on the JS reference's sort: its comparator
`(a,b) => (b.roundBonus-a.roundBonus) || (a.pick==='TIE'?1:-1)`
never inspects `b` in its tiebreak term, which is not a valid total order.
In V8's stable sort this behaves as: sort by roundBonus descending, then keep
original insertion order (WIN, LOSS, TIE) except TIE is always pushed after
whatever it's compared against. That's what's reproduced below explicitly,
rather than the invalid comparator itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .scoring import Outcome, Pick, is_upset_pick

_PICK_ORDER = {"WIN": 0, "LOSS": 1, "TIE": 2}


@dataclass
class Candidate:
    pick: Pick
    bet: int
    label: str
    round_bonus: int


def _is_effectively_integer(bet: float) -> bool:
    rounded = round(bet, 2)
    return rounded == round(rounded)


def reconstruct_candidates(
    prev_points: Optional[float],
    new_points: Optional[float],
    spread: float,
    outcome: Optional[Outcome],
    min_bet_config: float,
    *,
    upset_threshold: float = 10.0,
    upset_multiplier: float = 10.0,
    tie_multiplier: float = 10.0,
    counts_favorite_loss: bool = True,
) -> List[Candidate]:
    if outcome is None or prev_points is None or new_points is None:
        return []

    delta = new_points - prev_points
    max_bet = prev_points
    min_bet_eff = min(min_bet_config, prev_points)

    out: List[Candidate] = []
    for pick in ("WIN", "LOSS", "TIE"):
        bet: Optional[float] = None
        label = ""
        if outcome == "TIE_REG":
            if pick == "TIE":
                bet = delta / tie_multiplier
                label = "Tie correct (10x)"
            else:
                bet = -delta
                label = f"{pick} — tied at regulation, loses"
        else:
            if pick == "TIE":
                bet = -delta
                label = "Picked tie — incorrect"
            elif pick == outcome:
                upset = is_upset_pick(
                    spread,
                    pick,
                    upset_threshold=upset_threshold,
                    counts_favorite_loss=counts_favorite_loss,
                )
                mult = upset_multiplier if upset else 1
                bet = delta / mult
                label = f"{pick} correct" + (" (10x upset)" if upset else "")
            else:
                bet = -delta
                label = f"{pick} — incorrect"

        if (
            bet is not None
            and _is_effectively_integer(bet)
            and bet > 0
            and bet >= min_bet_eff
            and bet <= max_bet
        ):
            bet_int = round(bet)
            out.append(
                Candidate(
                    pick=pick,
                    bet=bet_int,
                    label=label,
                    round_bonus=1 if bet_int % 10 == 0 else 0,
                )
            )

    out.sort(key=lambda c: (-c.round_bonus, _PICK_ORDER[c.pick]))
    return out
