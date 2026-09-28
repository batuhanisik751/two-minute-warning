import assert from "node:assert/strict";
import { test } from "node:test";
import type { TrackRow } from "../../lib/queries/track";
import { headline, pooledP10, select, widestRange } from "../../lib/track";

const row = (o: Partial<TrackRow>): TrackRow => ({
  model: "logit",
  label: "y_hit",
  metric: "p_at_10",
  scope: "pooled",
  scopeValue: "",
  seasonFrom: 2014,
  seasonTo: 2025,
  exclRostered: false,
  value: 0.5,
  low: 0.48,
  high: 0.52,
  nLists: 732,
  nPositives: null,
  nRows: null,
  nTopHits: null,
  nTop: null,
  ...o,
});

// the real 2014-2025 and 2015-2025 pooled rows (reports/waiver_radar/evaluation.csv)
const rows: TrackRow[] = [
  row({ model: "logit", value: 0.478279, low: 0.462222, high: 0.494519 }),
  row({ model: "baseline_last_points", value: 0.403825, low: 0.387567, high: 0.420418 }),
  row({ model: "logit", seasonFrom: 2015, value: 0.480178 }),
  row({ model: "baseline_last_points", seasonFrom: 2015, value: 0.407249 }),
  row({ model: "logit", exclRostered: true, value: 0.455055 }),
  row({ model: "logit", label: "y_sustained", value: 0.160246 }),
  row({ model: "logit", metric: "p_at_10_diff", scope: "diff", scopeValue: "baseline_last_points", value: 0.074454, low: 0.062427, high: 0.08675 }),
  row({ model: "logit", metric: "p_at_10_diff", scope: "diff", scopeValue: "lgbm", value: 0.002459 }),
];

test("widestRange prefers the earliest start, then the latest end", () => {
  assert.deepEqual(
    widestRange([
      { seasonFrom: 2015, seasonTo: 2025 },
      { seasonFrom: 2014, seasonTo: 2024 },
      { seasonFrom: 2014, seasonTo: 2025 },
    ]),
    { seasonFrom: 2014, seasonTo: 2025 },
  );
  assert.equal(widestRange([]), null);
});

test("the headline is the Radar vs last week's points, y_hit, all pool rows, widest range", () => {
  const h = headline(rows, "logit")!;
  assert.deepEqual(h.range, { seasonFrom: 2014, seasonTo: 2025 });
  assert.equal(h.radar.value, 0.478279);
  assert.equal(h.radar.low, 0.462222);
  assert.equal(h.baseline.value, 0.403825);
  assert.equal(h.diff?.value, 0.074454);
});

test("no headline without both rows for the same range", () => {
  assert.equal(headline(rows.filter((r) => r.model !== "baseline_last_points"), "logit"), null);
  assert.equal(headline(rows, "lgbm"), null);
  // the range must be one both methods have
  const only2015 = rows.filter((r) => !(r.model === "baseline_last_points" && r.seasonFrom === 2014));
  assert.deepEqual(headline(only2015, "logit")?.range, { seasonFrom: 2015, seasonTo: 2025 });
});

test("select and pooledP10 filter on every key", () => {
  const range = { seasonFrom: 2014, seasonTo: 2025 };
  assert.equal(pooledP10(rows, "logit", "y_hit", true, range)?.value, 0.455055);
  assert.equal(pooledP10(rows, "logit", "y_sustained", false, range)?.value, 0.160246);
  assert.equal(select(rows, { scope: "pooled", metric: "p_at_10", label: "y_hit", exclRostered: false, range }).length, 2);
});
