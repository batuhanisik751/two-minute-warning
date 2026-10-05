# Model card: Teammate out (`teammate_out`)

Source: `config/production_models.yaml`
Pin key `teammate_out`, pin id (model_version) `alloc-a9b857bb4f1b6a00`, `model: alloc`,
approved 2026-10-05, scoring season 2026. Every number below is copied from the file named on
the `Source:` line above it (`tests/test_model_cards.py` checks this).

## Purpose and intended use

Source: `docs/teammate_out.md`, "The live list"
When a starting RB, WR or TE is Out, Doubtful or on a reserve list: which teammates get his
carries and targets, how much, and the points that follow (with an 80% range). It answers a
beginner's "my team's RB1 is out this week: who gets his work?" **Not intended for:** a
guarantee about one player's points (they are noisy); a starter listed Doubtful may still play.

## Inputs and point-in-time rules

Source: `docs/teammate_out.md`, "Data and definitions"
- Seasons 2013-2025, regular season. Starter: a RB with a carry share of 45% or more, or a WR or
  TE with a target share of 20% or more, over the games he played among his team's previous 4
  games (at least 2). Event: he took no offensive snap while still on the team.
- 1203 starters sat; 1099 were kept; 984 team games with a starter out, 9053 teammate rows.

Source: `docs/teammate_out.md`, "The live list"
- The live list reads the injury rows exactly as the Questionable list reads them, rosters and
  snaps through the as-of view, and only games that have not kicked off.

## Model and training

Source: `docs/teammate_out.md`, "The event study"
An allocation table, no machine learning: of the work an absent starter leaves, the share a
teammate in a role takes (a ratio of sums over single-starter events), shrunk toward its
position group with 20 teammate-games, fit on 2013-2025. With a RB out, the RB2 takes 0.454242
of his carries.

Source: `docs/teammate_out.md`, "Choosing (the rule, fixed before the backtest ran) and the owner's override"
The rule fixed before the backtest picked "nothing changes" (lower pooled points MAE in at
least 6 of the 10 test seasons was required; role beats it in 3 of the 10 seasons). The owner
overrode it on 2026-10-05: the role table is used.

## Evaluation

Source: `reports/teammate_out/report.md`, "## backtest"
Walk-forward, test seasons 2016-2025 (each fit only on earlier seasons), 7751 teammate rows in
840 games, pooled (points MAE / carry-share MAE / target-share MAE / top-gainer hit rate):
nothing changes 4.360331 / 0.044046 / 0.052034 / 0.114286; pro rata 4.573741 / 0.040087 /
0.055054 / 0.271429; group 4.437258 / 0.043131 / 0.051188 / 0.185714; role (used) 4.393169 /
0.040152 / 0.050800 / 0.269048.

## Calibration

Source: `reports/teammate_out/report.md`, "## coverage"
80% range of the points, walk-forward (each season's range from earlier seasons' misses,
2017-2025): 5475 of 6985 rows inside, 0.783822; RB 0.785412, WR 0.774026, TE 0.797317.

## Known limits and failure cases

Source: `docs/teammate_out.md`, "Choosing (the rule, fixed before the backtest ran) and the owner's override"
- On points alone "nothing changes" is more accurate: 4.360331 vs 4.393169 MAE. Points are
  skewed, and the mean absolute error rewards the median; the gains are real on average.

Source: `docs/teammate_out.md`, "Limits"
- The team's volume is kept at its baseline; roles come from usage, not depth charts; QBs are
  not modelled; a player with fewer than 2 games is not listed.

## Owner decisions that shaped it

Source: `docs/teammate_out.md`, "Choosing (the rule, fixed before the backtest ran) and the owner's override"
- 2026-10-05: use the role table although the rule picked "nothing changes": the feature
  answers who gets the work, and role names the biggest gainer far more often (0.269048 vs
  0.114286). Points are shown as an 80% range. The pin records `rule_choice` and `chosen_by`.

## Reproducibility

Source: `docs/teammate_out.md`, "Frozen and pinned"
`uv run twm teammate_out build`, then `uv run twm teammate_out pin --candidate role --reason
"..."` (refused unless it reproduces `reports/teammate_out/`); `uv run twm model check
teammate_out` checks the sha256 and the reports. The nightly runner never rebuilds it.

## Ethical notes

Players are named only from public NFL data; an injury listing is the team's public report.
The list is about fantasy opportunity, not a medical judgement.
