import assert from "node:assert/strict";
import { test } from "node:test";
import { boardLinkText } from "../../lib/board";
import { THIRD_PARTY_NOTE, shownReasons, showThirdPartyRanks, templatePattern, thirdPartyReasonId } from "../../lib/third-party";
import manifest from "../../lib/third-party-reasons.json";

test("FantasyPros' per-player values: shown unless the site is public; SHOW_THIRD_PARTY_RANKS overrides", () => {
  assert.equal(showThirdPartyRanks({}), true);
  assert.equal(showThirdPartyRanks({ SITE_PUBLIC: "false" }), true);
  assert.equal(showThirdPartyRanks({ SITE_PUBLIC: "1" }), true);
  assert.equal(showThirdPartyRanks({ SITE_PUBLIC: " TRUE " }), false);
  assert.equal(showThirdPartyRanks({ SITE_PUBLIC: "true", SHOW_THIRD_PARTY_RANKS: "" }), false);
  assert.equal(showThirdPartyRanks({ SITE_PUBLIC: "true", SHOW_THIRD_PARTY_RANKS: "yes" }), false);
  assert.equal(showThirdPartyRanks({ SITE_PUBLIC: "true", SHOW_THIRD_PARTY_RANKS: "True" }), true);
  assert.equal(showThirdPartyRanks({ SHOW_THIRD_PARTY_RANKS: "false" }), false);
  assert.equal(THIRD_PARTY_NOTE, "The experts' consensus ranks are not shown on the public site (license).");
});

test("every listed template identifies its own example, and only its own", () => {
  assert.ok(manifest.reasons.length >= 5);
  for (const r of manifest.reasons) {
    assert.equal(thirdPartyReasonId(r.example), r.id, r.example);
    for (const o of manifest.reasons) if (o.id !== r.id) assert.ok(!templatePattern(o.template).test(r.example), `${o.id} vs ${r.example}`);
  }
  // with another number, position or name, and with a clause appended
  assert.equal(thirdPartyReasonId("Was ranked No. 3 among WRs before the season"), "preseason_pos_rank");
  assert.equal(thirdPartyReasonId("Was ranked #12 at DST before the season"), "kdst_preseason_rank");
  assert.equal(thirdPartyReasonId("Experts ranked the North Harbor Gulls #1 at DST last week"), "weekly_ecr_rank");
  assert.equal(thirdPartyReasonId("Was ranked No. 27 among QBs before the season (orders it among picks with the same score)"), "preseason_pos_rank");
});

test("a stored reason object counts by its feature key, else by its text", () => {
  assert.equal(thirdPartyReasonId({ feature: "weekly_ecr_listed", text: "anything" }), "weekly_ecr_listed");
  assert.equal(thirdPartyReasonId({ feature: "snap_share_avg3", text: "Was ranked No. 9 among RBs before the season" }), "preseason_pos_rank");
  assert.equal(thirdPartyReasonId({ feature: "snap_share_avg3", text: "Played 71% of snaps" }), null);
  assert.equal(thirdPartyReasonId(null), null);
  assert.equal(thirdPartyReasonId(42), null);
});

test("our own reasons are never taken for FantasyPros'", () => {
  for (const t of [
    "Was No. 2 at RB on his team's depth chart a week earlier",
    "Was drafted in round 3: teams give higher draft picks more chances",
    "Was on the field for about 30 pass plays per game over the last 3 games",
    "Seed reason A for Rowan Fielding",
    "Ranked No. 27 among QBs in points per game",
    "He was ranked No. 27 among QBs before the season",
  ]) assert.equal(thirdPartyReasonId(t), null, t);
});

test("the reasons shown: all privately, without FantasyPros' in public", () => {
  const rs = ["Played 71% of snaps", "Was ranked No. 27 among QBs before the season", "Was on a preseason ranking list"];
  assert.deepEqual(shownReasons(rs, true), rs);
  assert.deepEqual(shownReasons(rs, false), ["Played 71% of snaps"]);
  assert.deepEqual(shownReasons([], false), []);
});

test("the board link names the experts' ranks only where they are shown", () => {
  assert.equal(boardLinkText(true), "Every player, the experts' ranks, the drivers and how to read it");
  assert.equal(boardLinkText(false), "Every player, the drivers and how to read it");
});
