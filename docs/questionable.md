# Questionable outcomes (feature #1): model card

The question, for a beginner: "my player is listed Questionable: will he play, and if he
plays, does he score like usual?" The answer is a transparent lookup table of counted rates
from past seasons. No machine learning. Code: `src/twm/modules/questionable/`; frozen table:
`artifacts/production_models/questionable/lookup-<16 hex>.json` (pin `questionable`, `model:
lookup`); reports: `reports/questionable/{backtest,calibration,if_plays}.csv`.

## Data and definitions

- **Rows**: one per QB/RB/WR/TE player-week on a team's final injury report
  (`fact_injury_report`, regular season, 2016-2025) with the game status (`report_status`)
  Questionable or Doubtful, for a week his team played (rows of teams without a game are
  dropped; 2 duplicate player-weeks are deduplicated). Out rows are counted, not modelled:
  Out means he does not play (3,165 rows, 0.06% took a snap).
- **Why 2016**: the NFL dropped "Probable" and redefined "Questionable" in 2016. Earlier
  Questionable tags meant something else (roughly a coin flip under the old 50% wording, and
  Probable players were the near-certain ones), so earlier seasons are not used.
- **Played**: at least one offensive snap in that game (`fact_snaps.offense_snaps > 0`; PFR
  lists only players who took a snap). `fact_roster_week.status` 'ACT' is not game-day active
  and is not used.
- **Practice status** (`practice_status`): Full / Limited / Did not participate / none
  (blank, NULL or 'Note').
- **Missed his team's previous game** (`missed_prev`): no offensive snap in his team's
  previous regular-season game of the season while he was on that team's weekly roster
  (`fact_roster_week`). False in a team's first game.
- **Body part**: the primary injury with 'right'/'left' removed; the 8 most frequent (knee,
  ankle, hamstring, shoulder, foot, illness, groin, hip), the rest 'other'.
- Counts 2016-2025: Questionable 4,294 (63.1% played), Doubtful 520 (1.2% played).

## Groupings tried and the backtest

Every grouping is the game status plus some keys. Each cell is shrunk toward its parent cell
(the same keys without the last one): `rate = (played + 20 x parent rate) / (n + 20)`. The
pseudo-count 20 was fixed before any backtest and never tuned. Walk-forward: test season s
(2018-2025) is scored by a table fit on 2016..s-1 only; 3,752 test rows. Baseline: the overall
rate for the status.

| grouping | pooled log loss | pooled Brier |
|---|---:|---:|
| baseline: status only | 0.6022 | 0.2127 |
| (a) status x practice | 0.5877 | 0.2055 |
| (b) (a) + position | 0.5811 | 0.2021 |
| (c) (a) + missed previous game | 0.5634 | 0.1945 |
| (d) (a) + body part | 0.5869 | 0.2047 |
| (c) + position | 0.5578 | 0.1916 |
| status x missed previous game | 0.5871 | 0.2053 |
| **status x missed previous game x position (chosen)** | **0.5812** | **0.2023** |

Every grouping evaluated (with each season) is in `reports/questionable/backtest.csv`.

## Choosing (the rule, fixed before the backtest ran)

A richer grouping "wins clearly" over the one it refines when its pooled log loss AND pooled
Brier are lower AND its log loss is lower in at least 6 of the 8 test seasons. Forward
selection: keep adding the one key whose refinement wins clearly with the lowest pooled log
loss; stop when none does. With practice: status -> (a) -> (c) -> (c) + position; body part
never wins clearly. Without practice: status -> missed previous game -> + position.

**Why practice status is not used (2025+).** nflverse's 2025 and 2026 injury files come from
a different source (they have no `date_modified`), and their practice statuses do not behave
like 2016-2024's. Questionable players who played, by practice status (Full / Limited / DNP):
2023 71% / 59% / 42%, 2024 69% / 58% / 42%, 2025 59% / 59% / 54%; among players who missed
the previous game the 2025 gradient is gone (50% / 47% / 46%; 2024: 64% / 40% / 11%). On the
2025 test season alone, (a) is worse than the status baseline (log loss 0.648 vs 0.633), and
the best grouping with practice ((c) + position: 0.619 / Brier 0.217) loses to the best one
without it (0.601 / 0.211). The rule in code (`table.select`, `CHECK_SEASON = 2025`): keep
practice only if the with-practice winner beats the practice-free winner on BOTH log loss and
Brier in 2025; it does not, so the chosen grouping is **status x missed previous game x
position** (22 cells). The cost: on 2018-2024 the practice grouping was better (pooled 0.558
vs 0.581). Practice status is still shown on the list, for context. Revisit once the 2026
live record (below) shows whether 2026 practice statuses carry signal again.

