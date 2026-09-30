import assert from "node:assert/strict";
import { test } from "node:test";
import {
  DROPPED_TAGS,
  TAGS,
  droppedRates,
  dropTags,
  paramNumber,
  reasonWithout,
  parseGarbage,
  regressionHref,
  reliability,
  shrinkRows,
  signed,
  statsOf,
  tagRows,
  tagVerdict,
  trackRow,
  type RegressionRow,
  type RegressionTrackRow,
} from "../../lib/regression";

const r = (id: string, p: Partial<RegressionRow>): RegressionRow => ({
  gsisId: id, name: id, position: "WR", team: "AAA", teamName: "A", teamColor: null, teamColor2: null, games: 4,
  ppg: 10, ppgNg: 9, xfpPg: 9, xfpPgNg: 8, fpoePg: 1, fpoePgNg: 1, projection: 9.5, shrinkage: 0.1,
  tag: null, tags: [], tagReason: null, outcome: null, ...p,
});

test("tag tables: Sell-high by projection below PPG, Buy-low by projection above; only the two shown tags", () => {
  assert.deepEqual([...TAGS], ["sell_high", "buy_low"]);
  const rows = [
    r("00-1", { ppg: 20, projection: 15, tags: ["sell_high"], tag: "sell_high" }),
    r("00-2", { ppg: 30, projection: 20, tags: ["sell_high"], tag: "sell_high" }),
    r("00-3", { ppg: 5, projection: 9, tags: ["buy_low"], tag: "buy_low" }),
    r("00-4", { ppg: 4, projection: 10, tags: ["buy_low"], tag: "buy_low" }),
    r("00-7", {}),
  ];
  assert.deepEqual(tagRows(rows, "sell_high").map((x) => x.gsisId), ["00-2", "00-1"]);
  assert.deepEqual(tagRows(rows, "buy_low").map((x) => x.gsisId), ["00-4", "00-3"]);
});

test("dropped tags: legit leaves the tags, the primary tag and the reason; other rows are untouched", () => {
  const both = "Buy-low: the projection (23.6) is 5.2 points per game above his 18.4 PPG (the cutoff is 3.5). Legit: his PPG ranks No. 8 among RBs, inside the starter threshold (24).";
  const a = dropTags({ tag: "buy_low", tags: ["buy_low", "legit"], tagReason: both });
  assert.deepEqual(a, { tag: "buy_low", tags: ["buy_low"], tagReason: "Buy-low: the projection (23.6) is 5.2 points per game above his 18.4 PPG (the cutoff is 3.5)." });
  assert.deepEqual(dropTags({ tag: "legit", tags: ["legit"], tagReason: "Legit: his PPG ranks No. 10 among QBs." }), { tag: null, tags: [], tagReason: null });
  // a primary legit tag with a shown tag behind it: the shown one becomes the primary
  assert.deepEqual(dropTags({ tag: "legit", tags: ["legit", "sell_high"], tagReason: "Legit: x. Sell-high: y." }), { tag: "sell_high", tags: ["sell_high"], tagReason: "Sell-high: y." });
  const plain = { tag: "sell_high", tags: ["sell_high"], tagReason: "Sell-high: his 20.0 PPG. Legitimate words stay." };
  assert.equal(dropTags(plain), plain);
  assert.equal(reasonWithout("Sell-high: a. Legit: b. Buy-low: c."), "Sell-high: a. Buy-low: c.");
  assert.equal(reasonWithout(null), null);
  assert.ok(Object.hasOwn(DROPPED_TAGS, "legit"));
});

test("the garbage-time toggle: gt=off shows the no-garbage values", () => {
  assert.equal(parseGarbage(undefined), true);
  assert.equal(parseGarbage("off"), false);
  assert.equal(parseGarbage(["off"]), false);
  assert.equal(parseGarbage("on"), true);
  const x = r("00-1", {});
  assert.deepEqual(statsOf(x, true), { ppg: 10, xfp: 9, fpoe: 1 });
  assert.deepEqual(statsOf(x, false), { ppg: 9, xfp: 8, fpoe: 1 });
  assert.equal(regressionHref({ season: 2025, week: 6, withGarbage: false }), "/regression?season=2025&week=6&gt=off");
  assert.equal(regressionHref({ season: 2026, week: 3, kind: "live" }), "/regression?season=2026&week=3&kind=live");
  assert.equal(regressionHref({ anchor: "tag-buy_low" }), "/regression#tag-buy_low");
});

test("signed numbers", () => {
  assert.equal(signed(4.26), "+4.3");
  assert.equal(signed(-0.04), "0.0");
  assert.equal(signed(-1.25, 2), "-1.25");
});

const t = (p: Partial<RegressionTrackRow>): RegressionTrackRow => ({
  section: "tag", weeks: "headline", position: "all", method: null, metric: "legit", rowGroup: "tagged", season: null,
  value: 0.6, lo: 0.58, hi: 0.62, n: 100, nSeasons: 15, perAsof: null, notGraded: null, ...p,
});

test("tag verdicts: above only when the interval clears the base rate", () => {
  assert.equal(tagVerdict(t({ value: 0.924, lo: 0.8765, hi: 0.9643 }), t({ value: 0.6048 })), "above");
  assert.equal(tagVerdict(t({ value: 0.6056, lo: 0.5877, hi: 0.6244 }), t({ value: 0.611 })), "same");
  assert.equal(tagVerdict(t({ value: 0.3, lo: 0.2, hi: 0.4 }), t({ value: 0.5 })), "below");
  const rows = [t({ season: 2020 }), t({ rowGroup: "base", value: 0.61 }), t({})];
  assert.equal(trackRow(rows, { section: "tag", metric: "legit", rowGroup: "tagged" })?.season, null);
  assert.equal(trackRow(rows, { section: "tag", rowGroup: "base" })?.value, 0.61);
  // the dropped tag's sentence reads the same rows (headline, pooled)
  const d = droppedRates([t({ metric: "sell_high" }), t({ lo: 0.5877, hi: 0.6244, value: 0.6056, n: 3195 }), t({ rowGroup: "base", value: 0.611 })]);
  assert.deepEqual(d.map((x) => [x.tag, x.tagged.n, x.base.value, x.verdict]), [["legit", 3195, 0.611, "same"]]);
  assert.deepEqual(droppedRates([t({})]), [], "no base row, no sentence");
});

test("the shrinkage table comes from the frozen parameters", () => {
  const params = {
    x_sell: 4.5,
    shrinkage: {
      rows: [
        { n: 2240, metric: "fpoe", position: "WR", var_noise: 22.12, var_signal: 0.4262, prior_mean: -0.01, first_season: 2009, last_season: 2025 },
        { n: 607, metric: "fpoe", position: "QB", var_noise: 34.9, var_signal: 0.588, prior_mean: -0.78 },
        { metric: "fpoe", position: "TE" },
      ],
    },
  };
  const rows = shrinkRows(params);
  assert.deepEqual(rows.map((x) => x.position), ["QB", "WR"]);
  assert.equal(Math.round(rows[0].halfWeight!), 59);
  assert.equal(reliability(rows[0], 4).toFixed(2), "0.06");
  assert.equal(reliability(rows[1], 8).toFixed(2), "0.13");
  assert.equal(paramNumber(params, "x_sell"), 4.5);
  assert.equal(paramNumber(params, "nope"), null);
  assert.deepEqual(shrinkRows(null), []);
});
