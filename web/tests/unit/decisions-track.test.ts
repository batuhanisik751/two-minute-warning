// The methodology section's picks from decisions_track_record (lib/decisions-track.ts).
import assert from "node:assert/strict";
import { test } from "node:test";
import { nfl4th, smoothness, submodelLines, verdict, widestSpan, wpComparison, type TrackRow } from "../../lib/decisions-track";

let line = 0;
const row = (source: string, section: string, scope: string | null, subset: string | null, method: string | null, metric: string, value: number | null, n: number | null = 100): TrackRow => ({
  source, line: ++line, section, scope, subset, method, metric, value, lo: value === null ? null : value - 0.01, hi: value === null ? null : value + 0.01, n, nBlocks: 3,
});

const ROWS: TrackRow[] = [
  row("wp_backtest", "metrics", "pooled", "2006-2025", "own", "brier", 0.15, 1000),
  row("wp_backtest", "metrics", "pooled", "2006-2025", "own", "log_loss", 0.45, 1000),
  row("wp_backtest", "metrics", "pooled", "2006-2025", "nflfastr_wp", "brier", 0.16, 1000),
  row("wp_backtest", "metrics", "era", "2006-2014", "own", "brier", 0.99, 500),
  row("wp_backtest", "difference", "pooled", "2006-2025", "own - nflfastr_wp", "brier", -0.01, 1000),
  row("wp_backtest", "smoothness", "limit", "-", "threshold", "score_step_h1", 6, 0),
  row("wp_backtest", "smoothness", "fold", "2006", "own", "score_step_h1", 3.5, 0),
  row("wp_backtest", "smoothness", "fold", "2025", "own", "score_step_h1", 4.1, 0),
  row("wp_backtest", "smoothness", "fold", "2026", "g1_before", "score_step_h1", 19, 0),
  row("wp_backtest", "smoothness", "reference", "2006-2025", "nflfastr_vegas_wp", "score_step_h1", 3.8, 0),
  row("wp_backtest", "smoothness", "validation", "2004-2005", "spline_sym", "score_step_h1", 3.1, 0),
  row("submodels", "conversion", "down 4", "2006-2025", "model", "log_loss", 0.64),
  row("submodels", "conversion", "down 4", "2006-2025", "lookup", "log_loss", 0.65),
  row("submodels", "conversion", "down 4", "2006-2025", "model - lookup", "log_loss", -0.006),
  row("submodels", "conversion", "down 4", "2023-2025", "model", "log_loss", 0.5),
  row("nfl4th_benchmark", "agreement", "all", "2024", "nfl4th", "agree_rate", 0.79, 3894),
  row("nfl4th_benchmark", "agreement", "all", "all", "nfl4th", "agree_rate", 0.8, 7783),
  row("nfl4th_benchmark", "agreement", "clear", "all", "nfl4th", "agree_rate", 0.94, 3405),
  row("nfl4th_benchmark", "go_rate", "all", "all", "ours", "go_rate", 0.43, 7783),
  row("nfl4th_benchmark", "go_rate", "all", "all", "real", "go_rate", 0.21, 7783),
];

test("the widest span is the pooled one; non-spans are ignored", () => {
  assert.equal(widestSpan(["2006-2014", "2006-2025", "2015-2022", "all", null]), "2006-2025");
  assert.equal(widestSpan(["all", "-"]), null);
});

test("the WP comparison: pooled rows only, every method with a number, the differences", () => {
  const c = wpComparison(ROWS)!;
  assert.equal(c.span, "2006-2025");
  assert.equal(c.n, 1000);
  assert.deepEqual(c.methods.map((m) => m.method), ["own", "nflfastr_wp"]);
  assert.equal(c.methods[0].brier?.value, 0.15);
  assert.equal(c.diffs[0].brier?.value, -0.01);
  assert.equal(c.diffs[1].brier, null);
  assert.equal(wpComparison([]), null);
});

test("smoothness: the limit, the worst test fold of ours and of G1 before, nflfastR's reference", () => {
  const s = smoothness(ROWS);
  assert.equal(s.folds, "2006-2025");
  assert.equal(s.validation, "2004-2005");
  assert.deepEqual(s.metrics, [{ metric: "score_step_h1", limit: 6, ours: 4.1, before: 19, vegas: 3.8 }]);
});

test("sub-models: the pooled metric, its baseline and the difference; a missing model is null", () => {
  const [conv, fg] = submodelLines(ROWS);
  assert.equal(conv.span, "2006-2025");
  assert.equal(conv.model?.value, 0.64);
  assert.equal(conv.base?.value, 0.65);
  assert.equal(conv.diff?.value, -0.006);
  assert.equal(fg.span, null);
  assert.equal(fg.model, null);
});

test("nfl4th: agreement overall, on clear calls, by season; go rates", () => {
  const b = nfl4th(ROWS)!;
  assert.deepEqual(b.seasons, ["2024"]);
  assert.equal(b.agreement.all?.value, 0.8);
  assert.equal(b.agreement.clear?.n, 3405);
  assert.equal(b.agreement.tossUp, null);
  assert.equal(b.agreement.bySeason[0].all?.value, 0.79);
  assert.equal(b.goRate.ours?.value, 0.43);
  assert.equal(b.goRate.nfl4th, null);
  assert.equal(nfl4th([]), null);
});

test("a sub-model's verdict comes from its difference's interval and the metric's direction", () => {
  const d = (lo: number, hi: number) => ({ value: (lo + hi) / 2, lo, hi, n: 1 });
  assert.equal(verdict({ metric: "log_loss", diff: d(-0.0078, -0.0043) }), "better");
  assert.equal(verdict({ metric: "log_loss", diff: d(-0.001, 0.002) }), "no clear difference");
  assert.equal(verdict({ metric: "log_loss", diff: d(0.001, 0.002) }), "worse");
  assert.equal(verdict({ metric: "log_score", diff: d(0.04, 0.05) }), "better");
  assert.equal(verdict({ metric: "log_score", diff: d(-0.05, -0.04) }), "worse");
  assert.equal(verdict({ metric: "log_loss", diff: null }), null);
});
