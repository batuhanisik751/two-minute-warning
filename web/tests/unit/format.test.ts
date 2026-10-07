import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  chancePct,
  fmtInt,
  fmtNum,
  fmtPoints,
  fmtShare,
  fmtUtc,
  fmtUtcDate,
  kindLabel,
  MINUS,
  outcomeLabel,
  outcomeOf,
  pct,
  pctRange,
  points,
  pointsRange,
  seasonWeek,
  signedNum,
  TIER_CUTOFFS,
  tierLabel,
  wholePct,
  windowSummary,
} from "../../lib/format";

test("UTC dates and times are fixed-format, whatever the machine's zone", () => {
  assert.equal(fmtUtc("2026-09-22T14:00:00+00:00"), "Tue 22 Sep 2026, 14:00 UTC");
  assert.equal(fmtUtc("2026-09-28T19:46:01.531423+00:00"), "Mon 28 Sep 2026, 19:46 UTC");
  // a time given in another zone is shown in UTC
  assert.equal(fmtUtc("2026-09-22T10:00:00-04:00"), "Tue 22 Sep 2026, 14:00 UTC");
  assert.equal(fmtUtcDate("2026-01-04T23:59:00Z"), "Sun 4 Jan 2026");
  assert.equal(fmtUtc("not a date"), "unknown time");
});

test("rates, ranges and differences", () => {
  assert.equal(pct(0.5601), "56%");
  assert.equal(pct(0.478279, 1), "47.8%");
  assert.equal(pct(0), "0%");
  assert.equal(pctRange(0.5263, 0.5933), "53–59%");
  assert.equal(pctRange(0.462222, 0.494519, 1), "46.2–49.5%");
  assert.equal(points(0.074454), "+7.4 points");
  assert.equal(points(-0.0035), "\u22120.4 points");
  assert.equal(points(0.0002), "0.0 points");
  assert.equal(pointsRange(0.062427, 0.08675), "+6.2 to +8.7");
  assert.equal(pointsRange(-0.003176, 0.008424), "\u22120.3 to +0.8");
});

test("negative numbers use a real minus sign (U+2212), and nothing rounds to a negative zero", () => {
  const M = "\u2212";
  assert.equal(MINUS, M);
  assert.equal(pct(-0.0546), `${M}5%`);
  assert.equal(pct(-0.0004), "0%");
  assert.equal(pct(-0.00004, 1), "0.0%");
  assert.equal(pct(-0), "0%");
  assert.equal(pctRange(-0.031, 0.052), `${M}3–5%`);
  assert.equal(pctRange(-0.004, 0.052), "0–5%");
  assert.equal(points(-0.0004), "0.0 points");
  assert.equal(pointsRange(-0.0004, 0.0004), "0.0 to 0.0");
  assert.equal(fmtPoints(-1.64), `${M}1.6`);
  assert.equal(fmtPoints(-0.04), "0.0");
  assert.equal(fmtPoints(-0), "0.0");
  assert.equal(fmtShare(-0.0546), `${M}5%`);
  assert.equal(fmtShare(-0.004), "0%");
  assert.equal(fmtNum(-2.345, 2), `${M}2.35`);
  assert.equal(fmtNum(-0.004, 2), "0.00");
  assert.equal(fmtNum(3.14159, 0), "3");
  assert.equal(signedNum(1.64), "+1.6");
  assert.equal(signedNum(-1.64), `${M}1.6`);
  assert.equal(signedNum(0.04), "0.0");
  assert.equal(signedNum(-0.04), "0.0");
  for (const s of [pct(-0.001), fmtPoints(-0.01), fmtShare(-0.001), points(-0.0001), signedNum(-0.01)]) {
    assert.ok(!s.includes("-"), s);
  }
});

test("counts, points, shares, weeks", () => {
  assert.equal(fmtInt(91638), "91,638");
  assert.equal(fmtInt(7), "7");
  assert.equal(fmtPoints(14.2), "14.2");
  assert.equal(fmtPoints(6), "6.0");
  assert.equal(fmtShare(0.83), "83%");
  assert.equal(fmtShare(0.1795), "18%");
  assert.equal(seasonWeek(2026, 3), "2026 week 3");
});

test("tiers, kinds and outcomes", () => {
  assert.equal(tierLabel("must-add"), "Must-add");
  assert.equal(tierLabel("speculative"), "Speculative");
  assert.equal(tierLabel("watch"), "Watch");
  assert.equal(tierLabel(null), "Not available");
  assert.equal(kindLabel("live").short, "Live");
  assert.equal(kindLabel("backtest").short, "Reconstructed");
  // no weekday: the "made on" time beside it can be a Wednesday in UTC
  assert.doesNotMatch(kindLabel("live").long, /day/);
  assert.match(kindLabel("live").long, /before the games/);
  assert.equal(outcomeOf(true, "final"), "hit");
  assert.equal(outcomeOf(false, "final"), "no-hit");
  // a pending label is never a miss, even with a stale boolean
  assert.equal(outcomeOf(null, "pending"), "pending");
  assert.equal(outcomeOf(false, "pending"), "pending");
  assert.equal(outcomeOf(null, null), "unknown");
  assert.equal(outcomeOf(null, "final"), "unknown");
  assert.equal(outcomeLabel("no-hit"), "No hit");
});

test("window summaries follow radar_outcome's conventions", () => {
  // rank NULL + points NULL = did not play; rank NULL + points 0 = snaps without a stat line
  assert.equal(windowSummary([6, 7, 8], [7, null, null], [24.7, null, 0], "WR"), "W6 WR7, W7 did not play, W8 no stat line");
  assert.equal(windowSummary([], [], [], "RB"), null);
  assert.equal(windowSummary(null, null, null, "RB"), null);
});

test("a chance never rounds up across a priority cutoff", () => {
  // 0.4981 is speculative and 0.2481 watch: "50%" / "25%" would contradict the priority beside them
  assert.equal(chancePct(0.4981), "49.8%");
  assert.equal(chancePct(0.2481), "24.8%");
  assert.equal(chancePct(0.49996), "49.9%");
  assert.equal(chancePct(0.5), "50%");
  assert.equal(chancePct(0.5049), "50%");
  assert.equal(chancePct(0.4949), "49%");
  assert.equal(chancePct(0.2449), "24%");
  assert.equal(chancePct(0.7312), "73%");
});

test("the priority cutoffs are confidence.py's MUST_ADD and SPECULATIVE", () => {
  const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
  const src = readFileSync(join(repo, "src/twm/modules/waiver_radar/confidence.py"), "utf8");
  const num = (name: string) => Number(src.match(new RegExp(`^${name}\\s*=\\s*([0-9.]+)`, "m"))?.[1]);
  assert.deepEqual([...TIER_CUTOFFS], [num("SPECULATIVE"), num("MUST_ADD")]);
});

test("a field-goal or conversion chance never reads as certain", () => {
  assert.equal(wholePct(0.9962), ">99%");
  assert.equal(wholePct(0.995), ">99%");
  assert.equal(wholePct(1), "100%");
  assert.equal(wholePct(0.004), "<1%");
  assert.equal(wholePct(0), "0%");
  assert.equal(wholePct(0.873), "87%");
});
