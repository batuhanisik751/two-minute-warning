# Teammate out (feature #5)

"My team's RB1 / WR1 / TE1 is out this week: which teammate gets his work, and how much?"
The answer comes from every regular-season game since 2013 in which a starter sat: how the
carries and targets he left behind were shared among his teammates. A transparent allocation
table, no machine learning: every number is a ratio of counted sums. Code:
`src/twm/modules/teammate_out/`; frozen table: `artifacts/production_models/teammate_out/`;
reports: `reports/teammate_out/`.

## Data and definitions

- **Seasons**: 2013-2025 regular season (PFR snap counts, `fact_snaps`, start in 2013).
- **Carry share / target share**: a player's carries (targets) over his team's in that game
  (`fact_player_week`, `fact_team_week`). Averages are over the games he played.
- **Played**: at least one offensive snap (`fact_snaps.offense_snaps` > 0) or a carry or target.
- **Position**: PFR's game position, else his weekly-roster position (FB counts as RB).
- **Starter** before a team game: a RB with a carry share of 45% or more, or a WR or TE with a
  target share of 20% or more, averaged over the games he played among his team's previous 4
  games of the season (at least 2 of them).
- **Starter out (an event)**: that starter took no offensive snap while his team played, and he
  was still with the team: a weekly-roster row on the team that week whose status is not CUT,
  TRD, RET, UFA or RFA. Traded, released and retired players are gone, not out.
- **Baseline games**: the team's earlier games of the season in which every starter out in that
  game played (at least 2). A teammate's baseline is his average over the baseline games he
  played (at least 2); the team's baseline volume is its average carries and targets in them.
- **Vacated share**: the absent starter's baseline carry (target) share; summed when two or
  more starters are out.
- **Role**: a teammate's position and usage rank (baseline carry share + target share, then
  snap share) among the teammates who play, the absent starters of his position counted
  first: with the WR1 out, the next receiver is WR2. Deeper than RB3, WR4 or TE2 is "RB+",
  "WR+", "TE+".

Event counts (`reports/teammate_out/events.csv`): 1203 starters sat (RB 566, WR 524, TE 113);
1099 were kept (RB 516, WR 477, TE 106); 95 had no roster row on the team that week and 9 had a
gone status. 984 team games with a starter out and at least one teammate to grade (882 with one
starter out, 102 with two or more), 9053 teammate rows.

## The event study

When a RB starter sits, his team runs about as often as usual (carries 1.019 x its baseline,
targets 1.012 x); when a WR sits, 1.044 x and 0.972 x; when a TE sits, 0.968 x and 0.99 x
(single-starter events).
So the prediction keeps the team's volume at its baseline and moves only the shares.

The allocation table (`reports/teammate_out/allocation.csv`, fit on 2013-2025): of the work an
absent starter leaves, the share a teammate in a role takes on average. It is a ratio of sums
over single-starter events (the sum of the role's share changes over the sum of the vacated
shares), shrunk toward its position group (absent position x teammate position) with 20
teammate-games. Carries are allocated only when a RB is out: a WR's or TE's vacated carry share
is about 0 and its ratio is noise. Examples: with a RB out, the RB2 takes 0.454242 of his
carries and 0.405479 of his targets and the RB3 0.231284 of his carries, while the WR1 loses
targets (-0.156853); with a WR out, the WR2 takes 0.150887 of his targets, the WR3 0.184445 and
the WR4 0.223966, the TE1 0.097421; with a TE out, the TE2 takes 0.262121.

## The prediction

For each teammate: predicted share = his baseline share + allocation[absent position, role] x
the vacated share (summed over the absent starters). Predicted points = (predicted carry share
x the team's baseline carries + predicted target share x its baseline targets) x his PPR points
per opportunity (carry or target) over his baseline games, with 20 opportunities at his
position's past average added (shrinkage: a few lucky games do not make a big number).

## Choosing (the rule, fixed before the backtest ran) and the owner's override

Four candidates, simplest first: **nothing changes** (baseline shares; points = his baseline
points per game), **pro rata** (the vacated shares split among same-position teammates in
proportion to their baseline shares), **group** (the allocation by absent position x teammate
position) and **role** (the allocation table above). Walk-forward: each test season 2016-2025 is
predicted with cells fit only on the seasons before it. The rule, written down before any test
number existed: from "nothing changes", the next candidate replaces the choice only if its
pooled PPR-points MAE is lower and lower in at least 6 of the 10 test seasons.

Pooled walk-forward results (`reports/teammate_out/backtest.csv`; 7751 teammate rows in 840
games; MAE = mean absolute error, lower is better; top gainer = the teammate predicted to gain
the most was the one who gained the most points):

| candidate | points MAE | carry-share MAE | target-share MAE | top gainer |
|---|---|---|---|---|
| nothing changes | 4.360331 | 0.044046 | 0.052034 | 0.114286 |
| pro rata | 4.573741 | 0.040087 | 0.055054 | 0.271429 |
| group | 4.437258 | 0.043131 | 0.051188 | 0.185714 |
| role (used) | 4.393169 | 0.040152 | 0.050800 | 0.269048 |

**The rule picked "nothing changes"**: no candidate had a lower pooled points MAE (role beats it
in 3 of the 10 seasons, pro rata in none). Why: the gains are real on average (teammates in the
same position score more when the starter sits) but a single player's points are skewed (most
weeks a little, some weeks a lot), and the mean absolute error rewards predicting the median.
Adding the average gain moves the prediction toward the mean and costs a little MAE.

