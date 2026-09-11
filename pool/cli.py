"""Command-line entrypoint. Run `python -m pool.cli <command> --help` for
usage on any subcommand.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from . import db, importer, live_data
from .config import PoolConfig
from .export_html import render_report_html
from .profiles import build_competitor_profiles
from .queries import compute_field_reconstruction, compute_my_entry_timeline
from .recommend import build_weekly_recommendations
from .simulation import SimAssumptions


def _read_text(file_arg: Optional[str]) -> str:
    if file_arg:
        return Path(file_arg).read_text()
    print("Paste input, then press Ctrl-D (Ctrl-Z on Windows) when done:", file=sys.stderr)
    return sys.stdin.read()


def cmd_init_db(args):
    db.init_db(args.db)
    print(f"Initialized database at {args.db}")


def cmd_config_show(args):
    conn = db.connect(args.db)
    cfg = PoolConfig.load(conn)
    for k, v in vars(cfg).items():
        print(f"{k}: {v}")
    conn.close()


def cmd_config_set(args):
    conn = db.connect(args.db)
    cfg = PoolConfig.load(conn)
    if args.start_points is not None:
        cfg.start_points = args.start_points
    if args.min_bet is not None:
        cfg.min_bet = args.min_bet
    if args.entry_fee is not None:
        cfg.entry_fee = args.entry_fee
    if args.payouts is not None:
        cfg.payouts = [float(p) for p in args.payouts.split(",")]
    if args.upset_threshold is not None:
        cfg.upset_spread_threshold = args.upset_threshold
    if args.spread_source is not None:
        cfg.spread_source_name = args.spread_source
    if args.counts_favorite_loss is not None:
        cfg.upset_counts_favorite_loss = args.counts_favorite_loss == "yes"
    if args.default_field_size is not None:
        cfg.default_field_size = args.default_field_size
    cfg.save(conn)
    print("Config updated.")
    conn.close()


def cmd_import_standings(args):
    conn = db.connect(args.db)
    text = _read_text(args.file)
    count = importer.import_standings(conn, args.season, args.week, text)
    print(f"Applied standings for {count} entries, week {args.week}.")
    conn.close()


def cmd_import_schedule(args):
    conn = db.connect(args.db)
    text = _read_text(args.file)
    count = importer.import_schedule(conn, args.season, args.week, text)
    print(f"Imported {count} games for week {args.week}.")
    conn.close()


def cmd_import_assignments(args):
    conn = db.connect(args.db)
    text = _read_text(args.file)
    count, errors = importer.import_assignments(conn, args.season, args.week, text)
    print(f"Imported {count} assignments for week {args.week}.")
    for e in errors:
        print(f"  SKIPPED: {e.name} -> {e.team}: {e.reason}", file=sys.stderr)
    conn.close()


def cmd_import_week_csv(args):
    conn = db.connect(args.db)
    text = _read_text(args.file)
    try:
        count = importer.import_week_csv(conn, args.season, args.week, text)
    except ValueError as e:
        print(f"Import failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Imported {count} entries (schedule + assignments + standings) for week {args.week}.")
    conn.close()


def cmd_import_week_sheet(args):
    conn = db.connect(args.db)
    try:
        text = live_data.fetch_google_sheet_csv(args.sheet, gid=args.gid)
    except live_data.LiveDataError as e:
        print(f"Could not fetch the sheet: {e}", file=sys.stderr)
        sys.exit(1)
    try:
        count = importer.import_week_csv(conn, args.season, args.week, text)
    except ValueError as e:
        print(f"Import failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Imported {count} entries from Google Sheet for week {args.week}.")
    conn.close()


def cmd_import_lookahead_spreads(args):
    """Loads a full-season point-spread lookahead grid — for future weeks
    the live Odds API fetch doesn't have real lines for yet. Never
    overwrites a game whose spread is already confirmed.
    """
    conn = db.connect(args.db)
    if args.sheet:
        try:
            text = live_data.fetch_google_sheet_csv(args.sheet, gid=args.gid)
        except live_data.LiveDataError as e:
            print(f"Could not fetch the sheet: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        text = _read_text(args.file)
    count = importer.import_lookahead_spreads(conn, args.season, text)
    print(f"Loaded {count} game-week spreads from the lookahead sheet for season {args.season}.")
    print("These are estimates (spread_source='lookahead_sheet'), not authoritative for 10x qualification.")
    conn.close()


def cmd_calibrate_simulation(args):
    """Derives p_upset_freq / p_upset_win for the Monte Carlo simulator from
    real season-wide spread + moneyline data instead of the fixed defaults,
    and saves them to pool_config.
    """
    conn = db.connect(args.db)
    try:
        if args.spread_sheet:
            spread_text = live_data.fetch_google_sheet_csv(args.spread_sheet)
        else:
            spread_text = Path(args.spread_file).read_text()
        if args.moneyline_sheet:
            moneyline_text = live_data.fetch_google_sheet_csv(args.moneyline_sheet)
        else:
            moneyline_text = Path(args.moneyline_file).read_text()
    except live_data.LiveDataError as e:
        print(f"Could not fetch a sheet: {e}", file=sys.stderr)
        sys.exit(1)

    calibration = importer.calibrate_from_lookahead_sheets(spread_text, moneyline_text)
    if calibration is None:
        print("Could not calibrate — no games found in the spread sheet.", file=sys.stderr)
        sys.exit(1)

    cfg = PoolConfig.load(conn)
    cfg.sim_p_upset_freq = calibration.p_upset_freq
    cfg.sim_p_upset_win = calibration.p_upset_win
    cfg.save(conn)
    print(
        f"Calibrated from {calibration.games_considered} scheduled games "
        f"({calibration.qualifying_games} qualify as 10+-point spreads):"
    )
    print(f"  sim_p_upset_freq = {calibration.p_upset_freq:.3f}")
    print(f"  sim_p_upset_win  = {calibration.p_upset_win:.3f}")
    conn.close()


def cmd_record_result(args):
    conn = db.connect(args.db)
    # Only pass fields that were actually given, so e.g. recording the
    # outcome after the fact doesn't wipe a previously-recorded spread.
    kwargs = {}
    if args.favorite is not None:
        kwargs["favorite"] = args.favorite
    if args.margin is not None:
        kwargs["margin"] = args.margin
    if args.outcome is not None:
        kwargs["outcome"] = args.outcome
    importer.record_game_result(conn, args.season, args.week, args.away, args.home, **kwargs)
    print(f"Recorded result for {args.away} @ {args.home}, week {args.week}.")
    conn.close()


def cmd_fetch_result(args):
    conn = db.connect(args.db)
    try:
        result = live_data.fetch_completed_score(args.away, args.home, api_key=args.api_key)
    except live_data.LiveDataError as e:
        print(f"Could not fetch a result: {e}", file=sys.stderr)
        sys.exit(1)
    importer.record_game_result(
        conn, args.season, args.week, args.away, args.home, outcome=result.outcome
    )
    print(
        f"{args.away} {result.away_score} @ {args.home} {result.home_score} "
        f"[{result.source}] -> outcome recorded as '{result.outcome}'"
    )
    if result.outcome != "tie":
        print(
            "Note: this source reports only the final score (including any overtime), not the "
            "score at the end of regulation. If this game went to OT and was tied after regulation, "
            "the pool rule scores any WIN/LOSS pick as a loss regardless of the OT winner — verify "
            "separately if you know this game went to OT."
        )
    conn.close()


def cmd_record_my_pick(args):
    conn = db.connect(args.db)
    importer.record_my_pick(
        conn,
        args.season,
        args.week,
        args.entry,
        args.away,
        args.home,
        args.side,
        args.pick,
        args.bet,
        is_late_default=args.late_default,
    )
    print(f"Recorded pick for {args.entry}, week {args.week}: {args.pick} {args.bet}.")
    conn.close()


def cmd_fetch_spread(args):
    conn = db.connect(args.db)
    try:
        estimate = live_data.fetch_spread_estimate(args.away, args.home, api_key=args.api_key)
    except live_data.LiveDataError as e:
        print(f"Could not fetch a live spread: {e}", file=sys.stderr)
        sys.exit(1)
    live_data.save_spread_snapshot(conn, args.season, args.week, args.away, args.home, estimate)
    print(
        f"EARLY ESTIMATE (not authoritative for 10x qualification): "
        f"{args.away} @ {args.home} — {estimate.favorite} favored by {estimate.margin} "
        f"[{estimate.source}]"
    )
    cfg = PoolConfig.load(conn)
    print(f"Confirm against {cfg.spread_source_name} before trusting this for upset qualification.")
    conn.close()


def cmd_fetch_my_spreads(args):
    """Fetches an early spread estimate for every game one of 'my' entries
    is assigned to this week — no per-game args needed, since it reads the
    assignments already in the DB. Built for unattended use (e.g. a GitHub
    Action triggered with just --season/--week), not just interactive use.
    """
    conn = db.connect(args.db)
    games = conn.execute(
        """
        SELECT DISTINCT g.away_team, g.home_team
        FROM assignment a
        JOIN entry e ON e.id = a.entry_id
        JOIN game g ON g.id = a.game_id
        WHERE e.is_mine = 1 AND g.week_id = (
            SELECT id FROM week WHERE season_year = ? AND week_number = ?
        )
        """,
        (args.season, args.week),
    ).fetchall()

    if not games:
        print(f"No assignments logged for any of my entries in week {args.week} yet — nothing to fetch.")
        conn.close()
        return

    failures = 0
    for g in games:
        away, home = g["away_team"], g["home_team"]
        try:
            estimate = live_data.fetch_spread_estimate(away, home, api_key=args.api_key)
        except live_data.LiveDataError as e:
            print(f"  {away} @ {home}: could not fetch ({e})", file=sys.stderr)
            failures += 1
            continue
        live_data.save_spread_snapshot(conn, args.season, args.week, away, home, estimate)
        print(f"  {away} @ {home}: {estimate.favorite} favored by {estimate.margin} [{estimate.source}] (early estimate)")

    conn.close()
    if failures:
        sys.exit(1)


def cmd_confirm_spread(args):
    conn = db.connect(args.db)
    cfg = PoolConfig.load(conn)
    source = args.source or cfg.spread_source_name
    live_data.confirm_spread(
        conn, args.season, args.week, args.away, args.home, args.favorite, args.margin, source
    )
    print(f"Confirmed authoritative spread for {args.away} @ {args.home}: {args.favorite} by {args.margin} ({source}).")
    conn.close()


def cmd_timeline(args):
    conn = db.connect(args.db)
    cfg = PoolConfig.load(conn)
    entry_row = conn.execute(
        "SELECT id FROM entry WHERE display_name = ?", (args.entry,)
    ).fetchone()
    if entry_row is None:
        print(f"No entry named '{args.entry}'.", file=sys.stderr)
        sys.exit(1)
    tl = compute_my_entry_timeline(conn, entry_row["id"], cfg)
    print(f"{args.entry} — current points: {tl.current_points}{' (ELIMINATED)' if tl.eliminated else ''}")
    for r in tl.rows:
        after = r.points_after if r.points_after is not None else "pending"
        print(
            f"  wk{r.week_number}: {r.away_team}@{r.home_team} ({r.assigned_side}) "
            f"pick={r.pick} bet={r.bet} -> {after}"
        )
    conn.close()


def cmd_field(args):
    conn = db.connect(args.db)
    cfg = PoolConfig.load(conn)
    rows = compute_field_reconstruction(conn, args.season, args.week, cfg)
    if not rows:
        print(f"No field assignments for week {args.week}.")
    for r in rows:
        cands = ", ".join(f"{c.pick} {c.bet}" for c in r.candidates) or "no clean match"
        print(
            f"{r.entry_name}: {r.away_team}@{r.home_team} ({r.assigned_side}) "
            f"{r.prev_points}->{r.current_points} (delta {r.delta}) :: {cands}"
        )
    conn.close()


def cmd_profiles(args):
    conn = db.connect(args.db)
    profiles = build_competitor_profiles(conn)
    for p in profiles:
        print(
            f"{p.entry_name}: weeks_observed={p.weeks_observed} "
            f"avg_bet_fraction={p.avg_bet_fraction} favorite_bias={p.favorite_bias} "
            f"upset_take_rate={p.upset_take_rate}"
        )
    conn.close()


def cmd_weekly(args):
    """The one weekly command: refresh lines, prompt for anything unfetchable,
    update the DB, print recommendations for all 3 of my entries.
    """
    conn = db.connect(args.db)
    cfg = PoolConfig.load(conn)

    my_entries = conn.execute(
        "SELECT id, display_name FROM entry WHERE is_mine = 1 ORDER BY display_name"
    ).fetchall()

    entry_inputs = []
    for row in my_entries:
        entry_id, name = row["id"], row["display_name"]
        assignment = conn.execute(
            """
            SELECT a.id AS assignment_id, g.id AS game_id, g.away_team, g.home_team,
                   g.favorite, g.spread_margin, a.assigned_side
            FROM assignment a
            JOIN game g ON g.id = a.game_id
            WHERE a.entry_id = ? AND g.week_id = (
                SELECT id FROM week WHERE season_year = ? AND week_number = ?
            )
            """,
            (entry_id, args.season, args.week),
        ).fetchone()

        if assignment is None:
            print(f"\n--- {name}: no assignment logged for week {args.week} yet ---")
            away = input("  Away team: ").strip()
            home = input("  Home team: ").strip()
            side = input(f"  {name} is assigned which side? (home/away): ").strip().lower()
            week_id = db.get_or_create_week(conn, args.season, args.week)
            game_id = db.get_or_create_game(conn, week_id, away, home)
            db.get_or_create_assignment(conn, entry_id, game_id, side)
            conn.commit()
            favorite, margin = None, 0
        else:
            away, home, side = assignment["away_team"], assignment["home_team"], assignment["assigned_side"]
            favorite, margin = assignment["favorite"], assignment["spread_margin"]

        if favorite is None and margin == 0:
            try:
                estimate = live_data.fetch_spread_estimate(away, home, api_key=args.api_key)
                print(
                    f"  {name}: fetched EARLY ESTIMATE {away}@{home} -> "
                    f"{estimate.favorite} favored by {estimate.margin} [{estimate.source}]"
                )
                live_data.save_spread_snapshot(conn, args.season, args.week, away, home, estimate)
                favorite = None if estimate.favorite == "even" else estimate.favorite
                margin = estimate.margin
            except live_data.LiveDataError as e:
                print(f"  {name}: could not fetch a live spread ({e})")
                manual = input(
                    f"  Enter spread manually for {away}@{home} as 'home <margin>' / "
                    f"'away <margin>' / blank to skip (pick'em): "
                ).strip()
                if manual:
                    parts = manual.split()
                    if len(parts) == 2:
                        favorite, margin = parts[0], float(parts[1])

        timeline = compute_my_entry_timeline(conn, entry_id, cfg)
        current_points = timeline.current_points
        spread = margin if favorite != side else -margin
        entry_inputs.append(
            {
                "name": name,
                "current_points": current_points,
                "away": away,
                "home": home,
                "side": side,
                "spread": spread,
            }
        )

    field_count_row = conn.execute(
        """
        SELECT COUNT(DISTINCT entry_id) AS n FROM entry_week_points ewp
        JOIN week w ON w.id = ewp.week_id WHERE w.season_year = ?
        """,
        (args.season,),
    ).fetchone()
    field_size = max(field_count_row["n"] or 0, cfg.default_field_size)

    assumptions = SimAssumptions(
        weeks_remaining=max(1, 18 - args.week),
        field_size=field_size,
        runs=args.runs,
        min_bet=cfg.min_bet,
        start_points=cfg.start_points,
        p_win=cfg.sim_p_win,
        p_upset_freq=cfg.sim_p_upset_freq,
        p_upset_win=cfg.sim_p_upset_win,
    )
    recs = build_weekly_recommendations(
        entry_inputs, assumptions, cfg.payouts, cfg.entry_fee, aggression=args.aggression,
        upset_threshold=cfg.upset_spread_threshold,
        upset_multiplier=cfg.upset_multiplier,
        counts_favorite_loss=cfg.upset_counts_favorite_loss,
    )

    print(f"\n=== Week {args.week} recommendations ===")
    for e, rec in zip(entry_inputs, recs):
        print(f"\n{rec.entry_name} — {e['away']} @ {e['home']} (you: {e['side']}), spread {e['spread']}")
        print(f"  Current points: {rec.current_points}")
        print(f"  Recommended pick: {rec.recommended_pick}")
        print(f"  Recommended bet: {rec.recommended_bet}")
        print(f"  Reasoning: {rec.recommended_pick_reasoning}")
        s = rec.sim_result
        print(
            f"  Simulated under this policy: P(1st)={s.p_first:.1%} P(top3)={s.p_top3:.1%} "
            f"P(top10)={s.p_top10:.1%} Expected payout=${s.avg_payout_dollars:.2f}"
        )
    conn.close()


def cmd_export_html(args):
    conn = db.connect(args.db)
    cfg = PoolConfig.load(conn)
    html_text = render_report_html(
        conn,
        cfg,
        season=args.season,
        week=args.week,
        include_field_names=args.include_field_names,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_text)
    print(f"Wrote static report to {out_path}")
    if not args.include_field_names:
        print("(field entry names/points omitted per --no-field-names)")
    conn.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pool", description="Office-Pool-4-Fun strategy console")
    p.add_argument("--db", default=str(db.DEFAULT_DB_PATH), help="Path to the SQLite database")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init-db", help="Create/initialize the database")
    sp.set_defaults(func=cmd_init_db)

    sp = sub.add_parser("config-show", help="Show current pool config")
    sp.set_defaults(func=cmd_config_show)

    sp = sub.add_parser("config-set", help="Update pool config")
    sp.add_argument("--start-points", type=float)
    sp.add_argument("--min-bet", type=float)
    sp.add_argument("--entry-fee", type=float)
    sp.add_argument("--payouts", help="Comma-separated percentages, e.g. 40,18,10,9,7,6,4,3,2,1")
    sp.add_argument("--upset-threshold", type=float)
    sp.add_argument("--spread-source")
    sp.add_argument("--counts-favorite-loss", choices=["yes", "no"])
    sp.add_argument("--default-field-size", type=int)
    sp.set_defaults(func=cmd_config_set)

    for name, help_, fn in [
        ("import-standings", "Import a standings paste ('Name, Points' per line)", cmd_import_standings),
        ("import-schedule", "Import a schedule paste ('Away, Home' per line)", cmd_import_schedule),
        ("import-assignments", "Import assignments ('Name, Team' per line)", cmd_import_assignments),
    ]:
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--season", type=int, required=True)
        sp.add_argument("--week", type=int, required=True)
        sp.add_argument("--file", help="Read from this file instead of stdin")
        sp.set_defaults(func=fn)

    sp = sub.add_parser(
        "import-week",
        help="One-shot import matching the pool's own weekly export "
        "(header: Rank,Team Name,Total Pts,Away,Home)",
    )
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.add_argument("--file", help="Read from this file instead of stdin")
    sp.set_defaults(func=cmd_import_week_csv)

    sp = sub.add_parser(
        "import-week-sheet",
        help="Same as import-week, but fetched live from a public Google Sheet "
        "(must be shared 'Anyone with the link can view')",
    )
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.add_argument("--sheet", required=True, help="Sheet ID or full share URL")
    sp.add_argument("--gid", default=None, help="Specific tab's gid, if not the first tab")
    sp.set_defaults(func=cmd_import_week_sheet)

    sp = sub.add_parser(
        "import-lookahead-spreads",
        help="Load a full-season point-spread grid (one row per team, one column per "
        "week) for future weeks the live Odds API doesn't have real lines for yet",
    )
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--sheet", default=None, help="Sheet ID or full share URL")
    sp.add_argument("--gid", default=None, help="Specific tab's gid, if not the first tab")
    sp.add_argument("--file", default=None, help="Local CSV file instead of --sheet")
    sp.set_defaults(func=cmd_import_lookahead_spreads)

    sp = sub.add_parser(
        "calibrate-simulation",
        help="Derive Monte Carlo p_upset_freq/p_upset_win from real season-wide spread "
        "+ moneyline data instead of fixed guesses, and save to config",
    )
    sp.add_argument("--spread-sheet", default=None, help="Spread lookahead sheet ID or URL")
    sp.add_argument("--spread-file", default=None, help="Local spread CSV instead of --spread-sheet")
    sp.add_argument("--moneyline-sheet", default=None, help="Moneyline lookahead sheet ID or URL")
    sp.add_argument("--moneyline-file", default=None, help="Local moneyline CSV instead of --moneyline-sheet")
    sp.set_defaults(func=cmd_calibrate_simulation)

    sp = sub.add_parser("record-result", help="Record a game's spread and/or outcome")
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.add_argument("--away", required=True)
    sp.add_argument("--home", required=True)
    sp.add_argument(
        "--favorite", choices=["home", "away"], default=None,
        help="Omit to leave the currently recorded favorite unchanged",
    )
    sp.add_argument(
        "--margin", type=float, default=None,
        help="Omit to leave the currently recorded margin unchanged (pass 0 explicitly for pick'em)",
    )
    sp.add_argument(
        "--outcome", choices=["home", "away", "tie"], default=None,
        help="Omit to leave the currently recorded outcome unchanged",
    )
    sp.set_defaults(func=cmd_record_result)

    sp = sub.add_parser("fetch-result", help="Fetch a completed game's final score and record its outcome")
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.add_argument("--away", required=True)
    sp.add_argument("--home", required=True)
    sp.add_argument("--api-key", default=None)
    sp.set_defaults(func=cmd_fetch_result)

    sp = sub.add_parser("record-my-pick", help="Record one of my entries' pick/bet for a week")
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.add_argument("--entry", required=True)
    sp.add_argument("--away", required=True)
    sp.add_argument("--home", required=True)
    sp.add_argument("--side", choices=["home", "away"], required=True)
    sp.add_argument("--pick", choices=["WIN", "LOSS", "TIE"], required=True)
    sp.add_argument("--bet", type=float, required=True)
    sp.add_argument("--late-default", action="store_true")
    sp.set_defaults(func=cmd_record_my_pick)

    sp = sub.add_parser("fetch-spread", help="Fetch a live spread estimate for one game")
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.add_argument("--away", required=True)
    sp.add_argument("--home", required=True)
    sp.add_argument("--api-key", default=None)
    sp.set_defaults(func=cmd_fetch_spread)

    sp = sub.add_parser(
        "fetch-my-spreads",
        help="Fetch early spread estimates for every game any 'my' entry is assigned to "
        "this week, no per-game args needed — built for unattended/scripted use",
    )
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.add_argument("--api-key", default=None)
    sp.set_defaults(func=cmd_fetch_my_spreads)

    sp = sub.add_parser("confirm-spread", help="Record a manually-confirmed, authoritative spread")
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.add_argument("--away", required=True)
    sp.add_argument("--home", required=True)
    sp.add_argument("--favorite", choices=["home", "away"], default=None)
    sp.add_argument("--margin", type=float, required=True)
    sp.add_argument("--source", default=None)
    sp.set_defaults(func=cmd_confirm_spread)

    sp = sub.add_parser("timeline", help="Show one entry's week-by-week timeline")
    sp.add_argument("--entry", required=True)
    sp.set_defaults(func=cmd_timeline)

    sp = sub.add_parser("field", help="Show field reconstruction for a week")
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.set_defaults(func=cmd_field)

    sp = sub.add_parser("profiles", help="Show thin competitor profiles")
    sp.set_defaults(func=cmd_profiles)

    sp = sub.add_parser("export-html", help="Render a static HTML snapshot (e.g. for GitHub Pages)")
    sp.add_argument("--season", type=int, default=None)
    sp.add_argument("--week", type=int, default=None)
    sp.add_argument("--out", default="docs/index.html")
    sp.add_argument(
        "--no-field-names",
        dest="include_field_names",
        action="store_false",
        default=True,
        help="Omit per-entry field standings/reconstruction, showing only aggregate counts "
        "(full field names/points are included by default — note this is public info about "
        "other pool members if the export is published somewhere public)",
    )
    sp.set_defaults(func=cmd_export_html)

    sp = sub.add_parser("weekly", help="Weekly workflow: refresh lines, prompt, recommend")
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.add_argument("--api-key", default=None)
    sp.add_argument("--aggression", type=float, default=50, help="0=conservative .. 100=aggressive")
    sp.add_argument("--runs", type=int, default=1500)
    sp.set_defaults(func=cmd_weekly)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
