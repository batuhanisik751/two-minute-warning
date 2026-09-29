# Regression Watch: xFP and garbage time (step D1)

Seasons 2006-2026, regular season, QB/RB/WR/TE. Warehouse built 2026-09-29 18:49:52 UTC. Regenerate with `uv run twm regression xfp-report` (docs/regression_watch.md explains every number).

**xFP** (expected fantasy points) is what an average player would have scored from the same targets and carries; **FPOE** = fantasy points - xFP. Both are computed twice: from the weekly files (what the app shows) and play by play, which is what lets us drop **garbage-time** plays (the game is decided: win probability below 5% or above 95%, except in a one-score game's last two minutes of a half).

## 1. The per-play tables join the play-by-play

| table (all built seasons) | plays | not in fact_play | join rate | player differs from fact_play | duplicates dropped |
|---|---|---|---|---|---|
| `fact_opportunity_pass` | 374,311 | 3 | 99.999% | passer_player_id 2, receiver_player_id 1,120 | 2 |
| `fact_opportunity_rush` | 295,636 | 4 | 99.999% | rusher_player_id 27 | 0 |

Seen from the play-by-play (2006-2026, regular season): 357,891 of 357,944 pass attempts that are not sacks (99.99%) and 283,223 of 283,244 runs (99.99%) have expected values; ffopportunity leaves out sacks (24,341). A differing receiver is almost always a target ffopportunity names where today's play-by-play has none (mostly 2006-2008): the per-play xFP follows ffopportunity, like its weekly file.

## 2. Points: the plays add up to the weekly stat line

109,610 player-games. Summing the fantasy points of every play (config/scoring.yaml, the same weights) reproduces the weekly stat line within 0.1 points for **109,543 (99.94%)**, to the cent for 109,537 (99.93%). The other 67 by cause (a player-game can have two):

| cause | player-games | why |
|---|---|---|
| yards | 59 | the official stat line credits yards the play-by-play's yard columns do not (stat corrections, a pass caught by the passer himself, several laterals on one play) |
| lost fumbles | 8 | the official stat line and the play-by-play's fumbler columns disagree on who lost the ball (mostly plays with two fumbles) |
| touchdowns | 1 | a touchdown counted differently: an upstream duplicate play (a run repeated as a kickoff row) or a return credited another way |

The 15 largest differences:

| game | player | pos | weekly | plays | difference | stats (weekly vs plays) |
|---|---|---|---|---|---|---|
| 2011_13_DET_NO | 00-0027966 | RB | 25.20 | 19.20 | +6.00 | special_teams_tds 1 vs 0 |
| 2011_05_CIN_JAX | 00-0024275 | RB | 14.50 | 12.50 | +2.00 | fumbles_lost_total 0 vs 1 |
| 2011_05_CIN_JAX | 00-0027948 | QB | 9.94 | 11.94 | -2.00 | fumbles_lost_total 2 vs 1 |
| 2017_05_GB_DAL | 00-0033045 | RB | 14.20 | 12.20 | +2.00 | receiving_yards 16 vs -4 |
| 2019_17_MIA_NE | 00-0031062 | RB | 10.70 | 12.70 | -2.00 | fumbles_lost_total 1 vs 0 |
| 2020_03_LA_BUF | 00-0033943 | WR | 8.00 | 10.00 | -2.00 | fumbles_lost_total 1 vs 0 |
| 2020_03_LA_BUF | 00-0036415 | WR | 0.00 | -2.00 | +2.00 | fumbles_lost_total 0 vs 1 |
| 2025_14_PHI_LAC | 00-0036389 | QB | 0.40 | 2.40 | -2.00 | fumbles_lost_total 1 vs 0 |
| 2025_17_SEA_CAR | 00-0034869 | QB | 6.08 | 8.08 | -2.00 | fumbles_lost_total 1 vs 0 |
| 2006_04_IND_NYJ | 00-0018958 | WR | 14.10 | 12.20 | +1.90 | receiving_yards 81 vs 62 |
| 2006_04_IND_NYJ | 00-0024318 | WR | 1.40 | -0.30 | +1.70 | fumbles_lost_total 0 vs 1; receiving_yards -4 vs -1 |
| 2024_03_SF_LA | 00-0033576 | TE | 6.10 | 4.40 | +1.70 | receiving_yards 41 vs 24 |
| 2015_13_GB_DET | 00-0031384 | TE | 28.60 | 30.20 | -1.60 | receiving_yards 146 vs 162 |
| 2015_09_GB_CAR | 00-0031381 | WR | 18.30 | 19.50 | -1.20 | receiving_yards 93 vs 105 |
| 2015_12_OAK_TEN | 00-0031544 | WR | 18.50 | 19.70 | -1.20 | receiving_yards 115 vs 127 |

Without garbage time, the frame subtracts the garbage-time plays' points from the weekly points, so these rare differences stay in the non-garbage part.

## 3. xFP: the plays add up to the weekly expected points

98,449 player-games have an ffopportunity row (the others had no target, carry or pass). The per-play xFP sum is within the rounding bound (0.1262 points: every weekly expected stat is stored with 2 decimals) of the weekly xFP for **98,447 (99.998%)**; the largest gap is 1.9017.

The 2 outside are the passers of the two 2013 pass plays ffopportunity lists twice (a target repeated under a linebacker's name): its weekly file counts the play twice for the passer, the warehouse keeps one row.

## 4. How much comes from garbage time

Share of the points, the xFP and the opportunities (targets, carries, pass attempts, two-point tries) that come from garbage-time plays:

| position | player-games | points | from garbage time | xFP | xFP from garbage time | opportunities in garbage time | FPOE per game | FPOE per game without garbage time |
|---|---|---|---|---|---|---|---|---|
| QB | 12,572 | 165,252 | 15.2% | 177,277 | 15.3% | 16.3% | -0.96 | -0.80 |
| RB | 29,738 | 233,880 | 16.3% | 231,900 | 16.3% | 15.9% | +0.07 | +0.06 |
| TE | 22,006 | 123,988 | 15.2% | 119,339 | 15.1% | 15.4% | +0.24 | +0.19 |
| WR | 45,294 | 348,002 | 15.4% | 331,850 | 15.5% | 15.6% | +0.40 | +0.35 |
| all | 109,610 | 871,123 | 15.6% | 860,366 | 15.6% | 16.0% | +0.11 | +0.09 |

## 5. Coverage by season and position

Per season: the share of incomplete passes whose target is named (an unnamed target is nobody's expected catch), then per position the player-games in the frame, the share with xFP and the share whose plays reproduce the weekly points (the CSV next to this file has the garbage-time shares too).

| season | incompletions with a target | QB (games / with xFP / points match) | RB (games / with xFP / points match) | WR (games / with xFP / points match) | TE (games / with xFP / points match) |
|---|---|---|---|---|---|
| 2006 | 1.1% | 601 / 100% / 99.8% | 1,266 / 87% / 100.0% | 1,943 / 83% / 99.8% | 859 / 86% / 100.0% |
| 2007 | 7.6% | 639 / 100% / 100.0% | 1,263 / 90% / 100.0% | 1,998 / 83% / 100.0% | 897 / 85% / 100.0% |
| 2008 | 9.0% | 596 / 99% / 100.0% | 1,272 / 89% / 100.0% | 1,985 / 85% / 99.9% | 930 / 85% / 100.0% |
| 2009 | 96.5% | 646 / 100% / 100.0% | 1,354 / 91% / 100.0% | 2,110 / 87% / 99.9% | 1,016 / 89% / 99.9% |
| 2010 | 96.4% | 605 / 100% / 100.0% | 1,295 / 89% / 100.0% | 2,123 / 88% / 100.0% | 1,054 / 88% / 100.0% |
| 2011 | 96.8% | 602 / 100% / 99.8% | 1,343 / 93% / 99.8% | 2,186 / 88% / 100.0% | 1,051 / 88% / 100.0% |
| 2012 | 96.7% | 598 / 100% / 100.0% | 1,401 / 91% / 99.9% | 2,157 / 90% / 99.8% | 1,049 / 89% / 100.0% |
| 2013 | 95.9% | 586 / 100% / 99.7% | 1,386 / 89% / 99.9% | 2,169 / 89% / 99.9% | 1,074 / 89% / 100.0% |
| 2014 | 96.2% | 598 / 99% / 99.8% | 1,434 / 92% / 100.0% | 2,221 / 89% / 100.0% | 1,110 / 88% / 100.0% |
| 2015 | 96.8% | 604 / 98% / 100.0% | 1,441 / 92% / 100.0% | 2,228 / 88% / 99.7% | 1,093 / 91% / 99.9% |
| 2016 | 95.7% | 598 / 99% / 100.0% | 1,577 / 89% / 99.9% | 2,320 / 89% / 99.9% | 1,063 / 90% / 100.0% |
| 2017 | 95.3% | 595 / 98% / 100.0% | 1,629 / 89% / 99.8% | 2,257 / 89% / 100.0% | 1,133 / 90% / 100.0% |
| 2018 | 92.0% | 617 / 99% / 99.8% | 1,551 / 90% / 99.9% | 2,229 / 90% / 99.9% | 1,116 / 91% / 100.0% |
| 2019 | 89.1% | 605 / 100% / 100.0% | 1,560 / 88% / 99.9% | 2,259 / 88% / 100.0% | 1,105 / 91% / 100.0% |
| 2020 | 88.7% | 640 / 99% / 100.0% | 1,587 / 90% / 100.0% | 2,332 / 89% / 99.9% | 1,113 / 91% / 100.0% |
| 2021 | 90.1% | 671 / 100% / 99.9% | 1,634 / 90% / 100.0% | 2,490 / 89% / 99.9% | 1,179 / 89% / 100.0% |
| 2022 | 88.2% | 638 / 100% / 99.8% | 1,667 / 88% / 100.0% | 2,396 / 89% / 99.9% | 1,217 / 90% / 100.0% |
| 2023 | 87.2% | 680 / 100% / 100.0% | 1,545 / 91% / 99.9% | 2,475 / 89% / 100.0% | 1,192 / 89% / 100.0% |
| 2024 | 87.1% | 664 / 100% / 100.0% | 1,603 / 88% / 100.0% | 2,455 / 87% / 99.8% | 1,235 / 88% / 99.9% |
| 2025 | 86.7% | 677 / 100% / 99.7% | 1,644 / 86% / 100.0% | 2,502 / 85% / 99.9% | 1,283 / 88% / 100.0% |
| 2026 | 85.5% | 112 / 100% / 100.0% | 286 / 87% / 100.0% | 459 / 84% / 100.0% | 237 / 86% / 100.0% |

**Warning: 2006, 2007, 2008.** The play-by-play (and so ffopportunity) names the target of almost no incomplete pass in these seasons, and nflverse's weekly `targets` is empty too. A receiver's xFP then counts little more than his catches: WR xFP is 60.1% of WR points there, 100.9% in the other seasons. Receivers' xFP and FPOE of those seasons are not comparable with later ones (passers' are: every pass has its passer).

Left out: 1,153 player-games without any point-in-time position (no roster row or snap count public at the week's as-of; 310 of them had a target, carry or pass), and every player at another position (FB, K, defense ...).

## 6. Limitation (PROJECT_SPEC 6.3)

The expected values come from ffopportunity's models and the garbage-time flag from nflfastR's win probability model. Both models were trained on many seasons, including seasons after some of the weeks shown here, so an old week's xFP knows a little about the future (a mild, known leak). Regression Watch uses them as they are in P1 and will re-estimate xFP walk-forward in P2 (only earlier seasons) and compare.
