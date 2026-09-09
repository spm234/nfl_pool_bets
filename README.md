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
seeds your 3 entries ("My Entry 1/2/3" under owner "Me").

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

# Import a schedule, then assignments (paste "Name, Team" — resolved
# against the schedule to get home/away right)
python -m pool.cli import-schedule --season 2026 --week 1 --file schedule.txt
python -m pool.cli import-assignments --season 2026 --week 1 --file assignments.txt

# Import posted standings (paste "Name, Points" — same shape as the
# prototype's manual paste box)
python -m pool.cli import-standings --season 2026 --week 1 --file standings.txt

# Record a settled game's spread + outcome
python -m pool.cli record-result --season 2026 --week 1 \
    --away Atlanta --home Pittsburgh --favorite away --margin 3 --outcome home

# Record one of my own picks
python -m pool.cli record-my-pick --season 2026 --week 1 --entry "My Entry 1" \
    --away Atlanta --home Pittsburgh --side home --pick WIN --bet 30

# Live spread estimate (never authoritative for 10x qualification — see below)
python -m pool.cli fetch-spread --season 2026 --week 1 --away Atlanta --home Pittsburgh

# Manually confirmed, authoritative spread (only path that can be used for
# 10x-upset qualification)
python -m pool.cli confirm-spread --season 2026 --week 1 \
    --away Atlanta --home Pittsburgh --favorite away --margin 10 \
    --source "Cleveland Plain Dealer, 2026-09-11"

python -m pool.cli timeline --entry "My Entry 1"
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

`pool export-html` renders your entries' timelines, this week's
recommendations, and a scenario-lab comparison across four bet-sizing
policies into a single static page — no server, no live DB access. This is
a **snapshot**, not a live view: rerun the command and push whenever you
want the published page to reflect the current state.

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

## Live data (Phase 4) — what was built and what wasn't

- **Spread estimates**: `pool/live_data.py` fetches from
  [The Odds API](https://the-odds-api.com) (needs a free-tier key in
  `THE_ODDS_API_KEY` or `--api-key`) — a documented, scriptable JSON API,
  not a scrape. Every value it returns is stored with
  `is_authoritative_for_upset = 0` and printed as an "EARLY ESTIMATE." The
  only way to mark a spread authoritative for 10x-upset qualification is
  `confirm-spread`, which you run after checking the pool's actual source
  (Cleveland Plain Dealer, Thursday) yourself.
- **officepool4fun.com standings**: **not built.** This session's sandbox
  network policy blocked outbound access to the domain entirely, so
  reachability/login-wall status couldn't be verified before writing a
  scraper against it (the starter prompt was explicit: check first, don't
  assume). You chose to stick with the manual-paste path (`import-standings`)
  for now rather than have a scraper built against an unverified target. If
  you later confirm the site is reachable without login, a fetcher can be
  added to `pool/live_data.py` alongside the spread fetcher; if it needs
  auth, the manual-paste path is the intended answer, not a workaround.

## Open assumptions to validate (carried over from the prototype)

These affect real point totals — validate against a settled week before
trusting the numbers, then flip the config value if wrong:

1. **Upset direction**: does betting the *favorite* to LOSE against a 10+
   spread also pay 10x, or is it underdog-WIN only? Currently assumed both
   directions qualify (`upset_counts_favorite_loss = 1` in `pool_config`).
   Flip with `config-set --counts-favorite-loss no` if the pool operator
   confirms otherwise.
2. **Spread source snapshot**: the 10+ threshold is read from the Thursday
   Cleveland Plain Dealer at a fixed time — confirm which exact snapshot the
   pool operator uses. `spread_source_name` in config documents whatever
   you confirm.
3. **Regulation tie**: a tie pick on a regulation tie pays 10x regardless of
   the OT winner; any WIN/LOSS pick loses regardless of the OT winner. This
   one is not user-configurable since it's stated as a firm rule, not an
   assumption.

To validate: enter one fully-settled real week via `record-my-pick` +
`record-result`, then `timeline --entry "My Entry 1"` and compare the
computed total against the pool's actual posted total. A mismatch almost
always means assumption #1 above needs flipping.

## What's deliberately thin

- **Competitor profiles** (`pool/profiles.py`): built from the top-confidence
  reconstruction candidate per entry. Additive and approximate by design —
  meaningful once there's a few weeks of real data, not before.
- **Simulation** (`pool/simulation.py`, `pool/recommend.py`): models the
  field with configurable behavioral assumptions (win probability, upset
  frequency, bet-fraction policy), not a full behavioral simulation of every
  real competitor. Treat outputs as directional, not precise.
- **CLI output only** — no dashboard UI, per the starter prompt's scope.
