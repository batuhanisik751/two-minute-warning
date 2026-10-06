// lib/coach-tendencies.ts (feature #10): how values and percentiles read (pace as "faster than"),
// the seasons grouped, the current head coaches, the league table's sort, the persistence note and
// the fantasy link, both written from the published rows only.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  COMPARISONS,
  currentCoaches,
  fantasyLinkText,
  fmtTendency,
  fmtVsLeague,
  groupSeasons,
  parseTendencySort,
  percentileText,
  persistenceNote,
  sortSeasons,
  TENDENCY_METRICS,
  type LinkRow,
  type PersistenceRow,
  type TendencyRow,
} from "../../lib/coach-tendencies";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

test("the metrics are the module's, in its order", () => {
  const py = readFileSync(join(root, "src/twm/modules/coach_tendencies/season.py"), "utf8");
  const block = py.slice(py.indexOf("METRICS: dict"), py.indexOf("}", py.indexOf("METRICS: dict")));
  assert.deepEqual([...block.matchAll(/^ {4}"(\w+)":/gm)].map((m) => m[1]), [...TENDENCY_METRICS]);
});

test("values, differences and percentiles read as the pages show them", () => {
  assert.equal(fmtTendency("neutral_pass_rate", 0.6234), "62%");
  assert.equal(fmtTendency("proe", 4.56), "+4.6 pts");
  assert.equal(fmtTendency("proe", -0.04), "0.0 pts");
  assert.equal(fmtTendency("neutral_sec_per_play", 27.04), "27.0 s");
  assert.equal(fmtTendency("shotgun_rate", null), "–");
  assert.equal(fmtVsLeague("neutral_sec_per_play", -1.24), "1.2 s faster");
  assert.equal(fmtVsLeague("neutral_sec_per_play", 0.6), "0.6 s slower");
  assert.equal(fmtVsLeague("neutral_pass_rate", 0.061), "+6.1 pts");
  assert.equal(fmtVsLeague("proe", -2.25), "-2.3 pts");
  assert.equal(percentileText("shotgun_rate", 71.4), "71st percentile");
  assert.equal(percentileText("proe", 12.2), "12th percentile");
  assert.equal(percentileText("neutral_sec_per_play", 10), "faster than 90%");
  assert.equal(percentileText("fourth_go_rate", null), "not ranked");
});

const row = (coachId: string, team: string, season: number, metric: string, value: number, throughWeek = 18): TendencyRow => ({
  coachId, team, season, metric, value, throughWeek, isCurrent: season === 2026, games: 17, plays: 1000, sample: 600, leagueAvg: 0.5, percentile: 50,
});

test("seasons group newest first; the current coach of a team is the one of its latest game", () => {
  const rows = [row("a", "NHG", 2025, "proe", 1), row("a", "NHG", 2026, "proe", 2, 3), row("a", "NHG", 2026, "shotgun_rate", 0.6, 3), row("b", "SRO", 2026, "proe", 5, 2), row("c", "SRO", 2026, "proe", -1, 3)];
  const g = groupSeasons(rows);
  assert.deepEqual(g.map((s) => [s.coachId, s.season]), [["a", 2026], ["c", 2026], ["b", 2026], ["a", 2025]]);
  assert.deepEqual(Object.keys(g[0].cells).sort(), ["proe", "shotgun_rate"]);
  const cur = currentCoaches(g.filter((s) => s.season === 2026));
  assert.deepEqual(cur.map((s) => s.coachId).sort(), ["a", "c"]);
  assert.deepEqual(sortSeasons(cur, "proe").map((s) => s.coachId), ["a", "c"]);
  assert.deepEqual(sortSeasons(cur, "team").map((s) => s.team), ["NHG", "SRO"]);
  // pace: fastest (fewest seconds) first; a row without the metric goes last
  const pace = groupSeasons([row("x", "AAA", 2026, "neutral_sec_per_play", 31), row("y", "BBB", 2026, "neutral_sec_per_play", 27), row("z", "CCC", 2026, "proe", 1)]);
  assert.deepEqual(sortSeasons(pace, "neutral_sec_per_play").map((s) => s.coachId), ["y", "x", "z"]);
  assert.equal(parseTendencySort("shotgun_rate"), "shotgun_rate");
  assert.equal(parseTendencySort(["team"]), "team");
  assert.equal(parseTendencySort("nonsense"), "proe");
  assert.equal(parseTendencySort(undefined), "proe");
});

const P = (metric: string, rs: (number | null)[]): PersistenceRow[] =>
  COMPARISONS.map((comparison, i) => ({ metric, comparison, nPairs: [600, 50, 190][i], nSeasons: 25, firstSeason: 1999, lastSeason: 2024, r: rs[i], ciLow: null, ciHigh: null }));

test("the persistence note comes from the rows: what persists, what follows the coach", () => {
  assert.equal(persistenceNote([]), null);
  const rows = [...P("neutral_pass_rate", [0.47, 0.22, 0.22]), ...P("proe", [0.52, 0.11, 0.11]), ...P("no_huddle_rate", [0.68, 0.55, 0.16]), ...P("shotgun_rate", [0.73, 0.44, 0.21])];
  const n = persistenceNote(rows)!;
  assert.match(n.stays, /coach and the team both stay: season to season, r 0\.47 to 0\.73 across the 4 tendencies\./);
  assert.equal(n.moves, "At a new team, only no-huddle rate (r 0.55) and shotgun rate (r 0.44) carry over; the others keep r 0.11 to 0.22.");
  assert.equal(n.team, "A team that changes coach keeps little of its style: r 0.11 to 0.22.");
  assert.equal(n.sample, "Coaches who moved: 50 pairs of seasons, so those intervals are wide.");
  // nothing above the reading rule: said so, with the numbers
  const none = persistenceNote([...P("proe", [0.3, 0.1, 0.05]), ...P("shotgun_rate", [0.5, null, 0.2])])!;
  assert.match(none.stays, /1 of 2 reach 0\.40/);
  assert.equal(none.moves, "At a new team, no tendency carries over: r 0.10 to 0.10.");
});

test("the fantasy link states PROE against targets, same season and next, from the rows", () => {
  const L = (target: string, horizon: string, r: number, y: number): LinkRow => ({ metric: "proe", target, horizon, n: 416, nSeasons: 13, r, ciLow: r - 0.04, ciHigh: r + 0.04, xSd: 4.04, yPerXSd: y });
  assert.equal(fantasyLinkText([]), null);
  const t = fantasyLinkText([L("targets_per_game", "same_season", 0.716, 2.53), L("recv_ppr_per_game", "same_season", 0.7, 5.38), L("targets_per_game", "next_season", 0.32, 1.02)])!;
  assert.match(t.same, /r = 0\.72 \(95% interval 0\.68 to 0\.76\) over 416 team-seasons/);
  assert.match(t.same, /One standard deviation of PROE \(4\.0 points\) comes with \+2\.5 targets per game and \+5\.4 receiving PPR points per game/);
  assert.match(t.next, /weaker: r = 0\.32 .*about \+1\.0 targets per game/);
});
