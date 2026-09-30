import assert from "node:assert/strict";
import { test } from "node:test";
import { positionShort } from "../../lib/positions";
import {
  isStreamPosition,
  nextGame,
  starterWeek,
  streamComparison,
  streamMethodOf,
  streamResult,
  streamTopN,
  verdictOf,
  type StreamTrackRow,
} from "../../lib/streamer";

const row = (p: Partial<StreamTrackRow>): StreamTrackRow => ({
  position: "K", method: "logit", trainOn: "pool", scope: "pooled", seasons: "2013-2025", key: null, metric: "p_at_5",
  value: null, lo: null, hi: null, nGroups: 210, nRows: 2318, nPos: 571, ...p,
});

const ROWS: StreamTrackRow[] = [
  row({ method: "logit", value: 0.3836, lo: 0.36, hi: 0.4 }),
  row({ method: "lgbm", value: 0.37 }),
  row({ method: "baseline_last_points", value: 0.3693 }),
  row({ method: "baseline_ppg", value: 0.3674 }),
  row({ method: "baseline_opponent", value: 0.28 }),
  row({ scope: "diff", method: "logit", key: "baseline_last_points", value: 0.0143, lo: -0.002, hi: 0.0298, nRows: null, nPos: null }),
  row({ scope: "diff", method: "logit", key: "baseline_opponent", value: 0.1, lo: 0.06, hi: 0.14 }),
  row({ trainOn: "universe", method: "logit", value: 0.99 }),
  // D/ST: the rule is published
  row({ position: "DST", method: "logit", value: 0.3912, nRows: 1424, nPos: 532 }),
  row({ position: "DST", method: "lgbm", value: 0.3847, nRows: 1424, nPos: 532 }),
  row({ position: "DST", method: "baseline_opponent", value: 0.4025, nRows: 1424, nPos: 532 }),
  row({ position: "DST", scope: "diff", method: "logit", key: "baseline_opponent", value: -0.0113, lo: -0.0255, hi: 0.0019 }),
];

test("a model is compared with the best simple rule; the diff row is the published one", () => {
  const c = streamComparison(ROWS, "K", "logit")!;
  assert.equal(c.other.method, "baseline_last_points");
  assert.deepEqual(c.diff, { value: 0.0143, lo: -0.002, hi: 0.0298 });
  assert.equal(c.verdict, "ahead");
  assert.equal(c.baseRate, 571 / 2318);
  assert.equal(c.ours.value, 0.3836);
});

test("a rule is compared with the best model, the model's diff row negated", () => {
  const c = streamComparison(ROWS, "DST", "baseline_opponent")!;
  assert.equal(c.other.method, "logit");
  assert.equal(c.diff!.value, 0.0113);
  assert.equal(c.diff!.lo, -0.0019);
  assert.equal(c.diff!.hi, 0.0255);
  assert.equal(c.verdict, "ahead");
  assert.equal(streamComparison(ROWS, "DST", "nope"), null);
  assert.equal(streamComparison([], "K", "logit"), null);
});

test("verdicts follow the interval", () => {
  assert.equal(verdictOf(0.1, 0.05, 0.15), "ahead-clearly");
  assert.equal(verdictOf(-0.1, -0.15, -0.05), "behind-clearly");
  assert.equal(verdictOf(-0.01, -0.03, 0.01), "behind");
  assert.equal(verdictOf(0, null, null), "tied");
});

test("the published model's name maps onto the track record's method", () => {
  assert.equal(streamMethodOf("logit_k"), "logit");
  assert.equal(streamMethodOf("baseline_opponent_dst"), "baseline_opponent");
  assert.equal(streamMethodOf("lgbm"), "lgbm");
});

test("the starter cutoff is read from the published y_start formula, never typed in", () => {
  const g = [{ name: "y_start", formula: "rank in the top (teams x slots): top 10 K and top 8 DST in this league (ties count)" }];
  assert.deepEqual(streamTopN(g), { K: 10, DST: 8 });
  assert.equal(starterWeek(streamTopN(g), "DST"), "top-8 week");
  assert.equal(starterWeek(streamTopN([]), "K"), "starter week");
});

test("next game and next week's result in words", () => {
  assert.equal(nextGame("PHI", false), "at PHI (away)");
  assert.equal(nextGame("NYG", true), "vs NYG (home)");
  assert.equal(nextGame("NYG", null), "NYG (home or away not known yet)");
  assert.equal(nextGame(null, true), null);
  const top = { K: 12 };
  assert.equal(streamResult(true, "final", 11, top, "K"), "Next week: top 12, 11.0 pts");
  assert.equal(streamResult(false, "final", null, top, "K"), "Next week: outside the top 12, no points recorded (no game or no kick)");
  assert.equal(streamResult(false, "final", 4, {}, "K"), "Next week: not a starter week, 4.0 pts");
  assert.equal(streamResult(null, "pending", null, top, "K"), null);
});

test("positions: K and DST are streamed; DST reads D/ST", () => {
  assert.ok(isStreamPosition("K") && isStreamPosition("DST") && !isStreamPosition("QB"));
  assert.equal(positionShort("DST"), "D/ST");
  assert.equal(positionShort("K"), "K");
});
