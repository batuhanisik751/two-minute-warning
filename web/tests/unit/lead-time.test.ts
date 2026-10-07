// lib/lead-time.ts (feature #8): the fixed rules mirror the Python study, the shares and leads read
// as published ("–" when suppressed), the histogram's bins (zeros filled, "never" from the
// summary), the seasons behind the pooled numbers and the reverse / head-to-head lookups.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { HIST_HI, HIST_LO, MOMENTUM_POINTS, PRIMARY, SECONDARY, h2hOf, histBins, leadLabel, pooled, reverseOf, seasonsOf, share, verdict, weeks, type LTCoverageRow, type LTSummaryRow } from "../../lib/lead-time";
import { LEAD_TIME_LICENSE, LEAD_TIME_SOURCE } from "../../lib/third-party";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const py = (f: string) => readFileSync(join(root, "src/twm/modules/lead_time", f), "utf8");

test("the fixed rules are the Python study's", () => {
  const init = py("__init__.py");
  assert.match(init, new RegExp(`^THRESHOLDS = \\(${PRIMARY}\\.0, ${SECONDARY}\\.0\\)`, "m"));
  assert.match(init, new RegExp(`^PRIMARY = ${PRIMARY}\\.0$`, "m"));
  assert.match(init, new RegExp(`^MOMENTUM_POINTS = ${MOMENTUM_POINTS}\\.0`, "m"));
  assert.match(py("study.py"), new RegExp(`def lead_hist\\(leads: pl.DataFrame, lo: int = ${HIST_LO}, hi: int = ${HIST_HI}\\)`));
});

test("shares and leads read as published, suppressed cells as a dash", () => {
  assert.equal(share(0.6124), "61.2%");
  assert.equal(share(null), "–");
  assert.deepEqual([weeks(3), weeks(6.25), weeks(-2), weeks(null)], ["3", "6.25", "−2", "–"]);
  assert.deepEqual([leadLabel(3), leadLabel(1), leadLabel(0), leadLabel(-1), leadLabel(10), leadLabel(-6)], ["3 weeks before", "1 week before", "same week", "1 week after", "10+ weeks before", "6+ weeks after"]);
});

const row = (signal: string, threshold: number, nNever: number, scope = "complete"): LTSummaryRow => ({
  threshold, signal, scope, scopeValue: scope === "complete" ? "" : "2020", nAdds: 10, nBefore: 5, nSame: 1, nAfter: 10 - 6 - nNever, nNever, nNeverOutOfPool: 0,
  shareBefore: 0.5, shareSame: 0.1, shareAfter: null, shareNever: null, leadMedian: 2, leadQ1: 1, leadQ3: 4, nearestMedian: 0, shareBefore4: 0.4,
});

test("pooled rows are the complete seasons at one threshold, in the signals' order", () => {
  const rows = [row("momentum", 50, 1), row("listed", 50, 2), row("listed", 25, 1), row("listed", 50, 0, "season")];
  assert.deepEqual(pooled(rows).map((r) => r.signal), ["listed", "momentum"]);
  assert.deepEqual(pooled(rows, 25).map((r) => r.signal), ["listed"]);
});

test("the verdict: who flagged more crowd adds before the crowd, at both thresholds (G4.11)", () => {
  const at = (signal: string, threshold: number, shareBefore: number) => ({ ...row(signal, threshold, 0), shareBefore });
  const rows = [at("listed", 50, 0.6124), at("momentum", 50, 0.6744), at("listed", 25, 0.5614), at("momentum", 25, 0.3099)];
  assert.equal(
    verdict(rows),
    "In short: at the 50% mark the momentum baseline flagged more crowd adds before the crowd (67.4% vs 61.2%); at the 25% mark the Radar's lists flagged more crowd adds before the crowd (56.1% vs 31.0%).",
  );
  assert.match(verdict([at("listed", 50, 0.5), at("momentum", 50, 0.5), at("listed", 25, 0.5), at("momentum", 25, 0.4)])!, /^In short: at the 50% mark the Radar's lists and the momentum baseline flagged the same share before the crowd \(50\.0%\);/);
  assert.equal(verdict(rows.slice(0, 3)), null, "a threshold's pair missing: no verdict");
});

test("the histogram fills empty leads with zeros and ends with the never bin", () => {
  const bins = histBins([{ threshold: 50, signal: "listed", lead: 3, n: 4 }, { threshold: 25, signal: "listed", lead: 3, n: 9 }], [row("listed", 50, 2)], ["listed", "momentum"]);
  assert.equal(bins.length, HIST_HI - HIST_LO + 2);
  assert.deepEqual(bins.find((b) => b.lead === 3)!.counts, { listed: 4, momentum: 0 });
  assert.deepEqual(bins[bins.length - 1], { lead: null, label: "never flagged", counts: { listed: 2, momentum: 0 } });
});

test("the seasons behind the pooled numbers, the partial and the excluded ones", () => {
  const c = (season: number, complete: boolean, inStudy: boolean): LTCoverageRow => ({ season, baselinePeriod: complete ? 0 : 5, complete, inSeasonDays: inStudy ? 15 : 0, lastListWeek: 16, adds50: 1, adds25: 1, inStudy });
  const s = seasonsOf([c(2020, false, true), c(2021, true, true), c(2022, true, true), c(2024, false, false)]);
  assert.equal(s.span, "2021–2022");
  assert.deepEqual([s.partial.map((x) => x.season), s.excluded.map((x) => x.season)], [[2020], [2024]]);
  assert.equal(seasonsOf([]).span, null);
});

test("the reverse view and the head to head by kind, complete seasons only", () => {
  const rev = [{ threshold: 50, signal: "must_add", scope: "complete", scopeValue: "", state: "never", n: 64, hits: 24, hitRate: 0.375, leadMedian: null }, { threshold: 50, signal: "must_add", scope: "season", scopeValue: "2021", state: "never", n: 20, hits: 7, hitRate: 0.35, leadMedian: null }];
  assert.equal(reverseOf(rev, "must_add").never?.n, 64);
  assert.equal(reverseOf(rev, "must_add").already, undefined);
  const h = [{ threshold: 50, level: "listed", h2h: "both", n: 60, nRadarEarlier: 50, share: 0.47 }, { threshold: 25, level: "listed", h2h: "both", n: 35, nRadarEarlier: 28, share: 0.2 }];
  assert.equal(h2hOf(h, "listed").both?.nRadarEarlier, 50);
});

test("the section credits FantasyPros and says why only aggregates are shown", () => {
  assert.match(LEAD_TIME_SOURCE, /FantasyPros/);
  assert.match(LEAD_TIME_LICENSE, /no player/);
});
