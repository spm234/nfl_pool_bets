import math

import pytest

from loser_pool import db, importer, picks
from loser_pool.optimizer import full_season_plan, recommend_week


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    c = db.connect(db_path)
    yield c
    c.close()


def test_full_season_plan_picks_globally_optimal_assignment(conn):
    # Two weeks, two games each. Team A is a big underdog (~90% to lose) in
    # week 1 AND week 2, but can only be used once — so the optimal plan
    # must give week 2 to the next-best option (Team C) rather than greedily
    # taking A both times (which is impossible) or A only in week 1 leaving
    # a worse week-2 pick than necessary.
    importer.record_game_result(conn, 2026, 1, "TeamA", "TeamB", favorite="home", margin=20)  # A huge dog
    importer.record_game_result(conn, 2026, 2, "TeamA", "TeamC", favorite="home", margin=1)  # near pick'em
    importer.record_game_result(conn, 2026, 2, "TeamD", "TeamE", favorite="home", margin=14)  # D huge dog

    plan = full_season_plan(conn, 2026, [1, 2])
    by_week = {opt.week_number: opt.team for opt in plan}

    assert by_week[1] == "TeamA"  # only place TeamA's big mismatch is available
    assert by_week[2] == "TeamD"  # week 2's best real mismatch, since A is used up


def test_full_season_plan_respects_exclude_teams(conn):
    importer.record_game_result(conn, 2026, 1, "TeamA", "TeamB", favorite="home", margin=20)
    plan = full_season_plan(conn, 2026, [1], exclude_teams={"TeamA"})
    teams = {opt.team for opt in plan}
    assert "TeamA" not in teams
    assert "TeamB" in teams


def test_full_season_plan_empty_when_no_games(conn):
    assert full_season_plan(conn, 2026, [1]) == []


def test_recommend_week_ranks_by_loss_probability_without_horizon(conn):
    importer.record_game_result(conn, 2026, 1, "TeamA", "TeamB", favorite="home", margin=20)
    entry_id = db.get_or_create_entry(conn, "SPM", "SPM", is_mine=True)
    conn.commit()
    recs = recommend_week(conn, 2026, 1, entry_id)
    assert recs[0].team == "TeamA"  # heavy underdog = most likely to lose
    assert math.isclose(recs[0].p_lose + recs[-1].p_lose, 1.0)
    assert all(r.p_survive_season_if_picked is None for r in recs)


def test_recommend_week_with_horizon_adds_simulated_survival(conn):
    importer.record_game_result(conn, 2026, 1, "TeamA", "TeamB", favorite="home", margin=20)
    importer.record_game_result(conn, 2026, 2, "TeamC", "TeamD", favorite="home", margin=10)
    entry_id = db.get_or_create_entry(conn, "SPM", "SPM", is_mine=True)
    conn.commit()
    recs = recommend_week(conn, 2026, 1, entry_id, remaining_week_numbers=[1, 2], sim_runs=200)
    assert recs[0].p_survive_season_if_picked is not None
    assert 0.0 <= recs[0].p_survive_season_if_picked <= 1.0


def test_recommend_week_excludes_already_used_teams(conn):
    importer.import_schedule(conn, 2026, 1, "TeamA,TeamB\n")
    importer.import_schedule(conn, 2026, 2, "TeamA,TeamC\n")
    picks.record_pick(conn, 2026, 1, "SPM", "TeamA", is_mine=True)
    importer.record_game_result(conn, 2026, 1, "TeamA", "TeamB", outcome="home")
    picks.settle_week(conn, 2026, 1)  # TeamA now "used" for SPM

    entry_id = db.get_entry_by_name(conn, "SPM")["id"]
    recs = recommend_week(conn, 2026, 2, entry_id)
    assert all(r.team != "TeamA" for r in recs)


def test_recommend_week_no_available_teams_returns_empty(conn):
    entry_id = db.get_or_create_entry(conn, "SPM", "SPM", is_mine=True)
    conn.commit()
    assert recommend_week(conn, 2026, 1, entry_id) == []
