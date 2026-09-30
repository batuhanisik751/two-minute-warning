# Regression Watch: stability study and shrinkage (step D2)

Seasons 2009-2025, regular season, QB/RB/WR/TE. Warehouse built 2026-09-29 20:58:11 UTC. Regenerate with `uv run twm regression stability` (docs/regression_watch.md explains every number; notebooks/02_regression_stability.ipynb walks through it).

A player's fantasy points split into **opportunity** (xFP: what an average player would have scored from his targets and carries) and **efficiency** (FPOE = points - xFP). Which of the two repeats? If a player's efficiency in one half of a season predicts his efficiency in the other half, it is (at least partly) skill; if not, it was mostly luck and will shrink back toward zero.

**Headline.** Opportunity is far stickier than efficiency at every position: xFP/game correlates 0.76 (QB) to 0.88 (RB) between the halves, FPOE/game only 0.10 (QB) to 0.16 (RB).

## 1. How the study works

- Every player-season with at least 8 games; a game counts when he had a target, carry or pass. Each player-season gets one position (the one of most of his games).
- His games are split into two halves: odd (1st, 3rd, 5th ...) against even (2nd, 4th ...), and, as a harder test, the first half of his games against the second.
- The **split-half correlation r** compares the two halves across player-seasons: r = 1 means the halves rank players exactly the same, r = 0 means one half says nothing about the other. In brackets: a 95% interval from 1,000 bootstrap resamples of the player-seasons.

## 2. Opportunity against efficiency (odd against even games)

**All plays**

| position | player-seasons | xFP/game (opportunity) | FPOE/game (efficiency) | points/game |
|---|---|---|---|---|
| QB | 607 | 0.76 (0.72 to 0.80) | 0.10 (0.01 to 0.18) | 0.70 (0.66 to 0.74) |
| RB | 1,476 | 0.88 (0.87 to 0.89) | 0.16 (0.10 to 0.22) | 0.82 (0.81 to 0.84) |
| WR | 2,240 | 0.85 (0.84 to 0.86) | 0.11 (0.06 to 0.15) | 0.77 (0.75 to 0.79) |
| TE | 1,098 | 0.85 (0.83 to 0.87) | 0.12 (0.05 to 0.18) | 0.75 (0.72 to 0.78) |

**Without garbage time**

| position | player-seasons | xFP/game (opportunity) | FPOE/game (efficiency) | points/game |
|---|---|---|---|---|
| QB | 607 | 0.71 (0.66 to 0.75) | 0.09 (0.01 to 0.17) | 0.65 (0.60 to 0.70) |
| RB | 1,476 | 0.87 (0.86 to 0.88) | 0.13 (0.06 to 0.19) | 0.81 (0.79 to 0.83) |
| WR | 2,240 | 0.82 (0.81 to 0.83) | 0.08 (0.03 to 0.12) | 0.74 (0.72 to 0.76) |
| TE | 1,098 | 0.82 (0.80 to 0.84) | 0.11 (0.04 to 0.17) | 0.71 (0.68 to 0.74) |

## 3. The parts of efficiency (odd against even games)

Each part is a rate over expected per chance, added up over the half: **TD rate** = (touchdowns - expected touchdowns) per pass, carry or target; **catch rate** = (catches - expected catches) per target (for QBs the same for completions per pass, often called CPOE); **YAC** = (yards after the catch - expected YAC) per catch. A player-season counts for a rate when each half has at least 10 chances.

| position | TD rate over expected | catch rate over expected (QB: completion rate) | YAC over expected per catch |
|---|---|---|---|
| QB | 0.00 (-0.13 to 0.15), n 603 | 0.33 (0.26 to 0.40), n 594 | - |
| RB | 0.06 (-0.01 to 0.14), n 1,400 | 0.09 (0.02 to 0.15), n 857 | 0.03 (-0.06 to 0.11), n 660 |
| WR | 0.03 (-0.02 to 0.08), n 2,050 | 0.16 (0.11 to 0.21), n 2,015 | 0.19 (0.14 to 0.24), n 1,640 |
| TE | 0.00 (-0.07 to 0.08), n 854 | 0.16 (0.10 to 0.23), n 849 | 0.21 (0.13 to 0.29), n 668 |

