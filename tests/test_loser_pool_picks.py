import pytest

from loser_pool import db, importer, picks
from loser_pool.config import LoserPoolConfig


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    c = db.connect(db_path)
    yield c
    c.close()


def _seed_week1(conn):
    importer.import_schedule(conn, 2026, 1, "Jaguars,Browns\nBengals,Ravens\n")


def test_record_pick_requires_scheduled_team(conn):
    _seed_week1(conn)
    with pytest.raises(picks.PickError, match="No game found"):
        picks.record_pick(conn, 2026, 1, "SPM", "Cowboys", is_mine=True)


def test_record_pick_then_settle_survives_when_team_loses(conn):
    _seed_week1(conn)
    picks.record_pick(conn, 2026, 1, "SPM", "Jaguars", is_mine=True)
    importer.record_game_result(conn, 2026, 1, "Jaguars", "Browns", outcome="home")
    results = picks.settle_week(conn, 2026, 1)
    assert len(results) == 1
    assert results[0].outcome == "survived"
    assert results[0].lives_remaining == 2
    entry = db.get_entry_by_name(conn, "SPM")
    assert entry["lives_remaining"] == 2
    assert not entry["eliminated"]


def test_settle_busts_when_picked_team_wins(conn):
    _seed_week1(conn)
    picks.record_pick(conn, 2026, 1, "SPM", "Jaguars", is_mine=True)
    importer.record_game_result(conn, 2026, 1, "Jaguars", "Browns", outcome="away")
    results = picks.settle_week(conn, 2026, 1)
    assert results[0].outcome == "busted"
    assert results[0].lives_remaining == 1
    entry = db.get_entry_by_name(conn, "SPM")
    assert entry["lives_remaining"] == 1
    assert not entry["eliminated"]


def test_second_bust_eliminates_entry(conn):
    importer.import_schedule(conn, 2026, 1, "Jaguars,Browns\n")
    importer.import_schedule(conn, 2026, 2, "Bengals,Ravens\n")
    picks.record_pick(conn, 2026, 1, "SPM", "Jaguars", is_mine=True)
    importer.record_game_result(conn, 2026, 1, "Jaguars", "Browns", outcome="away")
    picks.settle_week(conn, 2026, 1)

    picks.record_pick(conn, 2026, 2, "SPM", "Bengals", is_mine=True)
    importer.record_game_result(conn, 2026, 2, "Bengals", "Ravens", outcome="away")
    results = picks.settle_week(conn, 2026, 2)
    assert results[0].outcome == "busted"
    assert results[0].lives_remaining == 0
    assert results[0].eliminated
    entry = db.get_entry_by_name(conn, "SPM")
    assert entry["eliminated"]
    assert entry["eliminated_week"] == 2


def test_cannot_pick_after_elimination(conn):
    importer.import_schedule(conn, 2026, 1, "Jaguars,Browns\n")
    importer.import_schedule(conn, 2026, 2, "Bengals,Ravens\n")
    entry_id = db.get_or_create_entry(conn, "SPM", "SPM", is_mine=True, lives_per_entry=1)
    picks.record_pick(conn, 2026, 1, "SPM", "Jaguars", is_mine=True)
    importer.record_game_result(conn, 2026, 1, "Jaguars", "Browns", outcome="away")
    picks.settle_week(conn, 2026, 1)
    entry = conn.execute("SELECT * FROM lp_entry WHERE id = ?", (entry_id,)).fetchone()
    assert entry["eliminated"]
    with pytest.raises(picks.PickError, match="already eliminated"):
        picks.record_pick(conn, 2026, 2, "SPM", "Bengals", is_mine=True)


def test_cannot_reuse_a_team(conn):
    importer.import_schedule(conn, 2026, 1, "Jaguars,Browns\n")
    importer.import_schedule(conn, 2026, 2, "Jaguars,Ravens\n")
    picks.record_pick(conn, 2026, 1, "SPM", "Jaguars", is_mine=True)
    importer.record_game_result(conn, 2026, 1, "Jaguars", "Browns", outcome="home")
    picks.settle_week(conn, 2026, 1)
    with pytest.raises(picks.PickError, match="already used"):
        picks.record_pick(conn, 2026, 2, "SPM", "Jaguars", is_mine=True)


def test_tie_treated_as_bust_by_default(conn):
    _seed_week1(conn)
    picks.record_pick(conn, 2026, 1, "SPM", "Jaguars", is_mine=True)
    importer.record_game_result(conn, 2026, 1, "Jaguars", "Browns", outcome="tie")
    results = picks.settle_week(conn, 2026, 1)
    assert results[0].outcome == "busted"


def test_tie_treated_as_survive_when_configured(conn):
    cfg = LoserPoolConfig.load(conn)
    cfg.tie_treated_as = "survive"
    cfg.save(conn)
    _seed_week1(conn)
    picks.record_pick(conn, 2026, 1, "SPM", "Jaguars", is_mine=True)
    importer.record_game_result(conn, 2026, 1, "Jaguars", "Browns", outcome="tie")
    results = picks.settle_week(conn, 2026, 1)
    assert results[0].outcome == "survived"


def test_playoff_reset_frees_up_teams(conn):
    importer.import_schedule(conn, 2026, 1, "Jaguars,Browns\n")
    importer.import_schedule(conn, 2026, 20, "Jaguars,Ravens\n")
    picks.record_pick(conn, 2026, 1, "SPM", "Jaguars", is_mine=True)
    importer.record_game_result(conn, 2026, 1, "Jaguars", "Browns", outcome="home")
    picks.settle_week(conn, 2026, 1)
    entry_id = db.get_entry_by_name(conn, "SPM")["id"]
    assert "Jaguars" in picks.used_teams(conn, entry_id, 2026)

    picks.trigger_playoff_reset(conn, reset_after_week_number=17, note="no winner after regular season")
    assert "Jaguars" not in picks.used_teams(conn, entry_id, 2026)
    # And picking it again post-reset should succeed without error.
    picks.record_pick(conn, 2026, 20, "SPM", "Jaguars", is_mine=True)


def test_settle_week_skips_unsettled_games(conn):
    _seed_week1(conn)
    picks.record_pick(conn, 2026, 1, "SPM", "Jaguars", is_mine=True)
    results = picks.settle_week(conn, 2026, 1)
    assert results == []
    entry = db.get_entry_by_name(conn, "SPM")
    assert entry["lives_remaining"] == 2
