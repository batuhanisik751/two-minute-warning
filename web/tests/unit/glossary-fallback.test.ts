import assert from "node:assert/strict";
import { test } from "node:test";
import { GLOSSARY_FALLBACK, resolveTerm, type TermEntry } from "../../lib/glossary-fallback";

// the terms that were web/lib/site-terms.ts until T1 (registry entries since) and the metrics the
// pages show (ROC-AUC, PR-AUC, Brier score, log loss, MAE, PPG, G)
const MOVED = [
  "chance", "model_probability", "priority", "list_kind", "precision_at_10", "rank_bucket_hit_rate", "interval",
  "calibration", "walk_forward", "flex", "listed_position", "stream_chance", "stream_pool", "garbage_time_view",
  "current_franchise", "player_season", "stability_interval", "decisions_graded", "clear_call", "toss_up",
  "wrong_call", "clock_case", "against_convention", "hot_seat_estimate", "hot_seat_let_go", "hot_seat_driver",
  "board_cliff_chance", "board_missed_chance", "board_ecr", "board_kind", "board_disagree", "wp_points",
];
const METRICS = ["roc_auc", "pr_auc", "brier", "log_loss", "mae", "ppg", "games"];

const row = (name: string): TermEntry => ({ name, title: `Published ${name}`, explanation: `Published text of ${name}.`, formula: "f" });

test("the generated fallback holds every site term and metric, with real text", () => {
  for (const n of [...MOVED, ...METRICS]) {
    const t = GLOSSARY_FALLBACK[n];
    assert.ok(t, n);
    assert.ok(t.title && t.explanation.length > 40 && t.formula, n);
  }
  assert.equal(GLOSSARY_FALLBACK.toss_up.title, "Toss-up");
  assert.match(GLOSSARY_FALLBACK.brier.explanation, /Lower is better/);
  assert.ok(Object.keys(GLOSSARY_FALLBACK).length > 300, "every registry entry");
});

test("the published table wins; the fallback fills the gaps; dropped tags and unknown names are null", () => {
  assert.deepEqual(resolveTerm([row("chance")], "chance"), row("chance"));
  const f = resolveTerm([], "chance");
  assert.equal(f?.name, "chance");
  assert.equal(f?.title, "Chance");
  assert.equal(f?.explanation, GLOSSARY_FALLBACK.chance.explanation);
  assert.equal(resolveTerm([row("legit")], "legit"), null, "a dropped tag is never a term");
  assert.equal(resolveTerm([], "legit"), null);
  assert.equal(resolveTerm([], "no_such_term"), null);
  assert.equal(resolveTerm([], "constructor"), null, "only the file's own keys");
  assert.equal(resolveTerm([row("x")], "x", {})?.title, "Published x");
});