**The owner overrode the rule (2026-10-05)** for the feature's purpose: it answers "who gets
the work", and the role table predicts the shares better than "nothing changes" (carry and
target share MAE above) and names the biggest gainer far more often (0.269048 vs 0.114286). The
pin records it: `chosen` role, `rule_choice` nothing, `chosen_by` the owner's reason. Pro rata is
about as good on carry share and the top gainer but the worst on target share and points.
Points are therefore shown as an 80% range, not as a single number to trust.

## The 80% range

From the walk-forward misses of the role predictions (real minus predicted points), by teammate
position and predicted-points band (below 6, 6 to 12, 12 and more; the position's own when a
band has fewer than 30 misses): the 10th and 90th percentiles added to the prediction (the low
end at least 0). Coverage, each test season using only the misses of the test seasons before it
(`reports/teammate_out/coverage.csv`, 2017-2025): 0.783822 overall (RB 0.785412, WR 0.774026,
TE 0.797317). The live list uses the cells of all ten test seasons
(`reports/teammate_out/ranges.csv`).

## The live list

`uv run twm teammate_out weekly [--season --week --as-of --store]` (nightly runner stage
`teammate_out`, after `questionable`; exit 3 = no regular-season week, skipped; any other
failure stops the run). For the next regular-season week: every starter (the same thresholds
over his team's latest games) whose game has not kicked off and who is Out or Doubtful on the
week's injury report or on a reserve list (IR, PUP, suspended ...) while still on the team. The
injury rows are read exactly as the Questionable list reads them (`docs/questionable.md`, "Which
rows the list may see"). For each, his teammates on the team's latest roster with an active
status, not tagged Out or Doubtful themselves, with at least 2 baseline games: role, baseline and
predicted carry and target shares, the change, predicted points and the 80% range, plus the
allocation table's shares (`alloc_*`, the past average shift). A Doubtful starter may still
play; then nobody gains.

Snapshots: the predictions store's table `teammate_out_snapshots`, one append-only snapshot per
as-of (a stored as-of is never rewritten; empty lists are not stored).

## On the site

`twm publish` (`src/twm/publish/teammate_out.py`, migration 0008) publishes every stored snapshot
append-only (`teammate_out_list` / `teammate_out_row`), the pinned table's allocation, backtest,
coverage and event counts, and the live record graded from every published snapshot of the
season plus the night's (a fresh runner's store holds only the night's). The page
`/teammate-out` shows, for the week whose games are next (or `?season=&week=`), each absent
starter and why he is out, his teammates' usual and predicted carry and target shares, the
predicted PPR points with the 80% range, grouped by kickoff; "Where the work goes" (the
allocation table with its counts); the four candidates with the rule's pick, the one used and
the owner's recorded reason (the version's `chosen_by`); the range's coverage; and the season
so far. A player page shows a badge when the player is a predicted gainer (`pred_gain` > 0) or
an absent starter on the week's newest list; `/methodology#teammate-out` and
`/track-record#teammate-out` carry the method and the record.

## Grading

After the games, each teammate's last row before his kickoff is graded against his game
(`weekly.read_actuals`: the warehouse's shares and points; `weekly.grade`): 'played', 'did not
play', 'starter played' (an absent starter played after all: nothing to grade) or pending.
`weekly.summary` gives the live record: MAE of the predicted points and of his baseline points
("nothing changes"), the share MAEs, the range coverage and the top-gainer hit rate.
`uv run twm teammate_out grade` prints it.

## Frozen and pinned

`uv run twm teammate_out build` writes `reports/teammate_out/` (backtest, allocation, ranges,
coverage, events, report.md); `uv run twm teammate_out pin --candidate role --reason "..."`
writes `artifacts/production_models/teammate_out/alloc-<16 hex>.json` and its pin (key
`teammate_out`, `model: alloc`), refused unless it reproduces the committed reports.
`uv run twm model check teammate_out` (and `all`) checks the sha256 and the reports. The runner
never rebuilds it.

## Limits

- Per-player points are noisy: the 80% range is wide on purpose.
- The team's volume is kept at its baseline (the event study shows it barely moves).
- Roles come from usage, not depth charts; a newly signed player with fewer than 2 games is not
  listed.
- QBs are not modelled (a QB out changes every share at once).
