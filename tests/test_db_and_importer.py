import sqlite3

import pytest

from pool import db, importer
from pool.config import PoolConfig
from pool.queries import compute_field_reconstruction, compute_my_entry_timeline


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    c = db.connect(db_path)
    yield c
    c.close()


def test_init_db_creates_default_config(conn):
    cfg = PoolConfig.load(conn)
    assert cfg.start_points == 150
    assert cfg.min_bet == 20
    assert cfg.payouts[0] == 40


def test_import_standings_paste_shape(conn):
    text = "Always Hot, 170\nAprils Best Guess, 130\n"
    count = importer.import_standings(conn, 2026, 1, text)
    assert count == 2
    row = conn.execute(
        """
        SELECT ewp.points FROM entry_week_points ewp
        JOIN entry e ON e.id = ewp.entry_id
        WHERE e.display_name = 'Always Hot'
        """
    ).fetchone()
    assert row["points"] == 170


def test_import_standings_upsert_same_week(conn):
    importer.import_standings(conn, 2026, 1, "Always Hot, 170\n")
    importer.import_standings(conn, 2026, 1, "Always Hot, 190\n")
    row = conn.execute(
        """
        SELECT ewp.points FROM entry_week_points ewp
        JOIN entry e ON e.id = ewp.entry_id WHERE e.display_name = 'Always Hot'
        """
    ).fetchone()
    assert row["points"] == 190


def test_import_schedule_and_assignments_resolve_side(conn):
    importer.import_schedule(conn, 2026, 1, "Atlanta, Pittsburgh\n")
    count, errors = importer.import_assignments(
        conn, 2026, 1, "Always Hot, Pittsburgh\nMr Brownstone, Atlanta\n"
    )
    assert count == 2
    assert errors == []

    row = conn.execute(
        """
        SELECT a.assigned_side FROM assignment a
        JOIN entry e ON e.id = a.entry_id WHERE e.display_name = 'Always Hot'
        """
    ).fetchone()
    assert row["assigned_side"] == "home"

    row2 = conn.execute(
        """
        SELECT a.assigned_side FROM assignment a
        JOIN entry e ON e.id = a.entry_id WHERE e.display_name = 'Mr Brownstone'
        """
    ).fetchone()
    assert row2["assigned_side"] == "away"


def test_import_assignments_unmatched_team_reported(conn):
    importer.import_schedule(conn, 2026, 1, "Atlanta, Pittsburgh\n")
    count, errors = importer.import_assignments(conn, 2026, 1, "Some Guy, Cleveland\n")
    assert count == 0
    assert len(errors) == 1
    assert errors[0].team == "Cleveland"


def test_record_my_pick_and_timeline_scores_correctly(conn):
    importer.record_game_result(
        conn, 2026, 1, "Atlanta", "Pittsburgh", favorite="away", margin=3, outcome="home"
    )
    assignment_id = importer.record_my_pick(
        conn, 2026, 1, "My Entry 1", "Atlanta", "Pittsburgh", "home", "WIN", 30
    )
    assert assignment_id is not None

    entry_row = conn.execute(
        "SELECT id FROM entry WHERE display_name = 'My Entry 1'"
    ).fetchone()
    cfg = PoolConfig.load(conn)
    timeline = compute_my_entry_timeline(conn, entry_row["id"], cfg)
    assert timeline.current_points == 180
    assert timeline.rows[0].result.delta == 30


def test_field_reconstruction_end_to_end(conn):
    importer.import_schedule(conn, 2026, 1, "Atlanta, Pittsburgh\n")
    importer.import_assignments(conn, 2026, 1, "Always Hot, Pittsburgh\n")
    importer.record_game_result(
        conn, 2026, 1, "Atlanta", "Pittsburgh", favorite="away", margin=3, outcome="home"
    )
    importer.import_standings(conn, 2026, 1, "Always Hot, 180\n")

    cfg = PoolConfig.load(conn)
    rows = compute_field_reconstruction(conn, 2026, 1, cfg)
    assert len(rows) == 1
    row = rows[0]
    assert row.prev_points == 150  # week 1 defaults to start_points
    assert row.current_points == 180
    assert row.delta == 30
    picks = {c.pick: c.bet for c in row.candidates}
    assert picks.get("WIN") == 30

    # inference should have been persisted
    stored = conn.execute("SELECT COUNT(*) AS n FROM wager_inference").fetchone()
    assert stored["n"] >= 1


