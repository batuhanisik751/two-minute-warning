import assert from "node:assert/strict";
import { test } from "node:test";
import { FLEX_SIZE, compareFlex, flexOrderOf, flexRankCounts, mergeFlex, type FlexCandidate, type FlexHistoryRow } from "../../lib/flex";

const c = (position: string, rank: number, chance: number | null, modelProb: number, gsisId = `${position}${rank}`): FlexCandidate => ({
  position,
  rank,
  chance,
  modelProb,
  gsisId,
});
const ids = (xs: { gsisId: string }[]) => xs.map((x) => x.gsisId);

test("merge order: chance first, across positions", () => {
  const picks = [c("RB", 1, 0.5, 0.6), c("RB", 2, 0.3, 0.4), c("WR", 1, 0.64, 0.65), c("TE", 1, 0.44, 0.43), c("WR", 2, 0.2, 0.3)];
  const m = mergeFlex(picks);
  assert.deepEqual(ids(m), ["WR1", "RB1", "TE1", "RB2", "WR2"]);
  assert.deepEqual(m.map((x) => x.flexRank), [1, 2, 3, 4, 5]);
  // his own rank and position travel with him
  assert.deepEqual(m.map((x) => `${x.position}${x.rank}`), ["WR1", "RB1", "TE1", "RB2", "WR2"]);
});

test("ties: equal chances go to the higher model probability, then the better own rank, then RB, WR, TE", () => {
  const picks = [
    c("TE", 1, 0.56, 0.563),
    c("WR", 9, 0.56, 0.545),
    c("RB", 4, 0.56, 0.545),
    c("WR", 3, 0.56, 0.563),
    c("TE", 3, 0.56, 0.563),
    c("RB", 3, 0.56, 0.55),
  ];
  assert.deepEqual(ids(mergeFlex(picks)), ["TE1", "WR3", "TE3", "RB3", "RB4", "WR9"]);
  // same chance, probability and rank: RB before WR before TE, whatever the input order
  const tied = [c("TE", 2, 0.5, 0.5), c("WR", 2, 0.5, 0.5), c("RB", 2, 0.5, 0.5)];
  assert.deepEqual(ids(mergeFlex(tied)), ["RB2", "WR2", "TE2"]);
  assert.deepEqual(ids(mergeFlex([...tied].reverse())), ["RB2", "WR2", "TE2"]);
  // and finally the id, so the order is total
  assert.ok(compareFlex(c("RB", 2, 0.5, 0.5, "a"), c("RB", 2, 0.5, 0.5, "b")) < 0);
});

test("null chances: after every chance, then by model probability; all null = model order", () => {
  const mixed = [c("RB", 1, null, 0.9), c("WR", 1, 0.1, 0.2), c("TE", 1, null, 0.5)];
  assert.deepEqual(ids(mergeFlex(mixed)), ["WR1", "RB1", "TE1"]);
  assert.equal(flexOrderOf(mergeFlex(mixed)), "mixed");
  const none = [c("RB", 2, null, 0.3), c("WR", 1, null, 0.7), c("TE", 1, null, 0.5), c("RB", 1, null, 0.3)];
  const m = mergeFlex(none);
  assert.deepEqual(ids(m), ["WR1", "TE1", "RB1", "RB2"]);
  assert.equal(flexOrderOf(m), "model");
  assert.equal(flexOrderOf(mergeFlex([c("RB", 1, 0.4, 0.4)])), "chance");
  assert.equal(flexOrderOf([]), "empty");
});

test("only RB, WR and TE; each player once; cut to 25", () => {
  const picks = [c("QB", 1, 0.9, 0.9), c("K", 1, 0.9, 0.9), c("RB", 1, 0.2, 0.2, "same"), c("WR", 4, 0.3, 0.3, "same")];
  assert.deepEqual(mergeFlex(picks).map((x) => `${x.position}${x.rank}`), ["WR4"]);
  const many = ["RB", "WR", "TE"].flatMap((p) => Array.from({ length: 25 }, (_, i) => c(p, i + 1, 0.6 - i * 0.02, 0.6 - i * 0.02)));
  const m = mergeFlex(many);
  assert.equal(m.length, FLEX_SIZE);
  assert.equal(m[FLEX_SIZE - 1].flexRank, FLEX_SIZE);
  assert.deepEqual(ids(m.slice(0, 4)), ["RB1", "WR1", "TE1", "RB2"]);
  assert.equal(mergeFlex(many, 5).length, 5);
});

test("hit-rate counts: the merged list's ranks, final outcomes only, per week", () => {
  const row = (season: number, week: number, x: FlexCandidate, yHit: boolean | null, status: string | null): FlexHistoryRow => ({
    ...x,
    season,
    week,
    yHit,
    status,
  });
  const rows = [
    // 2024 week 5: WR1 (FLEX 1) hit, TE1 (FLEX 2) pending: it still takes rank 2, RB1 (FLEX 3)
    // no hit, TE2 (FLEX 4) hit
    row(2024, 5, c("RB", 1, 0.4, 0.4), false, "final"),
    row(2024, 5, c("WR", 1, 0.5, 0.5), true, "final"),
    row(2024, 5, c("TE", 1, 0.45, 0.45), null, "pending"),
    row(2024, 5, c("TE", 2, 0.1, 0.1), true, "final"),
    // 2025 week 1: no chances (ordered by model probability): RB1 (FLEX 1) has no outcome row
    // at all, WR1 (FLEX 2) hit
    row(2025, 1, c("RB", 1, null, 0.7), null, null),
    row(2025, 1, c("WR", 1, null, 0.6), true, "final"),
    // 2025 week 2: nothing final: not a counted list
    row(2025, 2, c("RB", 1, 0.3, 0.3), null, "pending"),
  ];
  const got = flexRankCounts(rows);
  assert.deepEqual(got.counts, [
    { rank: 1, picks: 1, hits: 1 },
    { rank: 2, picks: 1, hits: 1 },
    { rank: 3, picks: 1, hits: 0 },
    { rank: 4, picks: 1, hits: 1 },
  ]);
  assert.equal(got.lists, 2);
  assert.equal(got.seasonFrom, 2024);
  assert.equal(got.seasonTo, 2025);
  assert.deepEqual(flexRankCounts([]), { counts: [], seasonFrom: null, seasonTo: null, lists: 0 });
});
