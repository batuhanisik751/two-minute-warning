import assert from "node:assert/strict";
import { test } from "node:test";
import {
  boardBandName,
  boardCalibration,
  boardCell,
  boardDriverValue,
  boardHref,
  boardOutcomeWords,
  cellWords,
  diffWords,
  disagreementRecord,
  disagreements,
  disagreeWords,
  expectedFall,
  expertsTop,
  type RankRow,
} from "../../lib/board";
import { BOARD_DISAGREE_TOP } from "../../lib/method";

const row = (i: number, ecr: number | null, pos: number): RankRow => ({ gsisId: `00-00000${String(i).padStart(2, "0")}`, cliffRank: i, ecrRank: ecr, posRankS: pos });

test("the experts' top: the largest fall in rank first, unranked players ahead of all, ties by id", () => {
  const rows = [row(1, 5, 4), row(2, 30, 10), row(3, null, 20), row(4, 12, 2), row(5, null, 8), row(6, 30, 10)];
  assert.equal(expectedFall(rows[1]), 20);
  assert.equal(expectedFall(rows[2]), null);
  // unranked first (better last season first), then by fall, ties by id
  assert.deepEqual([...expertsTop(rows, 4)], ["00-0000005", "00-0000003", "00-0000002", "00-0000006"]);
  assert.deepEqual([...expertsTop(rows.map((r) => ({ ...r, ecrRank: null })), 3)], [], "no experts' ranks at all: nobody");
});

test("where we disagree: our top 10 not theirs, theirs not ours; both or neither unmarked", () => {
  // 14 players: our top 10 are 1..10; the experts drop 9..14 furthest (and 1..4 a little)
  const rows = Array.from({ length: 14 }, (_, k) => row(k + 1, k >= 8 ? 40 + k : 5, 4));
  const d = disagreements(rows);
  assert.equal(BOARD_DISAGREE_TOP, 10);
  assert.deepEqual([...expertsTop(rows)].slice(0, 6), ["00-0000014", "00-0000013", "00-0000012", "00-0000011", "00-0000010", "00-0000009"]);
  // the experts' other 4 are the lowest ids among the rest (ties)
  assert.equal(d.get("00-0000001"), undefined);
  assert.equal(d.get("00-0000005"), "model");
  assert.equal(d.get("00-0000009"), undefined, "on both lists");
  assert.equal(d.get("00-0000011"), "experts");
  assert.equal([...d.values()].filter((x) => x === "model").length, 4);
  assert.equal([...d.values()].filter((x) => x === "experts").length, 4);
  assert.equal(disagreements(rows.map((r) => ({ ...r, ecrRank: null }))).size, 0);
  const w = disagreeWords("experts", "TE", { ...rows[10], ecrRank: null });
  assert.equal(w.short, "The experts see more risk than we do");
  assert.equal(w.long, "the experts did not rank him after he finished TE4 last season, among the 10 they drop furthest; not in our top 10");
  assert.equal(disagreeWords("model", "RB", row(3, 3, 1)).long, "in our top 10 for a Cliff; the experts rank him RB3 after he finished RB1 last season");
});

test("drivers in words; missing inputs say so", () => {
  const d = (feature: string, value: number | null, missing = false) => ({ feature, label: feature, contribution: 0.2, value, missing });
  assert.equal(boardDriverValue(d("ppg_change", 12.55)), "+12.6 points per game against the season before");
  assert.equal(boardDriverValue(d("pos_te", 1)), "a tight end");
  assert.equal(boardDriverValue(d("depth_rank_s1", 3)), "number 3 at his spot on the week-1 depth chart");
  assert.equal(boardDriverValue(d("pos_rank_s", 22)), "22nd by points per game at his position last season");
  assert.equal(boardDriverValue(d("age_curve_ratio", 0.79)), "at his age, players at his position kept 79% of their points per game on average");
  assert.equal(boardDriverValue(d("vacated_targets_share_s1", null, true)), "not available (the model fills it in)");
});

test("outcomes: missed time, Cliff, no Cliff, pending", () => {
  const f = { labelStatus: "final" };
  assert.deepEqual(boardOutcomeWords(null, 10).tone, "pending");
  assert.equal(boardOutcomeWords({ ...f, gamesS1: 3, ppgS1: 8, yCliff: null, yMissed: true }, 10).long, "3 games (fewer than 6)");
  const c = boardOutcomeWords({ ...f, gamesS1: 12, ppgS1: 6.5, yCliff: true, yMissed: false }, 10);
  assert.deepEqual([c.tone, c.long], ["cliff", "6.5 points per game in 12 games (−35% from 10.0): a drop of 30% or more"]);
  assert.equal(boardOutcomeWords({ ...f, gamesS1: 16, ppgS1: 11, yCliff: false, yMissed: false }, 10).long, "11.0 points per game in 16 games (+10% from 10.0)");
  assert.equal(boardOutcomeWords({ labelStatus: "pending", gamesS1: null, ppgS1: null, yCliff: null, yMissed: null }, 10).short, "Pending");
});

test("calibration bands, the record, track cells and links", () => {
  assert.deepEqual([0, 1, 2, 3].map(boardBandName), ["Under 10%", "10–25%", "25–50%", "50% or more"]);
  const g = boardCalibration([
    { chance: "missed", band: 0, n: 10, hits: 1, meanPred: 0.05, boards: 2 },
    { chance: "cliff", band: 3, n: 4, hits: 3, meanPred: 0.6, boards: 2 },
    { chance: "cliff", band: 1, n: 0, hits: 0, meanPred: 0, boards: 0 },
  ]);
  assert.deepEqual(g.map((x) => [x.chance, x.cells.map((c) => [c.bandName, c.observed])]), [["cliff", [["50% or more", 0.75]]], ["missed", [["Under 10%", 0.1]]]]);
  const rec = disagreementRecord(
    [2020, 2021].flatMap((season) => [
      { variant: "cliff_main", model: "logit", season, pickGroup: "model_only", players: 6, hits: 3 },
      { variant: "cliff_main", model: "logit", season, pickGroup: "ecr_only", players: 6, hits: 1 },
      { variant: "cliff_main", model: "logit", season, pickGroup: "both", players: 4, hits: 4 },
    ]),
    "cliff_main",
  )!;
  assert.deepEqual([rec.from, rec.to, rec.groups.model_only, rec.groups.ecr_only.hits], [2020, 2021, { players: 12, hits: 6, rate: 0.5 }, 2]);
  assert.equal(disagreementRecord([], "cliff_main"), null);
  const t = [{ variant: "v", slice: "all", model: "logit", vs: "ecr", metric: "pr_auc_diff", value: -0.043, lo: -0.129, hi: 0.042, research: false }];
  const c = boardCell(t, { variant: "v", slice: "all", model: "logit", metric: "pr_auc_diff", vs: "ecr" });
  assert.equal(cellWords(c, true), "−0.043 (−0.129 to +0.042)");
  assert.equal(diffWords(c), "no clear difference");
  assert.equal(diffWords({ value: 0.1, lo: 0.01, hi: 0.2 }), "ahead");
  assert.equal(boardCell(t, { variant: "v", slice: "all", model: "logit", metric: "pr_auc_diff" }), null);
  assert.equal(boardHref({ season: 2024, pos: "TE" }), "/board?season=2024&pos=TE");
  assert.equal(boardHref({}), "/board");
});
