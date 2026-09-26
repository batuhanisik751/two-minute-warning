# Progress log (handoff)

Read this first in a new session. Then read `PROJECT_SPEC.md` Sections 0, 6 and 11.

## Decisions made by the owner
- 2026-09-26: Project name stays **Two-Minute Warning**. Repo public, MIT, named `two-minute-warning`. Python **3.13** (spec said 3.12).
- Commits authored by the owner only; no AI co-author trailers.

## Steps done
- **A1 Scaffold** (2026-09-26): uv project, `src/twm/` package skeleton, ruff, pytest, pre-commit, gitignore, `.env.example`, three config YAMLs, typed config loader, `twm` CLI with `version`/`doctor`, GitHub Actions CI.

## Next step
- A2 Ingestion wrapper (`src/twm/sources/nflverse.py` with caching + schema snapshots).

## Open questions
- None blocking. Decision Points 1-7 in spec Section 15 are asked at their phases (F1, E1, H3, I1, G5).

## Known issues / gotchas
- The miniconda Python 3.13.5 at `/opt/miniconda3` ignores the editable-install `.pth` file, so `import twm` fails in a venv built from it. The venv is built from Homebrew's `/opt/homebrew/bin/python3.13`. If `.venv` is recreated: `uv venv --python /opt/homebrew/bin/python3.13 && uv sync --all-extras`.
