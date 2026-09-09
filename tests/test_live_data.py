import sys
import types

import pytest

from pool import db, live_data


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    c = db.connect(db_path)
    yield c
    c.close()


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_spread_estimate_home_favored(monkeypatch):
    fake_requests = types.SimpleNamespace()

    def fake_get(url, params=None, timeout=None):
        return _FakeResponse(
            [
                {
                    "away_team": "Atlanta",
                    "home_team": "Pittsburgh",
                    "bookmakers": [
                        {
                            "markets": [
                                {
                                    "key": "spreads",
                                    "outcomes": [
                                        {"name": "Pittsburgh", "point": -3.5},
                                        {"name": "Atlanta", "point": 3.5},
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        )

    fake_requests.get = fake_get
    fake_requests.RequestException = Exception
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    est = live_data.fetch_spread_estimate("Atlanta", "Pittsburgh", api_key="test-key")
    assert est.favorite == "home"
    assert est.margin == 3.5


def test_fetch_spread_estimate_no_key_raises(monkeypatch):
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    fake_requests = types.SimpleNamespace(get=lambda *a, **k: None, RequestException=Exception)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    with pytest.raises(live_data.LiveDataError):
        live_data.fetch_spread_estimate("Atlanta", "Pittsburgh")


def test_save_spread_snapshot_never_authoritative(conn):
    est = live_data.SpreadEstimate("home", 3.5, "The Odds API, median of 4 books", "2026-09-09T00:00:00+00:00")
    live_data.save_spread_snapshot(conn, 2026, 1, "Atlanta", "Pittsburgh", est)
    row = conn.execute("SELECT is_authoritative_for_upset FROM market_snapshot").fetchone()
    assert row["is_authoritative_for_upset"] == 0


def test_confirm_spread_is_authoritative_and_updates_game(conn):
    game_id = live_data.confirm_spread(
        conn, 2026, 1, "Atlanta", "Pittsburgh", "home", 10.0, "Cleveland Plain Dealer"
    )
    snap = conn.execute(
        "SELECT is_authoritative_for_upset, margin FROM market_snapshot WHERE game_id = ?",
        (game_id,),
    ).fetchone()
    assert snap["is_authoritative_for_upset"] == 1
    assert snap["margin"] == 10.0

    game = conn.execute("SELECT favorite, spread_margin FROM game WHERE id = ?", (game_id,)).fetchone()
    assert game["favorite"] == "home"
    assert game["spread_margin"] == 10.0


def _fake_scores_requests(events):
    fake_requests = types.SimpleNamespace()
    fake_requests.get = lambda url, params=None, timeout=None: _FakeResponse(events)
    fake_requests.RequestException = Exception
    return fake_requests


def test_fetch_completed_score_home_win(monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "requests",
        _fake_scores_requests(
            [
                {
                    "away_team": "Atlanta",
                    "home_team": "Pittsburgh",
                    "completed": True,
                    "scores": [
                        {"name": "Atlanta", "score": "17"},
                        {"name": "Pittsburgh", "score": "24"},
                    ],
                }
            ]
        ),
    )
    result = live_data.fetch_completed_score("Atlanta", "Pittsburgh", api_key="test-key")
    assert result.outcome == "home"
    assert result.home_score == 24
    assert result.away_score == 17


def test_fetch_completed_score_tie(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "requests",
        _fake_scores_requests(
            [
                {
                    "away_team": "Atlanta",
                    "home_team": "Pittsburgh",
                    "completed": True,
                    "scores": [
                        {"name": "Atlanta", "score": "20"},
                        {"name": "Pittsburgh", "score": "20"},
                    ],
                }
            ]
        ),
    )
    result = live_data.fetch_completed_score("Atlanta", "Pittsburgh", api_key="test-key")
    assert result.outcome == "tie"


def test_fetch_completed_score_not_completed_raises(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "requests",
        _fake_scores_requests(
            [{"away_team": "Atlanta", "home_team": "Pittsburgh", "completed": False, "scores": None}]
        ),
    )
    with pytest.raises(live_data.LiveDataError, match="not marked completed"):
        live_data.fetch_completed_score("Atlanta", "Pittsburgh", api_key="test-key")


def test_fetch_completed_score_no_match_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "requests", _fake_scores_requests([]))
    with pytest.raises(live_data.LiveDataError, match="No score data found"):
        live_data.fetch_completed_score("Atlanta", "Pittsburgh", api_key="test-key")
