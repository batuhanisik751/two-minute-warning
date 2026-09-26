# Progress log (handoff)

Read this first in a new session. Then read `PROJECT_SPEC.md` Sections 0, 6 and 11.

## Decisions made by the owner
- 2026-09-26: Project name stays **Two-Minute Warning**. Repo public, MIT, named `two-minute-warning`. Python **3.13** (spec said 3.12).
- Commits authored by the owner only; no AI co-author trailers.

## Steps done
- **A1 Scaffold** (2026-09-26): uv project, `src/twm/` package skeleton, ruff, pytest, pre-commit, gitignore, `.env.example`, three config YAMLs, typed config loader, `twm` CLI with `version`/`doctor`, GitHub Actions CI.

- **A2 Ingestion** (2026-09-26): `src/twm/sources/nflverse.py`: registry of 29 datasets, per-season Parquet cache under `data/raw` (historical seasons immutable, current season refreshed after 12 h), schema snapshots in `data/schemas/`, `SchemaDriftError` on missing columns (current-season loads only), `twm ingest` CLI. Offline tests with fake loaders.
- **A3 Verification** (2026-09-26): `scripts/verify_sources.py` downloaded 2026/2025/first/first-1 of every dataset (184 MB cache). Findings in `docs/assumptions.md`. Headlines: snap counts start 2013 (not 2012); FantasyPros archive starts Dec 2019 so the candidate-pool proxy needs a pre-2020 fallback; `spread_line` > 0 = home favored; depth charts have two schemas (`dt` timestamps from 2025); injuries have no report date; ff_opportunity updates in season; PPR precomputed column includes return TDs. 29 + 1 (legacy depth chart) snapshots committed; 39 offline tests + 1 network test pass.

## Next step
- **Owner review of `docs/assumptions.md`** (Phase A stop point). Then B1 DuckDB warehouse (fact/dim tables, `dim_week` with official as-of timestamps).

## Open questions
- Candidate-pool proxy for 2013–2019 backtests (no preseason rankings before 2020): to be decided at C1 (recommendation: prior-season PPG rank + draft capital for rookies).
- Waiver Radar evaluation starts 2014 with only 2013 as training data for the first fold; consider starting evaluation at 2015 (decide at C4 once we see fold sizes).
- Decision Points 1-7 in spec Section 15 are asked at their phases (F1, E1, H3, I1, G5).

## Known issues / gotchas
- The Desktop folder appears to be cloud-synced: a stray `src/twm/__init__ 2.py` duplicate appeared once (uv init's original file). If files named `* 2.*` show up, delete them; they are never intentional.
- `scripts/verify_sources.py` is allowed to violate E501 (long expected-column lists); run ruff on it with `--ignore E501`.
- Drift checks run only on current-season / non-seasonal downloads (older seasons legitimately have fewer columns).
- The miniconda Python 3.13.5 at `/opt/miniconda3` ignores the editable-install `.pth` file, so `import twm` fails in a venv built from it. The venv is built from Homebrew's `/opt/homebrew/bin/python3.13`. If `.venv` is recreated: `uv venv --python /opt/homebrew/bin/python3.13 && uv sync --all-extras`.
