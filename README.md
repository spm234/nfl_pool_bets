# Office-Pool-4-Fun Strategy Console

A local Python port of the browser-based strategy prototype, with persistent
SQLite storage and a scheduled weekly workflow. The scoring, reconstruction,
and simulation logic is ported 1:1 from the original JS (`pool/scoring.py`,
`pool/reconstruction.py`, `pool/simulation.py`) — it is not re-derived from
the plain-English pool rules, since scoring-rule bugs are the biggest risk
in a tool like this.

## Setup

```
pip install -r requirements.txt
python -m pool.cli init-db
```

This creates `pool.db` in the project root (override with `--db path`) and
seeds your 3 entries (SPM, SPM 2, SPM 3 under owner "Me" — pass
`my_entry_names` to `db.init_db` if you want different names).

Run tests with:

```
python -m pytest
```

## Architecture: three layers, kept separate

Per the original design goal, raw observations, inferred data, and
recommendations are three distinct layers, not flattened together:

1. **Raw observations** — `game` (posted results/spread), `entry_week_points`
   (posted standings, same shape as the manual paste box: "Name, Points" per
   week), `market_snapshot` (fetched spread estimates), `wager_observation`
   (my own declared picks/bets — I know these directly, they aren't inferred).
2. **Inferred data** — `wager_inference`: reconstructed (pick, bet)
   hypotheses for field entries, computed from point deltas via
   `reconstruct_candidates`, with a confidence ranking.
3. **Recommendations** — computed on demand by the simulation layer
   (`pool/recommend.py`, `pool/simulation.py`), never persisted.

See `sql/schema.sql` for the full schema and column-level notes.

## A bug found in the reference prototype

The browser prototype's field-reconstruction code computed which side (home
or away) a field entry held with `assignments[name].home === home`, which is
always true by construction — both sides of that comparison come from the
same object. So every field entry was silently treated as having been
assigned the home team, which is wrong whenever two entries are assigned to
opposite sides of the same game (a common case, since assignment is
random). This port fixes it by making `assigned_side` an explicit, required
column on `assignment`, resolved from the actual team name at import time
(see `import_assignments` in `pool/importer.py`) rather than guessed.

## CLI usage

All commands take `--db path/to/pool.db` (defaults to `./pool.db`).

```
# Config (all pool rules are config-driven — see PoolConfig)
python -m pool.cli config-show
python -m pool.cli config-set --min-bet 20 --entry-fee 30 \
    --payouts 40,18,10,9,7,6,4,3,2,1

# One-shot weekly import matching the pool's own export shape exactly
# (header: Rank,Team Name,Total Pts,Away,Home) — schedule + assignments +
# standings in a single pass. Every entry is assigned the AWAY team
# (confirmed: that's how this pool actually works — WIN/LOSS/TIE is your
# bet on that team, there's no separate "pick the home team instead").
python -m pool.cli import-week --season 2026 --week 1 --file week1.csv

# Or piece by piece, if your data doesn't come as one file:
python -m pool.cli import-schedule --season 2026 --week 1 --file schedule.txt
python -m pool.cli import-assignments --season 2026 --week 1 --file assignments.txt
python -m pool.cli import-standings --season 2026 --week 1 --file standings.txt

# Record a settled game's spread and/or outcome (only the fields you pass
# are changed — recording the outcome later won't wipe an earlier spread)
python -m pool.cli record-result --season 2026 --week 1 \
    --away Atlanta --home Pittsburgh --favorite away --margin 3 --outcome home

# Fetch a completed game's final score and record its outcome automatically
python -m pool.cli fetch-result --season 2026 --week 1 --away Atlanta --home Pittsburgh

# Record one of my own picks
python -m pool.cli record-my-pick --season 2026 --week 1 --entry SPM \
    --away Atlanta --home Pittsburgh --side away --pick WIN --bet 30

# Live spread estimate (never authoritative for 10x qualification — see below)
python -m pool.cli fetch-spread --season 2026 --week 1 --away Atlanta --home Pittsburgh

# Manually confirmed, authoritative spread (only path that can be used for
# 10x-upset qualification)
python -m pool.cli confirm-spread --season 2026 --week 1 \
    --away Atlanta --home Pittsburgh --favorite away --margin 10 \
    --source "Cleveland Plain Dealer, 2026-09-11"

python -m pool.cli timeline --entry SPM
python -m pool.cli field --season 2026 --week 1
python -m pool.cli profiles

# The one weekly command: refreshes lines, prompts for anything it
# couldn't fetch, updates the DB, prints all 3 entries' recommendations.
python -m pool.cli weekly --season 2026 --week 1
```

