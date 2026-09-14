"""The core "which team should I pick" engine — the loser-pool analog of
clevanalytics' survivor optimizer.

Two layers, deliberately kept separate:

1. **full_season_plan()** — the theoretical best possible single sequence
   of (week, team) picks for one entry with unlimited lives, i.e. the
   sequence that maximizes P(every single pick loses) = the product of
   each picked team's weekly loss probability. Since each team can be used
   at most once and each week needs exactly one team, maximizing the sum
   of log(loss probability) over a one-team-per-week, one-week-per-team
   assignment is exactly the classic linear assignment problem — solved
   here with scipy's Hungarian-algorithm implementation rather than a
   hand-rolled one, since getting this silently wrong would be a correctness
   bug that's hard to notice (it would just look like "a slightly worse
   plan"). This ignores the 2-lives mechanic on purpose — it's the
   "if you had to guarantee it" reference plan, not the final
   recommendation.
2. **recommend_week()** — the actual weekly recommendation, which DOES
   account for lives remaining and already-used teams. It ranks this
   week's available teams by loss probability, then breaks ties/close
   calls using simulation.py's season-survival-probability estimate under
   each candidate (since with 2 lives, burning a decent-but-not-best team
   now to preserve a huge future mismatch can beat always taking the
   single best team available each week — a simple greedy-by-week-only
   policy can't see that).
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .config import LoserPoolConfig
from .matchups import TeamWeekOption, week_options_for_entry
from .picks import used_teams

# scipy is only needed for full_season_plan(); imported lazily inside it so
# recommend_week() (the one used every week) never pays for/depends on it.


def full_season_plan(
    conn: sqlite3.Connection,
    season_year: int,
    week_numbers: List[int],
    exclude_teams: Optional[set] = None,
    cfg: Optional[LoserPoolConfig] = None,
) -> List[TeamWeekOption]:
    """Best possible one-team-per-week, no-repeat-team plan across
    `week_numbers`, maximizing the product of weekly loss probabilities.
    Returns one TeamWeekOption per week that has an available team, in
    week order (a week with zero available options — e.g. every team
    already used — is simply omitted, not an error).
    """
    from scipy.optimize import linear_sum_assignment
    import numpy as np

    cfg = cfg or LoserPoolConfig.load(conn)
    exclude_teams = exclude_teams or set()
    options_by_week = week_options_for_entry(conn, season_year, week_numbers, exclude_teams, cfg)

    all_teams = sorted({opt.team for opts in options_by_week.values() for opt in opts})
    weeks_with_options = [w for w in week_numbers if options_by_week[w]]
    if not all_teams or not weeks_with_options:
        return []

    # Rows = teams, columns = weeks. Cost = -log(p_lose) (minimizing cost =
    # maximizing sum of log p_lose = maximizing product of p_lose). A team
    # with no game that week gets a very large cost so it's never chosen
    # for that column unless forced (fewer real options than weeks).
    NO_OPTION_COST = 1e6
    cost = np.full((len(all_teams), len(weeks_with_options)), NO_OPTION_COST)
    lookup: Dict[Tuple[int, int], TeamWeekOption] = {}
    for wi, week_number in enumerate(weeks_with_options):
        for opt in options_by_week[week_number]:
            ti = all_teams.index(opt.team)
            p = min(max(opt.p_lose, 1e-9), 1 - 1e-9)
            cost[ti, wi] = -math.log(p)
            lookup[(ti, wi)] = opt

    row_ind, col_ind = linear_sum_assignment(cost)
    plan = []
    for ti, wi in zip(row_ind, col_ind):
        opt = lookup.get((ti, wi))
        if opt is not None:  # skip the "forced, no real game" placeholder assignments
            plan.append(opt)
    plan.sort(key=lambda o: o.week_number)
    return plan


@dataclass
class WeeklyRecommendation:
    team: str
    opponent: str
    is_home: bool
    p_lose: float
    p_survive_season_if_picked: Optional[float]
    notes: str


def recommend_week(
    conn: sqlite3.Connection,
    season_year: int,
    week_number: int,
    entry_id: int,
    *,
    remaining_week_numbers: Optional[List[int]] = None,
    lives_remaining: Optional[int] = None,
    cfg: Optional[LoserPoolConfig] = None,
    sim_runs: int = 2000,
    top_n_for_sim: int = 5,
) -> List[WeeklyRecommendation]:
    """Ranked candidate picks for one entry's upcoming week, best first.

    Ranks all available teams by this week's raw loss probability, then —
    if `remaining_week_numbers` covers more than just this week — re-scores
    the top `top_n_for_sim` candidates by simulated full-season survival
    probability (via simulation.py) given the entry's actual lives_remaining,
    since a decent pick now can beat the single best pick now once you
    account for which teams it leaves available for harder future weeks.
    Without a remaining-weeks horizon, only the raw loss-probability
    ranking is returned (p_survive_season_if_picked is None for every row).
    """
    cfg = cfg or LoserPoolConfig.load(conn)
    entry = conn.execute("SELECT * FROM lp_entry WHERE id = ?", (entry_id,)).fetchone()
    if entry is None:
        raise ValueError(f"No entry with id {entry_id}")
    lives = lives_remaining if lives_remaining is not None else entry["lives_remaining"]

    exclude = used_teams(conn, entry_id, season_year)
    options_by_week = week_options_for_entry(conn, season_year, [week_number], exclude, cfg)
    options = sorted(options_by_week.get(week_number, []), key=lambda o: o.p_lose, reverse=True)

    if not options:
        return []

    if not remaining_week_numbers or len(remaining_week_numbers) <= 1:
        return [
            WeeklyRecommendation(o.team, o.opponent, o.is_home, o.p_lose, None, "")
            for o in options
        ]

    from .simulation import simulate_entry_survival

    future_weeks = [w for w in remaining_week_numbers if w != week_number]
    top_options = options[:top_n_for_sim]
    scored: List[WeeklyRecommendation] = []
    for o in top_options:
        p_survive = simulate_entry_survival(
            conn,
            season_year,
            future_weeks,
            exclude_teams=exclude | {o.team},
            lives_remaining=lives,  # this week's own outcome is handled via first_week_p_lose below
            cfg=cfg,
            runs=sim_runs,
            first_week_p_lose=o.p_lose,
        )
        scored.append(WeeklyRecommendation(o.team, o.opponent, o.is_home, o.p_lose, p_survive, ""))

    scored.sort(key=lambda r: r.p_survive_season_if_picked, reverse=True)
    remainder = [
        WeeklyRecommendation(o.team, o.opponent, o.is_home, o.p_lose, None, "")
        for o in options[top_n_for_sim:]
    ]
    return scored + remainder
