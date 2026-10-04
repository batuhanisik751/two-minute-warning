import assert from "node:assert/strict";
import { test } from "node:test";
import {
  bandName,
  calibrationGrid,
  driverEffect,
  driverValue,
  earlySeasonCheck,
  lastPerSeason,
  mergeTimeline,
  outcomeWords,
  parseDrivers,
  phaseName,
  recordPerSeason,
  recordWords,
  seasonNotStarted,
  signedNum,
  timelineWords,
  wholePct,
  type CalCell,
} from "../../lib/hot-seat";
import { HOT_SEAT_BANDS, HOT_SEAT_FIRST_WEEK, HOT_SEAT_PHASES, HOT_SEAT_WINDOW_DAYS } from "../../lib/method";

test("a probability is shown as a whole percent, never 0% or 100% when it is not", () => {
  assert.equal(wholePct(0.7061), "71%");
  assert.equal(wholePct(0.705), "71%");
  assert.equal(wholePct(0.5295), "53%");
  assert.equal(wholePct(0.004), "<1%");
  assert.equal(wholePct(0), "0%");
  assert.equal(wholePct(0.996), ">99%");
  assert.equal(wholePct(1), "100%");
});

test("signed numbers use a real minus sign and never sign a zero", () => {
  assert.equal(signedNum(1.62), "+1.6");
  assert.equal(signedNum(-1.62), "−1.6");
  assert.equal(signedNum(-0.01), "0.0");
  assert.equal(signedNum(0.123, 2), "+0.12");
});

test("drivers parse defensively and read in plain, signed words", () => {
  const ds = parseDrivers([
    { feature: "wins_vs_expected", label: "Wins vs market expectation", contribution: 1.18, value: -1.63, missing: false },
    { feature: "prev_playoff_round", label: "Previous season playoff result", contribution: -0.4, value: 5, missing: false },
    { feature: "fourth_down_wp_lost_per_game", label: "Fourth-down WP lost per game (missing)", contribution: 0, value: null, missing: true },
    { feature: 3, contribution: 1 },
    null,
  ]);
  assert.equal(ds.length, 3);
  assert.equal(driverValue(ds[0]), "1.6 wins below the market's expectation");
  assert.equal(driverEffect(ds[0]), "raises the estimate");
  assert.equal(driverValue(ds[1]), "last season the team won the Super Bowl");
  assert.equal(driverEffect(ds[1]), "lowers the estimate");
  assert.equal(driverValue(ds[2]), "not available yet");
  assert.equal(driverEffect(ds[2]), "barely moves the estimate");
  assert.deepEqual(parseDrivers("nope"), []);
  const d = (feature: string, value: number) => driverValue({ feature, label: feature, contribution: 1, value, missing: false });
  assert.equal(d("is_first_year_coach", 1), "his first season with the team");
  assert.equal(d("division_rank", 3), "3rd in the division");
  assert.equal(d("consecutive_losing_seasons", 1), "1 losing season in a row before this one");
  assert.equal(d("fourth_down_wp_lost_per_game", 0.0179), "1.79 WP points per game given away on clear fourth-down calls");
  assert.equal(d("point_diff_per_game", -5.67), "−5.7 points per game");
});

test("records and outcomes are worded carefully", () => {
  assert.equal(recordWords(0, 3), "0–3");
  assert.equal(recordWords(2.5, 4), "2.5 wins in 4 games (a tie counts half)");
  const base = { censored: false, departureType: null, announced: null, labelStatus: "final" };
  assert.equal(outcomeWords(null).tone, "pending");
  assert.equal(outcomeWords({ ...base, departed: null, labelStatus: "pending" }).long, "pending until the season's departures are labelled");
  const fired = outcomeWords({ ...base, departed: true, departureType: "fired_after_season", announced: "2025-01-06" });
  assert.deepEqual([fired.tone, fired.short, fired.long], ["let-go", "Let go", "fired after the season, announced 2025-01-06"]);
  const other = outcomeWords({ ...base, departed: false, censored: true, departureType: "retired" });
  assert.equal(other.long, "retired: not counted as let go");
  assert.match(outcomeWords({ ...base, departed: false }).long, new RegExp(`by ${HOT_SEAT_WINDOW_DAYS} days after`));
  for (const o of [fired, other]) assert.doesNotMatch(o.long, /will be|hot seat/i);
});

test("phases and bands are named from lib/method.ts", () => {
  assert.equal(phaseName(HOT_SEAT_PHASES[0].key), "Weeks 2–4");
  assert.equal(phaseName("weeks_late"), "Week 10 on");
  assert.equal(phaseName("end_of_season"), "End of season");
  assert.equal(bandName(0), "Under 10%");
  assert.equal(bandName(1), "10–25%");
  assert.equal(bandName(HOT_SEAT_BANDS.length - 1), "50% or more");
});

