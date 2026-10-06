# Lead time vs the crowd (feature #8)

The Waiver Radar promises to flag pickups early. This study measures how early: for the players
most ESPN leagues ended up rostering, how many weeks before that did the Radar flag them? It
also asks the reverse (what became of the Radar's must-adds the crowd never picked up) and
whether the Radar beats simply watching roster trends. Code: `src/twm/modules/lead_time/`;
tests: `tests/test_lead_time.py`.

## Sources

- **The crowd**: ESPN's "% rostered" as FantasyPros scraped it, `fact_ranking.player_owned_espn`
  (percent), per `scrape_date` (about weekly: Fridays 2021-2023, Thursdays 2020), `season`,
  `gsis_id` and `pos`, on the rest-of-season (`ecr_type = 'rp'`) and weekly (`'wp'`) pages.
  **License: FantasyPros data may not be republished.** Per-player roster % stays on the
  owner's machine; only the aggregate frames below may be published.
- **The Radar**: the pinned walk-forward backtest (`config/production_models.yaml`,
  `waiver_radar`: the weekly lists 2014-2025, each scored by a model that never saw its
  season; read sha256-checked with `twm.pins.load_backtest`). The snapshot keeps no priority,
  so it is recomputed exactly as the published lists do it (`collect.backtest_chances`): the
  similar-player bins of a season-S list come from the backtest seasons before S only. Checked:
  the recomputed chance equals `confidence.from_store` on the local store (difference 0.0).
- **The calendar**: `dim_week` (regular-season weeks, `window_end_utc` = the week's official
  as-of, Tuesday 14:00 UTC after its games).

## Definitions (all fixed before any result was computed)

1. **Waiver period k** = the stretch after week k's games: from the as-of of week k (when the
   Radar's list of week k is made) to the as-of of week k+1. A scrape counts at 00:00 UTC of
   its `scrape_date`, so a Tuesday scrape falls in the period before that day's list (ESPN's
   waivers clear on Wednesday); period 0 is before any game; postseason scrapes are dropped.
   **Radar list week W = period W.** The same period therefore means: the Tuesday list and the
   crowd's Friday number after that Wednesday's waivers.
2. **Roster %** of a player on a day: the largest value on that day's pages (pages disagree in
   about 1 of 5,000 cases). Position: his most frequent `pos` of the season.
3. **Baseline**: the season's last scrape before week 1's as-of (2021-2023); 2020 has ESPN %
   only from 2020-10-16 (period 5), which is its baseline. **Started below T**: under T at the
   baseline, or absent from it (the pages list players down to 0%: 26-199 players per position
   page under 2% in 2022).
4. **Crowd add** at T (50% primary, 25% secondary): a player who started below T and whose
   first later scrape at or above T falls after the baseline's period and no later than the
   season's last Radar list (week 16; 15 in 2020). Add period = that scrape's period.
5. **Radar flags** (top 25 of a weekly position list, priority from the chance):
   `must_add`; `spec_plus` (must-add or speculative); `listed` (top 25, any priority).
   First flag per player-season and level. "In the pool" = on any list of the season.
