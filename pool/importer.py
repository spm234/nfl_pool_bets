"""Import standings, schedules, assignments, and my own picks.

Standings paste shape matches the prototype's manual paste box exactly:
one entry per line, "Name, Points" (comma or tab separated).

Assignment import intentionally differs from the prototype: the prototype's
"Name, Away, Home" format only recorded which game an entry was in, not
which side of it — a bug (see sql/schema.sql header). Here, assignment
import takes the entry's actual assigned TEAM and resolves home/away by
matching it against that week's already-imported schedule.
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
from dataclasses import dataclass
from typing import List, Optional, Tuple

from . import db, nfl_teams


def _split_line(line: str) -> List[str]:
    if "\t" in line:
        parts = line.split("\t")
    else:
        parts = line.split(",")
    return [p.strip() for p in parts if p.strip()]


def parse_standings_paste(text: str) -> List[Tuple[str, float]]:
    """'Name, Points' per line -> [(name, points), ...]."""
    out = []
    for line in text.splitlines():
        parts = _split_line(line)
        if len(parts) < 2:
            continue
        name = parts[0]
        try:
            points = float(parts[1])
        except ValueError:
            continue
        out.append((name, points))
    return out


def import_standings(
    conn: sqlite3.Connection, season_year: int, week_number: int, text: str
) -> int:
    """Applies a standings paste for a week. Returns count of rows applied."""
    week_id = db.get_or_create_week(conn, season_year, week_number)
    count = 0
    for name, points in parse_standings_paste(text):
        entry_id = db.get_or_create_entry(conn, owner_name=name, display_name=name)
        conn.execute(
            """
            INSERT INTO entry_week_points (entry_id, week_id, points)
            VALUES (?, ?, ?)
            ON CONFLICT(entry_id, week_id) DO UPDATE SET points = excluded.points
            """,
            (entry_id, week_id, points),
        )
        count += 1
    conn.commit()
    return count


def parse_schedule_paste(text: str) -> List[Tuple[str, str]]:
    """'Away, Home' per line -> [(away, home), ...]."""
    out = []
    for line in text.splitlines():
        parts = _split_line(line)
        if len(parts) < 2:
            continue
        out.append((parts[0], parts[1]))
    return out


def import_schedule(
    conn: sqlite3.Connection, season_year: int, week_number: int, text: str
) -> int:
    week_id = db.get_or_create_week(conn, season_year, week_number)
    count = 0
    for away, home in parse_schedule_paste(text):
        db.get_or_create_game(conn, week_id, away, home)
        count += 1
    conn.commit()
    return count


def import_week_csv(
    conn: sqlite3.Connection, season_year: int, week_number: int, csv_text: str
) -> int:
    """One-shot import matching the pool's own weekly export format:
    header 'Rank,Team Name,Total Pts,Away,Home', one row per entry. This
    covers schedule + assignment + standings in a single pass.

    The assigned side is always 'away' — confirmed directly against how
    this pool actually works: every entry is nominally assigned the away
    team, and picking WIN/LOSS/TIE for that team is how you bet the game
    either way (there's no separate "pick the home team instead").
    """
    import csv
    import io

    week_id = db.get_or_create_week(conn, season_year, week_number)
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    fieldnames = {(f or "").strip(): f for f in (reader.fieldnames or [])}
    required = {"Team Name", "Total Pts", "Away", "Home"}
    missing = required - set(fieldnames)
    if missing:
        raise ValueError(
            f"CSV is missing required column(s): {', '.join(sorted(missing))}. "
            f"Expected header: Rank,Team Name,Total Pts,Away,Home"
        )

    count = 0
    for row in reader:
        name = (row[fieldnames["Team Name"]] or "").strip()
        if not name:
            continue
        points = float((row[fieldnames["Total Pts"]] or "").strip())
        away = (row[fieldnames["Away"]] or "").strip()
        home = (row[fieldnames["Home"]] or "").strip()

        game_id = db.get_or_create_game(conn, week_id, away, home)
        entry_id = db.get_or_create_entry(conn, owner_name=name, display_name=name)
        db.get_or_create_assignment(conn, entry_id, game_id, "away")
        conn.execute(
            """
            INSERT INTO entry_week_points (entry_id, week_id, points)
            VALUES (?, ?, ?)
            ON CONFLICT(entry_id, week_id) DO UPDATE SET points = excluded.points
            """,
            (entry_id, week_id, points),
        )
        count += 1

    conn.commit()
    return count


@dataclass
class AssignmentImportError:
    name: str
    team: str
    reason: str


def import_assignments(
    conn: sqlite3.Connection, season_year: int, week_number: int, text: str
) -> Tuple[int, List[AssignmentImportError]]:
    """'Name, Team' per line. Team is matched against that week's schedule
    (already imported via import_schedule) to resolve home/away. Entries
    whose team can't be matched to exactly one game are reported as errors
    rather than guessed at.
    """
    week_id = db.get_or_create_week(conn, season_year, week_number)
    games = conn.execute(
        "SELECT id, away_team, home_team FROM game WHERE week_id = ?", (week_id,)
    ).fetchall()

    count = 0
    errors: List[AssignmentImportError] = []
    for line in text.splitlines():
        parts = _split_line(line)
        if len(parts) < 2:
            continue
        name, team = parts[0], parts[1]

        matches = [
            (g["id"], "away" if g["away_team"] == team else "home")
            for g in games
            if g["away_team"] == team or g["home_team"] == team
        ]
        if len(matches) != 1:
            errors.append(
                AssignmentImportError(
                    name=name,
                    team=team,
                    reason="no matching game in schedule"
                    if not matches
                    else "team matches multiple games — schedule data is ambiguous",
                )
            )
            continue

        game_id, side = matches[0]
        entry_id = db.get_or_create_entry(conn, owner_name=name, display_name=name)
        db.get_or_create_assignment(conn, entry_id, game_id, side)
        count += 1

    conn.commit()
    return count, errors


_UNSET = object()


def record_game_result(
    conn: sqlite3.Connection,
    season_year: int,
    week_number: int,
    away_team: str,
    home_team: str,
    *,
    favorite=_UNSET,
    margin=_UNSET,
    outcome=_UNSET,
) -> int:
    """Updates a game's favorite/margin/outcome. Any argument left at its
    default is a partial update — it keeps whatever the game already has,
    rather than clobbering it back to None/0. This matters because spread
    and outcome are typically recorded at different times (spread before
    kickoff, outcome after) and a second call for one shouldn't erase the
    other.
    """
    week_id = db.get_or_create_week(conn, season_year, week_number)
    game_id = db.get_or_create_game(conn, week_id, away_team, home_team)
    current = conn.execute(
        "SELECT favorite, spread_margin, outcome FROM game WHERE id = ?", (game_id,)
    ).fetchone()
    new_favorite = current["favorite"] if favorite is _UNSET else favorite
    new_margin = current["spread_margin"] if margin is _UNSET else margin
    new_outcome = current["outcome"] if outcome is _UNSET else outcome
    conn.execute(
        "UPDATE game SET favorite = ?, spread_margin = ?, outcome = ? WHERE id = ?",
        (new_favorite, new_margin, new_outcome, game_id),
    )
    conn.commit()
    return game_id


def record_my_pick(
    conn: sqlite3.Connection,
    season_year: int,
    week_number: int,
    entry_display_name: str,
    away_team: str,
    home_team: str,
    assigned_side: str,
    pick: str,
    bet: float,
    *,
    is_late_default: bool = False,
) -> int:
    week_id = db.get_or_create_week(conn, season_year, week_number)
    game_id = db.get_or_create_game(conn, week_id, away_team, home_team)
    entry_id = db.get_my_entry_by_name(conn, entry_display_name)
    assignment_id = db.get_or_create_assignment(conn, entry_id, game_id, assigned_side)
    conn.execute(
        """
        INSERT INTO wager_observation (assignment_id, declared_pick, declared_bet, is_late_default)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(assignment_id) DO UPDATE SET
            declared_pick = excluded.declared_pick,
            declared_bet = excluded.declared_bet,
            is_late_default = excluded.is_late_default
        """,
        (assignment_id, pick, bet, int(is_late_default)),
    )
    conn.commit()
    return assignment_id


# --- Season-long "lookahead" grids (spread and moneyline) ---
#
# Both share the same layout: one row per team (full mascot name), one
# column per week (1-18), each cell either "BYE", "TBD  @OPP"/"TBD  VS OPP"
# (no line posted that far out yet), or "<value>  @OPP"/"<value>  VS OPP"
# where <value> is a signed number relative to the row's own team (a point
# spread in one sheet, American moneyline odds in the other) and @/VS says
# whether the row's team is away/home. A leading backslash is tolerated
# (artifact of some markdown table renderers) but not required.

_LOOKAHEAD_CELL_RE = re.compile(
    r"^\\?([+-]?\d+(?:\.\d+)?|TBD)\s+(@|VS)\s*([A-Za-z]+)$", re.IGNORECASE
)


@dataclass
class LookaheadCell:
    week_number: int
    away_team: str  # short name (nfl_teams convention)
    home_team: str
    value: float  # relative to the row's own team; sign only meaningful for spreads
    row_team_is_away: bool


def _parse_lookahead_grid(csv_text: str) -> List[LookaheadCell]:
    reader = csv.reader(io.StringIO(csv_text.strip()))
    rows = list(reader)
    if not rows:
        return []
    header = rows[0]
    week_numbers = []
    for col in header[1:]:
        col = col.strip()
        if col.isdigit():
            week_numbers.append(int(col))
        else:
            week_numbers.append(None)

    out: List[LookaheadCell] = []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        team_short = nfl_teams.FULL_TO_SHORT.get(row[0].strip())
        if team_short is None:
            continue  # unrecognized row label — skip rather than guess
        for i, cell in enumerate(row[1:]):
            if i >= len(week_numbers) or week_numbers[i] is None:
                continue
            cell = cell.strip()
            if not cell or cell.upper() == "BYE":
                continue
            m = _LOOKAHEAD_CELL_RE.match(cell)
            if not m:
                continue
            value_str, side, opp_code = m.groups()
            if value_str.upper() == "TBD":
                continue
            opp_short = nfl_teams.CODE_TO_SHORT.get(opp_code.upper())
            if opp_short is None:
                continue
            row_team_is_away = side.upper() == "@"
            away_team = team_short if row_team_is_away else opp_short
            home_team = opp_short if row_team_is_away else team_short
            out.append(
                LookaheadCell(
                    week_number=week_numbers[i],
                    away_team=away_team,
                    home_team=home_team,
                    value=float(value_str),
                    row_team_is_away=row_team_is_away,
                )
            )
    return out


def import_lookahead_spreads(conn: sqlite3.Connection, season_year: int, csv_text: str) -> int:
    """Loads a full-season point-spread grid (see module docstring above)
    into game.favorite/spread_margin for every parseable cell across all
    18 weeks in one pass — meant for future weeks the live Odds API fetch
    doesn't have lines for yet. Never overwrites a game whose spread has
    already been confirmed (game.spread_confirmed = 1); that stays sticky
    regardless of source or ordering.
    """
    count = 0
    for cell in _parse_lookahead_grid(csv_text):
        if cell.value == 0:
            favorite, margin = None, 0.0
        elif cell.value < 0:
            favorite = "away" if cell.row_team_is_away else "home"
            margin = abs(cell.value)
        else:
            favorite = "home" if cell.row_team_is_away else "away"
            margin = cell.value

        week_id = db.get_or_create_week(conn, season_year, cell.week_number)
        game_id = db.get_or_create_game(conn, week_id, cell.away_team, cell.home_team)
        existing = conn.execute(
            "SELECT spread_confirmed FROM game WHERE id = ?", (game_id,)
        ).fetchone()
        if existing and existing["spread_confirmed"]:
            continue
        conn.execute(
            "UPDATE game SET favorite = ?, spread_margin = ?, spread_source = 'lookahead_sheet' WHERE id = ?",
            (favorite, margin, game_id),
        )
        count += 1
    conn.commit()
    return count


def moneyline_to_win_probability(moneyline: float) -> float:
    """American moneyline odds -> implied win probability (includes the
    book's vig, so this slightly overstates true probability on both sides
    of a game — a modeling approximation, not a precise figure).
    """
    if moneyline < 0:
        return -moneyline / (-moneyline + 100)
    return 100 / (moneyline + 100)


@dataclass
class SimCalibration:
    p_upset_freq: float  # fraction of scheduled games that qualify as a 10+ point spread
    p_upset_win: float  # average implied win probability of the underdog side, in those games
    games_considered: int
    qualifying_games: int


def calibrate_from_lookahead_sheets(
    spread_csv_text: str,
    moneyline_csv_text: str,
    *,
    upset_threshold: float = 10.0,
) -> Optional[SimCalibration]:
    """Derives p_upset_freq and p_upset_win for the Monte Carlo simulator
    from real season-wide data instead of the fixed guesses SimAssumptions
    otherwise defaults to. Cannot inform per-entry future-week win
    probability directly, since future weeks' random assignments aren't
    knowable in advance — this calibrates the simulator's global
    assumptions instead: across the whole schedule, what fraction of games
    are actually 10+-point spreads, and what's the underdog's real average
    win probability in those games (from the paired moneyline sheet).
    """
    spread_cells = _parse_lookahead_grid(spread_csv_text)
    moneyline_by_key = {}
    for c in _parse_lookahead_grid(moneyline_csv_text):
        key = (c.week_number, c.away_team, c.home_team, c.row_team_is_away)
        moneyline_by_key[key] = c.value

    seen_games = set()
    qualifying_win_probs = []
    total_games = 0
    for c in spread_cells:
        game_key = (c.week_number, c.away_team, c.home_team)
        if game_key in seen_games:
            continue  # each game appears twice (once per team's row) — count once
        seen_games.add(game_key)
        total_games += 1
        if abs(c.value) < upset_threshold:
            continue
        # c.value is relative to whichever team this cell came from; find
        # the underdog side's own moneyline (positive spread = underdog).
        underdog_is_away = (c.value > 0) == c.row_team_is_away
        ml = moneyline_by_key.get((c.week_number, c.away_team, c.home_team, underdog_is_away))
        if ml is None:
            continue
        qualifying_win_probs.append(moneyline_to_win_probability(ml))

    if total_games == 0:
        return None
    qualifying_games = len(qualifying_win_probs)
    return SimCalibration(
        p_upset_freq=qualifying_games / total_games if total_games else 0.0,
        p_upset_win=(sum(qualifying_win_probs) / qualifying_games) if qualifying_games else 0.0,
        games_considered=total_games,
        qualifying_games=qualifying_games,
    )
