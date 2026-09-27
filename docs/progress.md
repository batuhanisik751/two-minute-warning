# Progress log (handoff)

Read this first in a new session. Then read `PROJECT_SPEC.md` Sections 0, 6 and 11.

## Decisions made by the owner
- 2026-09-26: Project name stays **Two-Minute Warning**. Repo public, MIT, named `two-minute-warning`. Python **3.13** (spec said 3.12).
- Commits authored by the owner only; no AI co-author trailers.
- 2026-09-26 (Phase A audit): nflreadpy's own cache is **off**. The per-season Parquet files under `data/raw` are the single cache. This deviates from spec 4.1 ("enable nflreadpy's cache") on purpose: nflreadpy's 24 h cache silently defeated the 12 h current-season refresh and `--force`, and doubled disk use (see `docs/assumptions.md` §10).
- 2026-09-26: the ingestion layer stays dtype-agnostic (raw cache and snapshots keep upstream dtypes, which is how drift is detected); the warehouse (B1) casts join keys. The list of known cross-season dtype drift is in `docs/assumptions.md` §10.

## Steps done
- **A1 Scaffold** (2026-09-26): uv project, `src/twm/` package skeleton, ruff, pytest, pre-commit, gitignore, `.env.example`, three config YAMLs, typed config loader, `twm` CLI with `version`/`doctor`, GitHub Actions CI.
- **A2 Ingestion** (2026-09-26): `src/twm/sources/nflverse.py`: registry of 29 datasets, per-season Parquet cache under `data/raw` (historical seasons immutable, current season refreshed after 12 h), schema snapshots in `data/schemas/`, `SchemaDriftError` on missing columns (current-season loads only), `twm ingest` CLI. Offline tests with fake loaders.
- **A3 Verification** (2026-09-26): `scripts/verify_sources.py` downloaded 2026/2025/first/first-1 of every dataset. Findings in `docs/assumptions.md`. Headlines: snap counts start 2013 (not 2012); FantasyPros archive starts Dec 2019 so the candidate-pool proxy needs a pre-2020 fallback; `spread_line` > 0 = home favored; depth charts have two schemas (`dt` timestamps from 2025); ff_opportunity updates in season; PPR precomputed column includes return TDs. 29 + 1 (legacy depth chart) snapshots committed. A full `twm ingest` (1999–2026) was run the same evening.
- **Phase A audit** (2026-09-26): a six-lens review of A1–A3 (ingestion, data claims, spec compliance, fresh clone, code quality, scaffold) found 21 major and 50 minor items. Fixed:
  - *Ingestion* (`src/twm/sources/nflverse.py`): nflreadpy's cache off; drift is checked **before** the Parquet is written (a drifted frame is never cached) and also on current-season cache reads; Parquet and snapshot writes are atomic (temp file + `os.replace`); snapshots are only auto-written from current-season / one-file loads, never from a historical season; `check_drift`'s added/retyped report is logged instead of dropped; an immutable season that comes back with 0 rows is not cached and `cached_seasons()` ignores pre-coverage and 0-row files; `refresh_snapshot()` + `scripts/refresh_snapshots.py` regenerate every snapshot from the cache (2024 for `depth_charts_legacy`); dead code removed, helpful error on unknown dataset names, duplicate-name guard on the registry, explicit `summary_level="week"` on the PFR entries. Tests added for each.
  - *Data claims* (`docs/assumptions.md`): §4 injuries rewritten (`date_modified` is a real UTC report timestamp for 2010–2024; only 2009 and 2025+ need an assumed availability); §8 PPR reconciles exactly (0 rows left, not 2) and the `fumbles_lost_total` vs component-sum caveat for B4; §3 depth-chart cadence (league-wide pulls, some days have two); §6 full `ecr_type` list; report_status eras (`Probable` 2009–2015, `Note` 2024); §10 dtype-drift list and cache facts; a glossary.
  - *Tooling*: pre-commit ruff pinned to the locked 0.16.9; CI uses `uv sync --locked` and current action majors; `uv run pytest` is offline by default; `tests/test_config.py` asserts `settings.yaml` season boundaries match the dataset registry; `scripts/verify_sources.py` is cache-only by default, records rows/cols/errors for every probe, adds a mid-history (2015) probe and a `spread_line` sign check to the report.
  - *Housekeeping*: deleted the four 0-row probe files (`snap_counts/2012`, `schedules/1998`, `draft_picks/1979`, `combine/1999`) and the ~500 MB `data/raw/_nflreadpy_cache`; `config/settings.yaml` `snaps_start: 2013`.

