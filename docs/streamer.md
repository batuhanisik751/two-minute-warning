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
`cli.py` the commands); scoring in `src/twm/scoring_kdst.py` (docs/scoring.md); tables
`fact_kicker_week`, `fact_defense_week`, `fact_ranking_kdst` (docs/warehouse.md). Reports:
`reports/streamer/pool_labels.md` (pool sizes and start rates) and `reports/streamer/backtest.md`
(the backtest). Tests: `tests/test_scoring_kdst.py`, `tests/test_kdst_warehouse.py`,
`tests/test_streamer_pool.py`, `tests/test_streamer_labels.py`,
`tests/test_streamer_features.py`, `tests/test_streamer_backtest.py`.

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

In the pool, 24.6% of the kickers and 37.4% of the D/STs scored like a starter the next week
(2013-2025). Those are the numbers to beat: picking at random from the pool would be right
that often.

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
| a random pool pick (base rate) | 24.6% | 37.4% |
| model: top 5 (precision@5) | 37.9% (36.0-39.6) | 39.1% (36.1-42.2) |
| best simple rule: top 5 | 36.6% (last game; points per game the same) | 40.2% (next opponent) |
| model: #1 pick | 42.9% (38.2-47.8) | 46.5% (39.4-53.0) |
| best simple rule: #1 pick | 41.0% (last game) | 49.8% (next opponent) |

- **Kickers**: the model is ahead of every simple rule, but only by about 1 pick in 100 on the
  top 5 against "last game" and "points per game", and the interval of that gap includes zero
  (-0.4 to +2.9 points). It is clearly better than "next opponent" alone, mostly because that
  rule happily ranks practice-squad kickers who will not play.
- **D/STs**: the model beats "last game" (clearly) and "points per game" (almost clearly), but
  NOT "stream against the weakest offense": that rule is 1 point better on the top 5 and 3
  points better on the #1 pick (neither gap is certain). For D/STs the old streaming rule of
  thumb is as good as our model.
- **Probabilities**: the model also gives each pick a chance of starting. For kickers these
  chances are better than saying "25%" for everyone, but unreliable at the extremes; for D/STs
  they are WORSE than saying "37%" for everyone. Read the list as an order, not as percentages.

In plain words: streaming works (the top of any sensible list starts far more often than a
random free agent), but on Tuesday, without betting lines or weather, most of what can be known
is already in last week's points (kickers) or in the opponent's scoring (D/STs). The model adds
little on top.

## Limitations

- **Tuesday knowledge only**: no betting lines, weather, injury news or depth-chart changes
  after Tuesday. A Friday check of the weather and the injury report still matters.
- **Small samples**: each season gives only about 80-300 pool rows per position, so every number
  above has a range of a few points, and one unusual season can move the pooled result.
- **The pool is an estimate** of your waiver wire (see above): where FantasyPros rostership
  exists (late 2020 on), 93-100% of pool rows were rostered in fewer than half of leagues.
- **Scoring and league shape**: ESPN default scoring (docs/scoring.md) with yards-allowed tiers
  off, and a 12-team league with one K and one D/ST. Other settings change who counts as a top
  12 and would need a new backtest.
- **Gaps in the data**: no kicker pool in week 1 of 2012-2015 (rosters of that era are only
  published after the games), no answer for bye weeks or after the last regular-season week,
  and the FantasyPros weekly ranks start in late 2020 and are one week old on Tuesday.
- **Some trained models found nothing**: in a few seasons the chosen model had no signal left
  (a constant score) and ranked by id instead; `reports/streamer/backtest.md` marks those
  seasons ("constant model"). The logistic regression kept for kickers had one (2018).
- **Probabilities are calibrated on one season** (about 80-300 rows), which is noisy; see above.
- The models are trained once before each season (no updates during the season).

## Commands

```bash
uv run twm streamer dataset                  # pool + features + labels -> data/streamer/dataset.parquet
uv run twm streamer backtest                 # the backtest -> reports/streamer/backtest.md (+ .csv)
uv run twm streamer backtest --store PATH    # ... and store every prediction in a predictions store
```

`twm streamer backtest` does not write any predictions store unless `--store` is given (step
S1 never writes the owner's `data/predictions.duckdb`). The live weekly list, the site tabs and
the nightly pipeline come in step S2.
