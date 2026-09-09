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
    # "SPM" matches one of the pre-seeded is_mine entries from db.init_db —
    # using any other name here would silently create a new field entry
    # instead of attaching to the real "my entry" (see db.get_or_create_entry's
    # global-by-display-name lookup).
    importer.import_schedule(conn, 2026, 1, "Atlanta, Pittsburgh\n")
    importer.import_assignments(conn, 2026, 1, "SPM, Pittsburgh\nAlways Hot, Atlanta\n")
    importer.record_game_result(
        conn, 2026, 1, "Atlanta", "Pittsburgh", favorite="away", margin=12, outcome="home"
    )
    importer.import_standings(conn, 2026, 1, "Always Hot, 350\n")


def test_log_table_pending_pick_renders_dash_not_double_escaped(conn):
    # SPM has an assignment but no recorded pick/bet yet (pending) — the
    # em-dash placeholder must render as an actual entity, not literal
    # "&mdash;" text from being passed through html.escape() twice.
    _seed(conn)
    cfg = PoolConfig.load(conn)
    out = render_report_html(conn, cfg, season=2026, week=1)
    assert "&amp;mdash;" not in out
    assert "&mdash;" in out


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
    assert "SPM" in out
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
    # Never appears verbatim (would execute as HTML markup or terminate the
    # embedded JSON <script> tag early).
    assert "<script>alert(1)</script>" not in out
    # HTML-context: the field table must escape it properly.
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    # JSON-context: the embedded scenario-projector payload must use the
    # "<\/script>" escape rather than a literal "</script>" sequence.
    assert '<script>alert(1)<\\/script>' in out


def test_scenario_projector_included_by_default(conn):
    _seed(conn)
    cfg = PoolConfig.load(conn)
    out = render_report_html(conn, cfg, season=2026, week=1)
    assert "Scenario Projector" in out
    assert '"name": "SPM"' in out
    assert '"name": "Always Hot"' in out
    assert '"away": "Atlanta"' in out


def test_scenario_projector_respects_no_field_names(conn):
    _seed(conn)
    cfg = PoolConfig.load(conn)
    out = render_report_html(conn, cfg, season=2026, week=1, include_field_names=False)
    assert '"name": "SPM"' in out
    assert '"name": "Always Hot"' not in out


def test_scenario_projector_tab_shows_placeholder_without_week(conn):
    # The tab itself is always present (it's a nav button, not conditional
    # content) — without a season/week there's just nothing to project yet.
    _seed(conn)
    cfg = PoolConfig.load(conn)
    out = render_report_html(conn, cfg)
    assert "No assignments logged for this week yet" in out
    assert '"name": "SPM"' not in out
