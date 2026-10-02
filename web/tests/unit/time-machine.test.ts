import assert from "node:assert/strict";
import { test } from "node:test";
import { calendar, chooseWeek, notCovered, weekList, parseWeek, pickerIndex, weekNote, weekTitle, whenName, type Covered } from "../../lib/time-machine";

const ROWS: Covered[] = [
  { module: "board", season: 2025, week: 0, kind: "backtest" },
  { module: "radar", season: 2025, week: 5, kind: "backtest" },
  { module: "hot_seat", season: 2025, week: 5, kind: "backtest" },
  { module: "decisions", season: 2025, week: 5, kind: null },
  { module: "decisions", season: 2025, week: 20, kind: null },
  { module: "radar", season: 2026, week: 3, kind: "live" },
  { module: "radar", season: 2026, week: 3, kind: "backtest" },
  { module: "decisions", season: 2026, week: 3, kind: null },
  { module: "hot_seat", season: 2005, week: 4, kind: "backtest" },
];

test("calendar: the union of every module's weeks, newest first, modules in page order", () => {
  const cal = calendar(ROWS);
  assert.deepEqual(
    cal.map((w) => `${w.season}-${w.week}`),
    ["2026-3", "2025-20", "2025-5", "2025-0", "2005-4"],
  );
  assert.deepEqual(cal[0].kinds, ["live", "backtest"]);
  assert.deepEqual(cal[0].modules, ["radar", "decisions"]);
  assert.deepEqual(cal[2].modules, ["radar", "decisions", "hot_seat"]);
  assert.deepEqual(cal[1].kinds, []);
  // a decisions-only week is one picker row; a week with both kinds is two
  assert.equal(pickerIndex(cal).filter((r) => r.season === 2025 && r.week === 20).length, 1);
  assert.equal(pickerIndex(cal).filter((r) => r.season === 2026 && r.week === 3).length, 2);
});

test("chooseWeek: asked week, else the season's newest, else the newest; week 0 is a week", () => {
  const cal = calendar(ROWS);
  assert.deepEqual(chooseWeek(cal, null, null).chosen && whenName(chooseWeek(cal, null, null).chosen!), "2026 week 3");
  assert.equal(chooseWeek(cal, 2025, 0).chosen?.week, 0);
  assert.equal(chooseWeek(cal, 2025, 0).exact, true);
  const missing = chooseWeek(cal, 2025, 9);
  assert.equal(missing.chosen?.week, 20);
  assert.equal(missing.exact, false);
  assert.equal(chooseWeek(cal, 1999, 1).chosen?.season, 2026);
  assert.deepEqual(chooseWeek([], 2025, 5), { chosen: null, exact: false });
});

test("parseWeek accepts the preseason (0); weekTitle, whenName and weekNote", () => {
  assert.equal(parseWeek("0"), 0);
  assert.equal(parseWeek("17"), 17);
  assert.equal(parseWeek(["5", "6"]), 5);
  for (const bad of ["", "-1", "3.5", "100", "preseason", undefined]) assert.equal(parseWeek(bad), null);
  assert.equal(weekTitle(0), "Preseason");
  assert.equal(whenName({ season: 2024, week: 0 }), "2024 preseason");
  assert.equal(weekNote({ kinds: ["live", "backtest"] }), "live and reconstructed");
  assert.equal(weekNote({ kinds: ["backtest"] }), "reconstructed");
  assert.equal(weekNote({ kinds: [] }), "graded calls only");
});

test("notCovered says why, from the module's own weeks, and links the nearest covered week", () => {
  const hs = [2006, 2007].flatMap((s) => [2, 3, 17].map((week) => ({ season: s, week })));
  assert.match(notCovered("hot_seat", hs, { season: 2005, week: 4 }).reason, /lists start in 2006: there is nothing for 2005/);
  assert.equal(notCovered("hot_seat", hs, { season: 2005, week: 4 }).see, null);
  const w1 = notCovered("hot_seat", hs, { season: 2006, week: 1 });
  assert.match(w1.reason, /nothing for 2006 week 1: in 2006 its lists cover weeks 2, 3 and 17/);
  assert.deepEqual(w1.see, { season: 2006, week: 2 });
  assert.match(notCovered("hot_seat", hs, { season: 2008, week: 3 }).reason, /nothing for 2008 yet \(its newest lists are from 2007\)/);
  const board = notCovered("board", [{ season: 2006, week: 0 }], { season: 2006, week: 7 });
  assert.match(board.reason, /^The Cliff board is made once a season, on the eve of week 1: see the 2006 preseason\.$/);
  assert.deepEqual(board.see, { season: 2006, week: 0 });
  assert.match(notCovered("radar", [{ season: 2006, week: 3 }], { season: 2006, week: 0 }).reason, /nothing for the preseason/);
  assert.match(notCovered("decisions", [], { season: 2006, week: 3 }).reason, /^The Decision Report Card has published nothing yet\.$/);
  assert.match(notCovered("decisions", [{ season: 2006, week: 3 }], { season: 2001, week: 3 }).reason, /graded weeks start in 2006/);
  assert.match(notCovered("radar", [{ season: 2006, week: 3 }, { season: 2008, week: 3 }], { season: 2007, week: 3 }).reason, /published nothing for 2007/);
});

test("weekList names the weeks without implying a gap is covered", () => {
  assert.equal(weekList([3]), "week 3");
  assert.equal(weekList([4, 2, 3, 3]), "weeks 2–4");
  assert.equal(weekList([10, 4, 8, 6]), "weeks 4, 6, 8 and 10");
  assert.equal(weekList([1, 2, 3, 5, 7, 9, 11, 13, 17]), "9 weeks between 1 and 17");
  const rw = [4, 6, 8, 10].map((week) => ({ season: 2024, week }));
  assert.match(notCovered("regression", rw, { season: 2024, week: 5 }).reason, /in 2024 its lists cover weeks 4, 6, 8 and 10\.$/);
});
