import assert from "node:assert/strict";
import { test } from "node:test";
import type { TrackRow as DecisionsTrackRow } from "../../lib/decisions-track";
import type { TrackRow } from "../../lib/queries/track";
import type { StreamTrackRow } from "../../lib/streamer";
import {
  binEdges,
  calMarks,
  liveSummary,
  liveTagSummary,
  parseBin,
  radarCalibration,
  radarSeasons,
  streamCalibration,
  streamSeasons,
  tagCameTrue,
  topNOf,
  wpCalibration,
  wpSeasons,
  type LivePick,
} from "../../lib/track-record";

const radar = (o: Partial<TrackRow>): TrackRow => ({
  model: "logit", label: "y_hit", metric: "p_at_10", scope: "season", scopeValue: "", seasonFrom: 2024, seasonTo: 2024,
  exclRostered: false, value: 0.5, low: null, high: null, nLists: 64, nPositives: 900, nRows: 9000, nTopHits: 320, nTop: 640, ...o,
});
const stream = (o: Partial<StreamTrackRow>): StreamTrackRow => ({
  position: "K", method: "logit", trainOn: "pool", scope: "season", seasons: "2024", key: null, metric: "p_at_5",
  value: 0.4, lo: null, hi: null, nGroups: 16, nRows: 150, nPos: 45, ...o,
});
const dec = (o: Partial<DecisionsTrackRow>): DecisionsTrackRow => ({
  source: "wp_backtest", line: 1, section: "season", scope: "2024", subset: "all plays", method: "own", metric: "log_loss",
  value: 0.47, lo: null, hi: null, n: 38000, nBlocks: 0, ...o,
});

test("topNOf reads precision@N metric names only", () => {
  assert.equal(topNOf("p_at_10"), 10);
  assert.equal(topNOf("p_at_5"), 5);
  assert.equal(topNOf("p_at_10_diff"), null);
  assert.equal(topNOf("base_rate"), null);
});

test("parseBin reads probability groups and rejects anything else", () => {
  assert.deepEqual(parseBin("0.3-0.4"), { lo: 0.3, hi: 0.4 });
  assert.deepEqual(parseBin("0.9-1.0"), { lo: 0.9, hi: 1 });
  assert.equal(parseBin("2006-2025"), null);
  assert.equal(parseBin("0.4-0.3"), null);
  assert.equal(parseBin(null), null);
  assert.equal(parseBin("all"), null);
});

test("radarCalibration pairs mean_pred and observed per bin, sorted, model and label only", () => {
  const rows = [
    radar({ scope: "calibration_fixed", scopeValue: "0.5-0.6", metric: "observed", value: 0.54, nRows: 1547, seasonFrom: 2014, seasonTo: 2025 }),
    radar({ scope: "calibration_fixed", scopeValue: "0.5-0.6", metric: "mean_pred", value: 0.548, nRows: 1547 }),
    radar({ scope: "calibration_fixed", scopeValue: "0.0-0.1", metric: "observed", value: 0.027, nRows: 62869 }),
    radar({ scope: "calibration_fixed", scopeValue: "0.0-0.1", metric: "observed", value: 0.9, model: "lgbm" }),
    radar({ scope: "calibration_fixed", scopeValue: "0.0-0.1", metric: "observed", value: 0.9, label: "y_sustained" }),
  ];
  const pts = radarCalibration(rows, "logit", "y_hit");
  assert.deepEqual(pts.map((p) => [p.key, p.predicted, p.observed, p.n]), [["0.0-0.1", null, 0.027, 62869], ["0.5-0.6", 0.548, 0.54, 1547]]);
});

