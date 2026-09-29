import assert from "node:assert/strict";
import { test } from "node:test";
import { parsePosition } from "../../lib/params";
import { FLEX, positionLabel, sortPositions, tabPositions } from "../../lib/positions";

test("tabs: the published positions in order, FLEX after TE", () => {
  assert.deepEqual(tabPositions(["TE", "QB", "WR", "RB"]), ["QB", "RB", "WR", "TE", FLEX]);
  // K and D/ST appear once published, after FLEX; never before
  assert.deepEqual(tabPositions(["DST", "K", "QB", "RB", "WR", "TE"]), ["QB", "RB", "WR", "TE", FLEX, "K", "DST"]);
  // no FLEX without a FLEX position; FLEX with only one of them
  assert.deepEqual(tabPositions(["QB"]), ["QB"]);
  assert.deepEqual(tabPositions(["QB", "TE"]), ["QB", "TE", FLEX]);
  assert.deepEqual(tabPositions([]), []);
  // an unknown position goes last; duplicates and a stored "FLEX" do not repeat
  assert.deepEqual(tabPositions(["ZZ", "RB", "RB", "FLEX"]), ["RB", FLEX, "ZZ"]);
  assert.deepEqual(sortPositions(["K", "D/ST", "QB"]), ["QB", "K", "D/ST"]);
});

test("labels", () => {
  assert.equal(positionLabel("RB"), "Running backs");
  assert.equal(positionLabel(FLEX), "FLEX (RB, WR, TE)");
  assert.equal(positionLabel("XX"), "XX");
});

test("parsePosition accepts the tabs the page shows", () => {
  const tabs = tabPositions(["QB", "RB", "WR", "TE", "K"]);
  assert.equal(parsePosition("flex", tabs), FLEX);
  assert.equal(parsePosition("k", tabs), "K");
  assert.equal(parsePosition("DST", tabs), "QB");
  assert.equal(parsePosition(undefined, ["RB", FLEX]), "RB");
});
