// lib/questionable.ts (feature #1): choosing the week, ordering and wording the rows, the
// tables' helpers and the empty states; the inactives note equals the Python module's.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  INACTIVES_NOTE,
  WHEN_LISTS_FILL,
  backtestScore,
  chancePct,
  bucketRange,
  calibrationPoints,
  chooseWeek,
  historyRows,
  ifPlaysText,
  kickoffGroups,
  liveRecord,
  opponentText,
  practiceLabel,
  questionableHref,
  sortRows,
  type QBacktestRow,
  type QHistoryRow,
} from "../../lib/questionable";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

test("the inactives note is the Python module's, word for word", () => {
  const py = readFileSync(join(repo, "src/twm/modules/questionable/weekly.py"), "utf8");
  const m = /INACTIVES_NOTE = \(([\s\S]*?)\)  # fmt: skip/.exec(py);
  assert.ok(m, "INACTIVES_NOTE not found in weekly.py");
  const text = [...m[1].matchAll(/"([^"]*)"/g)].map((x) => x[1]).join("");
  assert.equal(INACTIVES_NOTE, text);
  assert.match(WHEN_LISTS_FILL, /Friday for Sunday games/);
});

test("chooseWeek: the asked week, else the week whose games are next, else the newest list", () => {
  const index = [{ season: 2026, week: 4 }, { season: 2026, week: 3 }];
  assert.deepEqual(chooseWeek({ season: 2026, week: 3 }, { season: 2026, week: 5 }, index), { season: 2026, week: 3 });
  assert.deepEqual(chooseWeek({ season: 2026, week: null }, { season: 2026, week: 5 }, index), { season: 2026, week: 5 });
  assert.deepEqual(chooseWeek({ season: null, week: null }, null, index), { season: 2026, week: 4 });
  assert.equal(chooseWeek({ season: null, week: null }, null, []), null);
  assert.equal(questionableHref({ season: 2026, week: 5 }), "/questionable?season=2026&week=5");
  assert.equal(questionableHref(), "/questionable");
});

test("rows by kickoff, then chance (highest first), grouped per kickoff", () => {
  const r = (name: string, kickoff: string, playChance: number) => ({ name, kickoff, playChance });
  const rows = [r("C", "2026-10-04T20:25:00Z", 0.7), r("A", "2026-10-04T17:00:00Z", 0.4), r("B", "2026-10-04T17:00:00.000Z", 0.9)];
  assert.deepEqual(sortRows(rows).map((x) => x.name), ["B", "A", "C"]);
  const g = kickoffGroups(rows);
  assert.deepEqual(g.map((x) => x.rows.map((y) => y.name)), [["B", "A"], ["C"]]);
  assert.deepEqual(kickoffGroups([]), []);
});

test("the opponent from the game id, the practice words, the 'if he plays' line", () => {
  assert.equal(opponentText("BAL", "CIN", "2024_05_BAL_CIN"), "at CIN");
  assert.equal(opponentText("CIN", "BAL", "2024_05_BAL_CIN"), "vs BAL");
  assert.equal(opponentText("CIN", "BAL", null), "vs BAL");
  assert.equal(opponentText("CIN", null, "2024_05_BAL_CIN"), null);
  assert.equal(practiceLabel("dnp"), "Did not practice");
  assert.equal(practiceLabel("odd"), "odd");
  assert.equal(ifPlaysText({ playsMedian: 0.796731, healthyMedian: 0.849242 }), "If he plays: usually 80% of his normal points (healthy players with similar averages: 85%)");
  assert.equal(ifPlaysText({ playsMedian: 0.72, healthyMedian: null }), "If he plays: usually 72% of his normal points");
  assert.equal(ifPlaysText({ playsMedian: null, healthyMedian: 0.85 }), null);
});

test("history rows: by tag, the total first, then by practice", () => {
  const h = (reportStatus: string, practice: string): QHistoryRow => ({ reportStatus, practice, seasons: "2016-2025", n: 10, played: 5, playedRate: 0.5 });
  const got = historyRows([h("Out", "dnp"), h("Questionable", "none"), h("Doubtful", "all"), h("Questionable", "all"), h("Questionable", "full")]);
  assert.deepEqual(got.map((r) => `${r.reportStatus}/${r.practice}`), ["Questionable/all", "Questionable/full", "Questionable/none", "Doubtful/all", "Out/dnp"]);
});

test("the backtest score: the chosen grouping against the baseline, pooled", () => {
  const b = (grouping: string, season: string, chosen: boolean, logLoss: number): QBacktestRow => ({ grouping, title: grouping, chosen, season, n: 100, logLoss, brier: logLoss / 3, meanP: 0.6, playedRate: 0.6 });
  const rows = [b("status", "2018", false, 0.63), b("status", "all", false, 0.6022), b("missed_prev+position", "2019", true, 0.6), b("missed_prev+position", "all", true, 0.5812), b("practice", "all", false, 0.58)];
  const s = backtestScore(rows)!;
  assert.equal(s.chosen.logLoss, 0.5812);
  assert.equal(s.baseline.logLoss, 0.6022);
  assert.deepEqual(s.seasons, ["2018", "2019"]);
  assert.equal(backtestScore(rows.filter((r) => r.grouping !== "status")), null);
  assert.equal(backtestScore([]), null);
});

test("calibration buckets: ranges, and only buckets with players are plotted", () => {
  assert.deepEqual(bucketRange("<30%"), { lo: 0, hi: 0.3 });
  assert.deepEqual(bucketRange("30-50%"), { lo: 0.3, hi: 0.5 });
  assert.deepEqual(bucketRange("85%+"), { lo: 0.85, hi: 1 });
  assert.equal(bucketRange("high"), null);
  const pts = calibrationPoints([
    { line: 2, bucket: "30-50%", n: 552, predicted: 0.452471, actual: 0.423913 },
    { line: 1, bucket: "<30%", n: 408, predicted: 0.014606, actual: 0.012255 },
    { line: 5, bucket: "85%+", n: 0, predicted: null, actual: null },
  ]);
  assert.deepEqual(pts.map((p) => [p.key, p.n, p.observed]), [["<30%", 408, 0.012255], ["30-50%", 552, 0.423913]]);
});

test("the live record: 'all' and each tag of the season; null when nothing is published", () => {
  const l = (reportStatus: string, n: number, season = 2026) => ({ season, reportStatus, n, predicted: n ? 0.6 : null, actual: n ? 0.5 : null, pending: reportStatus === "all" ? 3 : null, weeks: reportStatus === "all" ? 2 : null });
  const rec = liveRecord([l("Doubtful", 1), l("all", 5), l("Questionable", 4), l("all", 9, 2027)], 2026)!;
  assert.equal(rec.all.n, 5);
  assert.deepEqual(rec.byStatus.map((r) => r.reportStatus), ["Questionable", "Doubtful"]);
  assert.equal(liveRecord([], 2026), null);
  assert.equal(liveRecord([l("all", 0)], 2026)!.all.pending, 3);
});

test("a play chance never reads as certain", () => {
  assert.equal(chancePct(0.004), "under 1%");
  assert.equal(chancePct(0.0), "under 1%");
  assert.equal(chancePct(0.016), "2%");
  assert.equal(chancePct(0.71), "71%");
  assert.equal(chancePct(0.996), "over 99%");
});
