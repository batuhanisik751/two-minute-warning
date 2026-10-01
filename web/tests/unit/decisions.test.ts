// The Decision Report Card's words and rules (lib/decisions.ts).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  aggressivenessWords,
  againstConvention,
  clockAmountWords,
  clockSituation,
  clockWords,
  downWords,
  fieldWords,
  gainOverNext,
  leaderboardMinGames,
  optionName,
  optionsOf,
  ordinal,
  outcomeWords,
  rankCoaches,
  scoreWords,
  situationWords,
  weekWords,
  wpPct,
  wpPoints,
} from "../../lib/decisions";
import { LEADERBOARD_MIN_GAMES } from "../../lib/method";

const fourth = { kind: "fourth" as const, qtr: 4, quarterSeconds: 372, scoreDifferential: -3, ydstogo: 2, yardline100: 38 };

test("the situation in words: the task's example", () => {
  assert.equal(situationWords(fourth), "4th and 2 at the opponent's 38, down 3, 6:12 left in Q4");
});

test("field position: opponent's half, midfield, own half", () => {
  assert.equal(fieldWords(38), "the opponent's 38");
  assert.equal(fieldWords(1), "the opponent's 1");
  assert.equal(fieldWords(50), "midfield");
  assert.equal(fieldWords(75), "its own 25");
  assert.equal(fieldWords(99), "its own 1");
});

test("down and distance: goal to go when the line to gain is the goal line", () => {
  assert.equal(downWords(4, 2, 38), "4th and 2");
  assert.equal(downWords(4, 3, 3), "4th and goal");
  assert.equal(downWords(1, 10, 63), "1st and 10");
  assert.deepEqual([1, 2, 3, 4, 11, 12, 13, 21, 22, 102].map(ordinal), ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "102nd"]);
});

test("score and clock: up, down, tied; seconds padded; overtime", () => {
  assert.deepEqual([7, -3, 0].map(scoreWords), ["up 7", "down 3", "tied"]);
  assert.equal(clockWords(2, 5), "0:05 left in Q2");
  assert.equal(clockWords(1, 900), "15:00 left in Q1");
  assert.equal(clockWords(5, 431), "7:11 left in overtime");
  assert.equal(clockWords(4, -2), "0:00 left in Q4");
});

test("a fourth down at its own end, tied in overtime, and a try after a touchdown", () => {
  assert.equal(situationWords({ ...fourth, qtr: 5, quarterSeconds: 120, scoreDifferential: 0, ydstogo: 1, yardline100: 66 }), "4th and 1 at its own 34, tied, 2:00 left in overtime");
  assert.equal(situationWords({ ...fourth, ydstogo: 4, yardline100: 4 }), "4th and goal at the opponent's 4, down 3, 6:12 left in Q4");
  const tryRow = { kind: "two_point" as const, qtr: 4, quarterSeconds: 72, scoreDifferential: -1, ydstogo: null, yardline100: null };
  assert.equal(situationWords(tryRow), "Try after a touchdown, down 1 before the try, 1:12 left in Q4");
});

test("options: names, best first, missing options dropped, the gain of the call", () => {
  assert.equal(optionName("field_goal"), "Field goal");
  assert.equal(optionName("two_point"), "Go for two");
  const options = optionsOf({ go: 0.52, field_goal: null, punt: 0.47 });
  assert.deepEqual(options, [{ option: "go", wp: 0.52 }, { option: "punt", wp: 0.47 }]);
  assert.ok(Math.abs(gainOverNext({ chosen: "go", options })! - 0.05) < 1e-12);
  assert.ok(Math.abs(gainOverNext({ chosen: "punt", options })! + 0.05) < 1e-12);
  assert.equal(gainOverNext({ chosen: "go", options: [{ option: "go", wp: 0.5 }] }), null);
});

test("against convention: a clear go or two that was taken", () => {
  assert.equal(againstConvention({ grade: "clear", chosen: "go", recommended: "go" }), true);
  assert.equal(againstConvention({ grade: "clear", chosen: "two_point", recommended: "two_point" }), true);
  assert.equal(againstConvention({ grade: "toss_up", chosen: "go", recommended: "go" }), false);
  assert.equal(againstConvention({ grade: "clear", chosen: "punt", recommended: "punt" }), false);
  assert.equal(againstConvention({ grade: "clear", chosen: "go", recommended: "punt" }), false);
});

