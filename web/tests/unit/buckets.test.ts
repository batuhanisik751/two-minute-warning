import assert from "node:assert/strict";
import { test } from "node:test";
import { BUCKETS, badgeText, badges, bucketOf, seasonsText } from "../../lib/buckets";

test("the buckets are 1-5, 6-10, 11-25", () => {
  assert.deepEqual(BUCKETS.map((b) => b.label), ["1-5", "6-10", "11-25"]);
  assert.equal(bucketOf(1)?.label, "1-5");
  assert.equal(bucketOf(5)?.label, "1-5");
  assert.equal(bucketOf(6)?.label, "6-10");
  assert.equal(bucketOf(10)?.label, "6-10");
  assert.equal(bucketOf(11)?.label, "11-25");
  assert.equal(bucketOf(25)?.label, "11-25");
  assert.equal(bucketOf(26), null);
  assert.equal(bucketOf(0), null);
  assert.equal(bucketOf(2.5), null);
});

test("badges add picks and hits per bucket; ranks outside every bucket are ignored", () => {
  const b = badges([
    { rank: 1, picks: 10, hits: 6 },
    { rank: 5, picks: 10, hits: 4 },
    { rank: 7, picks: 10, hits: 3 },
    { rank: 25, picks: 10, hits: 1 },
    { rank: 26, picks: 10, hits: 9 },
  ]);
  assert.deepEqual(
    b.map((x) => [x.bucket.label, x.picks, x.hits, x.rate]),
    [
      ["1-5", 20, 10, 0.5],
      ["6-10", 10, 3, 0.3],
      ["11-25", 10, 1, 0.1],
    ],
  );
});

test("a bucket without picks has no rate (never 0%)", () => {
  const b = badges([{ rank: 2, picks: 4, hits: 1 }]);
  assert.equal(b[1].rate, null);
  assert.equal(badgeText(b[1]), "Ranks 6–10: no earlier lists");
  assert.equal(badgeText(b[0]), "Ranks 1–5: 25% hit (1 of 4)");
});

test("the real track record's pooled logit buckets are reproduced from per-rank counts", () => {
  // track_record scope 'bucket', logit, y_hit, 2014-2025: 2,064 of 3,660; 1,437 of 3,660;
  // 2,632 of 10,980 (reports/waiver_radar/evaluation.csv); per-rank counts summing to them
  const b = badges([
    { rank: 1, picks: 3660, hits: 2064 },
    { rank: 6, picks: 3660, hits: 1437 },
    { rank: 11, picks: 10980, hits: 2632 },
  ]);
  assert.deepEqual(b.map(badgeText), [
    "Ranks 1–5: 56% hit (2,064 of 3,660)",
    "Ranks 6–10: 39% hit (1,437 of 3,660)",
    "Ranks 11–25: 24% hit (2,632 of 10,980)",
  ]);
});

test("season spans", () => {
  assert.equal(seasonsText(2014, 2025), "2014–2025");
  assert.equal(seasonsText(2024, 2024), "2024");
  assert.equal(seasonsText(null, null), null);
});
