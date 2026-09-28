import assert from "node:assert/strict";
import { test } from "node:test";
import { chooseList, isGsisId, neighbors, parseInt4, parseKind, parsePosition, waiversHref, type ListKey } from "../../lib/params";

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

test("chooseList: exact, live first, season fallback, newest fallback", () => {
  assert.deepEqual(chooseList(index, null, null, null), { chosen: index[0], exact: true });
  assert.deepEqual(chooseList(index, 2026, 3, null), { chosen: index[0], exact: true });
  assert.deepEqual(chooseList(index, 2026, 3, "backtest"), { chosen: index[1], exact: true });
  assert.deepEqual(chooseList(index, 2026, 2, "live"), { chosen: index[2], exact: false });
  // a week the season does not have: that season's newest week
  assert.deepEqual(chooseList(index, 2025, 9, null), { chosen: index[3], exact: false });
  assert.deepEqual(chooseList(index, 2025, null, null), { chosen: index[3], exact: true });
  // a season that does not exist: the newest list
  assert.deepEqual(chooseList(index, 1999, 1, null), { chosen: index[0], exact: false });
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
