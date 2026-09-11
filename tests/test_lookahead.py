import pytest

from pool import db, importer
from pool.importer import (
    calibrate_from_lookahead_sheets,
    import_lookahead_spreads,
    moneyline_to_win_probability,
)


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    c = db.connect(db_path)
    yield c
    c.close()


_SPREAD_CSV = """Week,1,2
Arizona Cardinals,+9.5  @LAC,-3  VS SF
Los Angeles Chargers,-9.5  VS ARI,BYE
San Francisco 49ers,BYE,+3  @ARI
"""


def test_import_lookahead_spreads_writes_both_weeks(conn):
    count = import_lookahead_spreads(conn, 2026, _SPREAD_CSV)
    assert count == 4  # 2 cells per game x 2 games

    row = conn.execute(
        """
        SELECT g.favorite, g.spread_margin, g.spread_source FROM game g
        JOIN week w ON w.id = g.week_id
        WHERE w.week_number = 1 AND g.away_team = 'Arizona' AND g.home_team = 'LA Chargers'
        """
    ).fetchone()
    assert row["favorite"] == "home"  # LA Chargers favored
    assert row["spread_margin"] == 9.5
    assert row["spread_source"] == "lookahead_sheet"

    row2 = conn.execute(
        """
        SELECT g.away_team, g.home_team, g.favorite, g.spread_margin FROM game g
        JOIN week w ON w.id = g.week_id
        WHERE w.week_number = 2
        """
    ).fetchone()
    assert row2["away_team"] == "San Francisco"
    assert row2["home_team"] == "Arizona"
    assert row2["favorite"] == "home"  # Arizona favored by 3 at home
    assert row2["spread_margin"] == 3.0


def test_import_lookahead_spreads_never_overwrites_confirmed(conn):
    # Pre-confirm a spread for the week-1 game before importing lookahead data.
    importer.record_game_result(conn, 2026, 1, "Arizona", "LA Chargers", favorite="away", margin=2)
    week_id = db.get_or_create_week(conn, 2026, 1)
    game_id = db.get_or_create_game(conn, week_id, "Arizona", "LA Chargers")
    conn.execute("UPDATE game SET spread_confirmed = 1 WHERE id = ?", (game_id,))
    conn.commit()

    import_lookahead_spreads(conn, 2026, _SPREAD_CSV)

    row = conn.execute("SELECT favorite, spread_margin FROM game WHERE id = ?", (game_id,)).fetchone()
    assert row["favorite"] == "away"
    assert row["spread_margin"] == 2.0  # untouched by the lookahead sheet's 9.5


def test_import_lookahead_spreads_skips_bye_and_tbd(conn):
    csv_text = "Week,1\nArizona Cardinals,BYE\nLos Angeles Chargers,TBD  VS ARI\n"
    count = import_lookahead_spreads(conn, 2026, csv_text)
    assert count == 0


def test_moneyline_to_win_probability_favorite_and_underdog():
    assert moneyline_to_win_probability(-200) == pytest.approx(200 / 300)
    assert moneyline_to_win_probability(150) == pytest.approx(100 / 250)


_MONEYLINE_CSV = """Week,1,2,3
Arizona Cardinals,+380  @LAC,+280  VS SF,-150  @NYG
"""

_SPREAD_CSV_3WK = """Week,1,2,3
Arizona Cardinals,+9.5  @LAC,+12  VS SF,-3  @NYG
Los Angeles Chargers,-9.5  VS ARI,BYE,BYE
San Francisco 49ers,BYE,-12  @ARI,BYE
New York Giants,BYE,BYE,+3  VS ARI
"""


def test_calibrate_from_lookahead_sheets():
    calibration = calibrate_from_lookahead_sheets(_SPREAD_CSV_3WK, _MONEYLINE_CSV, upset_threshold=10.0)
    assert calibration.games_considered == 3
    assert calibration.qualifying_games == 1  # only the +12/-12 week-2 game qualifies
    assert calibration.p_upset_freq == pytest.approx(1 / 3)
    # Arizona is the underdog in the qualifying game (+12, home, VS) with
    # moneyline +280 -> implied probability 100/380.
    assert calibration.p_upset_win == pytest.approx(100 / 380)


def test_calibrate_from_lookahead_sheets_no_games_returns_none():
    assert calibrate_from_lookahead_sheets("Week,1\n", "Week,1\n") is None
