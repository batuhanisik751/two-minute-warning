# Time Machine: reproducibility check (step I3a)

The spec's P3 acceptance says "Time Machine for any backtest week reproduces the stored
predictions exactly (no recomputation drift)". `twm timemachine verify` checks it: for each
module it picks past seasons, recomputes those lists from the warehouse with the model that
was used then, and compares them with the rows the site serves.

```
uv run twm timemachine verify                       # every module, 4 seasons each (~3 min)
uv run twm timemachine verify --module board --sample 99   # every backtest season of one module
uv run twm timemachine verify --module streamer --seed 7   # another seeded sample
```

- **Sample**: `--sample N` seasons per module (default 4), drawn with `random.Random(--seed)`
  (default 20261002). The first and the last backtest season are always in the sample, and
  every list (week) of a sampled season is compared. The same arguments always give the same
  sample.
- **Comparison** (`src/twm/timemachine/compare.py`): for every list, the same entities, the
  same order and rank, every probability or score within **1e-9** (NULL only equals NULL),
  and every text column equal: versions, reasons or drivers JSON, bands, teams, as-ofs. The
  check reports, per module, the lists and rows compared, the largest absolute difference,
  the mismatches (counted, the first five shown) and notes. It exits 1 if any module has a
  mismatch or cannot be recomputed. A module that cannot be recomputed (a pin or a file
  missing, for example) is reported as NOT REPRODUCED with the reason, never skipped.
- **Side effects**: none. The warehouse, the pins and the predictions store are opened
  read-only. Recomputed grades, the Hot-Seat features and a copy of the own-xFP folds go to a
  temporary directory (`--work DIR` keeps them).
- **Tests**: `tests/test_timemachine.py` covers the comparison logic on synthetic lists: a
  1e-6 drift fails, a reordered list fails, missing, extra and changed rows fail.
  `tests/test_timemachine_realdata.py` (marked realdata, about 2.5 min) runs the first and
  last season of every module on the owner's data.

## What is recomputed, per module

