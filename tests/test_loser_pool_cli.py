import sys
import types
from argparse import Namespace

import pytest

from loser_pool import cli, db, importer, picks


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    db.init_db(path)
    return path


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_scores_get(events):
    def fake_get(url, params=None, timeout=None):
        return _FakeResponse(events)
    return types.SimpleNamespace(get=fake_get, RequestException=Exception)


def test_sync_results_no_games_is_a_noop(db_path, capsys):
    args = Namespace(db=str(db_path), api_key="test-key", days_from=3)
    cli.cmd_sync_results(args)
    out = capsys.readouterr().out
    assert "nothing to sync" in out


def test_sync_results_records_outcome_and_settles_pending_picks(db_path, monkeypatch, capsys):
    conn = db.connect(db_path)
    importer.import_schedule(conn, 2026, 1, "Jaguars,Browns\n")
    picks.record_pick(conn, 2026, 1, "SPM", "Jaguars", is_mine=True)
    conn.close()

    # Jaguars (away) win -> a "pick to lose" on Jaguars busts.
    fake_requests = _fake_scores_get(
        [
            {
                "away_team": "Jaguars", "home_team": "Browns", "completed": True,
                "scores": [{"name": "Jaguars", "score": "24"}, {"name": "Browns", "score": "17"}],
            },
        ]
    )
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    args = Namespace(db=str(db_path), api_key="test-key", days_from=3)
    cli.cmd_sync_results(args)
    out = capsys.readouterr().out
    assert "wk1 Jaguars @ Browns: outcome recorded as 'away'" in out
    assert "settled week(s) 1" in out

    conn = db.connect(db_path)
    entry = db.get_entry_by_name(conn, "SPM")
    assert entry["lives_remaining"] == 1
    assert not entry["eliminated"]
    pick_row = conn.execute("SELECT result FROM lp_pick").fetchone()
    assert pick_row["result"] == "busted"
    conn.close()


def test_sync_results_skips_unfinished_games_and_does_not_settle(db_path, monkeypatch, capsys):
    conn = db.connect(db_path)
    importer.import_schedule(conn, 2026, 1, "Jaguars,Browns\n")
    picks.record_pick(conn, 2026, 1, "SPM", "Jaguars", is_mine=True)
    conn.close()

    fake_requests = _fake_scores_get(
        [
            {"away_team": "Jaguars", "home_team": "Browns", "completed": False, "scores": None},
        ]
    )
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    args = Namespace(db=str(db_path), api_key="test-key", days_from=3)
    cli.cmd_sync_results(args)
    out = capsys.readouterr().out
    assert "not fetched yet" in out
    assert "Synced 0 new result(s), settled week(s) none." in out

    conn = db.connect(db_path)
    pick_row = conn.execute("SELECT result FROM lp_pick").fetchone()
    assert pick_row["result"] == "pending"
    conn.close()


def test_sync_results_skips_games_already_settled(db_path, monkeypatch, capsys):
    conn = db.connect(db_path)
    importer.import_schedule(conn, 2026, 1, "Jaguars,Browns\n")
    importer.record_game_result(conn, 2026, 1, "Jaguars", "Browns", outcome="home")
    conn.close()

    fetched = []

    def fake_get(url, params=None, timeout=None):
        fetched.append(1)
        return _FakeResponse([])

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=fake_get, RequestException=Exception))

    args = Namespace(db=str(db_path), api_key="test-key", days_from=3)
    cli.cmd_sync_results(args)
    assert len(fetched) == 0  # already-settled game never triggers an API call
    out = capsys.readouterr().out
    assert "nothing to sync" in out