## Next step
- **Owner review of `docs/assumptions.md`** (Phase A stop point). B1 DuckDB warehouse (fact/dim tables, `dim_week` with official as-of timestamps) is in progress in a separate workflow.

## Open questions
- Candidate-pool proxy for 2013–2019 backtests (no preseason rankings before 2020): to be decided at C1 (recommendation: prior-season PPG rank + draft capital for rookies).
- Waiver Radar evaluation starts 2014 with only 2013 as training data for the first fold; consider starting evaluation at 2015 (decide at C4 once we see fold sizes).
- Decision Points 1-7 in spec Section 15 are asked at their phases (F1, E1, H3, I1, G5).
- FTN charting license terms (spec 4.1: attribution / share-alike) must be checked before any public use (E1).

## Deferred (after B1, because `src/twm/cli.py` is locked by the B1 workflow)
- `twm ingest` error handling: exit non-zero when any dataset/season fails, wrap the one-file datasets in the same try/except as the per-season loop (today one transient error there aborts everything after it with a traceback), print the full error message in an end-of-run summary instead of the first 80 characters, never swallow `SchemaDriftError`, and cap `participation` at `current_season - 1` with a "published after the season" note. CliRunner tests for both paths.
- `twm snapshot <dataset> [--season N]` as a CLI wrapper around `refresh_snapshot()` (the script exists; the command is convenience).
- `ingest --start/--end` annotated `int | None`, `start <= end` validation, clamp to the dataset's `first_season` with one message instead of one failure per season.
- `doctor` should actually check (config loads, nflreadpy version vs snapshots, cached seasons vs expected range, `.env` parsed) and exit 1 on failure; B1 is rewriting it.

## Known issues / gotchas
- The Desktop folder appears to be cloud-synced: a stray `src/twm/__init__ 2.py` duplicate appeared once (uv init's original file). If files named `* 2.*` show up, delete them; they are never intentional.
- Python 3.13 skips `.pth` files that carry the macOS `hidden` flag, and the whole `.venv` was found with that flag once. Symptom: `ModuleNotFoundError: No module named 'twm'` from `uv run twm …` or `uv run pytest` even though `.venv/lib/python3.13/site-packages/twm.pth` exists and points at `src`. Fix: `chflags -R nohidden .venv`, then re-run. A fresh `uv venv` is not hidden and the origin of the flag is unknown (cloud sync is the suspect); keep an eye on it (`ls -lO .venv/lib/python3.13/site-packages/*.pth` shows the flags). If `.venv` is recreated: `uv venv --python /opt/homebrew/bin/python3.13 && uv sync --all-extras`, then re-check the flag. (An earlier note blamed the miniconda interpreter; that was wrong, the venv is Homebrew 3.13.12.)
- Drift checks run only on current-season / one-file loads (older seasons legitimately have fewer columns), on both downloads and cache reads. A missing column raises; a dtype change or new column is logged as a warning.
- `uv run pytest` is offline by default (`-m 'not network'` in `pyproject.toml`). `uv run pytest -m network` runs the live schedules drift check, which downloads and writes into `data/raw`.
- `scripts/verify_sources.py` is cache-only by default and skips seasons that are not in `data/raw`; pass `--allow-download` to probe them (network). Its report is `reports/verify_sources.json`.
- Raw cache: the full 1999–2026 history of all 29 datasets is about 0.5 GB (443 Parquet files) with nflreadpy's cache off.
- pre-commit hooks are configured (`.pre-commit-config.yaml`) but only enforced after `uv run pre-commit install` has been run once in the clone (README "First run"); the first run clones the two hook repos (network). Until then ruff and the file-hygiene checks run only in CI.