## Calibration (walk-forward, chosen grouping)

| chance bucket | rows | predicted | actual |
|---|---:|---:|---:|
| under 30% (nearly all Doubtful) | 408 | 1.5% | 1.2% |
| 30-50% | 552 | 45.2% | 42.4% |
| 50-70% | 1,345 | 60.9% | 59.9% |
| 70-85% | 1,447 | 74.1% | 71.0% |
| 85% and up | 0 | - | - |

The chances run 1-3 points high in the middle buckets; no cell reaches 85%.

## If he plays

For tagged players who played, with 2+ earlier games with a snap that season and 5+ points
per game so far: ratio = that week's points / his points per game so far. Any player's ratio
drifts below 1 (regression to the mean), so the comparison is with healthy players (no injury
report row that week) matched on season, week, position and points-per-game band (5-8, 8-11,
11-14, 14-18, 18+), each healthy row weighted so the healthy pool has the tagged mix. Dud =
below 50% of his usual. `reports/questionable/if_plays.csv`:

| bucket | rows | median ratio | dud rate | healthy median | healthy dud rate |
|---|---:|---:|---:|---:|---:|
| Questionable, all | 1,715 | 0.78 | 32% | 0.85 | 28% |
| Questionable, did not miss the previous game | 1,307 | 0.80 | 31% | 0.85 | 28% |
| Questionable, missed the previous game | 408 | 0.72 | 38% | 0.87 | 27% |
| Doubtful | 4 | - | - | - | - |

A bucket with fewer than 30 rows shows its status's line, or nothing (Doubtful).

## The live list

`twm questionable weekly [--season --week --as-of]` (nightly in `twm pipeline run`): every
QB/RB/WR/TE tagged Questionable or Doubtful for the week whose game has not kicked off, with
the chance he plays, his bucket's "if he plays" line and his points per game so far. Each run
is stored in the predictions store's `questionable_snapshots` table keyed by its as-of,
append-only. Lists fill in as teams post final reports (Friday for Sunday games; Thursday
games earlier in the week). **The tag is not the final word: teams name their inactive
players about 90 minutes before kickoff.**

Which rows the list may see: the warehouse marks a 2025+ injury row as available only at its
team's kickoff (no `date_modified`), so the as-of view hides every 2026 row until the game. A
row the warehouse holds was in nflverse's file when it was downloaded, so the list also reads
the week's rows when the as-of is at or after the build of `fact_injury_report`
(`build_manifest.built_at`, `source = 'observed'`). A time-machine as-of before that build
sees only the as-of view's rows.

Live grading: each player's last snapshot before his kickoff is graded played / did not play
once his game's snap counts exist (`twm questionable grade`; `weekly.summary` gives n, the
average chance and the share that played).

## Frozen and pinned

`twm questionable build` writes the reports; `twm questionable pin` rebuilds the table and
approves it only if it reproduces them, writes the JSON and its pin. `twm model check
questionable` (and `all`) checks the sha256 before reading, the version (the hash of the
content) and that the JSON's backtest, calibration and buckets equal the committed CSVs. The
nightly runner never rebuilds the table; it only applies it.

## Limits

- Tags are team-reported and teams differ in how they use them.
- Thursday and Saturday games: the final report comes out earlier in the week, so those
  players drop off the list before Sunday's are added.
- Late scratches and game-time decisions are not in the injury report; the tag is not the
  final word (inactives about 90 minutes before kickoff).
- The 2016 rule change: earlier seasons are not comparable and are not used.
- "Played" means one offensive snap: a player who plays 3 snaps and leaves counts as played.
- 2025+ practice statuses are not comparable with earlier seasons (above).