Schedule `weekly` with cron for a hands-off Thursday/Friday check, e.g.:

```
0 10 * * 4 cd /path/to/nfl_pool_bets && THE_ODDS_API_KEY=... python -m pool.cli weekly --season 2026 --week $(date +%V) >> weekly.log 2>&1
```

(Week-number math depends on your season's actual week 1 date — this isn't
wired up automatically since NFL week numbers don't line up with ISO weeks;
pass `--week` explicitly if the log ever looks off.)

## Publishing a static snapshot (GitHub Pages)

`pool export-html` renders a tabbed static page — no server, no live DB
access. This is a **snapshot**, not a live view: rerun the command and push
whenever you want the published page to reflect the current state.

- **Overview** — scoreboard strip for your 3 entries.
- **My Entries** — game log + this week's recommended pick per entry.
- **Field** — the full field's standings/reconstruction (or aggregate
  counts only, see `--no-field-names` below).
- **Scenario Projector** — pick a hypothetical outcome per game and a
  generalized field bet %, and the whole real field's projected standings
  recompute instantly, entirely in the browser (no server round-trip).
  Your 3 entries get individual pick/wager controls; the field uses one
  shared bet-fraction slider assumed to bet WIN on their assigned team.
- **Simulation** — the Monte Carlo P(1st)/top3/top10/expected-payout
  comparison across four bet-sizing policies, for all 3 entries together.

```
python -m pool.cli export-html --season 2026 --week 3 --out docs/index.html
git add docs/index.html && git commit -m "Update weekly snapshot" && git push
```

By default the export **includes the full field standings/reconstruction**
(other pool members' names and points) — pass `--no-field-names` to reduce
it to aggregate counts only if you'd rather not publish that.

**Important**: GitHub Pages sites are public URLs by anyone with the link,
even when published from a private repository — GitHub only restricts Pages
visibility to org members on Enterprise Cloud. This repo is private, but the
published page itself won't be access-controlled unless you're on that plan.

To turn on Pages for this repo (one-time, done in the GitHub UI — there's no
API path wired into this tool for it):

1. On GitHub: **Settings → Pages**.
2. Under "Build and deployment" → Source, choose **Deploy from a branch**.
3. Branch: pick the branch this project lives on (e.g. `claude/new-session-f661ve`,
   or `main` once merged) — folder: **/docs**.
4. Save. GitHub will give you a URL like
   `https://spm234.github.io/nfl_pool_bets/` within a minute or two.

## Fetching spreads without running Python yourself

`.github/workflows/fetch-spreads.yml` is a click-to-run GitHub Action
(Actions tab → "Fetch spreads" → "Run workflow", enter season/week) that
fetches an early spread estimate for every game one of "my" entries is
assigned to that week, and commits the updated `pool.db` back to the repo
automatically — no local Python, no terminal.

One-time setup:
1. **Settings → Secrets and variables → Actions → New repository secret**,
   name it `THE_ODDS_API_KEY`, paste your key.
2. That's it — the workflow already exists in this repo.

This is why `pool.db` is tracked in git (not gitignored) as of this
change: for the Action's fetch to persist anywhere, the database has to
live somewhere the Action can commit back to. The tradeoff is real and
worth knowing — your full season's data (assignments, picks, field
standings) now lives in git history, not just the periodic HTML snapshots
already published to Pages. If you'd rather keep the database local-only,
the alternative is running `fetch-my-spreads` (or `fetch-spread` for one
game at a time) yourself, same as any other CLI command.

`fetch-my-spreads --season Y --week N` (the command the Action runs) is
also available locally — it fetches for every game any "my" entry is
assigned to that week, without needing per-game `--away`/`--home` args.

## Live data (Phase 4) — what was built and what wasn't

