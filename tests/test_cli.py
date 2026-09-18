import sys
import types
from argparse import Namespace

import pytest

from pool import cli, db, importer


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    db.init_db(path)
    return path


def _seed_my_assignment(path, season, week, away, home):
    conn = db.connect(path)
    importer.import_schedule(conn, season, week, f"{away}, {home}\n")
    importer.import_assignments(conn, season, week, f"SPM, {away}\n")
    conn.close()


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_odds_requests(away, home, home_point):
    def fake_get(url, params=None, timeout=None):
        return _FakeResponse(
            [
                {
                    "away_team": away,
                    "home_team": home,
                    "bookmakers": [
                        {
                            "markets": [
                                {
                                    "key": "spreads",
                                    "outcomes": [
                                        {"name": home, "point": home_point},
                                        {"name": away, "point": -home_point},
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        )

    fake = types.SimpleNamespace(get=fake_get, RequestException=Exception)
    return fake


def test_fetch_my_spreads_no_assignments_is_a_noop(db_path, capsys):
    args = Namespace(db=str(db_path), season=2026, week=1, api_key="test-key")
    cli.cmd_fetch_my_spreads(args)
    out = capsys.readouterr().out
    assert "nothing to fetch" in out


def test_fetch_my_spreads_fetches_only_my_games(db_path, monkeypatch, capsys):
    _seed_my_assignment(db_path, 2026, 1, "Atlanta", "Pittsburgh")
    # A field-only game that SPM is not assigned to should never be fetched.
    conn = db.connect(db_path)
    importer.import_schedule(conn, 2026, 1, "Buffalo, Houston\n")
    importer.import_assignments(conn, 2026, 1, "Always Hot, Buffalo\n")
    conn.close()

    fetched = []

    def fake_get(url, params=None, timeout=None):
        fetched.append(params)
        return _FakeResponse(
            [
                {
                    "away_team": "Atlanta",
                    "home_team": "Pittsburgh",
                    "bookmakers": [
                        {"markets": [{"key": "spreads", "outcomes": [
                            {"name": "Pittsburgh", "point": -3.0},
                            {"name": "Atlanta", "point": 3.0},
                        ]}]}
                    ],
                }
            ]
        )

    fake_requests = types.SimpleNamespace(get=fake_get, RequestException=Exception)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    args = Namespace(db=str(db_path), season=2026, week=1, api_key="test-key")
    cli.cmd_fetch_my_spreads(args)

    out = capsys.readouterr().out
    assert "Atlanta @ Pittsburgh" in out
    assert "Buffalo @ Houston" not in out
    assert len(fetched) == 1  # only one game (mine), not both

    conn = db.connect(db_path)
    row = conn.execute("SELECT COUNT(*) AS n FROM market_snapshot").fetchone()
    assert row["n"] == 1
    conn.close()


def _fake_scores_get(events):
    def fake_get(url, params=None, timeout=None):
        return _FakeResponse(events)
    return types.SimpleNamespace(get=fake_get, RequestException=Exception)


def test_fetch_results_no_games_is_a_noop(db_path, capsys):
    args = Namespace(db=str(db_path), season=2026, week=1, api_key="test-key")
    cli.cmd_fetch_results(args)
    out = capsys.readouterr().out
    assert "nothing to fetch" in out


def test_fetch_results_records_completed_games_and_skips_unfinished(db_path, monkeypatch, capsys):
    conn = db.connect(db_path)
    importer.import_schedule(conn, 2026, 1, "Atlanta, Pittsburgh\nBuffalo, Houston\n")
    conn.close()

    fake_requests = _fake_scores_get(
        [
            {
                "away_team": "Atlanta", "home_team": "Pittsburgh", "completed": True,
                "scores": [{"name": "Atlanta", "score": "17"}, {"name": "Pittsburgh", "score": "24"}],
            },
            {
                "away_team": "Buffalo", "home_team": "Houston", "completed": False, "scores": None,
            },
        ]
    )
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    args = Namespace(db=str(db_path), season=2026, week=1, api_key="test-key")
    cli.cmd_fetch_results(args)
    out = capsys.readouterr().out
    assert "Atlanta 17 @ Pittsburgh 24" in out
    assert "outcome recorded as 'home'" in out
    assert "Buffalo @ Houston: not fetched yet" in out
    assert "Recorded 1 new result(s)" in out

    conn = db.connect(db_path)
    rows = {
        (r["away_team"], r["home_team"]): r["outcome"]
        for r in conn.execute("SELECT away_team, home_team, outcome FROM game")
    }
    assert rows[("Atlanta", "Pittsburgh")] == "home"
    assert rows[("Buffalo", "Houston")] is None
    conn.close()


def test_fetch_results_skips_games_already_settled(db_path, monkeypatch, capsys):
    conn = db.connect(db_path)
    importer.import_schedule(conn, 2026, 1, "Atlanta, Pittsburgh\n")
    importer.record_game_result(conn, 2026, 1, "Atlanta", "Pittsburgh", outcome="away")
    conn.close()

    fetched = []

    def fake_get(url, params=None, timeout=None):
        fetched.append(1)
        return _FakeResponse([])

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=fake_get, RequestException=Exception))

    args = Namespace(db=str(db_path), season=2026, week=1, api_key="test-key")
    cli.cmd_fetch_results(args)
    assert len(fetched) == 0  # already-settled game never triggers an API call
    out = capsys.readouterr().out
    assert "Recorded 0 new result(s)" in out


def test_fetch_my_spreads_exits_nonzero_on_failure(db_path, monkeypatch):
    _seed_my_assignment(db_path, 2026, 1, "Atlanta", "Pittsburgh")
    fake_requests = types.SimpleNamespace(
        get=lambda *a, **k: (_ for _ in ()).throw(Exception("boom")),
        RequestException=Exception,
    )
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    args = Namespace(db=str(db_path), season=2026, week=1, api_key="test-key")
    with pytest.raises(SystemExit) as exc_info:
        cli.cmd_fetch_my_spreads(args)
    assert exc_info.value.code == 1


def test_config_set_recommend_aggression(db_path):
    args = Namespace(
        db=str(db_path), start_points=None, min_bet=None, entry_fee=None, payouts=None,
        upset_threshold=None, spread_source=None, counts_favorite_loss=None,
        default_field_size=None, recommend_aggression=90,
    )
    cli.cmd_config_set(args)
    from pool.config import PoolConfig
    conn = db.connect(db_path)
    cfg = PoolConfig.load(conn)
    assert cfg.recommend_aggression == 90
    conn.close()


def test_sync_results_no_games_is_a_noop(db_path, capsys):
    args = Namespace(db=str(db_path), api_key="test-key", days_from=3)
    cli.cmd_sync_results(args)
    out = capsys.readouterr().out
    assert "nothing to sync" in out


def test_sync_results_finds_completed_games_across_weeks_without_season_or_week_args(
    db_path, monkeypatch, capsys
):
    conn = db.connect(db_path)
    importer.import_schedule(conn, 2026, 1, "Atlanta, Pittsburgh\n")
    importer.import_schedule(conn, 2026, 2, "Buffalo, Houston\n")
    conn.close()

    fake_requests = _fake_scores_get(
        [
            {
                "away_team": "Atlanta", "home_team": "Pittsburgh", "completed": True,
                "scores": [{"name": "Atlanta", "score": "17"}, {"name": "Pittsburgh", "score": "24"}],
            },
            {
                "away_team": "Buffalo", "home_team": "Houston", "completed": False, "scores": None,
            },
        ]
    )
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    args = Namespace(db=str(db_path), api_key="test-key", days_from=3)
    cli.cmd_sync_results(args)
    out = capsys.readouterr().out
    assert "wk1 Atlanta @ Pittsburgh: outcome recorded as 'home'" in out
    assert "wk2 Buffalo @ Houston: not fetched yet" in out
    assert "Synced 1 new result(s), week(s) 1." in out

    conn = db.connect(db_path)
    rows = {
        (r["away_team"], r["home_team"]): r["outcome"]
        for r in conn.execute("SELECT away_team, home_team, outcome FROM game")
    }
    assert rows[("Atlanta", "Pittsburgh")] == "home"
    assert rows[("Buffalo", "Houston")] is None
    conn.close()


def test_sync_results_skips_games_already_settled(db_path, monkeypatch, capsys):
    conn = db.connect(db_path)
    importer.import_schedule(conn, 2026, 1, "Atlanta, Pittsburgh\n")
    importer.record_game_result(conn, 2026, 1, "Atlanta", "Pittsburgh", outcome="away")
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