6. **Lead = add period - first flag period** (positive = the Radar first). **before**: lead >= 1;
   **same**: 0; **after**: <= -1; **never**: no flag at that level all season (split by whether
   he was ever in the Radar's pool). Sensitivity: the **nearest lead**, from the latest flag at
   or before the add.
7. **Reverse view**: each must-add first flag (with crowd data before it) by what the crowd did:
   **already** (>= T at the last scrape before the list), **added later** (first >= T in the
   flag's period or later, any regular-season period), **never**; and whether it hit (the
   Radar's own label `y_hit`).
8. **Momentum baseline** ("watch the roster trends", X = 10 points, fixed in advance): per period
   the % of the player's last scrape (absent in a period with scrapes = 0); flagged in the first
   period p (after the baseline, up to the last list week) with v[p] - v[p-1] >= 10 while
   v[p-1] < T. Scored with the same lead code. It favours the baseline: it is a Friday number,
   three days after the Tuesday list of the same period.
9. Headline numbers pool the **complete seasons 2021-2023**; 2020 is shown per season only.

## Coverage

| season | ESPN % in season | baseline | crowd adds (50% / 25%) | status |
|---|---|---|---|---|
| 2020 | 2020-10-16 to 2021-01-01 (13 scrape days) | 2020-10-16, period 5 | 35 / 37 | partial: adds from period 6 only |
| 2021 | 2021-09-17 to 2022-01-07 (19) | 2021-09-10 | 43 / 60 | complete |
| 2022 | 2022-09-16 to 2023-01-06 (17) | 2022-09-09 | 42 / 57 | complete |
| 2023 | 2023-09-15 to 2024-01-05 (17) | 2023-09-08 | 44 / 54 | complete |
| 2024 | none (ESPN % only 2024-03-01 to 2024-08-09) | - | - | excluded |
| 2025-2026 | none in the warehouse | - | - | live tracker only (2026) |

## Results (2021-2023 pooled, T = 50%: 129 crowd adds)

| Radar level / baseline | before | same | after | never | lead median (Q1-Q3) |
|---|---|---|---|---|---|
| must-add | 17.8% | 7.8% | 18.6% | 55.8% | 0 (-2 to 2) |
| must-add or speculative | 58.9% | 4.7% | 16.3% | 20.2% | 3 (0 to 6) |
| listed (top 25) | 61.2% | 4.7% | 14.7% | 19.4% | 3 (1 to 6.25) |
| momentum, +10 points | 67.4% | 27.1% | 0% | 5.4% | 1 (0 to 2) |

- **Listed**: the Radar had 61% of the crowd's adds on a top-25 list first, a median 3 weeks
  ahead; 21 of its 25 misses were never in its pool (the pool rule treats them as rostered:
  starters back from injury, preseason top-N players). But the nearest-flag lead's median is 0:
  the Radar usually still lists him in the crossing week, and part of the long leads is a wide
  net (a top 25 per position every week, 13 new players under 50% per list week).
- **Must-add** is rare (2.2 new players a week) and catches only 18% of the crowd's adds first;
  56% are never must-adds. By position (before / never): QB 24% / 67%, RB 17% / 40%, TE 9% / 82%,
  WR 21% / 58%.
- **At T = 25%** (171 adds): listed 56% before vs momentum 31% (momentum: 56% same week, the
  rise and the crossing are the same scrape); must-add 11% before, 60% never.
- **Head to head** (T = 50%, listed vs momentum, same 129 adds): both before 60 (the Radar
  earlier in 50 of them), momentum only 27, Radar only 19, neither 23. Must-add vs momentum:
  momentum only 66, both 21, Radar only 2, neither 40.
- **Volume and conversion** (flags of players under 50% at the flag): must-add 105 flags, 39% later
  crossed 50% (27% in a later period); momentum 240 flags, 58% (37% later); listed 634, 14%.
- **Reverse** (must-add first flags, T = 50%): 55 already rostered by most leagues (the Radar's
  label hit 74.5%), 41 added later (hit 90.2%, a median 1 period later), **64 never added (hit
  37.5%)**. Per season the never-added hit rate is 32-42%, the added-later 82-100%.
- 2020 (partial) shows longer leads (median 7 listed): its baseline is week 5, so flags from
  weeks 1-5 all count as early. Never pooled.

**Reading.** The Radar's lists see most crowd pickups coming (about 6 in 10 a week or more ahead
at the listed or speculative level), but its must-add tier is conservative and late: it fires
for few of the crowd's adds and, on the same adds, a plain "+10 points in a week" roster-trend
watch flags more of them before the 50% mark. Where both fire, the Radar is usually earlier,
and at the 25% mark the Radar's lists beat the trend watch clearly (56% vs 31% before). The
must-adds the crowd ignored hit far less often (38%) than those it took (90%).

## Limits

- **FantasyPros' ESPN % is a snapshot** taken on the scrape day, not ESPN's own history; one
  value a week (weekly granularity), so leads are whole waiver periods and "same week" hides
  up to a few days either way. Pages disagree slightly on the same day (we take the largest).
- **Coverage gaps**: 2020 starts in week 6 (its adds are only the later-season ones, and its
  leads are biased long); 2024 has no in-season ESPN % at all; 2025 and 2026 have none in the
  warehouse.
- **Survivorship**: the crowd adds are players who reached 50% of ESPN leagues, a crowd verdict
  that is partly self-fulfilling (rostered players get the touches noticed); players the crowd
  never added are only in the reverse view. "Started below" uses the baseline scrape; a player
  absent from a page is assumed low-owned (the pages reach 0%).
- **The momentum baseline** reads the same series as the outcome (a rise of 10 points often is
  half of the way to 50%), and it is a Friday number compared with a Tuesday list. Its "absent
  = 0" fill let 3-4 flags fire for players missing one week while above the threshold.
- **The first flag** can be weeks before a breakout that had other causes (an injury ahead of
  him); the nearest-flag lead is the conservative reading.
- ESPN's audience is not the owner's league: the 50% of ESPN leagues is a proxy for "gone".

## 2026 live (local only)

`twm.modules.lead_time.live`: the owner's league sync stores ESPN's `percent_owned` of every
free agent of that league (`data/league.duckdb`, `league_free_agents`: season, week,
synced_at, gsis_id, percent_owned ...; opened read-only; no league id or name is read).
`read_live_owned` -> `live_path` (one value per waiver period, the latest `synced_at` in it:
ESPN returns the current %, so the backfilled weeks 1-3 carry one identical snapshot) ->
`live_lists` (the season's lists from the predictions store, one model version per list) ->
`live_tracker` (state: added / already / below / left_pool / unseen, first flag per level,
`lead_<level>`) -> `live_summary`. Absence is not a low % here (the player may be rostered in
the owner's league), so there is no 0-fill and a crossing after the league rostered him is
invisible (`left_pool`). As of 2026-10-05 all syncs fall in period 3 (one observation per
player), so no lead can be measured yet.

## Frames a publish may carry (aggregates only)

`study.build_frames(study.run())` returns, deterministically, frames with no player id, name
or roster % column (`study.assert_aggregate_only`, tested); cells with fewer than 5 adds or
flags keep their count only:

- `coverage`: per season with ESPN %: baseline, in-season scrape days, last list week, complete,
  in_study, players with a %, crowd adds at 50% and 25%;
- `summary`: per threshold, signal (must_add, spec_plus, listed, momentum) and scope (complete,
  season, position): counts and shares before / same / after / never, never-out-of-pool, the
  lead's median and quartiles, the nearest lead's median, the share 1-4 weeks before;
- `hist`: the lead's distribution (clipped to -6..10; NULL = never), complete seasons;
- `reverse`: first flags by crowd state (already / added_later / never), Radar hits and hit rate;
- `conversion`: flags per list week and the share the crowd added later;
- `h2h`: per Radar level vs momentum: radar_only / momentum_only / both / neither.

Reproduce (reads only, about 2 s):
`uv run python -c "from twm.modules.lead_time import study as st; print(st.build_frames(st.run())['summary'])"`.

## Published, the CLI and the local report (L8b)

- **CLI**: `uv run twm radar lead-time [--threshold 50|25] [--db WAREHOUSE]` prints the coverage,
  the summary at both thresholds, the share flagged before by season, the lead histogram, the
  head to head, the reverse view and the volume table (aggregates only; about 2 s, writes
  nothing). The terms are in the glossary: `rostered_pct`, `crowd_add`, `lead_time`,
  `momentum_baseline` (`twm glossary <name>`).
- **Publish** (migration 0011, `src/twm/publish/lead_time.py`): every publish runs the study
  and writes ONLY `build_frames`' output into six replaced tables, `lead_time_coverage`,
  `_summary`, `_hist`, `_reverse`, `_conversion`, `_h2h` (checked aggregate-only before and
  after; tests/test_publish_lead_time.py proves no per-player column or id reaches them). The
  frames' nullable season / position become `scope_value` ('' = complete seasons pooled), the
  threshold an integer, and the histogram's never-flagged bin is left out (it is the summary's
  `n_never`). The runner can compute it: on a runner-built 2012+ warehouse the six frames are
  equal to the Mac's (checked 2026-10-06), and the Radar pin is committed.
- **Site**: `/track-record#lead-time`, "Does the Radar beat the crowd?" (linked from
  `/waivers`): every number from the published rows, no player named, the same in public mode.
- **Local report** (`twm league report` / `weekly`): "Lead time this season"
  (`twm.modules.lead_time.league`, from `live`): the states' counts, the crowd's adds with the
  Radar's leads, the Radar's must-adds still under 50% (names: local only) and the `left_pool`
  count. Neither it nor `live` is ever imported by the publish or the pipeline
  (tests/test_league.py, tests/test_startsit.py).