- **Spread estimates**: `pool/live_data.py` fetches from
  [The Odds API](https://the-odds-api.com) (needs a free-tier key in
  `THE_ODDS_API_KEY` or `--api-key`) — a documented, scriptable JSON API,
  not a scrape. Every value it returns is stored with
  `is_authoritative_for_upset = 0` and printed as an "EARLY ESTIMATE." The
  only way to mark a spread authoritative for 10x-upset qualification is
  `confirm-spread`, which you run after checking your actual settlement
  source (`spread_source_name` in config — set it to whatever you check;
  it's not tied to any particular publication) yourself. This confirm step
  is deliberately not automated: a fetched line is a market estimate, and
  the pool's real qualification source may not agree with it.
- **Results**: `fetch-result` pulls a completed game's final score from the
  same Odds API and records the outcome — same API key, no new dependency.
  Unlike spreads, there's no separate confirm step for results, since a
  final score isn't a matter of interpretation. The one real caveat: this
  source reports only the final score (including any overtime), not the
  score at the end of regulation. The pool's rule scores a game tied at
  regulation as a loss for any WIN/LOSS pick regardless of who wins in OT —
  that distinction can't be recovered from a final-score-only source. A
  *tied* final score is unambiguous (it can only happen if regulation was
  also tied), but a decisive final score for a game that actually went to
  OT from a regulation tie would be recorded as a normal win/loss.
  `fetch-result` prints a reminder of this every time it records a
  decisive (non-tie) outcome — verify separately if you know a game went
  to OT.
- **officepool4fun.com standings**: **not built.** This session's sandbox
  network policy blocked outbound access to the domain entirely, so
  reachability/login-wall status couldn't be verified before writing a
  scraper against it (the starter prompt was explicit: check first, don't
  assume). You chose to stick with the manual-paste path (`import-standings`)
  for now rather than have a scraper built against an unverified target. If
  you later confirm the site is reachable without login, a fetcher can be
  added to `pool/live_data.py` alongside the spread fetcher; if it needs
  auth, the manual-paste path is the intended answer, not a workaround.

## Confirmed rules (previously open assumptions)

- **Upset direction**: betting the *favorite* to LOSE against a 10+ spread
  does pay 10x, same as an underdog-WIN — confirmed directly by you, not
  just inherited from the prototype. `upset_counts_favorite_loss = 1` in
  `pool_config` reflects this; flip it with `config-set
  --counts-favorite-loss no` if that ever turns out wrong in practice.
- **Assignment**: every entry (yours and the field's) is assigned the AWAY
  team specifically — confirmed. `import-week` and `import_week_csv` bake
  this in directly rather than trying to infer a side.

## Open assumptions still to validate

These affect real point totals — validate against a settled week before
trusting the numbers, then flip the config value if wrong:

1. **Spread source snapshot**: the 10+ threshold is read from whatever
   source the pool operator actually uses, at whatever time they check it —
   confirm which exact source and snapshot that is. `spread_source_name` in
   config documents whatever you confirm; it defaults to a placeholder, not
   a real source, until you set it.
2. **Regulation tie**: a tie pick on a regulation tie pays 10x regardless of
   the OT winner; any WIN/LOSS pick loses regardless of the OT winner. This
   one is not user-configurable since it's stated as a firm rule, not an
   assumption.

To validate: enter one fully-settled real week via `record-my-pick` +
`record-result`, then `timeline --entry SPM` and compare the
computed total against the pool's actual posted total. A mismatch means
whatever source is confirming your spreads doesn't match the pool
operator's actual snapshot (assumption #1 above) — the direction and
assignment rules are already confirmed, so they're not the likely culprit.

## Pick recommendation: WIN vs LOSS, actually compared

`recommend.choose_pick` (`pool/recommend.py`) compares betting WIN vs LOSS
on your assigned team and recommends whichever has higher expected value —
it does not default to WIN. Win probability comes from a logistic model
driven by the recorded spread (`scoring.win_prob_from_spread`, ported from
the original prototype but previously unused anywhere). This matters
because a favorite betting LOSS against a qualifying spread pays the same
10x upset bonus as an underdog betting WIN (confirmed rule, see above) —
sometimes fading your own team is the better play, and the tool will now
actually say so.

This compares linear expected points only — it does not account for how
variance interacts with the pool's top-10 payout structure (a long-shot
upset can be worth more than its raw EV suggests late in the season when
you're chasing 1st, and worth less if you're just trying to survive). The
reasoning text shown alongside each recommendation states both picks' EV
per point so you can see the comparison, not just the conclusion.

## What's deliberately thin

- **Competitor profiles** (`pool/profiles.py`): built from the top-confidence
  reconstruction candidate per entry. Additive and approximate by design —
  meaningful once there's a few weeks of real data, not before.
- **Simulation** (`pool/simulation.py`, `pool/recommend.py`): models the
  field with configurable behavioral assumptions (win probability, upset
  frequency, bet-fraction policy), not a full behavioral simulation of every
  real competitor. Treat outputs as directional, not precise.
- **CLI output only** — no dashboard UI, per the starter prompt's scope.
