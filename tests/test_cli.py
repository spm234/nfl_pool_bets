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
