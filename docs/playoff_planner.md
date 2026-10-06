# Playoff planner (feature #6)

A beginner's question in October or November: "which of my players (or free agents) have easy or
hard matchups in my fantasy playoff weeks 15, 16 and 17, and how much does that even matter?"
The honest answer is "a little", and the planner says how much, per position, from 13 seasons
of walk-forward tests. Code: `src/twm/modules/playoff_planner/`; frozen spec:
`artifacts/production_models/playoff_planner/` (pin key `playoff_planner`); reports:
`reports/playoff_planner/`; model card: `docs/model_cards/playoff_planner.md`.

## Data and definitions

- **Unit-game**: one fantasy unit's points in one regular-season game. QB / RB / WR / TE:
  `fact_player_week` scored with config/scoring.yaml (PPR). Kicker: `fact_kicker_week` scored
  with `twm.scoring_kdst.score_kicking_sql`. D/ST: `fact_defense_week` scored with
  `twm.scoring_kdst.score_defense_sql` (the team is the unit).
- **Position**: the point-in-time `fact_roster_week.position` of that week (HB and FB are running
  backs), else the player's most common roster position of the season. `fact_player_week`'s own
  `position` is today's snapshot and is not used.
- **Team-game**: a `fact_defense_week` row; a position with no unit scoring in a game counts 0.
- **Opponent**: a rating is always about the opponent: the defense a QB / RB / WR / TE / K
  faces, or the offense a D/ST faces (the D/ST points that offense gives up).

## Matchup ratings

For a position P and a team X, from X's games through a week: the points units at P scored
against X per game over the league's average per team-game at P (`lg_ppg`). 1.00 is an average
matchup; 1.20 means units at P scored 20% more than average against X.

- `raw`: allowed / (games x lg_ppg).
- `shrunk`: (allowed + k x lg_ppg) / ((games + k) x lg_ppg): k pseudo-games at exactly 1.00.
- `adjusted` (schedule-adjusted): (allowed + k x lg_ppg) / (expected + k x lg_ppg), where
  `expected` sums what the units X faced usually score (their own mean at P in their other
  games, shrunk to lg_ppg with k games).

The pseudo-games k per position are sigma2_within / sigma2_between of a team's per-game points
allowed (over the season's league average), estimated once on 2006-2012, before every test
season: D/ST 7.6, RB 11.8, K 26.7, QB 33.4, WR 52.4 (rounded to 8, 12, 27, 33, 52). For TE the
between-team variance was not above zero (estimate -0.00008): k is capped at 100. That alone
says a lot: over a full season, how many points a defense allows to tight ends is almost all
noise; to D/ST (an offense's giveaways) it is the most real.

## Backtest

Walk-forward, test seasons 2013-2025. As of after week H (4, 8, 12 and 14) of a season, each
unit's **base** is its points per game to date (at least 2 games; QB / RB / WR / TE need a base
of 5.0 or more PPR points: fantasy-relevant players). Its points in each of weeks 15, 16 and 17
are predicted as base x the multiplier of its opponent that week, from ratings of weeks <= H
only (nothing after the as-of enters a rating or a base). A unit is scored in a week it played
for the same team; a player who did not play has no points to predict. Candidates: (a) `none`
(base only), (b) `raw`, (c) `shrunk`, (d) `adjusted`.

**The rule (fixed before any test number was seen), per position, pooled over the four
horizons:** from `none`, the next candidate (raw, shrunk, adjusted) replaces the current choice
only if its pooled points MAE is lower AND lower in at least 7 of the 13 test seasons.

## Results (the pinned spec, `reports/playoff_planner/`)

Pooled points MAE over the four horizons and 13 seasons (lower is better), the seasons a
candidate beat the current choice, and the rule's choice:

| position | none | raw | shrunk | adjusted | seasons won | chosen |
|---|---|---|---|---|---|---|
| QB | 6.701 | 6.965 | 6.686 | 6.684 | shrunk 7 of 13 vs none; adjusted 10 vs shrunk | adjusted |
| RB | 6.129 | 6.192 | 6.102 | 6.104 | shrunk 10 vs none; adjusted 5 vs shrunk | shrunk |
| WR | 6.052 | 6.113 | 6.043 | 6.043 | shrunk 10 vs none; adjusted 7 vs shrunk | adjusted |
| TE | 5.232 | 5.500 | 5.230 | 5.230 | shrunk 6, adjusted 6 vs none | **none** |
| K | 3.610 | 3.855 | 3.615 | 3.614 | shrunk 6, adjusted 6 vs none | **none** |
| D/ST | 5.548 | 5.673 | 5.404 | 5.397 | shrunk 10 vs none; adjusted 8 vs shrunk | adjusted |

The raw rating is worse than no matchup at all at every position: a few weeks of points allowed
are mostly noise. Shrunk, the ratings help a little at QB, RB, WR and D/ST; for tight ends and
kickers they do not (every matchup counts as 1.00, and the planner says so).

**How much it matters** (all horizons): players facing the easiest fifth of matchups by the
as-of raw rating vs the hardest fifth, points per game in weeks 15-17. "Rated" is what the raw
ratings implied, "realized" what happened relative to each player's base:

| position | rated gap | realized gap | survived |
|---|---|---|---|
| D/ST | 8.8 | 4.3 | 49% |
| QB | 7.9 | 1.8 | 23% |
| RB | 5.6 | 1.6 | 28% |
| WR | 4.6 | 0.9 | 20% |
| TE | 6.6 | 0.5 | 7% |
| K | 4.9 | 0.2 | 4% |

So an easy playoff schedule is worth about 1 to 2 points per game for a QB, RB or WR (against a
week-to-week miss of about 6 points), about 4 for a D/ST, and next to nothing for a TE or K.
The shrunk ratings' own gaps (D/ST 4.3, QB 1.6, RB 2.2, WR 0.6, TE 0.5, K 1.1) are close to
what was realized, except the kicker's.

**Stability**: the Spearman rank correlation of a team's as-of raw rating with its rating in
weeks 15-17 alone, averaged over the 13 seasons (as of week 4 / 14): D/ST 0.26 / 0.41, RB 0.11 /
0.24, QB 0.14 / 0.16, WR 0.10 / 0.11, TE 0.07 / 0.07, K 0.02 / 0.06. Defenses change during a
season (injuries, trades, coaching), and three games are a small sample.

**Late weeks** (as of week 14, the share of based players who played that week): through 2020,
when week 17 was the last NFL week, the share drops more from week 16 to week 17 (QB 0.68 to
0.62, RB 0.73 to 0.68, TE 0.75 to 0.68, WR 0.79 to 0.73) than since 2021 (QB 0.63 to 0.59, RB
0.75 to 0.73): teams that had clinched rested starters in the final week. Since 2021 the final
week is 18, outside the fantasy playoffs. `reports/playoff_planner/late_weeks.csv` has every
cell.

## Frozen and pinned

`uv run twm playoff_planner build` runs the backtest on the warehouse and writes
`reports/playoff_planner/{backtest,effects,stability,late_weeks}.csv` and `report.md` (nothing is
pinned); `uv run twm playoff_planner pin` rebuilds the spec, refuses unless it reproduces those
reports, writes `artifacts/production_models/playoff_planner/matchup-<16 hex>.json` (the rule,
the pseudo-games, the chosen candidate per position with `rule_choice`, `path` and `chosen_by`,
and every report row) and pins it (`model: matchup`). An override needs `--candidate
POS=candidate --reason "who, when, why"` and is recorded as `chosen_by`; the 2026 pin uses the
rule's choice. `uv run twm model check playoff_planner` (and `all`) checks the sha256, the
version and the reports. The nightly runner never rebuilds it.

## Live

`uv run twm playoff_planner weekly [--season S] [--as-of T] [--weeks 15,16,17]`: through the
as-of view, the season's games of the **completed weeks** (a week whose every game is in the
warehouse, or kicked off more than 3 days ago and never arrived: a cancelled game) and the
schedule. For every NFL team, week and position: the opponent (none on a bye), the opponent's
ratings with the pinned spec, the chosen candidate's `rating` (1.00 where the rule chose
`none`) and its rank among the 32 teams (1 = the easiest). Stored in the predictions store's
`playoff_planner_snapshots` table, append-only, one snapshot per (season, completed week); exit
3 when nothing is to do (no completed week, the playoff weeks are complete, or this completed
week is stored already). The nightly runner runs it after Teammate out.

