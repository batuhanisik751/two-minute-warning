# Model card: start/sit odds (`startsit`) -- LOCAL ONLY

Source: `config/production_models.yaml`
Pin key `startsit`, pin id (model_version) `rank_dist-61ea917e5a85b44a`, `model: rank_dist`,
approved 2026-10-05, scoring season 2026. Every number below is copied from the file named on
the `Source:` line above it (`tests/test_model_cards.py` checks this).

**Local only.** The input is FantasyPros' weekly expert ranks, which may not be republished:
the odds appear only in the owner's terminal and local weekly report, never on the site.

## Purpose and intended use

Source: `docs/start_sit.md`, "Using it"
Answer "should I start A or B this week?" with the chance A outscores B, for QB, RB, WR and TE,
once the week's ranks are out (Fridays). **Not intended for:** publishing (license), K or D/ST,
season-long rankings, betting.

## Inputs and point-in-time rules

Source: `docs/start_sit.md`, "Data"
- `fact_ranking` weekly pages; each snapshot is mapped to the week whose `dim_week` window holds
  its `available_at`; a player whose game kicked off before the snapshot was available is not a
  valid row; the newest valid snapshot of a week is used.
- League points from `fact_player_week`, REG weeks 2020-2025; a ranked player without a stat
  line scores 0 and stays in.
- The CLI reads the ranks through an as-of view: only ranks available at its `--as-of`.

## Model and training

Source: `docs/start_sit.md`, "Method"
- Per position and rank r: the points of players ranked r - k .. r + k, k = 1 + floor(c r),
  stored as 200 quantiles plus the share without a stat line.
- c chosen walk-forward from 0, 0.05, 0.1, 0.2, 0.3; P(A > B) from independent draws, ties
  one half; an optional play chance mixes 0 points in.

Source: `reports/startsit/backtest.md`
The pinned distributions are fit on 2020-2025 (35,925 ranked player-weeks) with c = 0.2.

## Evaluation

Source: `reports/startsit/backtest.md`, "Brier score and log loss (lower is better)"
Walk-forward, test seasons 2022-2025, 272,553 pairs: Brier 0.2355 (model) vs 0.2389 (the
higher rank wins at its training rate) vs 0.2475 (coin); log loss 0.6687 vs 0.6758 vs 0.6931.
Same-position pairs 0.2306 vs 0.2352; FLEX pairs 0.2451 vs 0.2460 (close calls by design).

## Calibration

Source: `reports/startsit/backtest.md`, "Calibration (the favourite's predicted chance vs how often it won)"
The favourite's predicted chance tracks how often it won up to 80% (60-65%: 62.2% predicted,
63.8% won); above that the model is too sure: 85%+ predicted 86.6%, won 78.8% (424 pairs).

## Known limits and failure cases

Source: `docs/start_sit.md`, "Limits"
- The rank is the experts' view; news after Friday's ranks is not in the odds.
- Teammates and same-game players are treated as independent.
- No K or D/ST; no odds for a week before its ranks land.

Source: `reports/startsit/backtest.md`, "Teammates (the independence assumption)"
Teammate pairs: 6,364; favourite predicted 56.4%, won 57.8%.

## Owner decisions that shaped it

Source: `docs/start_sit.md`
Local only (FantasyPros' terms): CLI and the local report; a test checks the publish and the
pipeline never import it. Default play chance: the rank's own history.

## Reproducibility

Source: `docs/start_sit.md`, "Using it"
`uv run twm model check startsit` checks the spec, every frozen file (sha256, rows, no
per-player column) and that `reports/startsit/backtest.md` equals the page the frozen tables
write; `uv run twm league startsit-pin` re-freezes (review, then commit).

## Ethical notes

Source: `docs/start_sit.md`
No player, id or FantasyPros value of a player is in the frozen files or the report; the odds
are estimates from past seasons, not promises.
