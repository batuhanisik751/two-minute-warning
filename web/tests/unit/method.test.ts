// The method constants of lib/method.ts are copied from the Python code; this test reads
// that code and fails when they drift apart.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  AS_OF_TIME_UTC,
  AS_OF_WEEKDAY,
  CHANCE_RANGE_LEVEL,
  CLOCK,
  END_OF_HALF_SECONDS,
  LATE_GAME_Q4_SECONDS,
  LEADERBOARD_MIN_GAMES,
  POSITIONS,
  RANK_BUCKETS,
  STABILITY_INTERVAL_LEVEL,
  STABILITY_MIN_GAMES,
  STABILITY_RESAMPLES,
  TOSS_UP_MARGIN,
  TRACK_INTERVAL_LEVEL,
} from "../../lib/method";
import { GSIS_PATTERN } from "../../lib/params";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const read = (p: string) => readFileSync(join(repo, p), "utf8");

test("the chance's range level is confidence.py's BAND_LEVEL", () => {
  const m = read("src/twm/modules/waiver_radar/confidence.py").match(/^BAND_LEVEL\s*=\s*([0-9.]+)/m);
  assert.ok(m, "BAND_LEVEL not found");
  assert.equal(Number(m[1]), CHANCE_RANGE_LEVEL);
});

test("the stability study's minimum games, resamples and interval level are stability.py's", () => {
  const src = read("src/twm/modules/regression_watch/stability.py");
  const games = src.match(/^MIN_GAMES\s*=\s*(\d+)/m);
  const boot = src.match(/^N_BOOT\s*=\s*(\d+)/m);
  const pct = src.match(/np\.nanpercentile\(rs, \[([0-9.]+), ([0-9.]+)\]\)/);
  assert.ok(games && boot && pct, "MIN_GAMES, N_BOOT or the percentile interval not found");
  assert.equal(Number(games[1]), STABILITY_MIN_GAMES);
  assert.equal(Number(boot[1]), STABILITY_RESAMPLES);
  assert.equal((Number(pct[2]) - Number(pct[1])) / 100, STABILITY_INTERVAL_LEVEL);
});

test("the track record's interval level and rank buckets are metrics.py's", () => {
  const src = read("src/twm/backtest/metrics.py");
  const level = src.match(/^LEVEL\s*=\s*([0-9.]+)/m);
  assert.ok(level, "LEVEL not found");
  assert.equal(Number(level[1]), TRACK_INTERVAL_LEVEL);
  const buckets = src.match(/^DEFAULT_BUCKETS[^=]*=\s*\(\s*(.+?)\)\s*$/m);
  assert.ok(buckets, "DEFAULT_BUCKETS not found");
  const pairs = [...buckets[1].matchAll(/\((\d+),\s*(\d+)\)/g)].map((x) => [Number(x[1]), Number(x[2])]);
  assert.deepEqual(pairs, RANK_BUCKETS.map((b) => [...b]));
});

test("the weekly as-of is config/settings.yaml's", () => {
  const src = read("config/settings.yaml");
  const block = src.match(/^as_of:\s*\n\s+weekly:\s*\n\s+weekday:\s*(\w+)\s*\n\s+time:\s*"([0-9:]+)"/m);
  assert.ok(block, "as_of.weekly not found");
  assert.equal(block[1].toLowerCase(), AS_OF_WEEKDAY.toLowerCase());
  assert.equal(block[2], AS_OF_TIME_UTC);
});

test("positions and the gsis pattern match the schema and the publisher", () => {
  const schema = read("web/db/schema.ts");
  const check = schema.match(/radar_list_position_check",\s*sql`\$\{t\.position\} in \(([^)]+)\)`/);
  assert.ok(check, "radar_list_position_check not found");
  assert.deepEqual(check[1].split(",").map((s) => s.trim().replace(/'/g, "")), [...POSITIONS]);
  const py = read("src/twm/publish/collect.py").match(/GSIS_PATTERN = re\.compile\(r"(.+)"\)/);
  assert.ok(py, "GSIS_PATTERN not found");
  assert.equal(py[1], GSIS_PATTERN.source);
});

test("the streamer's chance range uses the Radar's BAND_LEVEL (the K and D/ST pages say the same level)", () => {
  const src = read("src/twm/modules/streamer/confidence.py");
  assert.match(src, /from twm\.modules\.waiver_radar import confidence as cf/);
  assert.match(src, /"level": cf\.BAND_LEVEL/);
});

test("the Decision Report Card's margin, exclusions, leaderboard and clock thresholds are config/settings.yaml's", () => {
  const src = read("config/settings.yaml");
  const start = src.search(/^decisions:\s*$/m);
  assert.ok(start >= 0, "decisions: not found");
  const rest = src.slice(start + "decisions:".length);
  const end = rest.search(/^\S/m);
  const block = end >= 0 ? rest.slice(0, end) : rest;
  const num = (key: string) => {
    const m = block.match(new RegExp(`^\\s+${key}:\\s*([0-9.]+)\\s*$`, "m"));
    assert.ok(m, `decisions ${key} not found`);
    return Number(m[1]);
  };
  assert.equal(num("toss_up_margin"), TOSS_UP_MARGIN);
  assert.equal(num("end_of_half_seconds"), END_OF_HALF_SECONDS);
  assert.equal(num("q4_seconds"), LATE_GAME_Q4_SECONDS);
  assert.equal(num("leaderboard_min_games"), LEADERBOARD_MIN_GAMES);
  assert.equal(num("one_score_margin"), CLOCK.oneScoreMargin);
  assert.equal(num("final_window_seconds"), CLOCK.finalWindowSeconds);
  assert.equal(num("clock_ran_min_seconds"), CLOCK.clockRanMinSeconds);
  assert.equal(num("passivity_min_seconds"), CLOCK.passivityMinSeconds);
  assert.equal(num("passivity_min_timeouts"), CLOCK.passivityMinTimeouts);
  assert.equal(num("passivity_min_ep"), CLOCK.passivityMinEp);
  const report = read("src/twm/modules/decisions/decisions_report.py");
  assert.match(report, /need = min\(int\(c\["cfg"\]\.leaderboard_min_games\), int\(games\.max\(\) or 0\)\)/);
});
