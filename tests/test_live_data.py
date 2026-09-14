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


def test_save_spread_snapshot_updates_game_for_recommendations(conn):
    # Unconfirmed estimates now DO feed game.favorite/spread_margin (so
    # recommendations/the Scenario Projector reflect them) — just never
    # marked authoritative for 10x qualification.
    est = live_data.SpreadEstimate("home", 3.5, "The Odds API, median of 4 books", "2026-09-09T00:00:00+00:00")
    live_data.save_spread_snapshot(conn, 2026, 1, "Atlanta", "Pittsburgh", est)
    game = conn.execute("SELECT favorite, spread_margin, spread_confirmed, spread_source FROM game").fetchone()
    assert game["favorite"] == "home"
    assert game["spread_margin"] == 3.5
    assert game["spread_confirmed"] == 0
    assert game["spread_source"] == "odds_api"


def test_save_spread_snapshot_never_overwrites_confirmed_game(conn):
    live_data.confirm_spread(conn, 2026, 1, "Atlanta", "Pittsburgh", "away", 7.0, "real source")
    est = live_data.SpreadEstimate("home", 3.5, "The Odds API, median of 4 books", "2026-09-09T00:00:00+00:00")
    live_data.save_spread_snapshot(conn, 2026, 1, "Atlanta", "Pittsburgh", est)

    game = conn.execute("SELECT favorite, spread_margin, spread_confirmed FROM game").fetchone()
    assert game["favorite"] == "away"  # untouched by the later estimate
    assert game["spread_margin"] == 7.0
    assert game["spread_confirmed"] == 1

    # the estimate is still logged for the audit trail, just never applied
    snapshots = conn.execute("SELECT COUNT(*) AS n FROM market_snapshot").fetchone()
    assert snapshots["n"] == 2


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

    game = conn.execute(
        "SELECT favorite, spread_margin, spread_confirmed FROM game WHERE id = ?", (game_id,)
    ).fetchone()
    assert game["favorite"] == "home"
    assert game["spread_margin"] == 10.0
    assert game["spread_confirmed"] == 1


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


def test_extract_google_sheet_id_from_full_url():
    url = "https://docs.google.com/spreadsheets/d/1VVnewww8IAjkrMpDjJzl73iKK2DHlqVRN1wJgaWjDJo/edit?usp=sharing"
    assert live_data.extract_google_sheet_id(url) == "1VVnewww8IAjkrMpDjJzl73iKK2DHlqVRN1wJgaWjDJo"


def test_extract_google_sheet_id_from_bare_id():
    assert live_data.extract_google_sheet_id("abc123") == "abc123"


class _FakeSheetResponse:
    def __init__(self, status_code, content_type, text):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.text = text


def test_fetch_google_sheet_csv_success(monkeypatch):
    csv_text = "Rank,Team Name,Total Pts,Away,Home\n1,SPM,150,Atlanta,Pittsburgh\n"
    fake_requests = types.SimpleNamespace(
        get=lambda url, params=None, timeout=None: _FakeSheetResponse(200, "text/csv", csv_text),
        RequestException=Exception,
    )
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    result = live_data.fetch_google_sheet_csv("some-id")
    assert result == csv_text


def test_fetch_google_sheet_csv_permission_denied_raises(monkeypatch):
    html = "<html><body>Sign in to continue</body></html>"
    fake_requests = types.SimpleNamespace(
        get=lambda url, params=None, timeout=None: _FakeSheetResponse(200, "text/html", html),
        RequestException=Exception,
    )
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    with pytest.raises(live_data.LiveDataError, match="Anyone with the link"):
        live_data.fetch_google_sheet_csv("some-id")


def test_fetch_google_sheet_csv_http_error_raises(monkeypatch):
    fake_requests = types.SimpleNamespace(
        get=lambda url, params=None, timeout=None: _FakeSheetResponse(404, "text/html", "not found"),
        RequestException=Exception,
    )
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    with pytest.raises(live_data.LiveDataError, match="HTTP 404"):
        live_data.fetch_google_sheet_csv("some-id")