test("the calibration grid orders phases and bands, and the early check is the first phase's top band", () => {
  const cell = (phase: string, band: number, n: number, departed: number): CalCell => ({ phase, band, n, departed, meanPred: 0.5, coachSeasons: Math.ceil(n / 3) });
  const rows = [cell("end_of_season", 0, 300, 9), cell("weeks_early", 3, 90, 46), cell("weeks_early", 0, 900, 86), cell("bogus", 1, 5, 1), cell("weeks_mid", 2, 0, 0)];
  const g = calibrationGrid(rows);
  assert.deepEqual(g.map((x) => x.phase), ["weeks_early", "end_of_season"]);
  assert.deepEqual(g[0].cells.map((c) => c.band), [0, 3]);
  assert.equal(g[0].cells[1].observed, 46 / 90);
  const e = earlySeasonCheck(rows);
  assert.ok(e);
  assert.equal(e.phaseName, "Weeks 2–4");
  assert.equal(e.bandName, "50% or more");
  assert.equal(e.n, 90);
  assert.equal(earlySeasonCheck([cell("weeks_early", 0, 10, 1)]), null);
});

test("timelines merge live over reconstructed and read in words", () => {
  const m = mergeTimeline([
    { coachId: "a", week: 3, snapshot: "weekly", kind: "backtest", probability: 0.7 },
    { coachId: "a", week: 2, snapshot: "weekly", kind: "backtest", probability: 0.61 },
    { coachId: "a", week: 3, snapshot: "weekly", kind: "live", probability: 0.72 },
    { coachId: "b", week: 2, snapshot: "weekly", kind: "backtest", probability: 0.1 },
  ]);
  assert.deepEqual(m.get("a")!.map((p) => [p.week, p.kind]), [[2, "backtest"], [3, "live"]]);
  assert.equal(timelineWords(m.get("a")!), "week 2: 61%, week 3: 72%");
  assert.equal(timelineWords([]), "No weekly estimate yet");
  const long = [2, 3, 4, 5, 6].map((w) => ({ week: w, snapshot: "weekly", kind: "backtest", probability: w === 4 ? 0.8 : 0.2 }));
  long.push({ week: 18, snapshot: "end_of_season", kind: "backtest", probability: 0.3 });
  assert.equal(timelineWords(long), "20% (week 2) to 30% (end of season), highest 80% (week 4)");
});

test("a coach's past seasons: the end-of-season snapshot, else his last week", () => {
  const rows = [
    { season: 2024, week: 18, snapshot: "end_of_season", kind: "backtest" },
    { season: 2024, week: 17, snapshot: "weekly", kind: "backtest" },
    { season: 2025, week: 8, snapshot: "weekly", kind: "backtest" },
    { season: 2025, week: 6, snapshot: "weekly", kind: "backtest" },
  ];
  assert.deepEqual(lastPerSeason(rows).map((r) => [r.season, r.week, r.snapshot]), [[2025, 8, "weekly"], [2024, 18, "end_of_season"]]);
});

test("track-record cells are found by model, slice and metric (main run, own probability by default)", async () => {
  const { trackCell, metricWithInterval } = await import("../../lib/hot-seat");
  const row = (o: Partial<Parameters<typeof trackCell>[0][number]>) => ({ variant: "main", model: "logit", prob: "prob", slice: "all", metric: "roc_auc", value: 0.788, lo: 0.745, hi: 0.825, nRows: 10059, nPos: 1744, nSeasons: 20, ...o });
  const rows = [row({}), row({ prob: "prob_iso", value: 0.7 }), row({ variant: "censored_dropped", value: 0.79 }), row({ metric: "brier", value: null })];
  assert.deepEqual(trackCell(rows, { model: "logit", slice: "all", metric: "roc_auc" })?.value, 0.788);
  assert.equal(trackCell(rows, { model: "logit", slice: "all", metric: "roc_auc", prob: "prob_iso" })?.value, 0.7);
  assert.equal(trackCell(rows, { model: "logit", slice: "all", metric: "brier" }), null);
  assert.equal(trackCell(rows, { model: "lgbm", slice: "all", metric: "roc_auc" }), null);
  assert.equal(metricWithInterval(trackCell(rows, { model: "logit", slice: "all", metric: "roc_auc" })), "0.788 (0.745 to 0.825)");
  assert.equal(metricWithInterval(null), "not published");
  assert.equal(metricWithInterval({ value: 0.12021, lo: null, hi: null, nRows: 1, nPos: 1, nSeasons: 1 }, 4), "0.1202");
});

