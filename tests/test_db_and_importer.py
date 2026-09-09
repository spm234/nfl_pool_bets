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
