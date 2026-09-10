"""Sync picks from the pool operator's shared Google Sheet.

This does NOT call the Google Sheets API itself — there's no service-account
or OAuth setup in this repo, and for a personal-use tool that's a lot of
credential-management overhead for what's really a one-column CSV grid.
Instead: whoever has Drive access to the sheet (Claude, in a chat session,
or a person exporting it manually) pulls its CSV export and hands the text
to sync_picks_from_sheet()/pick_ownership() below — same "paste box"
pattern every other importer in this repo uses, just with a sheet as the
source of the paste instead of a manual copy/type.

**Sheet shape** (confirmed against the real "Loser Pool 26-27" sheet,
2026-27 season): one row per entry, first column is the entry's display
name, then one column per period — "Week 1".."Week 18" for the regular
season, "Wild Card" / "Divisional" / "Conference" / "Super Bowl" for the
playoffs (trailing spaces on some playoff headers are stripped). A cell
holds the team that entry picked-to-lose for that period, blank if they
haven't picked yet. There is no separate lives/result/elimination column
in that sheet — this tool's own settle_week/lives tracking is still the
source of truth for that; the sheet only tells you *what everyone picked*.

Playoff periods are mapped to week numbers 19-22 so they sit on the same
week_number axis as the rest of this tool's tables; import the actual
playoff schedule under those same numbers when the time comes.
"""
from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from . import picks

PERIOD_TO_WEEK: Dict[str, int] = {
    **{f"Week {i}": i for i in range(1, 19)},
    "Wild Card": 19,
    "Divisional": 20,
    "Conference": 21,
    "Super Bowl": 22,
}


def parse_pool_sheet_csv(csv_text: str) -> List[Tuple[str, Dict[str, str]]]:
    """Returns [(entry_name, {period_label: team_or_blank}), ...], skipping
    rows with a blank name (the source sheet has at least one stray blank
    row) and normalizing period-label whitespace against PERIOD_TO_WEEK's
    keys (the sheet has trailing spaces on some playoff headers).
    """
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    if not rows:
        return []
    header = [h.strip() for h in rows[0][1:]]

    out: List[Tuple[str, Dict[str, str]]] = []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        name = row[0].strip()
        picks_by_period = {}
        for period, cell in zip(header, row[1:]):
            picks_by_period[period] = cell.strip()
        out.append((name, picks_by_period))
    return out


@dataclass
class SyncError:
    entry: str
    period: str
    team: str
    reason: str


@dataclass
class SyncResult:
    applied: int = 0
    skipped_blank: int = 0
    errors: List[SyncError] = field(default_factory=list)


def sync_picks_from_sheet(
    conn: sqlite3.Connection, season_year: int, csv_text: str
) -> SyncResult:
    """Applies every non-blank pick in the sheet via picks.record_pick.
    Requires that week's schedule to already be imported (record_pick
    needs a matching game to resolve the team against) — a pick for a week
    with no schedule yet is reported as an error, not a crash, so one bad
    row/week doesn't stop the rest of the sync.
    """
    result = SyncResult()
    for entry_name, picks_by_period in parse_pool_sheet_csv(csv_text):
        for period, team in picks_by_period.items():
            if not team:
                result.skipped_blank += 1
                continue
            week_number = PERIOD_TO_WEEK.get(period)
            if week_number is None:
                result.errors.append(SyncError(entry_name, period, team, f"unrecognized period '{period}'"))
                continue
            try:
                picks.record_pick(conn, season_year, week_number, entry_name, team)
                result.applied += 1
            except picks.PickError as e:
                result.errors.append(SyncError(entry_name, period, team, str(e)))
    return result


@dataclass
class PickOwnership:
    team: str
    count: int
    fraction_of_submitted: float
    fraction_of_all_entries: float


def pick_ownership(
    rows: List[Tuple[str, Dict[str, str]]], period: str
) -> List[PickOwnership]:
    """Field ownership % for one period, straight from the sheet — how much
    of the field picked each team, out of both the field that's actually
    submitted so far and the full roster (including not-yet-picked). This
    is the *current* ownership, not clevanalytics' survival-adjusted
    projected ownership for a future week — projecting who's still alive
    by some future week isn't something this data source can tell you on
    its own; it'd need this tool's own settle_week history layered in, and
    is a reasonable next step rather than something to fake here.
    """
    total_entries = len(rows)
    counts: Dict[str, int] = {}
    submitted = 0
    for _, picks_by_period in rows:
        team = picks_by_period.get(period, "")
        if team:
            counts[team] = counts.get(team, 0) + 1
            submitted += 1

    out = [
        PickOwnership(
            team=team,
            count=count,
            fraction_of_submitted=count / submitted if submitted else 0.0,
            fraction_of_all_entries=count / total_entries if total_entries else 0.0,
        )
        for team, count in counts.items()
    ]
    out.sort(key=lambda o: o.count, reverse=True)
    return out
