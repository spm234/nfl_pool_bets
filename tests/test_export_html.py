import pytest

from pool import db, importer
from pool.config import PoolConfig
from pool.export_html import render_report_html


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    c = db.connect(db_path)
    yield c
    c.close()


def _seed(conn):
    importer.import_schedule(conn, 2026, 1, "Atlanta, Pittsburgh\n")
    importer.import_assignments(conn, 2026, 1, "My Entry 1, Pittsburgh\nAlways Hot, Atlanta\n")
    importer.record_game_result(
        conn, 2026, 1, "Atlanta", "Pittsburgh", favorite="away", margin=12, outcome="home"
    )
    importer.import_standings(conn, 2026, 1, "Always Hot, 350\n")


def test_render_report_no_args_does_not_crash(conn):
    cfg = PoolConfig.load(conn)
    out = render_report_html(conn, cfg)
    assert "Office-Pool-4-Fun" in out
    assert "<html" in out


def test_render_report_includes_field_names_by_default(conn):
    _seed(conn)
    cfg = PoolConfig.load(conn)
    out = render_report_html(conn, cfg, season=2026, week=1)
    assert "Always Hot" in out


def test_render_report_can_exclude_field_names(conn):
    _seed(conn)
    cfg = PoolConfig.load(conn)
    out = render_report_html(conn, cfg, season=2026, week=1, include_field_names=False)
    assert "Always Hot" not in out
    assert "omitted" in out


def test_render_report_includes_my_entries_and_scenarios(conn):
    _seed(conn)
    cfg = PoolConfig.load(conn)
    out = render_report_html(conn, cfg, season=2026, week=1)
    assert "My Entry 1" in out
    assert "Scenario lab" in out
    assert "Aggressive" in out


def test_render_report_escapes_html_in_names(conn):
    # Must have an assignment (not just standings) to actually appear in the
    # field reconstruction table — otherwise this test would pass trivially
    # without ever exercising the escaping path.
    importer.import_schedule(conn, 2026, 1, "Atlanta, Pittsburgh\n")
    importer.import_assignments(
        conn, 2026, 1, "<script>alert(1)</script>, Atlanta\n"
    )
    importer.record_game_result(
        conn, 2026, 1, "Atlanta", "Pittsburgh", favorite="away", margin=12, outcome="home"
    )
    importer.import_standings(conn, 2026, 1, "<script>alert(1)</script>, 350\n")
    cfg = PoolConfig.load(conn)
    out = render_report_html(conn, cfg, season=2026, week=1, include_field_names=True)
    assert "<script>alert" not in out
    assert "&lt;script&gt;" in out
