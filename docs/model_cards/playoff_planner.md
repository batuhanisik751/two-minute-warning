# Model card: Playoff planner (`playoff_planner`)

Source: `config/production_models.yaml`
Pin key `playoff_planner`, pin id (model_version) `matchup-d2c2fa511bc3045d`, `model: matchup`,
approved 2026-10-06, scoring season 2026. Every number below is copied from the file named on
the `Source:` line above it (`tests/test_model_cards.py` checks this).

## Purpose and intended use

Source: `docs/playoff_planner.md`, "Playoff planner (feature #6)"
Which players (or free agents) have easy or hard matchups in the fantasy playoff weeks 15, 16
and 17, and how much that matters, per position, from 13 seasons of walk-forward tests. It
answers a beginner's planning question in October or November. **Not intended for:** a weekly
start/sit call on its own; a matchup moves points a little, a role or an injury moves them more.

## Inputs and point-in-time rules

Source: `docs/playoff_planner.md`, "Data and definitions"
- Unit-games: QB / RB / WR / TE from `fact_player_week` (PPR), kickers from `fact_kicker_week`,
  D/ST from `fact_defense_week`; positions are the point-in-time `fact_roster_week.position`.

Source: `docs/playoff_planner.md`, "Backtest"
- Test seasons 2013-2025; as of after week 4, 8, 12 and 14 of a season, only games of weeks up
  to the as-of enter a rating or a base (at least 2 games; a base of 5.0 or more for QB / RB /
  WR / TE). Live, the grid reads the completed weeks through the as-of view.

## Model and training

Source: `docs/playoff_planner.md`, "Matchup ratings"
A rating rule, no machine learning: points a team allowed to a position per game over the
league's average (1.00 is an average matchup), shrunk toward 1.00 with k pseudo-games estimated
once on 2006-2012: D/ST 7.6, RB 11.8, K 26.7, QB 33.4, WR 52.4; TE capped at 100 (no
between-team variance found). `adjusted` also divides by what the units faced usually score.

Source: `reports/playoff_planner/report.md`, "Chosen (rule)"
The rule fixed before the backtest chose, per position: QB adjusted, RB shrunk, WR adjusted,
TE none, K none, DST adjusted.

## Evaluation

Source: `reports/playoff_planner/report.md`, "## backtest"
Walk-forward points MAE in weeks 15-17, pooled over the four horizons and 2013-2025 (none / raw
/ shrunk / adjusted): QB 6.701449 / 6.964601 / 6.686017 / 6.684222 (4275 rows); RB 6.129182 /
6.192336 / 6.101745 / 6.104366 (7455); WR 6.052334 / 6.113291 / 6.043075 / 6.042922 (12146);
TE 5.232375 / 5.500265 / 5.230304 / 5.230378 (4383); K 3.609810 / 3.854836 / 3.614589 /
3.613798 (4387); DST 5.548044 / 5.673214 / 5.403626 / 5.397293 (4984). Every position pooled
(37630 rows): none 5.694249, raw 5.832678, shrunk 5.665260, adjusted 5.664603.

## Calibration

Source: `reports/playoff_planner/report.md`, "## effects"
Best vs worst fifth of matchups by the as-of raw rating, points per game (raw rating's gap /
shrunk rating's gap / realized gap): DST 8.779098 / 4.297045 / 4.321718; QB 7.857459 /
1.569884 / 1.821652; RB 5.649876 / 2.227001 / 1.570833; WR 4.633114 / 0.630874 / 0.946007; TE
6.567336 / 0.499055 / 0.483605; K 4.932138 / 1.121532 / 0.178171. The raw ratings overstate
the effect; the shrunk ones are close to it, except the kicker's.

## Known limits and failure cases

Source: `reports/playoff_planner/report.md`, "## stability"
- Ratings drift: the rank correlation of a team's as-of raw rating with its weeks 15-17 rating,
  mean over 13 seasons, as of week 4 and week 14: DST 0.264819 and 0.414407; RB 0.109534 and
  0.244582; QB 0.139234 and 0.157476; WR 0.099299 and 0.105742; TE 0.065413 and 0.069301; K
  0.018862 and 0.061762.

Source: `docs/playoff_planner.md`, "Limits"
- Injuries, weather and role changes move points far more than the opponent; players who did
  not play are not scored; teams resting starters fell on week 17 until 2020 (week 18 since
  2021, outside the default playoff weeks).

## Owner decisions that shaped it

Source: `docs/playoff_planner.md`, "Backtest"
- The owner approved building feature #6 with a selection rule fixed before scoring and no
  override by the builder: per position, a candidate must lower the pooled MAE and win at least
  7 of the 13 test seasons. TE and K kept `none`, as the rule chose; the planner still shows
  their opponents and says plainly that the ratings did not help.

## Reproducibility

Source: `docs/playoff_planner.md`, "Frozen and pinned"
`uv run twm playoff_planner build`, then `uv run twm playoff_planner pin` (refused unless it
reproduces `reports/playoff_planner/`); `uv run twm model check playoff_planner` checks the
sha256, the version and the reports. The nightly runner never rebuilds it.

## Ethical notes

Teams and players are named only from public NFL data. The planner is about fantasy matchups;
it makes no claim about any player's health.