test("streamCalibration takes the one method with bins, observed rates only", () => {
  const rows = [
    stream({ scope: "calibration", key: "0.1-0.2", metric: "observed", value: 0.244, nRows: 131 }),
    stream({ scope: "calibration", key: "0.0-0.1", metric: "observed", value: 0.045, nRows: 815 }),
    stream({ scope: "calibration", key: "0.0-0.1", position: "DST", value: 0.2 }),
    stream({ scope: "brier", metric: "brier", value: 0.17 }),
  ];
  const k = streamCalibration(rows, "K");
  assert.equal(k.method, "logit");
  assert.deepEqual(k.points.map((p) => [p.key, p.predicted, p.observed, p.n]), [["0.0-0.1", null, 0.045, 815], ["0.1-0.2", null, 0.244, 131]]);
  assert.deepEqual(streamCalibration([], "K"), { method: null, points: [] });
});

test("wpCalibration keeps the observed interval and the method's bins", () => {
  const rows = [
    dec({ section: "reliability", scope: "pooled", subset: "0.9-1.0", metric: "observed", value: 0.9688, lo: 0.9633, hi: 0.9733, n: 110727 }),
    dec({ section: "reliability", scope: "pooled", subset: "0.9-1.0", metric: "mean_predicted", value: 0.9644 }),
    dec({ section: "reliability", scope: "pooled", subset: "0.9-1.0", metric: "observed", method: "nflfastr_wp", value: 0.5 }),
  ];
  assert.deepEqual(wpCalibration(rows, "own"), [
    { key: "0.9-1.0", lo: 0.9, hi: 1, predicted: 0.9644, observed: 0.9688, observedLo: 0.9633, observedHi: 0.9733, n: 110727 },
  ]);
});

test("radarSeasons: newest first, the two models and the base rate per season", () => {
  const rows = [
    radar({ seasonFrom: 2014, seasonTo: 2014, value: 0.455 }),
    radar({ seasonFrom: 2025, seasonTo: 2025, value: 0.531 }),
    radar({ seasonFrom: 2025, seasonTo: 2025, model: "baseline_last_points", value: 0.453 }),
    radar({ seasonFrom: 2025, seasonTo: 2025, model: "base_rate", metric: "base_rate", value: 0.099 }),
    radar({ seasonFrom: 2025, seasonTo: 2025, exclRostered: true, value: 0.9 }),
    radar({ scope: "pooled", seasonFrom: 2014, seasonTo: 2025, value: 0.48 }),
  ];
  const s = radarSeasons(rows, "logit", "baseline_last_points", "y_hit", "p_at_10");
  assert.deepEqual(s.map((r) => [r.season, r.ours?.value, r.other?.value ?? null, r.baseRate?.value ?? null]), [
    [2025, 0.531, 0.453, 0.099],
    [2014, 0.455, null, null],
  ]);
});

test("streamSeasons: one position, newest first", () => {
  const rows = [
    stream({ seasons: "2013", value: 0.393 }),
    stream({ seasons: "2025", value: 0.41 }),
    stream({ seasons: "2025", method: "baseline_last_points", value: 0.38 }),
    stream({ seasons: "2025", position: "DST", value: 0.9 }),
    stream({ scope: "pooled", seasons: "2013-2025", value: 0.39 }),
  ];
  const s = streamSeasons(rows, "K", "logit", "baseline_last_points", "p_at_5");
  assert.deepEqual(s.map((r) => [r.season, r.ours?.value, r.other?.value ?? null]), [["2025", 0.41, 0.38], ["2013", 0.393, null]]);
});

test("wpSeasons: our Brier and log loss, the two differences with their intervals", () => {
  const rows = [
    dec({ scope: "2006", metric: "log_loss", value: 0.48, n: 37027 }),
    dec({ scope: "2025", metric: "brier", value: 0.158 }),
    dec({ scope: "2025", metric: "log_loss", value: 0.4736, n: 38060 }),
    dec({ scope: "2025", method: "own - nflfastr_wp", value: -0.0314, lo: -0.054, hi: -0.009 }),
    dec({ scope: "2025", method: "own - nflfastr_vegas_wp", value: -0.0023, lo: -0.006, hi: 0.0012 }),
    dec({ section: "metrics", scope: "pooled", subset: "2006-2025", value: 0.47 }),
  ];
  const s = wpSeasons(rows);
  assert.equal(s.length, 2);
  assert.deepEqual(s[0], {
    season: "2025", n: 38060, brier: { value: 0.158, lo: null, hi: null }, logLoss: { value: 0.4736, lo: null, hi: null },
    vsWp: { value: -0.0314, lo: -0.054, hi: -0.009 }, vsVegas: { value: -0.0023, lo: -0.006, hi: 0.0012 },
  });
  assert.equal(s[1].season, "2006");
  assert.equal(s[1].brier, null);
});