| Module (pin) | Stored rows (what the site serves) | Recomputed from the warehouse with |
|---|---|---|
| `waiver_radar` | pin snapshot `predictions.parquet` 2014-2025; also the predictions store's rows of the pinned versions | dataset rebuilt from 2013 (`twm radar dataset` code); each season's walk-forward fold refit (`run_backtest`, logit + isotonic): score, raw score, rank per position, version |
| `streamer_k` | pin snapshot `predictions.parquet` 2013-2025 | streamer dataset rebuilt from 2012; each season's fold refit (`twm streamer backtest` code) |
| `streamer_dst` | pin `hit_rates.parquet`, plus the D/ST lists that the publish derives again from `data/streamer/dataset.parquet` | the next-opponent rule (nothing fitted) applied to a dataset rebuilt from 2012: the lists and the hit-rate table (exact counts) |
| `regression_watch` | pin snapshot `predictions` + `model_versions` (weeks 4/6/8/10, 2011-2025) | `frozen.build_snapshot` (the code that froze it): each season's variant, X and shrinkage chosen walk-forward, the lists, tags, reasons JSON and teams |
| `decisions` | pin history (fourth downs, tries, clock cases, team games, season inputs; 2006-2025) | each season regraded (G3 `grade_season`, G4 `clock_season`) into the scratch directory, then turned into site rows by `frozen.season_tables` |
| `hot_seat` | pin snapshot `predictions.parquet` 2006-2025 | features rebuilt (H3a batch path), the verified targets, then each season's fold refit (`production.fit_live`): probability, rank, drivers, key features, version |
| `board` | pin snapshot `predictions.parquet` (boards 2008-2025) + the 2026 board frozen in the pin | preseason dataset rebuilt (kickoff-eve anchor); both folds (Cliff, missed time) refit per board (`production.fit_live`): chances, ranks, ECR, drivers, versions. The 2026 board is re-scored from its frozen inputs by the pinned models (`twm model check board`'s check) |

The Radar, streamer and Hot-Seat folds, and both board models, are refit from scratch. A
version match proves that each refit learned from the same training rows: the version
hashes the training rows' content, the features, the parameters and the seasons.

## What is reused, not recomputed (and why)

- **Decision Report Card fold models.** The check regrades every play from the warehouse,
  but it uses the stored fold models of that season, loaded by version from
  `models/decisions/` (G1's walk-forward WP model, LightGBM, and the G2 sub-models). It does
  not retrain them. Retraining every WP fold means running the full G1 backtest (1,009,826
  play states, 1999-2025), which is out of scope for this check. A changed model file would
  still show up: the fold models must pass `Models.check`, and every graded row stores the
  versions of its models.
- **Own xFP folds** (Regression Watch, and the board's xFP features). The per-play
  expectations come from the stored walk-forward folds in `data/regression_watch/own_xfp/`
  whenever a fold is current by its content hash. Otherwise the fold is refit. The check
  works on a scratch copy because the history reader rewrites its input files. H6-b2
  measured refits to be byte-identical.
- **Derived at publish, not by a model.** The Radar's and K's backtest rows have no band and
  no reasons (stored as `[]`). The site's chance for a backtest pick is computed at publish
  time from earlier seasons' stored rows, which are the rows this check compares.

## What is not checked (and why)

- **Live lists** (`kind = 'live'`: the Radar's, the streamer's and Regression Watch's 2026
  week 3). They are what the app said in real time and are kept append-only. The spec's
  criterion covers backtest weeks. Regression Watch's live week 3 also used the previous xFP
  source (ffopportunity, before the H6-b2 switch), so a recomputation with today's pin would
  differ by design.
- **The 2026 lists reconstructed on the Mac** (548 Radar rows in the store, `kind =
  'backtest'`, season 2026). They are not in any pin. This check covers the pinned history.
- **Other platforms.** The check runs on the owner's Mac against its warehouse. The scheduled
  Linux job never recomputes history: it serves the frozen pins, because Linux float noise
  could flip near ties (P2, P3). The check therefore says nothing about a Linux rebuild.
- **Unsampled seasons.** A default run checks 4 seasons per module. `--sample 99` checks every
  season (results below).

## Results (owner's Mac, 2026-10-02: commit c8921e9 + I3a; 48d3ada since changed only web/)

`twm timemachine verify --module M --sample 99` was run for every module, which covers every
backtest season and every stored row of each pin. Every module reproduced exactly: the
largest absolute difference of any compared number was **0.0**, so the recomputation is
bit-identical, not just within 1e-9.

| Module | Seasons | Lists | Rows compared | Max abs diff | Mismatches | Time |
|---|---|---:|---:|---:|---:|---:|
| waiver_radar | 2014-2025 | 732 | 91,638 (+ 91,638 store rows vs pin) | 0.0 | 0 | 93 s |
| streamer_k | 2013-2025 | 210 | 2,318 | 0.0 | 0 | 57 s (both streamer pins) |
| streamer_dst | 2013-2025 | 277 | 434 hit-rate rows + 1,491 served list rows | 0.0 | 0 | (above) |
| regression_watch | 2011-2025 | 255 | 10,826 list rows + 15 versions | 0.0 | 0 | 14 s |
| decisions | 2006-2025 | 100 | 122,824 (83,462 fourth downs, 27,362 tries, 1,118 clock, 10,862 team games, 20 inputs) | 0.0 | 0 | 44 s |
| hot_seat | 2006-2025 | 325 | 10,361 | 0.0 | 0 | 24 s |
| board | 2008-2025 | 18 | 1,674 (+ the 94-row 2026 board re-scored) | 0.0 | 0 | 16 s |

The default run (4 seasons per module, seed 20261002) takes about 3 minutes and also
reproduced every module.
