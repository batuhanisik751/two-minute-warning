import assert from "node:assert/strict";
import { test } from "node:test";
import { leagueShape, startersPhrase } from "../../lib/league";

// worded like the registry's entries (src/twm/registry.py, published as the glossary table)
const REGISTRY_LIKE = [
  {
    name: "starter_threshold",
    formula:
      "teams x dedicated starters at the position (derived from config/league.yaml teams and lineup): QB top 12, RB top 24, WR top 24, TE top 12 by fantasy points that week; FLEX-worthy (teams x (dedicated starters + multi-position slots the position can fill), for the positions in flex_worthy_positions): RB/WR top 36",
    explanation: "A player 'finished as a starter' in a week when he scored well enough that a typical 12-team league would have started him.",
  },
  { name: "candidate_pool", formula: "...", explanation: "The players who are probably still on waivers in a typical 12-team league." },
];

test("the league shape comes from the glossary text; FLEX-worthy thresholds are not starters", () => {
  const shape = leagueShape(REGISTRY_LIKE);
  assert.deepEqual(shape, { starters: { QB: 12, RB: 24, WR: 24, TE: 12 }, teams: 12 });
  assert.equal(startersPhrase(shape, ["RB", "WR", "TE"]), "top-24 RB, top-24 WR or top-12 TE");
  assert.equal(startersPhrase(shape, ["TE"]), "top-12 TE");
});

test("other numbers are read, not assumed", () => {
  const shape = leagueShape([
    { name: "starter_threshold", formula: "QB top 10, RB top 20, WR top 30, TE top 8; FLEX: RB/WR top 45", explanation: "a 10-team league" },
  ]);
  assert.deepEqual(shape, { starters: { QB: 10, RB: 20, WR: 30, TE: 8 }, teams: 10 });
  assert.equal(startersPhrase(shape, ["RB", "WR", "TE"]), "top-20 RB, top-30 WR or top-8 TE");
});

test("falls back to is_starter_finish, and to nothing", () => {
  const shape = leagueShape([
    { name: "starter_threshold", formula: "no numbers here", explanation: "" },
    { name: "is_starter_finish", formula: "weekly_pos_rank <= the position's starter threshold (QB top 12, RB top 24, WR top 24, TE top 12)", explanation: "" },
  ]);
  assert.deepEqual(shape, { starters: { QB: 12, RB: 24, WR: 24, TE: 12 }, teams: null });
  assert.equal(leagueShape([{ name: "starter_threshold", formula: "seed formula", explanation: "x" }]), null);
  assert.equal(leagueShape([]), null);
  assert.equal(startersPhrase(null, ["RB"]), null);
  // a position the text does not state: no phrase rather than a partial one
  assert.equal(startersPhrase({ starters: { RB: 24 }, teams: null }, ["RB", "WR"]), null);
});