const pick = (o: Partial<LivePick>): LivePick => ({ season: 2026, week: 3, position: "RB", rank: 1, hit: null, status: "pending", ...o });

test("liveSummary counts the top N only; pending is never a miss; final without a label is not graded", () => {
  const picks = [
    pick({ week: 2, rank: 1, hit: true, status: "final" }),
    pick({ week: 2, rank: 2, hit: false, status: "final" }),
    pick({ week: 2, rank: 3, hit: null, status: "final" }),
    pick({ week: 2, rank: 11, hit: true, status: "final" }),
    pick({ week: 2, position: "WR", rank: 1, hit: true, status: "final" }),
    pick({ week: 3, rank: 1 }),
    pick({ week: 3, position: "QB", rank: 12 }),
  ];
  const s = liveSummary(picks, 10);
  assert.deepEqual(s.weeks, [
    { season: 2026, week: 3, lists: 2, picks: 1, graded: 0, hits: 0, pending: 1, notGraded: 0 },
    { season: 2026, week: 2, lists: 2, picks: 4, graded: 3, hits: 2, pending: 0, notGraded: 1 },
  ]);
  assert.deepEqual(s.total, { lists: 4, picks: 5, graded: 3, hits: 2, pending: 1, notGraded: 1 });
  assert.deepEqual(liveSummary([], 10), { weeks: [], total: { lists: 0, picks: 0, graded: 0, hits: 0, pending: 0, notGraded: 0 } });
});

test("tagCameTrue follows tags.py: sell-high below, buy-low above, unknown without an outcome", () => {
  assert.equal(tagCameTrue("sell_high", 18, 14), true);
  assert.equal(tagCameTrue("sell_high", 18, 18), false);
  assert.equal(tagCameTrue("buy_low", 8, 11), true);
  assert.equal(tagCameTrue("buy_low", 8, 8), false);
  assert.equal(tagCameTrue("buy_low", 8, null), null);
  assert.equal(tagCameTrue("legit", 8, 11), null);
});

test("liveTagSummary counts each tag separately", () => {
  const rows = [
    { season: 2026, week: 3, tags: ["sell_high"], ppg: 18, rosPpg: null, status: "pending" },
    { season: 2025, week: 6, tags: ["buy_low"], ppg: 8, rosPpg: 11, status: "final" },
    { season: 2025, week: 6, tags: ["buy_low"], ppg: 8, rosPpg: null, status: "final" },
  ];
  const s = liveTagSummary(rows, ["sell_high", "buy_low"]);
  assert.deepEqual(s.sell_high.total, { lists: 1, picks: 1, graded: 0, hits: 0, pending: 1, notGraded: 0 });
  assert.deepEqual(s.buy_low.total, { lists: 1, picks: 2, graded: 1, hits: 1, pending: 0, notGraded: 1 });
});

test("binEdges and calMarks: edges from the bins, the middle only when no prediction is published", () => {
  const pts = [
    { key: "0.1-0.2", lo: 0.1, hi: 0.2, predicted: null, observed: 0.244, observedLo: null, observedHi: null, n: 131 },
    { key: "0.0-0.1", lo: 0, hi: 0.1, predicted: 0.03, observed: 0.045, observedLo: null, observedHi: null, n: 815 },
  ];
  assert.deepEqual(binEdges(pts), [0, 0.1, 0.2]);
  const m = calMarks(pts);
  assert.ok(Math.abs(m[0].x - 0.15) < 1e-12);
  assert.equal(m[0].mid, true);
  assert.deepEqual(m[1], { x: 0.03, y: 0.045, n: 815, mid: false, key: "0.0-0.1" });
});
