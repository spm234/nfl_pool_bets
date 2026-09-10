"""Command-line entrypoint for the Loser Pool tool. Run
`python -m loser_pool.cli <command> --help` for usage on any subcommand.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from . import db, importer, live_data, picks
from .config import LoserPoolConfig
from .optimizer import full_season_plan, recommend_week
from .sheets_sync import parse_pool_sheet_csv, pick_ownership, sync_picks_from_sheet


def _read_text(file_arg: Optional[str]) -> str:
    if file_arg:
        return Path(file_arg).read_text()
    print("Paste input, then press Ctrl-D (Ctrl-Z on Windows) when done:", file=sys.stderr)
    return sys.stdin.read()


def _require_entry(conn, name: str):
    entry = db.get_entry_by_name(conn, name)
    if entry is None:
        print(f"No entry named '{name}'. Use add-entry first.", file=sys.stderr)
        sys.exit(1)
    return entry


def cmd_init_db(args):
    db.init_db(args.db)
    print(f"Initialized database at {args.db}")


def cmd_config_show(args):
    conn = db.connect(args.db)
    cfg = LoserPoolConfig.load(conn)
    for k, v in vars(cfg).items():
        print(f"{k}: {v}")
    conn.close()


def cmd_config_set(args):
    conn = db.connect(args.db)
    cfg = LoserPoolConfig.load(conn)
    if args.buy_in is not None:
        cfg.buy_in = args.buy_in
    if args.lives_per_entry is not None:
        cfg.lives_per_entry = args.lives_per_entry
    if args.tie_treated_as is not None:
        cfg.tie_treated_as = args.tie_treated_as
    if args.margin_std_dev is not None:
        cfg.margin_std_dev = args.margin_std_dev
    if args.elo_home_advantage is not None:
        cfg.elo_home_advantage = args.elo_home_advantage
    if args.elo_k_factor is not None:
        cfg.elo_k_factor = args.elo_k_factor
    cfg.save(conn)
    print("Config updated.")
    conn.close()


def cmd_add_entry(args):
    conn = db.connect(args.db)
    cfg = LoserPoolConfig.load(conn)
    entry_id = db.get_or_create_entry(
        conn, owner_name=args.owner or args.name, display_name=args.name,
        is_mine=args.mine, lives_per_entry=cfg.lives_per_entry,
    )
    conn.commit()
    print(f"Entry '{args.name}' -> id {entry_id}, lives={cfg.lives_per_entry}")
    conn.close()


def cmd_import_schedule(args):
    conn = db.connect(args.db)
    text = _read_text(args.file)
    count = importer.import_schedule(conn, args.season, args.week, text, is_playoffs=args.playoffs)
    print(f"Imported {count} games for week {args.week}.")
    conn.close()


def cmd_import_win_totals(args):
    conn = db.connect(args.db)
    text = _read_text(args.file)
    count = importer.import_win_totals(conn, args.season, args.week, text)
    print(f"Seeded Elo ratings for {count} teams from win totals, as of week {args.week}.")
    conn.close()


def cmd_import_elo_ratings(args):
    conn = db.connect(args.db)
    text = _read_text(args.file)
    count = importer.import_elo_ratings(conn, args.season, args.week, text)
    print(f"Set Elo ratings for {count} teams, as of week {args.week}.")
    conn.close()


def cmd_fetch_spread(args):
    conn = db.connect(args.db)
    try:
        estimate = live_data.fetch_and_save_spread(
            conn, args.season, args.week, args.away, args.home, api_key=args.api_key
        )
    except live_data.LiveDataError as e:
        print(f"Could not fetch a live spread: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"{args.away} @ {args.home} — {estimate.favorite} favored by {estimate.margin} [{estimate.source}]")
    conn.close()


def cmd_record_spread(args):
    conn = db.connect(args.db)
    importer.record_game_result(
        conn, args.season, args.week, args.away, args.home,
        favorite=args.favorite, margin=args.margin, spread_source=args.source or "manual",
    )
    print(f"Recorded spread for {args.away} @ {args.home}: {args.favorite or 'even'} by {args.margin}.")
    conn.close()


def cmd_fetch_result(args):
    conn = db.connect(args.db)
    try:
        result = live_data.fetch_and_save_result(
            conn, args.season, args.week, args.away, args.home, api_key=args.api_key
        )
    except live_data.LiveDataError as e:
        print(f"Could not fetch a result: {e}", file=sys.stderr)
        sys.exit(1)
    print(
        f"{args.away} {result.away_score} @ {args.home} {result.home_score} "
        f"[{result.source}] -> outcome '{result.outcome}'"
    )
    conn.close()


def cmd_record_result(args):
    conn = db.connect(args.db)
    importer.record_game_result(
        conn, args.season, args.week, args.away, args.home,
        outcome=args.outcome, home_score=args.home_score, away_score=args.away_score,
    )
    if args.home_score is not None and args.away_score is not None:
        from .ratings import apply_elo_after_result
        apply_elo_after_result(conn, args.season, args.week, args.away, args.home)
    print(f"Recorded result for {args.away} @ {args.home}, week {args.week}: {args.outcome}.")
    conn.close()


def cmd_record_pick(args):
    conn = db.connect(args.db)
    try:
        picks.record_pick(
            conn, args.season, args.week, args.entry, args.team,
            owner_name=args.owner, is_mine=args.mine,
        )
    except picks.PickError as e:
        print(f"Could not record pick: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Recorded {args.entry}'s week {args.week} pick: {args.team} to lose.")
    conn.close()


def cmd_settle_week(args):
    conn = db.connect(args.db)
    results = picks.settle_week(conn, args.season, args.week)
    if not results:
        print("No pending picks with a recorded game outcome to settle.")
    for r in results:
        tag = "ELIMINATED" if r.eliminated else f"{r.lives_remaining} live(s) left"
        print(f"{r.entry_name}: {r.team_picked} -> {r.outcome} ({tag})")
    conn.close()


def cmd_playoff_reset(args):
    conn = db.connect(args.db)
    picks.trigger_playoff_reset(conn, args.after_week, note=args.note or "")
    print(
        f"Playoff reset recorded: picks after week {args.after_week} no longer count as "
        "'used' for team-reuse purposes."
    )
    conn.close()


def cmd_import_sheet_picks(args):
    conn = db.connect(args.db)
    text = _read_text(args.file)
    result = sync_picks_from_sheet(conn, args.season, text)
    print(f"Applied {result.applied} picks ({result.skipped_blank} blank cells skipped).")
    for e in result.errors:
        print(f"  SKIPPED: {e.entry} / {e.period} -> {e.team}: {e.reason}", file=sys.stderr)
    conn.close()


def cmd_sheet_ownership(args):
    text = _read_text(args.file)
    rows = parse_pool_sheet_csv(text)
    ownership = pick_ownership(rows, args.period)
    if not ownership:
        print(f"No picks recorded yet for '{args.period}'.")
    for o in ownership:
        print(
            f"{o.team}: {o.count} picks — {o.fraction_of_submitted:.1%} of submitted, "
            f"{o.fraction_of_all_entries:.1%} of full field"
        )
    print(f"({len(rows)} entries on the sheet)")


def cmd_status(args):
    conn = db.connect(args.db)
    if args.entry:
        entries = [_require_entry(conn, args.entry)]
    else:
        entries = conn.execute("SELECT * FROM lp_entry ORDER BY display_name").fetchall()
    for e in entries:
        used = sorted(picks.used_teams(conn, e["id"], args.season)) if args.season else []
        status = f"ELIMINATED (week {e['eliminated_week']})" if e["eliminated"] else f"{e['lives_remaining']} live(s)"
        print(f"{e['display_name']}: {status}" + (f" — used: {', '.join(used)}" if used else ""))
    conn.close()


def cmd_recommend(args):
    conn = db.connect(args.db)
    entry = _require_entry(conn, args.entry)
    remaining = list(range(args.week, args.through_week + 1)) if args.through_week else None
    recs = recommend_week(
        conn, args.season, args.week, entry["id"],
        remaining_week_numbers=remaining, sim_runs=args.runs,
    )
    if not recs:
        print("No available (unused) teams with a game this week.")
    for r in recs:
        loc = "home" if r.is_home else "away"
        line = f"{r.team} ({loc} vs {r.opponent}): P(loses)={r.p_lose:.1%}"
        if r.p_survive_season_if_picked is not None:
            line += f", P(survive through wk{args.through_week} incl. lives)={r.p_survive_season_if_picked:.1%}"
        print(line)
    conn.close()


def cmd_full_plan(args):
    conn = db.connect(args.db)
    entry = _require_entry(conn, args.entry)
    from .picks import used_teams as _used
    exclude = _used(conn, entry["id"], args.season)
    weeks = list(range(args.from_week, args.through_week + 1))
    plan = full_season_plan(conn, args.season, weeks, exclude_teams=exclude)
    if not plan:
        print("No feasible plan (no games/teams available across that range).")
    for opt in plan:
        loc = "home" if opt.is_home else "away"
        print(f"wk{opt.week_number}: {opt.team} ({loc} vs {opt.opponent}) — P(loses)={opt.p_lose:.1%}")
    conn.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="loser_pool", description="Loser Pool strategy console")
    p.add_argument("--db", default=str(db.DEFAULT_DB_PATH), help="Path to the SQLite database")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init-db", help="Create/initialize the database")
    sp.set_defaults(func=cmd_init_db)

    sp = sub.add_parser("config-show", help="Show current pool config")
    sp.set_defaults(func=cmd_config_show)

    sp = sub.add_parser("config-set", help="Update pool config")
    sp.add_argument("--buy-in", type=float)
    sp.add_argument("--lives-per-entry", type=int)
    sp.add_argument("--tie-treated-as", choices=["bust", "survive"])
    sp.add_argument("--margin-std-dev", type=float)
    sp.add_argument("--elo-home-advantage", type=float)
    sp.add_argument("--elo-k-factor", type=float)
    sp.set_defaults(func=cmd_config_set)

    sp = sub.add_parser("add-entry", help="Add/register a pool entry")
    sp.add_argument("--name", required=True)
    sp.add_argument("--owner", default=None)
    sp.add_argument("--mine", action="store_true")
    sp.set_defaults(func=cmd_add_entry)

    sp = sub.add_parser("import-schedule", help="Import a schedule paste ('Away, Home' per line)")
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.add_argument("--file", help="Read from this file instead of stdin")
    sp.add_argument("--playoffs", action="store_true", help="Mark this week as a playoff week")
    sp.set_defaults(func=cmd_import_schedule)

    sp = sub.add_parser(
        "import-win-totals",
        help="Seed Elo ratings from a projected-win-totals paste ('Team, WinTotal' per line)",
    )
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True, help="First week these ratings apply as-of")
    sp.add_argument("--file")
    sp.set_defaults(func=cmd_import_win_totals)

    sp = sub.add_parser(
        "import-elo-ratings", help="Seed literal Elo ratings paste ('Team, Rating' per line)"
    )
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.add_argument("--file")
    sp.set_defaults(func=cmd_import_elo_ratings)

    sp = sub.add_parser("fetch-spread", help="Fetch a live spread estimate for one game")
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.add_argument("--away", required=True)
    sp.add_argument("--home", required=True)
    sp.add_argument("--api-key", default=None)
    sp.set_defaults(func=cmd_fetch_spread)

    sp = sub.add_parser("record-spread", help="Manually record a game's spread")
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.add_argument("--away", required=True)
    sp.add_argument("--home", required=True)
    sp.add_argument("--favorite", choices=["home", "away"], default=None)
    sp.add_argument("--margin", type=float, required=True)
    sp.add_argument("--source", default=None)
    sp.set_defaults(func=cmd_record_spread)

    sp = sub.add_parser("fetch-result", help="Fetch a completed game's score and record it")
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.add_argument("--away", required=True)
    sp.add_argument("--home", required=True)
    sp.add_argument("--api-key", default=None)
    sp.set_defaults(func=cmd_fetch_result)

    sp = sub.add_parser("record-result", help="Manually record a game's outcome/score")
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.add_argument("--away", required=True)
    sp.add_argument("--home", required=True)
    sp.add_argument("--outcome", choices=["home", "away", "tie"], required=True)
    sp.add_argument("--home-score", type=int, default=None)
    sp.add_argument("--away-score", type=int, default=None)
    sp.set_defaults(func=cmd_record_result)

    sp = sub.add_parser("record-pick", help="Record an entry's pick-to-lose for a week")
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.add_argument("--entry", required=True)
    sp.add_argument("--team", required=True)
    sp.add_argument("--owner", default=None)
    sp.add_argument("--mine", action="store_true")
    sp.set_defaults(func=cmd_record_pick)

    sp = sub.add_parser("settle-week", help="Settle all pending picks with a recorded game outcome")
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.set_defaults(func=cmd_settle_week)

    sp = sub.add_parser("playoff-reset", help="Trigger the 'all teams reset' playoff rule")
    sp.add_argument("--after-week", type=int, required=True)
    sp.add_argument("--note", default=None)
    sp.set_defaults(func=cmd_playoff_reset)

    sp = sub.add_parser(
        "import-sheet-picks",
        help="Bulk-apply picks from the pool operator's Google Sheet CSV export "
        "(name row, one column per week/playoff round — see README)",
    )
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--file", help="Read from this file instead of stdin")
    sp.set_defaults(func=cmd_import_sheet_picks)

    sp = sub.add_parser(
        "sheet-ownership",
        help="Field pick-ownership % for one period, straight from the sheet CSV (no DB needed)",
    )
    sp.add_argument("--file", help="Read from this file instead of stdin")
    sp.add_argument("--period", required=True, help="e.g. 'Week 1' or 'Wild Card'")
    sp.set_defaults(func=cmd_sheet_ownership)

    sp = sub.add_parser("status", help="Show entry lives/elimination/used-teams")
    sp.add_argument("--entry", default=None, help="Omit to show all entries")
    sp.add_argument("--season", type=int, default=None, help="Needed to show used teams")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("recommend", help="Recommend this week's pick(s) for an entry")
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--week", type=int, required=True)
    sp.add_argument("--entry", required=True)
    sp.add_argument(
        "--through-week", type=int, default=None,
        help="Include season-survival simulation out through this week (omit for a quick single-week ranking)",
    )
    sp.add_argument("--runs", type=int, default=2000)
    sp.set_defaults(func=cmd_recommend)

    sp = sub.add_parser(
        "full-plan", help="Theoretical best single-life plan across a week range (reference only)"
    )
    sp.add_argument("--season", type=int, required=True)
    sp.add_argument("--entry", required=True)
    sp.add_argument("--from-week", type=int, required=True)
    sp.add_argument("--through-week", type=int, required=True)
    sp.set_defaults(func=cmd_full_plan)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
