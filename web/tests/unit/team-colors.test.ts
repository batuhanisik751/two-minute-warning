import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { parseHex, ratio } from "../../lib/contrast";
import { MIN_TEAM_CONTRAST, THEME_SURFACES, teamColorOn, teamColors, teamStyle } from "../../lib/team-colors";

const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "..", "app", "globals.css"), "utf8");
const surfaceIn = (selector: string) => {
  const i = css.indexOf(selector);
  const block = css.slice(css.indexOf("{", i), css.indexOf("}", i));
  return block.match(/--surface:\s*(#[0-9a-f]{6})/i)?.[1].toLowerCase();
};

test("the surfaces team colours are checked against are the themes' --surface", () => {
  assert.equal(surfaceIn(":root {"), THEME_SURFACES.light);
  assert.equal(surfaceIn(':root:not([data-theme="light"]) {'), THEME_SURFACES.dark);
  assert.equal(surfaceIn(':root[data-theme="dark"] {'), THEME_SURFACES.dark);
});

test("a team colour is used only where it stands out; else the second colour; else none", () => {
  // a dark navy primary with a silver second colour: navy on light, silver on dark
  assert.deepEqual(teamColors("#002244", "#B0B7BC"), { light: "#002244", dark: "#b0b7bc" });
  // a pale tan primary and a black second colour: black on light, tan on dark
  assert.deepEqual(teamColors("#D3BC8D", "#000000"), { light: "#000000", dark: "#d3bc8d" });
  // neither stands out on the dark surface: the page falls back to a neutral token
  assert.deepEqual(teamColors("#0b162a", "#101820"), { light: "#0b162a", dark: null });
  assert.deepEqual(teamColors(null, "not a colour"), { light: null, dark: null });
  for (const theme of ["light", "dark"] as const) {
    const c = teamColorOn(THEME_SURFACES[theme], "#97233F", "#FFB612");
    assert.ok(c && ratio(c, THEME_SURFACES[theme]) >= MIN_TEAM_CONTRAST);
  }
  assert.deepEqual(teamStyle(null, null), { "--team-l": "var(--team-fallback)", "--team-d": "var(--team-fallback)" });
});

test("hex parsing", () => {
  assert.equal(parseHex("#A5ACAF"), "#a5acaf");
  assert.equal(parseHex(" #abc "), "#aabbcc");
  assert.equal(parseHex("red"), null);
  assert.equal(parseHex("#12345"), null);
  assert.equal(parseHex(null), null);
});