YAC over expected needs ffopportunity's per-catch YAC expectation: every catch of 2009-2025 has one, so it is studied on every season of the study.

## 4. A harder test: first half against second half of his games

Here roles also change (injuries, depth charts, trades), so correlations are lower than with odd and even games; the gap between opportunity and efficiency is what matters.

**All plays**

| position | player-seasons | xFP/game (opportunity) | FPOE/game (efficiency) | points/game |
|---|---|---|---|---|
| QB | 607 | 0.65 (0.59 to 0.71) | 0.12 (0.05 to 0.20) | 0.62 (0.56 to 0.67) |
| RB | 1,476 | 0.75 (0.73 to 0.78) | 0.17 (0.11 to 0.23) | 0.72 (0.69 to 0.75) |
| WR | 2,240 | 0.77 (0.76 to 0.79) | 0.13 (0.09 to 0.18) | 0.71 (0.69 to 0.73) |
| TE | 1,098 | 0.79 (0.77 to 0.81) | 0.12 (0.05 to 0.18) | 0.71 (0.68 to 0.74) |

**Without garbage time**

| position | player-seasons | xFP/game (opportunity) | FPOE/game (efficiency) | points/game |
|---|---|---|---|---|
| QB | 607 | 0.60 (0.54 to 0.66) | 0.09 (0.01 to 0.17) | 0.58 (0.52 to 0.64) |
| RB | 1,476 | 0.74 (0.71 to 0.76) | 0.15 (0.10 to 0.21) | 0.72 (0.68 to 0.74) |
| WR | 2,240 | 0.75 (0.73 to 0.77) | 0.11 (0.06 to 0.15) | 0.69 (0.66 to 0.71) |
| TE | 1,098 | 0.77 (0.74 to 0.79) | 0.12 (0.05 to 0.18) | 0.68 (0.64 to 0.71) |

## 5. How much FPOE/game to believe: the shrinkage table

Treat each game's FPOE as the player's true level plus luck. Across player-seasons the two odd/even halves share the true level (their covariance estimates the **signal variance**) and differ only by luck (the variance of their difference estimates the **noise variance per game**). After g games the average is worth **r(g) = signal / (signal + noise / g)** of its value: the **shrinkage factor** D3's rest-of-season projection multiplies FPOE/game by. 'Games for half weight' = noise / signal, the number of games after which his FPOE/game deserves half its value.

**FPOE/game, all plays** (intervals: bootstrap over player-seasons)

| position | player-seasons | signal variance | noise variance per game | average per game | games for half weight | r(4) | r(8) | r(12) | r(17) |
|---|---|---|---|---|---|---|---|---|---|
| QB | 607 | 0.59 | 34.9 | -0.78 | 59 | 0.06 (0.01 to 0.12) | 0.12 (0.02 to 0.21) | 0.17 (0.03 to 0.29) | 0.22 |
| RB | 1,476 | 0.54 | 17.2 | 0.01 | 32 | 0.11 (0.07 to 0.15) | 0.20 (0.13 to 0.26) | 0.27 (0.18 to 0.35) | 0.35 |
| WR | 2,240 | 0.43 | 22.1 | -0.01 | 52 | 0.07 (0.04 to 0.10) | 0.13 (0.08 to 0.19) | 0.19 (0.11 to 0.25) | 0.25 |
| TE | 1,098 | 0.27 | 12.5 | 0.04 | 45 | 0.08 (0.03 to 0.13) | 0.15 (0.06 to 0.23) | 0.21 (0.09 to 0.31) | 0.27 |

