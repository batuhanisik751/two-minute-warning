# The offseason routine (step I6b; PROJECT_SPEC 9 and P3 item 7)

Once a year, from the Super Bowl to week 1, every module is re-approved with the season that
just finished, so next season's lists come from models that have seen it. PROJECT_SPEC 9:
retraining is a **manual workflow with a review step**. Three rules hold the whole time:

- **The production models are never retrained on the GitHub runner.** Every approval runs on
  the owner's Mac and is committed. A model trained on the runner gives slightly different
  numbers (Linux float noise, docs/deploy.md "Caches"), so the site would disagree with its own
  track record. The Radar's `retrain.yml` makes a candidate **for review only** (below).
- **Nothing is pushed half-done.** The job's preflight refuses a pin whose season is not
  `current_season`, so bump the season and re-approve all seven pins locally, then commit and
  push them together (step 4). Until then the weekly offseason run keeps the old pins.
- **`uv run twm offseason status`** (read-only) shows where you are. Per pinned module it prints
  the season it is approved for and the season due, and whether its frozen history covers the
  finished season. It also says whether that season's coach departures are labelled, shows the
  live board's window, and names the **next** step. Run it before and after each step.

Who does what: **Owner** (you, on your Mac; reviewing and committing is always yours),
**Claude** (code and docs changes, on request), **Job** (the scheduled GitHub Actions pipeline,
docs/deploy.md "The scheduled pipeline"). Each step is marked **yearly** (every offseason) or
**one-time** (only the first time, or only when you change your mind). Below, F is the season
that just finished (2026 the first time) and F+1 is the season due (2027).

## 0. During the season (Job)

The job runs every night of the season window and scores every week's lists with the pins.
There is nothing to do. `twm offseason status` says "the F season is under way".

## 1. After the Super Bowl (February)

1. **Owner, yearly: refresh the data.** `uv run twm ingest`, then
   `uv run twm ingest --start F --force` (the finished season again, now final), then
   `uv run twm build` (the full warehouse on the Mac: the decisions and Regression Watch
   approvals need 2006 on), then `uv run twm doctor`. The status should then say
   "finished season: F". Until it does, it says "1. refresh the data".
2. **Owner, yearly: the season.** Set `current_season: F+1` in `config/settings.yaml`, locally,
   and commit it in step 4 together with the pins. The pin commands default to it.
3. **Claude, yearly: the season constants.** Some backtests list their test seasons in the
   code. Extend each one by one season, with its tests. Found on 2026-10-02:
   `modules/decisions/wp.py` `TEST_SEASONS` (also used by `submodels.py`),
   `modules/hot_seat/backtest.py` `TEST_SEASONS`, `modules/hot_seat/candidates.py`
   `LAST_SEASON`, `modules/streamer/backtest.py` `DEFAULT_TESTS`, `modules/streamer/report.py`
   `DEFAULT_SEASONS`, `modules/board/backtest.py` `LAST_TEST_SEASON` (snapshot seasons: F-1),
   `modules/waiver_radar/feature_report.py` `LAST_EVAL_SEASON` and
   `modules/regression_watch/own_xfp_report.py` `WINDOWS`. Then grep for `2026))` and
   `= 2025` to catch any others.
4. **Owner (Claude helps), yearly: label F's coach departures.** The Hot-Seat model and its
   history need them.
   - Regenerate the candidates: `uv run twm hotseat candidates --last-season F+1`.
   - Add a row for each departure, as docs/labeling_coaches.md describes. Black Monday firings
     are often `source_only` rows: the next season's schedule still lists the fired coach until
     his successor's first game.
   - Then `uv run twm hotseat check-labels` must exit 0.
   - The status line "coach departures of F: ... labelled" needs three things: at least one
     row, every row verified (`y`), and no schedule candidate of F without a row.

## 2. Re-approve every module for F+1 (Owner, on the Mac; yearly)

Each module's walk-forward now has F as its newest test fold. For every module:

