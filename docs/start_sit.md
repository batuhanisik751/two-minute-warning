# Start/sit odds (feature #2): LOCAL ONLY

"Should I start A or B this week?" becomes "A outscores B about 62% of the time", from how
players at each FantasyPros weekly expert rank actually scored in league points.

**Local only.** The input is FantasyPros' weekly expert consensus ranks, whose Terms of Use
forbid republishing them (`web/lib/third-party.ts`, `docs/deploy.md` step 10). The feature is
the CLI `twm league startsit` and a section of the owner's local weekly report: nothing goes to
the publish layer, Postgres or `web/` (`tests/test_startsit.py` imports every publish and
pipeline module and checks none loads `twm.modules.startsit`). The frozen files hold only rank
-> points distributions and aggregate backtest numbers, never a player or a player's rank.

## Data

- `fact_ranking` weekly pages (`ecr_type` 'wp', `page_type` weekly-qb/-rb/-wr/-te; the
  2019-2020 'weekly-offense' pages are ignored): QB, RB, WR and TE only (no K or D/ST ranks).
- There is no week column: a snapshot belongs to the regular-season week whose `dim_week`
  window holds its `available_at` (after the previous Tuesday as-of, at or before this week's).
  Snapshots are scraped Fridays, after the Thursday game: a player whose team kicked off before
  the snapshot was available is not a valid row (his game was already on). With two snapshots
  before one week, a player's newest valid one is used.
- Outcome: league points (`twm.scoring`, config/scoring.yaml) from `fact_player_week`, REG
  weeks 2020-2025. A ranked player without a stat line that week scores 0 and stays in: missing
  a game is part of the risk (most of these are deep ranks; near the top it is a few percent).

## Method

1. For each position and weekly rank r, the distribution of the week's points of the players
   ranked r - k .. r + k, k = 1 + floor(c r) (cut symmetric at the top: rank 1 alone). Deeper
   ranks pool more neighbours. c is chosen walk-forward from 0, 0.05, 0.1, 0.2, 0.3 (log loss on
   the last training season, fit on the seasons before it). Ranks beyond 40 (QB, TE), 80 (RB)
   or 100 (WR) pool into that cap. Splitting by the experts' spread (`sd`) was not tried.
2. Stored as 200 equal-weight quantiles of the points of players who had a stat line, plus the
   share who did not.
3. P(A > B) from independent draws of the two distributions, a tie counting one half.
4. Play chance: by default each player is as likely to miss as players at his rank were. With
   `--play-chance-a 0.7`, A's played distribution gets weight 0.7 and 0 points weight 0.3. The
   hook `twm.modules.startsit.live.PlayChanceSource` lets another module (the Questionable
   module, later) feed that chance automatically.

## Backtest and calibration

Walk-forward by season: train 2020..s-1, test s = 2022..2025. Pairs: same position, 1-24 ranks
apart (within QB 30, RB 60, WR 72, TE 30), plus FLEX pairs (RB/WR/TE of two positions whose
ranks' training mean points are within 2 points). Scored against (i) "the higher-ranked wins"
at the training seasons' rate and (ii) a coin flip. All numbers: `reports/startsit/backtest.md`
(regenerated from the frozen files by `twm model check startsit`). Pooled over 272,553 pairs:
Brier 0.2355 vs 0.2389 (rank rate) vs 0.2475 (coin); log loss 0.6687 vs 0.6758 vs 0.6931. The
favourite's predicted chance is close to how often it won up to 80%; above 85% it is too sure
(86.6% predicted, 78.8% won, 424 pairs). Teammate pairs (6,364): favourite predicted 56.4%,
won 57.8%, so ignoring the correlation costs little on average.

## Using it

```
uv run twm league startsit "Jane Doe" "John Roe" [--season 2026 --week 5 --as-of 2026-10-11T15:00Z]
uv run twm league startsit "Jane Doe WR" "John Roe" --play-chance-b 0.6
uv run twm model check startsit      # the frozen files (sha256, rows, no per-player column)
uv run twm league startsit-pin       # re-freeze after a season (owner; review, then commit)
```

Exit codes: 1 no usable pin, 2 a name does not resolve (the choices are listed), 3 the week's
ranks are not out yet. No ESPN needed. The weekly report (`twm league report`) adds "Start/sit
odds for your closest calls" when a league is synced (docs/my_league.md).

## Limits

- The rank is the experts' view: the odds are only as good as the consensus, and late news
  (after Friday's ranks) is not in them.
- Independence: teammates and players in the same game are treated as unrelated.
- No K or D/ST (no weekly ranks), and ranks land Fridays: before that, no odds for the week.
- One frozen season of odds (the pin's season); history before 2020 has no weekly ranks.