**Grading** (`uv run twm playoff_planner grade`): each (week, team, position)'s last snapshot
rated before that week (`through_week` < week: its ratings hold no game of that week; kickoff
times of December games are often unset in October) against the realized multiplier (the
team's units' points at that position in that game / the snapshot's `lg_ppg`): MAE with the
ratings vs a flat 1.00.

**My League** (local, `twm league weekly`, ESPN optional): "Your playoff weeks": each rostered
player and the Radar's two best free agents per list with their opponents and ratings in the
league's playoff weeks (from its synced settings: the weeks after `reg_season_count`, one round
per `playoff_matchup_period_length` weeks; 15-17 if the settings say nothing), the mean rating,
and the "how much it matters" lines. Absent without a synced roster or a stored grid.

## Published and on the site (P6b)

`twm publish` carries the module (`src/twm/publish/playoff_planner.py`, migration 0009; see
docs/deploy.md): the snapshots, append-only by (season, through_week); the pinned choice per
position; the candidates' horizon-pooled backtest with the rule replayed from the per-season
MAEs (the seasons each beat the choice it was compared with); the effect sizes, stability and
late-weeks rows; and the live record, graded over the published snapshots plus the night's and
**empty until a game of week 17 is in** (the record is read after the fantasy playoffs).

`/playoff-planner` (nav "Playoffs"): (a) the newest snapshot's grid for weeks 15-17 (leagues
differ; the site uses 15-17): per position, every team's opponent each week, the rating with a
word (easy at 1.05 or more, hard at 0.95 or less, else neutral) and its rank, and the total over
the games (a bye adds nothing), sortable by team, week or total and filtered by position with
links; TE and K (rule: none) show the schedule only and say why; (b) "How much do matchups
matter?": the rated vs realized gaps, the stability and the resting note (late weeks + `NOTE`);
(c) the candidates per position with the rule's pick; (d) the season's live record. A player
page shows a "Playoff weeks" line when his position is rated; `/methodology#playoff-planner`
and `/track-record#playoff-planner` carry the method and the record.

## Limits

- Injuries, weather and role changes move a player's points far more than his opponent.
- A rating is about a team's season to date; trades and injuries on the defense change it
  (the stability above).
- Players who did not play are not scored, so the backtest says nothing about whether a hard
  schedule makes a player sit.
- Teams resting starters: measured above for the old 17-week seasons; since 2021 it falls on
  week 18, outside the default playoff weeks.