def test_record_game_result_partial_update_preserves_other_fields(conn):
    importer.record_game_result(
        conn, 2026, 1, "Atlanta", "Pittsburgh", favorite="away", margin=12, outcome=None
    )
    # Recording just the outcome later must not wipe the previously-set spread.
    importer.record_game_result(conn, 2026, 1, "Atlanta", "Pittsburgh", outcome="home")

    game = conn.execute(
        "SELECT favorite, spread_margin, outcome FROM game g JOIN week w ON w.id = g.week_id "
        "WHERE w.season_year = 2026 AND w.week_number = 1"
    ).fetchone()
    assert game["favorite"] == "away"
    assert game["spread_margin"] == 12
    assert game["outcome"] == "home"


def test_record_game_result_explicit_none_clears_favorite(conn):
    importer.record_game_result(
        conn, 2026, 1, "Atlanta", "Pittsburgh", favorite="away", margin=12
    )
    importer.record_game_result(conn, 2026, 1, "Atlanta", "Pittsburgh", favorite=None, margin=0)

    game = conn.execute(
        "SELECT favorite, spread_margin FROM game g JOIN week w ON w.id = g.week_id "
        "WHERE w.season_year = 2026 AND w.week_number = 1"
    ).fetchone()
    assert game["favorite"] is None
    assert game["spread_margin"] == 0


_SAMPLE_WEEK_CSV = """Rank,Team Name,Total Pts,Away,Home
 1,  01 Sportsbet, 150, Cleveland, Jacksonville
 2,  Creative Destruction, 150, NY Jets, Tennessee
 3, Always Hot, 150, Atlanta, Pittsburgh
 4, SPM, 150, Dallas, NY Giants
"""


def test_import_week_csv_matches_real_export_shape(conn):
    count = importer.import_week_csv(conn, 2026, 1, _SAMPLE_WEEK_CSV)
    assert count == 4

    # every entry gets assigned_side = 'away'
    sides = conn.execute(
        "SELECT DISTINCT assigned_side FROM assignment"
    ).fetchall()
    assert [r["assigned_side"] for r in sides] == ["away"]

    # standings applied
    points_row = conn.execute(
        """
        SELECT ewp.points FROM entry_week_points ewp
        JOIN entry e ON e.id = ewp.entry_id WHERE e.display_name = 'Always Hot'
        """
    ).fetchone()
    assert points_row["points"] == 150

    # schedule created
    games = conn.execute("SELECT away_team, home_team FROM game").fetchall()
    assert ("Cleveland", "Jacksonville") in {(g["away_team"], g["home_team"]) for g in games}


def test_import_week_csv_attaches_to_preseeded_my_entry(conn):
    importer.import_week_csv(conn, 2026, 1, _SAMPLE_WEEK_CSV)
    spm = conn.execute(
        "SELECT id, is_mine FROM entry WHERE display_name = 'SPM'"
    ).fetchone()
    assert spm["is_mine"] == 1
    # no duplicate "SPM" entry was created alongside the pre-seeded one
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM entry WHERE display_name = 'SPM'"
    ).fetchone()
    assert count["n"] == 1


def test_import_week_csv_missing_column_raises(conn):
    bad_csv = "Rank,Team Name,Away,Home\n1,Always Hot,Atlanta,Pittsburgh\n"
    with pytest.raises(ValueError, match="Total Pts"):
        importer.import_week_csv(conn, 2026, 1, bad_csv)


def test_import_week_csv_idempotent_on_rerun(conn):
    importer.import_week_csv(conn, 2026, 1, _SAMPLE_WEEK_CSV)
    importer.import_week_csv(conn, 2026, 1, _SAMPLE_WEEK_CSV)  # rerun, e.g. re-uploaded file
    count = conn.execute("SELECT COUNT(*) AS n FROM entry WHERE display_name = 'Always Hot'").fetchone()
    assert count["n"] == 1
