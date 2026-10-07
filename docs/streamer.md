# K and D/ST streamer: which kicker or team defense to pick up for next week (S1)

This page explains, for a first-season fantasy player, what the K and D/ST streamer does, what
it tries to predict, what it knows and does not know on the day it makes its list, and how good
it turned out to be when we replayed thirteen past seasons (2013-2025). Short version: the
model is **a little better than the simple rules for kickers and about as good as the best
simple rule for team defenses, and the gaps are small**. The details and the honest numbers are
below; the full tables are in `reports/streamer/backtest.md`.

Code: `src/twm/modules/streamer/` (`pool.py` who is probably on waivers, `labels.py` the target,
`features.py` what the model knows, `dataset.py` all three in one table, `models.py` the
baselines and models, `backtest.py` the walk-forward backtest, `backtest_report.py` its report,
`production.py` the approved K model and D/ST rule with their pins, `confidence.py` the chance
and priority, `reasons.py` the reasons, `weekly.py` the weekly list, `cli.py` the commands); scoring in `src/twm/scoring_kdst.py` (docs/scoring.md); tables
`fact_kicker_week`, `fact_defense_week`, `fact_ranking_kdst` (docs/warehouse.md). Reports:
`reports/streamer/pool_labels.md` (pool sizes and start rates) and `reports/streamer/backtest.md`
(the backtest). Tests: `tests/test_scoring_kdst.py`, `tests/test_kdst_warehouse.py`,
`tests/test_streamer_pool.py`, `tests/test_streamer_labels.py`,
`tests/test_streamer_features.py`, `tests/test_streamer_backtest.py`,
`tests/test_streamer_production.py`, `tests/test_streamer_weekly.py`.

## What streaming is

In the owner's league (`config/league.yaml`: 12 teams) every team starts one kicker (K) and one
team defense / special teams (D/ST). Unlike a running back, a kicker's or a defense's score
jumps around a lot from one week to the next and depends heavily on the opponent: a defense
facing a turnover-prone offense can score 15 points, the same defense facing a great offense
can score -3. So many players do not keep one K or D/ST all season. Each week they drop the
current one and pick up a free one with a good matchup that week. That is **streaming**.

The streamer answers one question every Tuesday of the season: *among the kickers and D/STs
that are probably still on the waiver wire, which ones are most likely to score like a starter
next week?* It ranks them, best first.