Example: a WR who scored 4.0 points per game over expected in his first 8 games keeps r(8) x 4.0 = 0.53 points per game of it in the projection; the rest is expected to fade.

**FPOE/game without garbage time**

| position | player-seasons | signal variance | noise variance per game | average per game | games for half weight | r(4) | r(8) | r(12) | r(17) |
|---|---|---|---|---|---|---|---|---|---|
| QB | 607 | 0.43 | 28.9 | -0.67 | 67 | 0.06 (0.00 to 0.11) | 0.11 (0.01 to 0.20) | 0.15 (0.01 to 0.27) | 0.20 |
| RB | 1,476 | 0.35 | 14.7 | 0.02 | 43 | 0.09 (0.04 to 0.13) | 0.16 (0.08 to 0.23) | 0.22 (0.11 to 0.31) | 0.29 |
| WR | 2,240 | 0.25 | 19.0 | -0.00 | 76 | 0.05 (0.02 to 0.08) | 0.10 (0.04 to 0.15) | 0.14 (0.05 to 0.21) | 0.18 |
| TE | 1,098 | 0.23 | 10.5 | 0.04 | 46 | 0.08 (0.03 to 0.13) | 0.15 (0.06 to 0.23) | 0.21 (0.09 to 0.31) | 0.27 |

## 6. Is the table stable across seasons?

r(8) for FPOE/game estimated on shorter windows: three blocks of seasons, then the growing windows a walk-forward backtest would use (it may only learn from seasons before the one it tests; `stability.shrinkage(seasons)` takes exactly those). The CSV has every g and the no-garbage-time version.

| seasons | QB | RB | WR | TE |
|---|---|---|---|---|
| 2009-2025 | 0.12 (n 607) | 0.20 (n 1,476) | 0.13 (n 2,240) | 0.15 (n 1,098) |
| 2009-2014 | 0.14 (n 217) | 0.33 (n 488) | 0.17 (n 747) | 0.16 (n 352) |
| 2015-2019 | 0.17 (n 169) | 0.17 (n 445) | 0.05 (n 659) | 0.07 (n 327) |
| 2020-2025 | 0.02 (n 221) | 0.10 (n 543) | 0.16 (n 834) | 0.18 (n 419) |
| 2009-2015 | 0.17 (n 252) | 0.31 (n 570) | 0.17 (n 881) | 0.16 (n 418) |
| 2009-2020 | 0.16 (n 423) | 0.23 (n 1,028) | 0.12 (n 1,542) | 0.13 (n 745) |
| 2009-2024 | 0.17 (n 569) | 0.21 (n 1,389) | 0.13 (n 2,107) | 0.14 (n 1,024) |

## 7. Limits

- **Who is in it.** Every player-season with 8+ games, backups included. Part of why opportunity looks so sticky is simply that starters stay starters and backups stay backups; efficiency has no such built-in spread.
- **One noise size per position.** The model gives every player of a position the same per-game luck, but a player with 10 targets a game has bigger FPOE swings than one with 3. D3 can test whether a per-opportunity version predicts better.
- **Rates need chances.** A rate over expected counts a player-season only with at least 10 chances in each half (targets for the catch rate, catches for YAC, passes, carries and targets for the TD rate), so its n is smaller than the per-game metrics' n.
- **No garbage-time split for the parts.** The parts (touchdowns, catches, YAC) come from ffopportunity's weekly rows, which do not separate garbage time; only xFP, FPOE and points have a no-garbage-time version.
- **Model outputs (PROJECT_SPEC 6.3).** xFP comes from ffopportunity's models, trained on many seasons including later ones; the garbage-time flag from nflfastR's win probability. A mild, known leak, the same as in the rest of Regression Watch P1.
- **Resampling player-seasons.** The intervals resample player-seasons, as if each were independent; the same player appears in several seasons, so the true intervals are a little wider.
- **Seasons.** 2006-2008 are left out: receiver xFP is unusable there (targets of incomplete passes are missing upstream, docs/regression_watch.md).
