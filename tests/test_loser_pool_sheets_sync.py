import pytest

from loser_pool import db, importer, picks
from loser_pool.sheets_sync import (
    parse_pool_sheet_csv,
    pick_ownership,
    sync_picks_from_sheet,
)

# A trimmed version of the real "Loser Pool 26-27" sheet's actual CSV shape
# (confirmed via the sheet's own Drive export): blank first header cell,
# then "Week 1".."Week N"/playoff round names; one row per entry, blank
# cells for weeks not yet picked, and one genuinely blank row (a real
# artifact of that sheet, kept here so parsing is tested against it).
SAMPLE_CSV = (
    ",Week 1,Week 2,Wild Card,Divisional \n"
    "Cade S,Patriots,,,\n"
    "David M,Texans ,,,\n"
    ",,,,\n"
    "Kiel G,Cardinals ,,,\n"
    "Sean M,,,,\n"
)


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    c = db.connect(db_path)
    yield c
    c.close()


def test_parse_pool_sheet_csv_skips_blank_rows_and_strips_whitespace():
    rows = parse_pool_sheet_csv(SAMPLE_CSV)
    names = [name for name, _ in rows]
    assert names == ["Cade S", "David M", "Kiel G", "Sean M"]
    by_name = dict(rows)
    assert by_name["David M"]["Week 1"] == "Texans"  # trailing space stripped
    assert by_name["Sean M"]["Week 1"] == ""


def test_parse_pool_sheet_csv_handles_trailing_space_playoff_headers():
    rows = parse_pool_sheet_csv(SAMPLE_CSV)
    by_name = dict(rows)
    # Header was "Divisional " (trailing space) — PERIOD_TO_WEEK's key has
    # no trailing space, so this only round-trips correctly if parsing
    # strips the header too.
    assert "Divisional" in by_name["Cade S"]


def test_sync_picks_from_sheet_applies_non_blank_picks(conn):
    importer.import_schedule(conn, 2026, 1, "Patriots,Bills\nTexans,Colts\nCardinals,Rams\n")
    result = sync_picks_from_sheet(conn, 2026, SAMPLE_CSV)
    assert result.applied == 3  # Patriots, Texans, Cardinals in Week 1
    assert result.errors == []
    entry = db.get_entry_by_name(conn, "Cade S")
    assert entry is not None
    assert "Patriots" in picks.used_teams(conn, entry["id"], 2026)


def test_sync_picks_from_sheet_reports_missing_schedule_as_error_not_crash(conn):
    # No schedule imported at all -> every non-blank pick should fail
    # cleanly via PickError, collected rather than raised.
    result = sync_picks_from_sheet(conn, 2026, SAMPLE_CSV)
    assert result.applied == 0
    assert len(result.errors) == 3
    assert all("No game found" in e.reason for e in result.errors)


def test_sync_picks_from_sheet_is_idempotent_on_rerun(conn):
    importer.import_schedule(conn, 2026, 1, "Patriots,Bills\nTexans,Colts\nCardinals,Rams\n")
    sync_picks_from_sheet(conn, 2026, SAMPLE_CSV)
    # Re-running before settlement just re-records the same pending pick
    # (record_pick allows overwriting a still-pending pick), not an error.
    result = sync_picks_from_sheet(conn, 2026, SAMPLE_CSV)
    assert result.applied == 3
    assert result.errors == []


def test_pick_ownership_fractions():
    rows = parse_pool_sheet_csv(SAMPLE_CSV)
    ownership = pick_ownership(rows, "Week 1")
    by_team = {o.team: o for o in ownership}
    assert by_team["Patriots"].count == 1
    # 3 of 4 entries submitted a Week 1 pick (Sean M is blank)
    assert by_team["Patriots"].fraction_of_submitted == pytest.approx(1 / 3)
    assert by_team["Patriots"].fraction_of_all_entries == pytest.approx(1 / 4)


def test_pick_ownership_empty_period_returns_empty_list():
    rows = parse_pool_sheet_csv(SAMPLE_CSV)
    assert pick_ownership(rows, "Week 2") == []
