import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { WEEKLY_FIRST_SEASON, chooseList, isGsisId, neighbors, parseInt4, parseKind, parsePosition, waiversHref, type ListKey } from "../../lib/params";

test("query-string parsing falls back instead of failing", () => {
  assert.equal(parsePosition("rb"), "RB");
  assert.equal(parsePosition(["WR", "TE"]), "WR");
  assert.equal(parsePosition("K"), "QB");
  assert.equal(parsePosition(undefined), "QB");
  assert.equal(parseInt4("2026"), 2026);
  assert.equal(parseInt4("0"), null);
  assert.equal(parseInt4("-3"), null);
  assert.equal(parseInt4("3.5"), null);
  assert.equal(parseInt4("12345"), null);
  assert.equal(parseKind("live"), "live");
  assert.equal(parseKind("backtest"), "backtest");
  assert.equal(parseKind("LIVE"), null);
});

test("gsis ids", () => {
  assert.ok(isGsisId("00-0038997"));
  assert.ok(isGsisId("ABC123456"));
  assert.ok(!isGsisId("00-003899"));
  assert.ok(!isGsisId("../etc"));
  assert.ok(!isGsisId("00-0038997 "));
});

// newest first, live before reconstructed (as getListIndex returns them)
const index: ListKey[] = [
  { season: 2026, week: 3, kind: "live" },
  { season: 2026, week: 3, kind: "backtest" },
  { season: 2026, week: 2, kind: "backtest" },
  { season: 2025, week: 16, kind: "backtest" },
  { season: 2025, week: 1, kind: "backtest" },
];

test("chooseList: exact, live first, nearest week, nearest season, newest fallback", () => {
  assert.deepEqual(chooseList(index, null, null, null), { chosen: index[0], exact: true });
  assert.deepEqual(chooseList(index, 2026, 3, null), { chosen: index[0], exact: true });
  assert.deepEqual(chooseList(index, 2026, 3, "backtest"), { chosen: index[1], exact: true });
  assert.deepEqual(chooseList(index, 2026, 2, "live"), { chosen: index[2], exact: false });
  // a week the season does not have: that season's nearest week (16 is 7 away from 9, 1 is 8)
  assert.deepEqual(chooseList(index, 2025, 9, null), { chosen: index[3], exact: false });
  assert.deepEqual(chooseList(index, 2025, 7, null), { chosen: index[4], exact: false });
  // a tie goes to the earlier week (5 and 12 are both 3 away from 8 and 9)
  const tie: ListKey[] = [
    { season: 2025, week: 12, kind: "backtest" },
    { season: 2025, week: 5, kind: "backtest" },
  ];
  assert.deepEqual(chooseList(tie, 2025, 8, null), { chosen: tie[1], exact: false });
  assert.deepEqual(chooseList(tie, 2025, 9, null), { chosen: tie[0], exact: false });
  assert.deepEqual(chooseList(index, 2025, null, null), { chosen: index[3], exact: true });
  // a season asked for with its kind found is what was asked for (no warning)
  assert.deepEqual(chooseList(index, 2025, null, "backtest"), { chosen: index[3], exact: true });
  assert.deepEqual(chooseList(index, 2026, null, "backtest"), { chosen: index[1], exact: true });
  assert.deepEqual(chooseList(index, 2026, null, "live"), { chosen: index[0], exact: true });
  assert.deepEqual(chooseList(index.slice(1), 2026, null, "live"), { chosen: index[1], exact: false });
  // a season without lists: the nearest season's end facing it (earlier -> its first week, later -> its last)
  assert.deepEqual(chooseList(index, 1999, 1, null), { chosen: index[4], exact: false });
  assert.deepEqual(chooseList(index, 2024, null, null), { chosen: index[4], exact: false });
  assert.deepEqual(chooseList(index, 2030, 2, null), { chosen: index[0], exact: false });
  // a week without a season: the newest list
  assert.deepEqual(chooseList(index, null, 2, null), { chosen: index[0], exact: false });
  assert.deepEqual(chooseList([], 2026, 3, null), { chosen: null, exact: false });
});

test("neighbouring weeks skip the second kind of the same week", () => {
  assert.deepEqual(neighbors(index, { season: 2026, week: 3 }), { newer: null, older: { season: 2026, week: 2 } });
  assert.deepEqual(neighbors(index, { season: 2026, week: 2 }), {
    newer: { season: 2026, week: 3 },
    older: { season: 2025, week: 16 },
  });
  assert.deepEqual(neighbors(index, { season: 2025, week: 1 }), { newer: { season: 2025, week: 16 }, older: null });
  assert.deepEqual(neighbors(index, { season: 2000, week: 1 }), { newer: null, older: null });
});

test("waivers links", () => {
  assert.equal(waiversHref({}), "/waivers");
  assert.equal(waiversHref({ pos: "RB", season: 2026, week: 3, kind: "live" }), "/waivers?pos=RB&season=2026&week=3&kind=live");
});

test("the player pages' weekly data starts at settings.yaml's snaps_start", () => {
  const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
  const m = readFileSync(join(repo, "config/settings.yaml"), "utf8").match(/^\s+snaps_start:\s*(\d{4})/m);
  assert.equal(WEEKLY_FIRST_SEASON, Number(m?.[1]));
});
