import pytest

from loser_pool import db, importer
from loser_pool.simulation import simulate_entry_survival


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    c = db.connect(db_path)
    yield c
    c.close()


def test_certain_loser_always_survives(conn):
    # favorite margin so large the picked (favored-to-lose... i.e. underdog)
    # team's loss probability is essentially 1.
    importer.record_game_result(conn, 2026, 1, "Dog", "Fav", favorite="home", margin=60)
    p = simulate_entry_survival(
        conn, 2026, [], exclude_teams=set(), lives_remaining=2,
        runs=200, first_week_p_lose=0.999999, seed=1,
    )
    assert p > 0.99


def test_certain_win_burns_a_life_but_two_lives_survive_one_bust(conn):
    p = simulate_entry_survival(
        conn, 2026, [], exclude_teams=set(), lives_remaining=2,
        runs=500, first_week_p_lose=0.0, seed=1,
    )
    assert p == 1.0  # one guaranteed bust, still has a life left


def test_certain_win_eliminates_with_one_life(conn):
    p = simulate_entry_survival(
        conn, 2026, [], exclude_teams=set(), lives_remaining=1,
        runs=500, first_week_p_lose=0.0, seed=1,
    )
    assert p == 0.0


def test_no_remaining_weeks_and_no_first_week_returns_based_on_current_lives(conn):
    assert simulate_entry_survival(conn, 2026, [], exclude_teams=set(), lives_remaining=1, runs=10) == 1.0
    assert simulate_entry_survival(conn, 2026, [], exclude_teams=set(), lives_remaining=0, runs=10) == 0.0


def test_future_weeks_use_greedy_best_available_each_week(conn):
    # Only 1 life, so this single week's outcome is do-or-die — a real test
    # of whether the greedy policy actually prefers the bigger mismatch
    # when it's available vs. when it's excluded. (With >=2 lives here, a
    # lone remaining week could never eliminate either way, masking the
    # policy difference entirely.)
    importer.record_game_result(conn, 2026, 2, "BigDog", "BigFav", favorite="home", margin=50)
    importer.record_game_result(conn, 2026, 2, "Even1", "Even2", favorite=None, margin=0)
    p_with_bigdog_available = simulate_entry_survival(
        conn, 2026, [2], exclude_teams=set(), lives_remaining=1, runs=3000, seed=1,
    )
    p_without_bigdog = simulate_entry_survival(
        conn, 2026, [2], exclude_teams={"BigDog"}, lives_remaining=1, runs=3000, seed=1,
    )
    assert p_with_bigdog_available > p_without_bigdog


def test_seed_makes_result_reproducible(conn):
    importer.record_game_result(conn, 2026, 1, "TeamA", "TeamB", favorite="home", margin=3)
    p1 = simulate_entry_survival(conn, 2026, [1], exclude_teams=set(), lives_remaining=2, runs=500, seed=42)
    p2 = simulate_entry_survival(conn, 2026, [1], exclude_teams=set(), lives_remaining=2, runs=500, seed=42)
    assert p1 == p2
