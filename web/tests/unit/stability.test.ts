import assert from "node:assert/strict";
import { test } from "node:test";
import { extremes, positionsOf, shrinkGames, shrinkTable, splitHalf, studyWindow, type StabilityRow } from "../../lib/stability";

const sh = (position: string, metric: string, value: number, p: Partial<StabilityRow> = {}): StabilityRow => ({
  section: "split_half", seasons: "2009-2025", split: "odd_even", position, metric, g: null, n: 100, value,
  lo: value - 0.05, hi: value + 0.05, varSignal: null, varNoise: null, priorMean: null, ...p,
});
const rg = (position: string, metric: string, g: number, value: number, p: Partial<StabilityRow> = {}): StabilityRow => ({
  section: "shrinkage", seasons: "2009-2025", split: "odd_even", position, metric, g, n: 600, value,
  lo: g === 17 ? null : value - 0.02, hi: g === 17 ? null : value + 0.02, varSignal: 0.5, varNoise: 30, priorMean: -0.7, ...p,
});

const ROWS: StabilityRow[] = [
  sh("QB", "xfp", 0.764, { n: 607 }), sh("QB", "fpoe", 0.098, { n: 607 }), sh("QB", "completion_rate_over_expected", 0.333, { n: 594 }),
  sh("RB", "xfp", 0.884, { n: 1476 }), sh("RB", "fpoe", 0.162, { n: 1476 }), sh("RB", "catch_rate_over_expected", 0.086, { n: 857 }),
  sh("RB", "xfp", 0.75, { split: "first_second" }),
  rg("QB", "fpoe", 4, 0.063), rg("QB", "fpoe", 8, 0.119), rg("QB", "fpoe", 17, 0.223),
  rg("QB", "fpoe", 8, 0.2, { seasons: "2009-2014" }),
  rg("RB", "fpoe", 4, 0.111, { varSignal: 0.54, varNoise: 17.2 }), rg("RB", "fpoe", 17, 0.346, { varSignal: 0.54, varNoise: 17.2 }),
  rg("RB", "fpoe_ng", 4, 0.086),
];

test("positions keep the study's order and the window is the split-half rows'", () => {
  assert.deepEqual(positionsOf(ROWS), ["QB", "RB"]);
  assert.equal(studyWindow(ROWS), "2009-2025");
  assert.equal(studyWindow(ROWS.filter((r) => r.section === "shrinkage")), null);
});

test("split-half table: a cell per metric with its interval and n; a per-position metric; missing cells are null", () => {
  const t = splitHalf(ROWS, "odd_even", ["xfp", "fpoe", { QB: "completion_rate_over_expected", "*": "catch_rate_over_expected" }, "yac_over_expected"]);
  assert.deepEqual(t.map((x) => [x.position, x.n]), [["QB", 607], ["RB", 1476]]);
  assert.equal(t[0].cells[0]?.value, 0.764);
  assert.equal(t[0].cells[2]?.n, 594, "QB: the completion rate");
  assert.equal(t[1].cells[2]?.value, 0.086, "RB: the catch rate");
  assert.equal(t[0].cells[3], null);
  assert.deepEqual(splitHalf(ROWS, "first_second", ["xfp"]).map((x) => x.position), ["RB"]);
});

test("extremes: the lowest and highest position of a column", () => {
  const t = splitHalf(ROWS, "odd_even", ["xfp", "fpoe"]);
  assert.deepEqual(extremes(t, 0), { low: { position: "QB", value: 0.764 }, high: { position: "RB", value: 0.884 } });
  assert.deepEqual(extremes(t, 1)?.low, { position: "QB", value: 0.098 });
  assert.equal(extremes([], 0), null);
});

test("shrinkage table: one window and metric, variances, games for half weight and r(g) cells", () => {
  const t = shrinkTable(ROWS, "fpoe", "2009-2025", [4, 6, 17]);
  assert.deepEqual(t.map((x) => x.position), ["QB", "RB"]);
  assert.equal(t[0].n, 600);
  assert.equal(t[0].halfWeight, 60);
  assert.equal(Math.round(t[1].halfWeight!), 32);
  assert.deepEqual(t[0].r.map((c) => c?.value ?? null), [0.063, null, 0.223]);
  assert.equal(t[0].r[2]?.lo, null, "r(17) has no interval");
  assert.equal(shrinkTable(ROWS, "fpoe", "2009-2014", [8])[0].r[0]?.value, 0.2);
  assert.deepEqual(shrinkTable(ROWS, "fpoe", "2009-2025", [4]).length, 2);
  assert.deepEqual(shrinkTable(ROWS, "xfp", "2009-2025", [4]), []);
});

test("r(g) columns: the backtest weeks the study has, then its last g", () => {
  assert.deepEqual(shrinkGames(ROWS, [4, 6, 8, 10]), [4, 8, 17]);
  assert.deepEqual(shrinkGames(ROWS, []), [17]);
  assert.deepEqual(shrinkGames(ROWS.filter((r) => r.section === "split_half"), [4]), []);
});
