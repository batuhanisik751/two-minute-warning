# Two-Minute Warning

**Two-Minute Warning** is an open, point-in-time NFL early-warning app. It reads every play of every NFL game since 1999 and flags what is about to change: players about to break into fantasy lineups, hot streaks that won't last, coaches whose decisions are costing their teams wins, coaches about to be fired, and players about to break out, or fall off a cliff, next season.

Every flag comes with a **track record**. Each model is tested the honest way: trained only on seasons before the one it predicts, using only data that was available at the moment of the prediction. A **time machine** lets you pick any week since 2013 (the first season with snap counts, which the earliest fantasy modules need) and see exactly what the app would have said then, and what actually happened.

Built on the open-source [nflverse](https://nflverse.nflverse.com/) data ecosystem. Unofficial, educational, not affiliated with the NFL or ESPN, and not betting advice.

## Requirements

Python **3.13** and [uv](https://docs.astral.sh/uv/). If your checkout lives in a cloud-synced folder (on macOS, `~/Desktop` and `~/Documents` sync to iCloud Drive), run `scripts/local_storage.sh` once: it keeps the virtualenv, the raw data cache, the warehouse, the Waiver Radar dataset, the predictions store and the trained models outside the synced folder and leaves symlinks in the project, so nothing else changes (re-run it after pulling this change: it now also moves `models/`).

## First run

```bash
uv sync --all-extras          # creates .venv (Python 3.13) with all dependencies
cp .env.example .env          # fill in locally; never commit
uv run pre-commit install     # ruff + file-hygiene hooks on every commit
uv run twm --help
uv run twm doctor             # config, .env, data and model checks: PASS / WARN / FAIL per line,
                              # with what to run; exit code 1 on any FAIL

# Quick start: two small datasets, a few MB, under a minute
uv run twm ingest schedules player_stats --start 2024

# Full history: all 29 datasets, 1999-2026, roughly 10-15 minutes on a home connection
# (an estimate, not yet timed end to end) and about 0.5 GB of Parquet under data/raw
# (gitignored). participation for the current
# season is expected to fail: nflverse publishes it only after the season.
uv run twm ingest

uv run twm build 2025 2026    # DuckDB warehouse data/warehouse.duckdb from the cache, never downloads
                              # (see docs/warehouse.md); `twm doctor` then lists its tables
uv run twm asof 2025 5        # what the warehouse looked like at week 5's Tuesday as-of
                              # (every row has an `available_at`; see docs/warehouse.md)
uv run twm ids                # player-id coverage report -> reports/ids/unmatched_ids.md (gitignored)
uv run twm glossary wopr      # what a metric means, how it is computed (docs/glossary.md)
                              # (docs/warehouse.md "Player IDs"; fix links in data/manual/)
uv run twm radar pool 2023 6  # Waiver Radar candidate pool at week 6's as-of (docs/waiver_radar.md)
uv run twm radar pool-report  # pool sizes and validation -> reports/waiver_radar/pool_sizes.md
uv run twm radar labels 2023 6  # did each pool player become a weekly starter soon after? (labels)
uv run twm radar labels-report  # label base rates and checks -> reports/waiver_radar/labels.md
uv run twm radar features 2023 6 --team min  # the features at week 6's as-of, teammates out
uv run twm radar dataset      # pool + features + labels 2013-2026 -> data/waiver_radar/dataset.parquet
uv run twm radar features-report  # single-feature check -> reports/waiver_radar/features.md
uv run twm radar backtest --label y_hit --label y_sustained   # walk-forward backtest 2014-2025 (~4 min; regenerates reports/waiver_radar/backtest.md)
                              # -> reports/waiver_radar/backtest.md, data/predictions.duckdb
uv run twm radar evaluate     # grades the stored predictions with 95% intervals, breakouts caught
                              # -> reports/waiver_radar/evaluation.md (+ .csv, figures/), ~5 s
uv run twm radar score        # this week's Waiver Radar list (after Monday night + Tuesday's data):
                              # checks the data arrived (exit 3 if not), chance, band, priority,
                              # reasons -> reports/waiver_radar/weekly/<season>-W<week>.md + the store
uv run twm radar score --season 2026 --week 2   # a past week (stored as a reconstructed list)
uv run twm radar week 2026 2 --pos RB           # any stored week (backtest or live) with outcomes

# The spec's generic commands (one per module; only waiver_radar exists so far)
uv run twm train waiver_radar     # build or refresh this season's production model
uv run twm backtest waiver_radar --label y_hit --label y_sustained   # = twm radar backtest
uv run twm score                  # every module's list for the latest Tuesday as-of
uv run twm score --as-of 2026-W3  # a season-week ("2026 3" works too)
uv run twm score --as-of 2026-09-30T18:00Z   # a moment (with a time zone): the week whose
                                  # list was current then (here week 3, made Tue 14:00 UTC)
uv run twm snapshot schedules     # rewrite one schema snapshot from the cache (--download to
                                  # re-fetch after a reviewed upstream change; never by default)
uv run jupyter lab notebooks/01_waiver_radar.ipynb   # the Waiver Radar explained step by step
uv run pytest                 # offline tests (the default), golden tests included;
                              # `uv run pytest -m network` runs the live drift check,
                              # `uv run pytest -m realdata` the tests on the real cache
```

## Your league

`config/league.yaml` describes the league the app is tuned for (the owner's: 12 teams, full PPR, 1 QB, 2 RB, 2 WR, 1 TE, 1 FLEX, K, D/ST, 7 bench). Only the number of teams and the lineup are set there; every threshold is derived from them: the weekly starter threshold (teams x starters at the position), the FLEX-worthy rank and the Waiver Radar's candidate-pool cutoffs (`docs/waiver_radar.md`, "League shape"; `uv run twm doctor` prints them). For a what-if run with another league, copy `config/` to a folder, edit `league.yaml` there and prefix any command with `TWM_CONFIG_DIR=<folder>`.

## Golden tests

`tests/golden/` holds a small frozen synthetic world (made-up players and games, never real data: `inputs/`, generated once by `tests/golden/make_inputs.py`) and the known outputs of every Waiver Radar step on it (`expected/`: pool, labels, features, a tiny walk-forward backtest, the weekly list with reasons). `uv run pytest` rebuilds the world with the real pipeline and fails with a readable diff when an output changes. When a change is meant to change them, regenerate them deliberately and review the diff before committing:

```bash
uv run python tests/golden/update.py
```

Dataset names: `uv run twm ingest --help` or `DATASETS` in `src/twm/sources/nflverse.py`.

## Data and schema snapshots

- `data/raw/<dataset>/<season>.parquet` is the only cache. Historical seasons are immutable once fetched; the current season and one-file datasets are refreshed after 12 hours. nflreadpy's own cache is switched off so nothing is stored twice.
- `data/schemas/<dataset>.json` (committed) records each dataset's columns and dtypes. Every current-season or one-file load is compared with it: a missing column raises `SchemaDriftError`, a changed dtype or a new column is logged. Snapshots are written on the first current-season fetch of a dataset and refreshed **deliberately** with `uv run python scripts/refresh_snapshots.py` (review the diff, then commit), never by `twm ingest`.
- What we verified about the data, and where it differs from the spec: `docs/assumptions.md`. Re-verify from the cache with `uv run python scripts/verify_sources.py` (no network unless you pass `--allow-download`).

See `PROJECT_SPEC.md` for the full specification, `docs/warehouse.md` for the DuckDB warehouse, `docs/scoring.md` for how fantasy points are computed, `docs/waiver_radar.md` for the Waiver Radar's candidate pool, labels, features, models, evaluation and weekly list, `notebooks/01_waiver_radar.ipynb` for a guided tour of it and `docs/progress.md` for the build log.

## Attribution

Data from nflverse; rankings and player ID maps from DynastyProcess / FantasyPros; snap counts originate from Pro Football Reference.

## License

MIT, see `LICENSE`.
