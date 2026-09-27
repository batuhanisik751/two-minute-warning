# Fantasy scoring (Step B4)

A fantasy score is a weighted sum. Every stat a player produced in a game (yards, touchdowns,
catches, ...) is multiplied by the points your league gives for one unit of it, and the results
are added up. The weights live in `config/scoring.yaml`; the arithmetic lives in
`src/twm/scoring.py`.

## The default: full PPR

"PPR" means *points per reception*: every catch is worth a point on top of the yards and
touchdowns. It is the spec's default (PROJECT_SPEC 7.1).

| Stat | Points | Column in `fact_player_week` |
|---|---|---|
| Passing yards | 0.04 each (1 per 25) | `passing_yards` |
| Passing touchdown | 4 | `passing_tds` |
| Interception thrown | -2 | `passing_interceptions` |
| Rushing yards | 0.1 each (1 per 10) | `rushing_yards` |
| Rushing touchdown | 6 | `rushing_tds` |
| Reception | 1 | `receptions` |
| Receiving yards | 0.1 each | `receiving_yards` |
| Receiving touchdown | 6 | `receiving_tds` |
| Two-point conversion (pass, run or catch) | 2 | `passing_2pt_conversions`, `rushing_2pt_conversions`, `receiving_2pt_conversions` |
| Fumble lost | -2 | see "fumbles" below |
| Kick or punt return touchdown | 6 | `special_teams_tds` |
| Fumble recovered for a touchdown | 6 | `fumble_recovery_tds` |

Half PPR is `receptions: 0.5`, standard scoring is `receptions: 0`. A stat that is missing
(NULL) counts as 0. The config rejects unknown names, so a typo fails loudly instead of quietly
scoring zero.

## Three choices, and what we picked

nflverse ships its own precomputed `fantasy_points_ppr`. We never use it for predictions, only
to check our arithmetic. On every QB/RB/WR/TE week from 1999 to 2026 (150,691 player-weeks), it
equals exactly:

    the components above + 6 x return touchdowns
    - 2 x fumbles lost on sacks, runs and catches only
    (no points for a fumble recovered for a touchdown)

Our default differs from that in two places, both set in `config/scoring.yaml`:

1. **Which lost fumbles cost points** (`options.fumbles_lost_scope`). `all` (our default) counts
   every lost fumble, including on kick and punt returns (`fumbles_lost_total`), the plain
   meaning of "fumbles lost". `scrimmage` counts only fumbles on sacks,
   runs and catches, like nflverse. They differ on 983 of the 150,691 player-weeks, almost all
   returners.
2. **Fumble recovered for a touchdown** (`misc.fumble_recovery_touchdowns`). We give 6 points
   (it is a touchdown the player scored); nflverse gives 0. This happens 67 times in 28 seasons.

Return touchdowns (`misc.special_teams_touchdowns`, 6) are counted by both.

Neither choice is verified against ESPN yet. When My League (ESPN) is connected (step F2),
your league's real settings replace this file, and step F3 compares our points with ESPN's box
scores for your team, which settles both choices for your league.

## Using it in code

```python
from twm.scoring import score, score_sql, ScoringRules, half_ppr

scored = score(player_weeks)  # adds a fantasy_points column
scored = score(player_weeks, half_ppr(), breakdown=True)  # + fp_<stat> columns per stat

# inside a point-in-time query (the same numbers, computed in DuckDB):
with AsOfView(db, as_of) as v:
    v.sql(f"SELECT player_id, week, {score_sql()} AS fantasy_points FROM fact_player_week")
```

`breakdown=True` gives the points each stat contributed; the Waiver Radar's plain-English
reasons will use it ("12 of his 18 points came from two touchdowns").

## How it is tested

- Hand-computed weeks for a QB, RB, WR and TE, with the arithmetic in comments
  (`tests/test_scoring.py`), under full PPR, half PPR, standard and both fumble options.
- The SQL and polars versions agree, including when a stat is missing.
- `uv run pytest -m realdata` reproduces nflverse's `fantasy_points_ppr` and `fantasy_points`
  exactly on every QB/RB/WR/TE week 1999-2026, and shows our default differs from them only by
  the two documented choices.

## Not supported (yet)

Yardage or long-touchdown bonuses, points per first down, kickers and team defenses (a P3
stretch in the spec), and defensive players. If an ESPN league uses any of these, step F2 will
report them instead of silently ignoring them.