test("formatting: WP as a percentage, WP points, outcomes, weeks", () => {
  assert.equal(wpPct(0.47312), "47.3%");
  assert.equal(wpPoints(0.0421), "4.2");
  assert.equal(wpPoints(-0.00001), "0.0");
  assert.equal(outcomeWords("made"), "the kick was good");
  assert.equal(outcomeWords("failed"), "failed");
  assert.equal(outcomeWords("failed", "two_point"), "the kick missed");
  assert.equal(outcomeWords("success", "two_point"), "converted");
  assert.equal(outcomeWords(null), null);
  assert.equal(weekWords(5, "REG"), "week 5");
  assert.equal(weekWords(20, "POST"), "playoffs, week 20");
});

test("the leaderboard: the minimum games (early season: the most anyone has), least WP lost per game first", () => {
  assert.equal(leaderboardMinGames([3, 3, 2]), 3);
  assert.equal(leaderboardMinGames([17, 4, 12]), LEADERBOARD_MIN_GAMES);
  assert.equal(leaderboardMinGames([]), 0);
  const rows = [
    { coachId: "b", name: "Bea", games: 17, wpLostPerGame: 0.02 },
    { coachId: "a", name: "Abe", games: 17, wpLostPerGame: 0.02 },
    { coachId: "c", name: "Cy", games: 4, wpLostPerGame: 0.001 },
    { coachId: "d", name: "Di", games: 12, wpLostPerGame: 0.01 },
  ];
  const r = rankCoaches(rows);
  assert.equal(r.minGames, LEADERBOARD_MIN_GAMES);
  assert.deepEqual(r.ranked.map((x) => x.coachId), ["d", "a", "b"]);
  assert.deepEqual(r.fewer.map((x) => x.coachId), ["c"]);
});

test("aggressiveness in words", () => {
  assert.equal(aggressivenessWords(3, 7), "3 of 7 (43%)");
  assert.equal(aggressivenessWords(0, 0), "no clear go");
});

test("clock cases: the opponent's snap turned to the team's view; the team's own snap; amounts", () => {
  const base = { qtr: 4, quarterSeconds: 83, down: 1, ydstogo: 10, yardline100: 63, scoreDifferential: 7, timeouts: 1 };
  assert.equal(clockSituation({ ...base, metric: "timeouts_unused", amount: 1 }), "Opponent's ball: 1st and 10 at its own 37, down 7, 1:23 left in Q4, 1 timeout in hand");
  assert.equal(
    clockSituation({ ...base, metric: "half_passivity", qtr: 2, quarterSeconds: 73, yardline100: 50, timeouts: 2, amount: 1.79 }),
    "1st and 10 at midfield, up 7, 1:13 left in Q2, 2 timeouts in hand",
  );
  assert.equal(clockSituation({ ...base, metric: "seconds_wasted", down: null, amount: 39 }), "Opponent's ball, down 7, 1:23 left in Q4, 1 timeout in hand");
  assert.equal(clockAmountWords("timeouts_unused", 2), "2 timeouts unused");
  assert.equal(clockAmountWords("timeouts_unused", 1), "1 timeout unused");
  assert.equal(clockAmountWords("half_passivity", 1.7883), "1.8 expected points left");
  assert.equal(clockAmountWords("seconds_wasted", 39), "39 seconds wasted");
});

test("coach totals and the league's season: sums of the published counts", async () => {
  const { coachTotals, leagueTotals } = await import("../../lib/decisions");
  const a = { fourthGraded: 5, fourthTossUps: 7, fourthWrong: 2, twoPointGraded: 1, twoPointTossUps: 3, twoPointWrong: 1, m1Cases: 1, m2Cases: 0, m3Cases: 2, goClear: 4, goClearWent: 1, games: 3, wpLost: 0.1 };
  assert.deepEqual(coachTotals(a), { decisions: 16, clear: 6, tossUps: 10, wrong: 3, clockCases: 3 });
  const l = leagueTotals([a, { ...a, goClear: 2, goClearWent: 2, games: 2, wpLost: 0.05 }]);
  assert.equal(l.coaches, 2);
  assert.equal(l.teamGames, 5);
  assert.equal(l.fourthGraded, 10);
  assert.equal(l.goClear, 6);
  assert.equal(l.goClearWent, 3);
  assert.ok(Math.abs(l.wpLost - 0.15) < 1e-12);
  assert.equal(l.clockCases, 6);
});

test("coach ids: dim_coach's slugs only", async () => {
  const { isCoachId } = await import("../../lib/decisions");
  for (const ok of ["andy-reid", "bill-o-brien", "a1"]) assert.ok(isCoachId(ok), ok);
  for (const bad of ["Andy-Reid", "andy--reid", "-andy", "andy_reid", "", "x".repeat(65), "../etc"]) assert.ok(!isCoachId(bad), bad);
});