1. Run its backtest.
2. **Review the new report against the old one** (`git diff reports/<module>/`): did the
   headline metric move? Is the new fold in line with the earlier ones?
3. Run its pin command, which refuses unless the frozen files reproduce the report.
4. Run `uv run twm model check <module>`.

The commands are the ones in docs/deploy.md "The approved model and its backtest", in the
order `twm offseason status` checks them:

| # | Module (pin) | Commands | Its frozen history afterwards |
|---|---|---|---|
| 1 | Waiver Radar (`waiver_radar`) | `uv run twm radar dataset`; `uv run twm radar backtest`; `uv run twm radar evaluate`; `uv run twm train waiver_radar`; `uv run twm model pin waiver_radar --version <new version>` | 2014-F |
| 2 | K streamer (`streamer_k`) | `uv run twm streamer dataset`; `uv run twm streamer backtest --store <store>`; `uv run twm streamer pin --store <store>` | 2013-F |
| 3 | D/ST streamer (`streamer_dst`) | `uv run twm streamer pin --store <store> --pos DST` | 2013-F |
| 4 | Regression Watch (`regression_watch`) | `uv run twm regression own-xfp` (the own xFP walk-forward with F); `uv run twm regression stability`; `uv run twm regression backtest`; `uv run twm regression pin` (it also fits F+1's own xFP live models and freezes the lists: `regression freeze`) | 2011-F |
| 5 | Decision Report Card (`decisions`) | `uv run twm decisions wp-backtest --season F+1`; `uv run twm decisions submodels-backtest --season F+1`; `uv run twm decisions grade` (F graded by its own fold models); `uv run twm decisions clock`; `uv run twm decisions pin` | 2006-F |
| 6 | Hot-Seat Meter (`hot_seat`) | `uv run twm hotseat features`; `uv run twm hotseat backtest --labels verified`; `uv run twm hotseat pin` (needs step 1.4) | 2006-F |
| 7 | Cliff board (`board`) | `uv run twm board backtest --snapshot preseason`; `uv run twm board pin` | snapshots 2007-(F-1), i.e. the boards of 2008-F |
| 8 | Questionable outcomes (`questionable`) | `uv run twm questionable build` (writes `reports/questionable/*.csv`); `uv run twm questionable pin` (refused unless it reproduces them) | 2016-F |
| 9 | Start/sit odds, local only (`startsit`) | `uv run twm league startsit-pin` (needs F's weekly FantasyPros ranks in the warehouse) | 2020-F |

Check each command's `--help` for its season and store options before running it. The
commands default to `current_season` (step 1.2). The Radar also has a **preview** that may run
on GitHub: `gh workflow run retrain.yml` trains a candidate on the runner and uploads it
together with `retrain_report.md`, the candidate compared with the approved model. Read it as a
preview only. It never commits and is never approved as it is (Linux float noise). The other
six modules have no such workflow. Extending `retrain.yml` to them would mean refitting
walk-forwards that take minutes to hours on the full 1999-F warehouse, and the runner only
builds from 2012. Their candidates would also never match the Mac's reports. So they are
retrained only here, on the Mac.

## 3. Refreeze the histories (yearly; done by the pin commands of step 2)

Each pin command freezes its module's history together with the new model. What the site
serves for past seasons then includes F:

- **Decisions:** `twm decisions pin` freezes F's regrade (made with F's own fold models in step
  2.5) into the frozen history, `history-<version>/`. The runner never regrades history.
- **Hot-Seat, board, Regression Watch:** their frozen backtests grow by one season (the
  board's by one snapshot, F-1, i.e. the board of F with its final outcomes).
- **Radar, streamer:** the backtest snapshots of their pins grow by one season too.

`twm offseason status` shows the `covers` column as `yes` once a module's history reaches F.
For the board it reaches snapshot F-1.

## 4. Check, review, commit (yearly)

1. **Owner:** run `uv run twm model check all`. Every pin must pass: the sha256 of each file,
   its rows, and the reports reproduced.
2. **Owner:** run `uv run twm timemachine verify`. It recomputes a sample of past lists of every
   module and compares them with the pinned rows the site serves.
3. **Owner:** run `uv run pytest`, then `uv run ruff check . && uv run ruff format --check .`
   (exactly as CI runs them).
4. **Claude:** update `docs/model_cards/*.md` from the new reports. Every number in them
   is checked against the report it cites.
5. **Owner:** run `twm offseason status`. It should say "every module is approved for the
   season due".
6. **Owner:** review `git diff`, then commit and push in ONE commit, from your own account:
   `config/settings.yaml` (`current_season`), `config/production_models.yaml`,
   `artifacts/production_models/`, `reports/`, the code constants and the cards. The next
   scheduled run uses the new pins. Its preflight names each one.

## 5. Preseason: the live board (August to September)

The board of F+1 is scored **live** once a season, by the pinned board models, from each
team's latest daily depth chart at the live as-of. Config `as_of.board.live_publish`
(`config/settings.yaml`) sets that as-of:

| Value | As-of | Who scores it |
|---|---|---|
| `"week1_kickoff_eve"` (**default**) | 1 hour before the first week-1 kickoff, the moment the models learned from | **Owner.** The window is one hour (about 23:20-00:20 UTC on the opening Thursday), and the 10:47 UTC nightly never falls inside it. In that hour, either run `uv run twm board score --live-publish` then `uv run twm publish --target remote` on the Mac, or start the Pipeline workflow by hand (`gh workflow run pipeline.yml --ref main`). |
| `"days_before_week1: N"` | 00:00 UTC N days before the kickoff's date | **Job.** Its `board_score` stage scores it on the first run in the window. With N <= 7 that is a nightly run: the season window starts 7 days before the first game. With larger N, the next Tuesday's offseason run. |
| `"MM-DD"` (e.g. `"09-01"`) | that day of F+1 at 00:00 UTC | **Job**, as above. A date on or after the kickoff never opens. |

The **owner** picks the value each summer. It is a one-time decision you can revisit every
year. Keep the default, or change the line and commit it before the date. `twm offseason status`
shows the window that results. An earlier date publishes the board sooner, but from an
earlier depth chart than the one the models learned from. The list's note says so on the site
(`board_list.note`, for example "Read as of 2027-09-02 00:00 UTC (days_before_week1: 7),
earlier than the kickoff eve the models learned from, from each team's latest daily depth
chart then (32 teams; pulls of 2027-09-01) ...").

**The stage** (`twm pipeline run`, `board_score`, after the Hot-Seat list and before the one
publish) runs `twm board score --live-publish --season F+1`:

- It runs only while the window is open (from the as-of to the first week-1 kickoff) and while
  the store holds no live board of the season. Otherwise it is skipped, and the summary says
  why.
- The board is stored 'live' (append-only: a stored live board is never overwritten).
- The publish carries it, and the database then keeps it as published. Live lists are frozen
  in the target, so if a later run in the window scores it again, the first one stays.
- The FantasyPros ECR column appears only when its preseason scrape was public by the as-of.
  The nightly ingest refreshes it.
- A pin approved after the kickoff eve also freezes a reconstructed board of F+1 (docs/board.md
  "The season's board in the pin"). A live board is published next to it.

## 6. Week 1 (Job)

The season window opens 7 days before the first game: the job runs nightly again and scores
each week's lists when they are due (docs/deploy.md "The scheduled pipeline"). Read the first
run's summary. `twm offseason status` then says "the F+1 season is under way".

## One-time items (not repeated each year)

- Migrations: each new published table needs its `web/drizzle/*.sql` applied to Neon once,
  before the first publish that carries it (docs/deploy.md "Setting up Neon").
- `FIRST_LIVE_SEASON = 2026` in `src/twm/publish/{hot_seat,regression,board}_lists.py`: the
  first live season. It stays.
- The `live_publish` choice (step 5) and the Hot-Seat labelling conventions
  (docs/labeling_coaches.md): decided once, revisited only if you change your mind.
