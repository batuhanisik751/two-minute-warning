# Two-Minute Warning

**Two-Minute Warning** is an open, point-in-time NFL early-warning app. It reads every play of every NFL game since 1999 and flags what is about to change: players about to break into fantasy lineups, hot streaks that won't last, coaches whose decisions are costing their teams wins, coaches about to be fired, and players about to break out, or fall off a cliff, next season.

Every flag comes with a **track record**. Each model is tested the honest way: trained only on seasons before the one it predicts, using only data that was available at the moment of the prediction. A **time machine** lets you pick any week since 2013 (the first season with snap counts, which the earliest fantasy modules need) and see exactly what the app would have said then, and what actually happened.

Built on the open-source [nflverse](https://nflverse.nflverse.com/) data ecosystem. Unofficial, educational, not affiliated with the NFL or ESPN, and not betting advice.

## Requirements

Python **3.13** and [uv](https://docs.astral.sh/uv/). If your checkout lives in a cloud-synced folder (on macOS, `~/Desktop` and `~/Documents` sync to iCloud Drive), run `scripts/local_storage.sh` once: it keeps the virtualenv, the raw data cache and the warehouse outside the synced folder and leaves symlinks in the project, so nothing else changes.

## First run

```bash
uv sync --all-extras          # creates .venv (Python 3.13) with all dependencies
cp .env.example .env          # fill in locally; never commit
uv run pre-commit install     # ruff + file-hygiene hooks on every commit
uv run twm --help
uv run twm doctor             # config, data and environment checks

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
uv run jupyter lab notebooks/01_waiver_radar.ipynb   # the Waiver Radar explained step by step
uv run pytest                 # offline tests (the default); `uv run pytest -m network` runs the live drift check
```

Dataset names: `uv run twm ingest --help` or `DATASETS` in `src/twm/sources/nflverse.py`.

## Data and schema snapshots

- `data/raw/<dataset>/<season>.parquet` is the only cache. Historical seasons are immutable once fetched; the current season and one-file datasets are refreshed after 12 hours. nflreadpy's own cache is switched off so nothing is stored twice.
- `data/schemas/<dataset>.json` (committed) records each dataset's columns and dtypes. Every current-season or one-file load is compared with it: a missing column raises `SchemaDriftError`, a changed dtype or a new column is logged. Snapshots are written on the first current-season fetch of a dataset and refreshed **deliberately** with `uv run python scripts/refresh_snapshots.py` (review the diff, then commit), never by `twm ingest`.
- What we verified about the data, and where it differs from the spec: `docs/assumptions.md`. Re-verify from the cache with `uv run python scripts/verify_sources.py` (no network unless you pass `--allow-download`).

See `PROJECT_SPEC.md` for the full specification, `docs/warehouse.md` for the DuckDB warehouse, `docs/scoring.md` for how fantasy points are computed, `docs/waiver_radar.md` for the Waiver Radar's candidate pool, labels, features, models and evaluation, `notebooks/01_waiver_radar.ipynb` for a guided tour of it and `docs/progress.md` for the build log.

## Attribution

Data from nflverse; rankings and player ID maps from DynastyProcess / FantasyPros; snap counts originate from Pro Football Reference.

## License

MIT, see `LICENSE`.
