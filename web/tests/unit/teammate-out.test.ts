// lib/teammate-out.ts (feature #5): the absent starters split from a row, why they are out, the
// grouping by game and team, the share / points wording, the candidates' disclosure, the
// allocation and coverage order and the live record; the inactives note equals the Python one.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  efficiencyText,
  INACTIVES_NOTE,
  absentStarters,
  allocationGroups,
  best,
  chooseWeek,
  coverageRows,
  disclosure,
  gainText,
  gameGroups,
  liveRecord,
  pointsWithRange,
  reasonLabel,
  shareMove,
  teammateOutHref,
  type TOBacktestRow,
  type TORow,
} from "../../lib/teammate-out";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

test("the inactives note is the Python module's, word for word", () => {
  const py = readFileSync(join(repo, "src/twm/modules/teammate_out/weekly.py"), "utf8");
  const m = /NOTE = \(([\s\S]*?)\)  # fmt: skip/.exec(py);
  assert.ok(m, "NOTE not found in weekly.py");
  assert.equal(INACTIVES_NOTE, [...m[1].matchAll(/"([^"]*)"/g)].map((x) => x[1]).join(""));
});

test("the absent starters and why they are out", () => {
  const two = { outIds: "00-0000001,00-0000002", outPlayers: "A One, B Two", outPositions: "WR, TE", outReasons: "roster RES, Doubtful" };
  assert.deepEqual(absentStarters(two), [
    { gsisId: "00-0000001", name: "A One", position: "WR", reason: "roster RES" },
    { gsisId: "00-0000002", name: "B Two", position: "TE", reason: "Doubtful" },
  ]);
  // a name list of another length (a name with ", " in it) is not used
  assert.deepEqual(absentStarters({ ...two, outPlayers: "A, One, B Two" })[0].name, "00-0000001");
  assert.equal(reasonLabel("Out"), "Out (injury report)");
  assert.equal(reasonLabel("roster RES"), "on a reserve list (injured reserve and similar)");
  assert.equal(reasonLabel("roster XYZ"), "roster status XYZ");
  assert.equal(reasonLabel(null), "out");
});

const row = (team: string, name: string, kickoff: string, predPoints: number): TORow => ({
  team, gsisId: `00-${name}`, name, opponent: "KC", gameId: null, kickoff, outIds: "00-9", outPlayers: "Z", outPositions: "RB", outReasons: "Out",
  nOut: 1, vacCarryShare: 0.5, vacTargetShare: 0.1, position: "RB", role: "RB2", baseGames: 3, baseCarryShare: 0.2, baseTargetShare: 0.1,
  basePoints: 6, predCarryShare: 0.4, predTargetShare: 0.12, predPoints, pointsLo: 2, pointsHi: 15, predGain: 2,
});

test("rows by kickoff, then team, then predicted points (highest first)", () => {
  const rows = [row("DAL", "d", "2026-10-11T20:25:00Z", 9), row("BUF", "b", "2026-10-11T17:00:00Z", 4), row("BUF", "a", "2026-10-11T17:00:00.000Z", 8)];
  const g = gameGroups(rows);
  assert.deepEqual(g.map((x) => x.teams.map((t) => [t.team, t.rows.map((r) => r.name)])), [[["BUF", ["a", "b"]]], [["DAL", ["d"]]]]);
  assert.equal(g[0].teams[0].absent[0].name, "Z");
  assert.deepEqual(gameGroups([]), []);
});

test("shares, points and gains in words", () => {
  assert.equal(shareMove(0.2, 0.28), "20% → 28% (+8)");
  assert.equal(shareMove(0.2, 0.15), "20% → 15% (−5)");
  assert.equal(shareMove(0.2, 0.2), "20% → 20% (±0)");
  assert.equal(shareMove(null, 0.2), "–");
  assert.equal(pointsWithRange({ predPoints: 11, pointsLo: 7, pointsHi: 17 }), "11.0 (7.0–17.0)");
  assert.equal(pointsWithRange({ predPoints: 11, pointsLo: null, pointsHi: 17 }), "11.0");
  assert.equal(gainText(2.46), "+2.5");
  assert.equal(gainText(-0.5), "−0.5");
  assert.equal(gainText(0.01), "±0.0");
  assert.equal(teammateOutHref({ season: 2024, week: 10 }), "/teammate-out?season=2024&week=10");
  assert.deepEqual(chooseWeek({ season: null, week: null }, null, [{ season: 2024, week: 10 }]), { season: 2024, week: 10 });
});

const bt = (candidate: string, season: string, maePoints: number, extra: Partial<TOBacktestRow> = {}): TOBacktestRow => ({
  candidate, title: candidate, chosen: false, rulePick: false, season, n: 100, events: 10, maeCarry: 0.04, maeTarget: 0.05, maePoints, topHit: 0.2, ...extra,
});

test("the disclosure: the rule's pick, the one used and the published reason", () => {
  const rows = [
    bt("role", "all", 4.39, { chosen: true, topHit: 0.27 }),
    bt("nothing", "all", 4.36, { rulePick: true }),
    bt("group", "all", 4.44),
    bt("pro_rata", "all", 4.57),
    bt("role", "2016", 4.5, { chosen: true }),
    bt("nothing", "2017", 4.3, { rulePick: true }),
  ];
  const d = disclosure(rows, { chosen_by: "  owner override: who gets the work  " });
  assert.ok(d);
  assert.deepEqual(d.rows.map((r) => r.candidate), ["nothing", "pro_rata", "group", "role"]);
  assert.equal(d.rulePick.candidate, "nothing");
  assert.equal(d.used.candidate, "role");
  assert.equal(d.overridden, true);
  assert.equal(d.why, "owner override: who gets the work");
  assert.deepEqual(d.seasons, ["2016", "2017"]);
  assert.equal(best(d.rows, "maePoints"), 4.36);
  assert.equal(best(d.rows, "topHit"), 0.27);
  // the rule's pick used: nothing to disclose; a missing pick: nothing published
  const same = disclosure([bt("role", "all", 4.39, { chosen: true, rulePick: true })], { chosen_by: "rule" });
  assert.equal(same?.overridden, false);
  assert.equal(same?.why, null);
  assert.equal(disclosure([bt("role", "all", 4.39, { chosen: true })], null), null);
});

test("the allocation by absent position, the coverage order, the live record", () => {
  const a = (outPos: string, role: string, position: string) => ({ outPos, role, position, n: 10, carry: 0.1, target: 0.2, nGroup: 50 });
  const groups = allocationGroups([a("WR", "WR+", "WR"), a("RB", "TE1", "TE"), a("RB", "RB+", "RB"), a("RB", "RB3", "RB"), a("RB", "RB2", "RB"), a("WR", "WR2", "WR")]);
  assert.deepEqual(groups.map((g) => [g.outPos, g.rows.map((r) => r.role)]), [["RB", ["RB2", "RB3", "RB+", "TE1"]], ["WR", ["WR2", "WR+"]]]);
  const c = (position: string) => ({ position, seasons: "2017-2025", n: 10, below: 1, above: 1, inside: 8, coverage: 0.8 });
  assert.deepEqual(coverageRows([c("WR"), c("all"), c("TE"), c("RB")]).map((r) => r.position), ["all", "RB", "WR", "TE"]);
  const live = (season: number) => ({ season, n: 3, pending: 0, starterPlayed: 0, didNotPlay: 0, weeks: 1, teamWeeks: 1, ranged: 3, maePoints: 1, maePointsBase: 2, maeCarryShare: null, maeCarryShareBase: null, maeTargetShare: null, maeTargetShareBase: null, coverage: 1, topHit: 1 });
  assert.equal(liveRecord([live(2026), live(2025)], 2025)?.season, 2025);
  assert.equal(liveRecord([live(2026)], 2024), null);
  assert.equal(liveRecord([live(2026)], null)?.season, 2026);
});

test("the efficiency clause explains the rest of the gap", () => {
  assert.equal(efficiencyText({ predPoints: 17.4, basePoints: 22.6, predGain: -1.3 }), "; his points per carry or target pulled toward the position average: −3.9");
  assert.equal(efficiencyText({ predPoints: 11.2, basePoints: 2.8, predGain: 8.6 }), "");
  assert.equal(efficiencyText({ predPoints: 5, basePoints: null, predGain: 1 }), "");
});
