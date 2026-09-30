// The method constants of lib/method.ts are copied from the Python code; this test reads
// that code and fails when they drift apart.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { AS_OF_TIME_UTC, AS_OF_WEEKDAY, CHANCE_RANGE_LEVEL, POSITIONS, RANK_BUCKETS, TRACK_INTERVAL_LEVEL } from "../../lib/method";
import { GSIS_PATTERN } from "../../lib/params";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const read = (p: string) => readFileSync(join(repo, p), "utf8");

test("the chance's range level is confidence.py's BAND_LEVEL", () => {
  const m = read("src/twm/modules/waiver_radar/confidence.py").match(/^BAND_LEVEL\s*=\s*([0-9.]+)/m);
  assert.ok(m, "BAND_LEVEL not found");
  assert.equal(Number(m[1]), CHANCE_RANGE_LEVEL);
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
