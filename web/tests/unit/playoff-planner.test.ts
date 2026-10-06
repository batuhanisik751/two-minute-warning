// lib/playoff-planner.ts (feature #6): the grid of a position (ratings only where the rule rates,
// byes, the total), its sorting and links, the bands, the resting note's numbers, the rule's
// verdict and the words for an unrated position.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  PLANNER_NOTE,
  band,
  candidatesOf,
  gridFor,
  lateEras,
  missRange,
  opponentLabel,
  parsePlannerPosition,
  parseSort,
  parseWeeks,
  plannerHref,
  sortGrid,
  unratedReason,
  verdict,
  type PPBacktestRow,
  type PPRow,
} from "../../lib/playoff-planner";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const W = [15, 16, 17];

test("the planner note is the Python module's, word for word", () => {
  const py = readFileSync(join(repo, "src/twm/modules/playoff_planner/weekly.py"), "utf8");
  const m = /NOTE = \(([\s\S]*?)\)  # fmt: skip/.exec(py);
  assert.ok(m, "NOTE not found in weekly.py");
  assert.equal(PLANNER_NOTE, [...m[1].matchAll(/"([^"]*)"/g)].map((x) => x[1]).join(""));
});

const row = (week: number, team: string, position: string, opponent: string | null, rating: number | null, candidate = "adjusted", rank: number | null = 5): PPRow => ({
  week, team, position, opponent, home: opponent ? team < opponent : null, candidate, rating, ratingRank: opponent && candidate !== "none" ? rank : null,
  raw: rating, shrunk: rating, adjusted: rating, oppGames: 3,
});
const ROWS: PPRow[] = [
  row(15, "AAA", "QB", "BBB", 1.1, "adjusted", 1), row(16, "AAA", "QB", "CCC", 0.9, "adjusted", 30), row(17, "AAA", "QB", null, null),
  row(15, "BBB", "QB", "AAA", 1.0), row(16, "BBB", "QB", "CCC", 1.0), row(17, "BBB", "QB", "CCC", 1.06),
  row(15, "AAA", "TE", "BBB", 1.0, "none"), row(16, "AAA", "TE", "CCC", 1.0, "none"), row(17, "AAA", "TE", null, null, "none"),
];

test("a rated position: ratings, a bye adds nothing, the total and the games", () => {
  const g = gridFor(ROWS, "QB", W);
  assert.equal(g.rated, true);
  assert.deepEqual(g.teams.map((t) => [t.team, t.total, t.games]), [["AAA", 2, 2], ["BBB", 3.06, 3]]);
  const bye = g.teams[0].cells[2];
  assert.deepEqual([bye.opponent, bye.rating, opponentLabel(bye)], [null, null, "Bye"]);
  assert.equal(opponentLabel(g.teams[0].cells[0]), "vs BBB");
  assert.equal(opponentLabel(g.teams[1].cells[0]), "@ AAA");
});

test("an unrated position shows the schedule without ratings or totals", () => {
  const g = gridFor(ROWS, "TE", W);
  assert.equal(g.rated, false);
  assert.deepEqual(g.teams[0].cells.map((c) => [c.opponent, c.rating, c.rank]), [["BBB", null, null], ["CCC", null, null], [null, null, null]]);
  assert.equal(g.teams[0].total, null);
  assert.match(unratedReason("TE", { position: "TE", horizon: "all", nWorst: 1, nBest: 1, ratedGap: 6.6, shrunkGap: 0.5, realizedGap: 0.48, survived: 0.07 }, 13), /in 13 seasons of tests \(.*only 0\.5 points per game\): every matchup counts as 1\.00/);
  assert.match(unratedReason("K", undefined, null), /^For K, .* in the tests: every matchup/);
});

test("sorting: easiest total or week first, byes last, else by team", () => {
  const g = gridFor(ROWS, "QB", W).teams;
  assert.deepEqual(sortGrid(g, "total").map((t) => t.team), ["BBB", "AAA"]);
  assert.deepEqual(sortGrid(g, "w15").map((t) => t.team), ["AAA", "BBB"]);
  assert.deepEqual(sortGrid(g, "w17").map((t) => t.team), ["BBB", "AAA"]);
  assert.deepEqual(sortGrid([...g].reverse(), "team").map((t) => t.team), ["AAA", "BBB"]);
  assert.deepEqual([parseSort("total", W), parseSort("w16", W), parseSort("w18", W), parseSort(["x"], W), parseSort(undefined, W)], ["total", "w16", "team", "team", "team"]);
});

test("bands, positions, links and the weeks", () => {
  // judged as shown (two decimals): 1.0497 shows "1.05" and is easy; 1.0449 shows "1.04"
  assert.deepEqual([band(1.05), band(1.0497), band(1.0449), band(1), band(0.9551), band(0.9549), band(0.95)], ["easy", "easy", "neutral", "neutral", "neutral", "hard", "hard"]);
  assert.deepEqual([parsePlannerPosition("rb"), parsePlannerPosition("D/ST"), parsePlannerPosition("FB"), parsePlannerPosition(undefined)], ["RB", "DST", "QB", "QB"]);
  assert.equal(plannerHref({ pos: "QB", sort: "team" }), "/playoff-planner");
  assert.equal(plannerHref({ pos: "DST", sort: "w16" }), "/playoff-planner?pos=DST&sort=w16");
  assert.deepEqual(parseWeeks("14,15,16"), [14, 15, 16]);
  assert.deepEqual([parseWeeks(""), parseWeeks(null), parseWeeks("15,x")], [W, W, W]);
});

test("the resting note: the last two playoff weeks' played shares per era", () => {
  const lw = (era: string, position: string, week: number, playedShare: number) => ({ era, position, week, based: 100, playedShare, vsBase: 0 });
  const eras = lateEras([lw("2013-2020 (17 weeks)", "QB", 16, 0.68), lw("2013-2020 (17 weeks)", "QB", 17, 0.62), lw("2013-2020 (17 weeks)", "QB", 15, 0.7), lw("2021+ (18 weeks)", "RB", 16, 0.75), lw("2021+ (18 weeks)", "RB", 17, 0.73), lw("2021+ (18 weeks)", "DST", 17, 1)]);
  assert.deepEqual(eras, [
    { era: "2013-2020 (17 weeks)", rows: [{ position: "QB", before: 0.68, last: 0.62 }] },
    { era: "2021+ (18 weeks)", rows: [{ position: "RB", before: 0.75, last: 0.73 }] },
  ]);
});

test("the candidates in the rule's order and its verdict", () => {
  const c = (candidate: string, vs: string | null, seasonsWon: number | null, tookOver: boolean, mae: number): PPBacktestRow => ({
    position: "RB", candidate, title: candidate, n: 100, mae, vs, seasonsWon, seasons: 13, tookOver, chosen: false, rulePick: false,
  });
  const rows = [c("shrunk", "none", 10, true, 6.1), c("adjusted", "shrunk", 5, false, 6.104), c("none", null, null, true, 6.13), c("raw", "none", 2, false, 6.19)];
  assert.deepEqual(candidatesOf(rows, "RB").map((r) => r.candidate), ["none", "raw", "shrunk", "adjusted"]);
  assert.equal(verdict(rows[2], 7), "the starting point: matchups ignored");
  assert.equal(verdict(rows[0], 7), "beat no matchup in 10 of 13 seasons: took over");
  assert.equal(verdict(rows[1], 7), "beat shrunk in 5 of 13 seasons (needs 7 and a lower MAE): not used");
  assert.deepEqual(missRange([...rows, { ...rows[2], position: "QB", mae: 6.7 }, { ...rows[2], position: "K", mae: 3.6 }]), [6.13, 6.7]);
  assert.equal(missRange([]), null);
});