How K and D/ST points are scored (ESPN's defaults, `config/scoring.yaml`) is in
docs/scoring.md: for a kicker, field goals by distance (3 to 6 points), -1 for a miss, 1 per
extra point; for a D/ST, sacks, interceptions, fumble recoveries, blocked kicks, safeties,
return touchdowns and a bonus or penalty for the points the opponent scored.

## Who is on the list (the pool)

We cannot see your league's waiver wire in the past, so the streamer estimates it the same
way as the Waiver Radar: a kicker or D/ST is **probably on waivers** when he is outside the top
18 (12 teams x 1 slot x 1.5) both in the experts' preseason ranking (FantasyPros from 2020;
last season's points per game before) and in points per game so far this season. That leaves
on average 7 to 20 kickers (most in 2020-2021, when teams kept extra kickers on practice squads)
and 6 to 9 D/STs each Tuesday of 2013-2025 (`reports/streamer/pool_labels.md`).

One thing to know about the kicker pool: it includes kickers who are on a practice squad, in
camp or released. They almost never kick (2.3% of them finished in the top 12 the next week,
against 36% of the pool kickers who kicked in their team's last game). Any good list pushes
them to the bottom; the model and two of the simple rules do.

## The target: did he score like a starter next week?

For each pool kicker or D/ST on a Tuesday, the right answer (the label `y_start`) is **yes** if
he finished in the top 12 of his position the following week (12 = teams x starting slots: in
a 12-team league, a top-12 week is a week worth starting). The label covers one week only,
because a streamer is picked up for one game. Some weeks have no answer and are left out: his
team's bye week, the week after the last regular-season week, and weeks not yet played. A
kicker who does not kick at all next week (inactive, released) counts as a **no**.

In the pool, 24.6% of the kickers and 34.4% of the D/STs scored like a starter the next week
(2013-2025; D/STs scored with the owner's league's yards-allowed tiers since step C1, 37.4%
before). Those are the numbers to beat: picking at random from the pool would be right that
often.

## What the streamer knows on Tuesday (the features)

Everything the model sees was public on that Tuesday (the warehouse's point-in-time rule,
docs/warehouse.md; leakage tests delete or change later data and check that nothing moves):

- the kicker's or D/ST's own fantasy points so far (per game, last game, rank) and the experts'
  preseason rank;
- kickers: field-goal and extra-point attempts per game, long attempts, accuracy by distance,
  whether he kicked in his team's last game;
- D/STs: sacks, takeaways, return touchdowns and points allowed per game so far;
- his team's offense: points per game, red-zone trips and how often they stall (end without a
  touchdown, the drives that give a kicker field-goal tries);
- next week's opponent **so far this season**: points it allowed and scored per game, red-zone
  trips allowed, sacks allowed, giveaways;
- next week's game: home or away, dome or retractable roof (when the stadium is known ahead);
- the FantasyPros weekly rank visible on Tuesday (from late 2020 only; it is last week's list).

The full list with formulas is in docs/glossary.md (module "streamer").

## Why no betting lines (and no weather)

A popular streaming clue is what the betting market expects from next week's game (a team
expected to score many points should give its kicker more chances; a defense facing a team
expected to score little should do well). But the lines in our data are **closing** lines, set
right before kickoff. On the Tuesday when you make your
waiver claim, next week's closing line does not exist yet. Using it in a backtest would let the
model peek at the future and make it look better than it can ever be in real use
(PROJECT_SPEC 6.4). So the streamer uses the opponent's strength so far instead. Weather has
the same problem (the forecast for Sunday is not known on Tuesday), so it is left out too.

## How we tested it: replaying past seasons (walk-forward)

To judge the streamer on, say, 2019, we pretend it is early September 2019: the model may learn
only from 2012-2018, and only from answers that were public by the first Tuesday of 2019. It
then ranks the pool every Tuesday of 2019, and we compare its lists with what happened. We did
this for every season from 2013 to 2025 (2013 learns from 2012 alone), with one model for
kickers and one for D/STs. The code refuses to train on a row of the tested season or on an
answer that was not public yet (tests check both).

Three simple rules, which need no model at all, are the **baselines** the model must beat:

1. **Last game**: rank by the fantasy points of his last game.
2. **Points per game**: rank by his average so far this season.
3. **Next opponent**: kickers, rank by how many points the next opponent allowed per game;
   D/STs, by how FEW points the next opponent scored per game ("stream against bad offenses").

Two models were tried: a regularized logistic regression and LightGBM (a tree model). LightGBM
would be kept only if it beat the logistic regression; it did not, for either position.

We grade each Tuesday list by **precision@5** (how many of the top 5 scored like a starter),
**precision@3**, and **did the #1 pick start**. The ranges in brackets are 95% intervals that
resample whole seasons: seasons differ a lot, so this is the honest amount of uncertainty.

## The honest result

Share of picks that scored like a starter the next week, 2013-2025 (from
`reports/streamer/backtest.md`):

| | Kickers | D/STs |
|---|---|---|
| a random pool pick (base rate) | 24.6% | 34.4% |
| model: top 5 (precision@5) | 38.4% (36.3-40.3) | 36.9% (33.9-40.0) |
| best simple rule: top 5 | 36.9% (last game; points per game 36.7%) | 37.3% (next opponent) |
| model: #1 pick | 42.4% (37.1-47.6) | 49.3% (40.6-57.1) |
| best simple rule: #1 pick | 40.0% (last game) | 52.1% (next opponent) |

(Numbers of the S2a re-run: ties are now broken by last game's points instead of by id, and a
tuning setting that makes a constant model is skipped; S1d's first run had kickers at 37.9%.
The D/ST column is the C1 re-run (2026-09-30) with the owner's league's D/ST scoring, which adds
yards-allowed tiers: the pool, the labels and the points features of D/STs changed, kickers did
not. Before C1: base rate 37.4%, model top 5 39.1%, rule 40.3%, model #1 46.5%, rule 49.8%.)

- **Kickers**: the model is ahead of every simple rule, but only by about 1 pick in 70 on the
  top 5 against "last game" and "points per game", and the interval of that gap includes zero
  (-0.2 to +3.0 points). It is clearly better than "next opponent" alone, mostly because that
  rule happily ranks practice-squad kickers who will not play.
- **D/STs**: the model beats "points per game" and "last game" (both clearly on the top 5: +2.6
  and +2.4 points), but NOT "stream against the weakest offense": that rule is 0.4 points
  better on the top 5 and 2.8 points better on the #1 pick (neither gap is certain). For D/STs the old streaming rule of
  thumb is as good as our model, so **the weekly D/ST list uses that rule** (below).
- **Probabilities**: the model also gives each pick a chance of starting. For kickers these
  chances are better than saying "25%" for everyone, but unreliable at the extremes; for D/STs
  they are WORSE than saying "34%" for everyone. Read the list as an order, not as percentages.

In plain words: streaming works (the top of any sensible list starts far more often than a
random free agent), but on Tuesday, without betting lines or weather, most of what can be known
is already in last week's points (kickers) or in the opponent's scoring (D/STs). The model adds
little on top.

## The weekly list (S2a)

Every Tuesday as-of, `uv run twm streamer score` ranks that week's pool with the methods the
owner approved, and writes `reports/streamer/weekly/<season>-W<nn>.md`:

- **Kickers: the logistic regression.** It is the fold the backtest would use for the season:
  trained on every earlier season (labels public by the season's first Tuesday), tuned and
  calibrated on the last one. A tuning setting whose model gives every kicker the same score is
  never chosen, and the production fold is refused if it is constant. Kickers with the same
  score are ordered by **last game's points, then points per game, then preseason rank**
  (never by an id, unless all of these are equal). The 2026 model (trained 2012-2025) kept one
  feature, "he kicked in his team's latest game": in practice, team kickers first, ordered by
  last game's points.
- **D/STs: a simple rule, not a model.** Ranked by how many points next week's opponent scores
  per game, fewer first (ties: last game's points, then points per game). In the 2013-2025
  backtest this rule picked starters a little more often than our model (top 5: 37.3% vs
  36.9%; #1 pick: 52.1% vs 49.3%; with the league's D/ST scoring, C1), so a method that lost
  is not shipped. A D/ST has no model
  probability.
- **Chance and its range**: read off the frozen backtest of the seasons BEFORE the list's
  season (a reconstructed 2020 list never uses 2020-2025 results). Kickers: how often kickers
  the model scored alike (bins of at least 200 backtest picks) started, with a 90% interval.
  A kicker who did not kick in his team's latest game (practice squad, camp, released) instead
  shows how often such pool kickers of those seasons started (flags from the streamer
  dataset): each season's model has its own probability scale, and the 2026 model scores such
  a kicker inside a kicking kicker's bin (audit 2026-10-06; lists made before keep their
  chance). D/STs: how often the rule's pick at that rank started (bins of ranks with at least 100 picks;
  a better rank never shows a lower chance). For 2026 the rule's #1 pick started 52.1% of the
  time, #2 and #3 36.9% (C1, the league's D/ST scoring; before: 49.8%, 41.3%, 39.2%).
- **Priority** from the chance, as on the Waiver Radar: must-add 50%+, speculative 25-50%,
  watch below 25%. In the 2013-2025 backtest no kicker bin reached 50%, so kickers have no
  must-add: streaming picks are coin flips at best, and the list says so. Since C1 (the
  league's D/ST scoring) the D/ST rule's #1 pick started 52.1% of the time (111 of 213 lists),
  so from week 4 of 2026 the top D/ST of each list is a must-add (its interval includes 50%);
  every other D/ST rank is speculative.
- **Why**: plain-English reasons from the registry's sentences (docs/glossary.md, "Streamer
  reason"): for kickers the features that push the model's score up (the Radar's rules), plus
  the tie-breaker when a kicker shares his score; for D/STs the rule's own number.
- **Data check**: the same freshness rules as the Radar (exit code 3 when the week's data has
  not arrived; `--allow-incomplete` marks the list), plus kicker and D/ST rows for every played
  game. **Live or reconstructed**: 'live' only when run on the real clock between the Tuesday
  as-of and the next week's first kickoff; any other run is stored as 'backtest'.
- **Stored** in the predictions store (`entity_type` 'kicker' / 'team_defense', one version for
  the K model and one for the D/ST rule); a live week is never overwritten. No outcomes are
  stored (the store's outcomes table holds the Radar's labels).
- **Approved and pinned** in `config/production_models.yaml` (`streamer_k`: `model: logit`;
  `streamer_dst`: `model: rule`) with their files under `artifacts/production_models/streamer/`:
  the K model (a pickle, opened only after its sha256 matches), the rule's definition (JSON),
  and the frozen backtests (the K predictions with their labels; the rule's hit-rate table: per
  season, list length and rank, lists and starts). `uv run twm model check` verifies every
  sha256 and that the snapshots reproduce `reports/streamer/backtest.csv`.
- **Published** (step P2) with the other modules by `twm publish` and the scheduled job
  (`streamer_dataset`, `streamer_backtest` = `twm model check streamer`, `streamer_score`):
  `stream_list`, `stream_pick` (every pick of a list), `stream_outcome`, `stream_track_record`,
  plus the 2013-2025 backtest lists for the time machine, read from the frozen backtest in place
  (the D/ST rule is applied again to the dataset and must give the pinned hit-rate table;
  docs/deploy.md "What a publish does").

## The owner's league's D/ST scoring (step C1, 2026-09-30)

The first real `twm league settings-diff` showed that the owner's ESPN league scores D/ST yards
allowed (docs/scoring.md). The owner chose to score them like the league, so
`config/scoring.yaml` gained the league's nine yards-allowed tiers, and everything built on D/ST
points was redone: `twm streamer dataset` (the D/ST pool, labels and points features; 680 pool
flags and 755 labels changed), `twm streamer backtest` (reports/streamer/backtest.md),
`twm streamer pool-labels-report`, and the D/ST rule's frozen hit-rate table, re-approved with
`uv run twm streamer pin --store <store> --pos DST` (only `streamer_dst` changes: its
`hit_rates` sha256, rows and approval date; the rule file and version are the same, since the
rule did not change). The rule still beats the D/ST model, so it stays.

Kickers are unaffected and their approval was not touched: every kicker row of the rebuilt
dataset is identical to the old one, the backtest's kicker rows in backtest.csv are identical,
the new backtest's K predictions equal the pinned snapshot on every column but the run stamps
(`created_at`, `code_version`), and the 2026 K fold retrains to the same version. `--pos DST`
leaves the K model, its snapshot and its pin entry byte for byte, and refuses unless the PINNED
K snapshot still reproduces the new backtest.csv (a re-pin of both would have re-stamped the K
snapshot with the new run's times).

**Lists already made stay as they are.** The live 2026 week-3 list (stored 2026-09-30, before
C1) ranked D/STs with chances read from the old hit-rate table, under the old scoring (no yards
allowed); it is frozen and is not re-scored. Outcomes are graded with the scoring of the time
they are computed: week 3's D/ST outcomes, computed after C1, count yards allowed. Lists from
week 4 on use the new table.

## Limitations

- **Tuesday knowledge only**: no betting lines, weather, injury news or depth-chart changes
  after Tuesday. A Friday check of the weather and the injury report still matters.
- **Small samples**: each season gives only about 80-300 pool rows per position, so every number
  above has a range of a few points, and one unusual season can move the pooled result.
- **The pool is an estimate** of your waiver wire (see above): where FantasyPros rostership
  exists (late 2020 on), 93-100% of pool rows were rostered in fewer than half of leagues.
- **Scoring and league shape**: ESPN default scoring (docs/scoring.md) with the owner's league's
  nine D/ST yards-allowed tiers (since step C1), and a 12-team league with one K and one D/ST. Other settings change who counts as a top
  12 and would need a new backtest.
- **Gaps in the data**: no kicker pool in week 1 of 2012-2015 (rosters of that era are only
  published after the games), no answer for bye weeks or after the last regular-season week,
  and the FantasyPros weekly ranks start in late 2020 and are one week old on Tuesday.
- **Some trained models found (almost) nothing**: a tuning setting whose model scores every
  pick alike (e.g. the strongest L1 penalty) is now skipped, but a model with ONE feature left
  is allowed: the kicker folds of 2021 and of 2026 (the one ranking this season's lists) keep
  only "is his team's kicker", so among team kickers the order is last game's points (the
  tie-breaker). LightGBM's thin 2013 fold (default settings, no tuning) is still constant;
  `reports/streamer/backtest.md` marks it.
- **Probabilities are calibrated on one season** (about 80-300 rows), which is noisy; see above.
- The models are trained once before each season (no updates during the season).

## Commands

```bash
uv run twm streamer dataset                  # pool + features + labels -> data/streamer/dataset.parquet
uv run twm streamer backtest                 # the backtest -> reports/streamer/backtest.md (+ .csv)
uv run twm streamer backtest --store PATH    # ... and store every prediction in a predictions store
```

```bash
uv run twm streamer score                    # this week's list (approved methods) -> store + report
uv run twm streamer score --season 2026 --week 3 --store PATH   # a given week, another store
uv run twm streamer pin --store PATH         # approve: train the K fold, write the rule, pin both
uv run twm streamer pin --store PATH --pos DST   # re-approve one position only (C1: D/ST scoring)
uv run twm model check                       # the Radar's and the streamer's pins (sha256, report)
uv run twm score                             # every module's list (the Radar, then the streamer)
```

`twm streamer backtest` does not write any predictions store unless `--store` is given;
`twm streamer score` writes the configured store (`data/predictions.duckdb`) unless `--store`
is given. `twm streamer pin` reads the store holding the backtest to approve (read-only) and
writes `artifacts/production_models/streamer/` and `config/production_models.yaml`: review,
then commit. The site tabs and the nightly pipeline come in step S2b.
