# Model card: Questionable outcomes (`questionable`)

Source: `config/production_models.yaml`
Pin key `questionable`, pin id (model_version) `lookup-5f0566085a6b1f24`, `model: lookup`,
approved 2026-10-05, scoring season 2026. Every number below is copied from the file named on
the `Source:` line above it (`tests/test_model_cards.py` checks this).

## Purpose and intended use

Source: `docs/questionable.md`, "The live list"
For every QB/RB/WR/TE tagged Questionable or Doubtful whose game has not kicked off: the chance
he plays (players like him in past seasons) and, if he plays, how tagged players scored
against their usual. It answers a beginner's "my player is listed Questionable: will he play,
and does he score like usual?" **Not intended for:** replacing the inactives list (teams name
their inactive players about 90 minutes before kickoff); betting.

## Inputs and point-in-time rules

Source: `docs/questionable.md`, "Data and definitions"
- History rows: QB/RB/WR/TE player-weeks with the game status Questionable or Doubtful on the
  team's final injury report, regular season, 2016-2025, teams with a game that week. Played =
  at least one offensive snap (`fact_snaps`). Out rows are counted, not modelled.
- Questionable 4,294 (63.1% played), Doubtful 520 (1.2% played).

Source: `docs/questionable.md`, "Which rows the list may see"
- The live list reads the injury rows the as-of view shows, plus the warehouse's rows of the
  week when the as-of is at or after the build of `fact_injury_report` (2025+ rows have no
  `date_modified`, so the as-of view hides them until kickoff).

## Model and training

Source: `docs/questionable.md`, "Choosing (the rule, fixed before the backtest ran)"
A lookup table, no machine learning: status x missed his team's previous game x position, each
cell shrunk toward its parent with a fixed pseudo-count (`rate = (played + 20 x parent rate) /
(n + 20)`), fit on every season before the pinned one. Chosen by forward selection with a rule
fixed before the backtest: lower pooled log loss and Brier, and lower log loss in at least 6
of the 8 test seasons. Practice status is kept only if it helps in 2025 (it does not).

## Evaluation

Source: `reports/questionable/report.md`, "## backtest"
Walk-forward (each test season scored by a table fit on the seasons before it; test
seasons 2018-2025; 3752 test rows), pooled log loss / Brier:
status only 0.602242 / 0.212709; status x practice 0.587727 / 0.205509; the chosen grouping
(missed_prev+position) 0.581199 / 0.202348; with practice (practice+missed_prev+position)
0.557816 / 0.191583. Test season 2025: status only 0.632681, the chosen grouping 0.600981,
practice+missed_prev+position 0.619168 (log loss).

## Calibration

Source: `reports/questionable/report.md`, "## calibration"
Walk-forward, chosen grouping (bucket: rows, mean chance, share played): <30%: 408, 0.014606,
0.012255; 30-50%: 552, 0.452471, 0.423913; 50-70%: 1345, 0.609302, 0.598513; 70-85%: 1447,
0.740754, 0.710435; 85%+: none.

Source: `reports/questionable/report.md`, "## if_plays"
If he plays (median ratio to his usual, share below half, healthy matched): Questionable all
1715 rows, 0.776978, 0.321866 vs 0.851117, 0.276031; after missing the previous game 408 rows,
0.718352, 0.375000 vs 0.865356, 0.268386.

## Known limits and failure cases

Source: `docs/questionable.md`, "Limits"
Tags are team-reported; late scratches are not in the report; played means one snap; earlier
seasons than 2016 are not comparable; 2025+ practice statuses are not comparable with earlier
seasons; Thursday-game players drop off the list before Sunday's are added.

## Owner decisions that shaped it

Source: `docs/questionable.md`, "Why practice status is not used (2025+)"
The owner approved the brief (a transparent table, honest backtested numbers).
The reviewer asked for the 2025+ practice check; by the coded rule practice was dropped.

## Reproducibility

`uv run twm questionable build` (reports) and `uv run twm questionable pin` (refused unless
the rebuilt table reproduces the reports); `uv run twm model check questionable` checks the
sha256 before reading, the version and the reports.

## Ethical notes

Injury tags concern real people's health: the list repeats only what teams publish and says
nothing about a player beyond his tag and his team's past patterns.
