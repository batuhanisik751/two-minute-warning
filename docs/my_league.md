# My League (ESPN, optional, local only)

My League reads the owner's ESPN league into `data/league.duckdb` (never published) and runs
only when `ENABLE_MY_LEAGUE=true` and the ESPN keys are in `.env` (PROJECT_SPEC 4.3, 8.3).
Every `twm league` command prints one line and exits with code 2 otherwise. The commands:
`sync`, `settings-diff`, `radar`, `regret`, `report` (steps F2-F4, docs/progress.md),
`trade` and `weekly` (below).

## Weekly routine

```
uv run twm league weekly [--week N] [--limit 3]
```

Run it on **Tuesday after 14:00 UTC** (the week's as-of: the lists of week N are made after
week N's games), or any day for a refresh. Why: `radar`, `report` and `trade` read the lists
from your LOCAL predictions store (`data/predictions.duckdb`); the scheduled job makes its
lists on GitHub and publishes them to the database only, so without this routine the Mac's
store goes stale. Code: `src/twm/league/weekly.py`, `commands.run_weekly`; tests:
`tests/test_league_weekly.py` (mocked steps). The steps, in order, one line each; any failure
stops the routine with one clear line naming the step's log:

1. **ingest**: `twm ingest --start <season> --force` (the current season's nflverse files and
   the one-file datasets, as the nightly job). A cold cache stops first: `twm ingest --end
   <season - 1>`.
2. **build**: `twm build --start 1999 --end <season>` (settings `seasons.pbp_start`): always
   the FULL warehouse. `twm build` replaces the warehouse with the seasons it is given, so a
   shorter range (the job's `build_start` 2012) would leave the Mac's warehouse partial.
3. **score**: week N (`--week`, default the latest week whose Tuesday as-of has passed) with
   the approved models, the runner's commands: `twm radar score --pinned`, `twm streamer
   score`, `twm regression score`. A module whose approved version already has rows for
   week N is skipped ("already stored"): a stored list is never scored again (the store would
   replace it); a partly stored streamer list stops. Scored in the live window (Tuesday's
   as-of to the next first kickoff) a list is stored as `live`; later it is a reconstructed
   `backtest` list (the scorers' own rule). Exit 3 when the week's data has not arrived (run
   again later). The scorers' reports and every log go to `reports/league/weekly/<run>/`
   (git-ignored), never over the committed weekly reports.
4. **sync**: `twm league sync` (ESPN's current week).
5. **report**: `twm league report --week N`, then the radar summary (`twm league radar --week
   N --limit 3`: the top 3 free agents per position and the drop candidate).

Nothing is published and the remote database is never touched. Measured on the owner's Mac
(Saturday 2026-10-03, a busy machine): 94 s in all: ingest 8 s, the full 1999-2026 build
58 s, score 4 s (the Radar and streamer week 3 lists were already stored; Regression Watch's
week 3 list of the approved version was scored, as a reconstructed list), sync 22 s, report
2 s.

**Model versions.** Every list is read in ONE model version (versions are never mixed within
a list): the approved one of `config/production_models.yaml` when it is stored for the week;
otherwise the newest stored version for that week, and the output says so, e.g. "Regression
Watch's projections from the previous model version mean_flat_all-77146b2e5d2169fc: the
approved zero_flat_all-9d98d6f251ee054c has no list stored for week 3 (`uv run twm league
weekly --week 3` scores it)". Same rule in `radar`, `report` and `trade`.

## Trade checker

```
uv run twm league trade --give "Player A" [--give ...] --get "Player B" [--get ...] [--week N] [--json]
```

Code: `src/twm/league/trade.py`, `commands.run_trade`; tests: `tests/test_league_trade.py`
(synthetic league, schedule and backtest only). It reads the league store, the predictions
store, the pinned Regression Watch backtest and the warehouse, all read-only, and writes nothing.
The output names players and fantasy teams: it stays on your terminal.

**Names.** Matched against the newest synced week's rosters and ESPN free agents, ignoring case,
accents and punctuation ("d'andre swift" = "D'Andre Swift"); a part of the name works when it
is unique ("mccaff"). Several or no matches: one line listing the candidates (the closest
names for a typo), exit 2. `--give` players must be yours; `--get` players are on ONE other
team (the partner) and/or free agents (then there is no partner side). K and D/ST are refused.

**The numbers, for you and for the partner.**

- **Weeks counted**: the weeks after Regression Watch's list week N (default: the latest list
  stored at or before the synced ESPN week, as `twm league radar` picks), up to the league's last
  scoring week (`final_week`, ESPN's finalScoringPeriod, 17 in the owner's league) and the NFL's
  last regular-season week.
- **Fantasy playoffs** (step H6-a2): `twm league sync` stores espn-api's `reg_season_count`
  (ESPN's regular-season matchup periods), `playoff_team_count` and
  `playoff_matchup_period_length` (weeks per playoff matchup; 0 = ESPN did not say) as
  `league_settings` keys, plus `reg_season_final_week`: the last week of matchup period
  `reg_season_count` in espn-api's `matchup_periods` (else `reg_season_count`: a regular-season
  matchup lasts one week). The weeks counted are split into the regular season and the fantasy
  playoff weeks after it, each with its own totals and range; the playoff weeks are labelled
  "only if you make the playoffs" and the verdict uses the regular season (with what the
  playoff weeks add). A store synced before these keys keeps the old rule (every week counted
  weighs the same) and the output says so. `settings-diff` does not compare them: no config key
  holds the playoffs.
- **Each week**: the best legal QB/RB/WR/TE lineup (the lineup optimizer of `twm league regret`
  over the league's own slots, FLEX included) by each player's rest-of-season points per game:
  Regression Watch's stored projection; outside its list, his season average from the warehouse
  (labelled "season average, not a projection"); neither: no number (0). A player whose NFL team
  has no game that week (`fact_schedule`) scores 0. "Before" and "after" are the sums.
- **K and D/ST** are not projected by Regression Watch: kept the same in both lineups, so they
  never move the numbers.
- **Injuries**: an IR-slot player is left out of every lineup and of the roster count (traded,
  he stays in an IR slot on the other side). ESPN's
  OUT or INJURY_RESERVE status counts the player out of the synced ESPN week only (when it is
  counted): the store has no return dates. Questionable and doubtful players count as playing.
- **Roster limit**: every slot but IR (16 in the owner's league). A side above it after the trade
  drops its lowest-numbered QB/RB/WR/TE outside its best lineup (shown); a side below it keeps
  the open spot empty (a pickup is not counted).
- **The newest week**: rosters and free agents come from the newest week synced
  (`store.current`, shared with `radar` and `report`), so a later backfill
  (`twm league sync --week 3` after week 4) never replaces the current roster.

**The range (80%).** Never a number without it. From the approved Regression Watch backtest
snapshot (`config/production_models.yaml`, sha256-checked: every universe player at the
as-of weeks 4, 6, 8, 10 of 2011-2025, graded when 3+ games were left), a player's *miss* is his
actual rest-of-season points per game minus the projection. Misses are pooled by position and
by games ahead: the rows whose weeks left are within 2 of the list's (else the nearest
available). With the 2026 week 3 list (15 weeks left) that is the 13-14-weeks-left rows:

| position | rows | mean miss | 10th / 90th percentile |
|---|---:|---:|---:|
| QB | 305 | -1.49 | -6.57 / +4.01 |
| RB | 819 | -0.53 | -5.59 / +4.74 |
| WR | 885 | -1.18 | -6.33 / +3.68 |
| TE | 575 | -0.29 | -3.98 / +3.75 |

In each of 4,000 simulated seasons (fixed seed: the same trade prints the same range), every
player whose number of starts changes on either side draws ONE miss from his position's pool;
the same draw is used on both sides and for the regular season and the playoff weeks (a traded
player's season is the same for both teams).
A side's simulated change = the sum over those players of (starts after - starts before) x
(points per game + miss). The interval is the 10th to 90th percentile of the 4,000 changes and
the printed point estimate ("change", "about X points") is their median (step H6-a2), so the
point, the range and the share of seasons with a gain all come from one distribution.
`--json` keeps the projection's own change (after - before, no misses) as `projection_delta`.
Choices to know: misses are drawn independently across players; the real misses are kept as
they are, so the simulation corrects the projection's average lean by position (it ran high:
QB and WR misses average about -1.5 and -1.2 points per game in the backtest, RB -0.5, TE
-0.3; table above): a trade that swaps a WR for an RB of the same projection gains a little;
a miss is a per-game rate, so the range covers how well players score, not games they miss;
a season average stands in with its position's misses; a player without a number draws none.

**The verdict** per side (the reviewer's rule, step H6-a2), on the regular season when it is
stored: from the percent of the 4,000 simulated seasons in which the side gains, as printed
(rounded), "probably helps" at 67% or more (two thirds), "probably hurts" at 33% or fewer (one
third), otherwise "a close call"; always with the point estimate (the median), the 80% range
and the percent, e.g. "a close call (weeks 4-14, the regular season): about +12 points
(median), range -40 to +65 (80% interval); gains in 61% of 4,000 simulated seasons."
(illustrative numbers). The rule is applied to the printed percent, so the word never
disagrees with the number shown. Never "will". The 80% level follows the task's request; the
Radar's chances use 90% Wilson intervals and the backtests 95% bootstrap intervals.

**Context lines**: your starters at the traded positions in a typical week (byes aside) before
and after (positional scarcity); the weeks counted in which two or more of your typical starters
are on bye, before and after, and each traded player's bye; Regression Watch's Sell-high /
Buy-low tag and numbers of each traded player (Legit is not shown, as everywhere); ESPN's injury
status. `--json` prints the same numbers for scripts and tests.