test("the headline tiles carry their interval and rows, and skip what is not published", async () => {
  const { headlineStats } = await import("../../lib/hot-seat");
  const base = { variant: "main", model: "logit", prob: "prob", nRows: 10059, nPos: 1744, nSeasons: 20 };
  const rows = [
    { ...base, slice: "all", metric: "roc_auc", value: 0.788, lo: 0.745, hi: 0.825 },
    { ...base, slice: "all", metric: "brier", value: 0.12021, lo: null, hi: null },
  ];
  const t = headlineStats(rows, 0.95);
  assert.deepEqual(t.map((x) => [x.value, x.interval]), [["0.788", "95% interval 0.745 to 0.825"], ["0.1202", null]]);
  assert.equal(t[0].note, "10,059 rows, 20 test seasons");
  // the top-5 hit rate at week 12 beside (before) the season-end one, both from the rows
  const top5 = [
    { ...base, slice: "end_of_season", metric: "top5_hit_rate", value: 0.523256, lo: 0.436778, hi: 0.621067 },
    { ...base, slice: "week_12", metric: "top5_hit_rate", value: 0.490385, lo: 0.424501, hi: 0.558573 },
  ];
  const t5 = headlineStats([...rows, ...top5], 0.95).slice(2);
  assert.deepEqual(t5.map((x) => [x.label, x.value]), [
    ["Coaches let go who were in their season's top 5 at week 12", "0.490"],
    ["Coaches let go who were in their season's top 5 at season end", "0.523"],
  ]);
});

test("record vs expectation (coach page): the season's end, else his newest weekly row; NULL-safe", () => {
  const row = (season: number, week: number, snapshot: string, kind: string, regWins: number, games: number, exp: number | null) => ({
    season, week, snapshot, kind, team: "KC", regWins, regGamesPlayed: games, expectedWins: exp, winsVsExpected: exp === null ? 0.4 : regWins - exp,
  });
  const out = recordPerSeason([
    row(2024, 17, "weekly", "backtest", 14, 16, 12.2),
    row(2024, 18, "end_of_season", "backtest", 15, 17, 12.9),
    // the season in progress: backtest and live rows of week 3, then a newer live week 4
    row(2026, 3, "weekly", "backtest", 1, 3, 1.5),
    row(2026, 4, "weekly", "live", 2, 4, 2.4),
    // a season whose market expectation is not known: the record stays, the difference is not invented
    row(2005, 18, "end_of_season", "backtest", 10, 16, null),
  ]);
  assert.deepEqual(out.map((r) => [r.season, r.throughWeek, r.record]), [[2026, 4, "2–2"], [2024, null, "15–2"], [2005, null, "10–6"]]);
  assert.equal(out[1].expectedWins, 12.9);
  assert.equal(signedNum(out[1].winsVsExpected!), "+2.1");
  assert.equal(out[2].expectedWins, null);
  assert.equal(out[2].winsVsExpected, null);
  assert.deepEqual(recordPerSeason([]), []);
});

test("this season's weekly lists not started: when the first is due, from the weekly as-of rule", () => {
  const meta = (week: number, at: string, generatedAt = "2026-10-04T21:34:09Z") => ({ currentSeason: 2026, asOf: { at, week: { season: 2026, week } }, generatedAt });
  // the newest list is this season's: nothing to say
  assert.equal(seasonNotStarted(2026, meta(3, "2026-09-29T14:00:00Z")), null);
  assert.equal(seasonNotStarted(2025, { currentSeason: null, asOf: null, generatedAt: null }), null);
  // week 3's as-of known (past the first weekly week): the next list is due one week later
  const late = seasonNotStarted(2025, meta(3, "2026-09-29T14:00:00Z"))!;
  assert.equal(late.firstWeek, HOT_SEAT_FIRST_WEEK);
  assert.equal(late.dueAt, new Date(Date.parse("2026-09-29T14:00:00Z") + 7 * 864e5).toISOString());
  assert.equal(late.overdue, false);
  // published after that next as-of without a list: it is overdue
  const missed = seasonNotStarted(2025, meta(3, "2026-09-29T14:00:00Z", "2026-10-07T03:00:00Z"))!;
  assert.equal(missed.overdue, true);
  // week 1's as-of known, published that day: the first list is still to come
  const early = seasonNotStarted(2025, meta(1, "2026-09-15T14:00:00Z", "2026-09-15T15:00:00Z"))!;
  assert.equal(early.dueAt, new Date(Date.parse("2026-09-15T14:00:00Z") + (HOT_SEAT_FIRST_WEEK - 1) * 7 * 864e5).toISOString());
  assert.equal(early.overdue, false);
  // no weekly as-of of this season yet (preseason): no date is made up
  const pre = seasonNotStarted(2025, { currentSeason: 2026, asOf: { at: "2026-01-06T14:00:00Z", week: { season: 2025, week: 18 } }, generatedAt: null })!;
  assert.deepEqual(pre, { season: 2026, firstWeek: HOT_SEAT_FIRST_WEEK, dueAt: null, overdue: false });
  // nothing published at all
  assert.equal(seasonNotStarted(null, meta(3, "2026-09-29T14:00:00Z"))?.season, 2026);
});
