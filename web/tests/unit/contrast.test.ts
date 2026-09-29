// Colour contrast of the theme tokens in app/globals.css, in both themes (axe cannot measure
// contrast in jsdom, which has no layout). WCAG 2.2 AA: 4.5:1 for text, 3:1 for graphics and
// focus indicators.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { ratio } from "../../lib/contrast";

const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "..", "app", "globals.css"), "utf8");

function block(selector: string): Record<string, string> {
  const i = css.indexOf(selector);
  assert.ok(i >= 0, `${selector} not in globals.css`);
  const open = css.indexOf("{", i);
  const close = css.indexOf("}", open);
  const out: Record<string, string> = {};
  for (const m of css.slice(open + 1, close).matchAll(/--([a-z0-9-]+):\s*(#[0-9a-f]{6})/gi)) out[m[1]] = m[2];
  return out;
}

export { ratio };

const POSITIONS = ["qb", "rb", "wr", "te", "flex", "k", "dst"];

const TEXT: [string, string][] = [
  ["fg", "bg"], ["fg", "surface"], ["fg", "raised"], ["fg", "accent-soft"], ["fg", "warn-bg"],
  ["muted", "bg"], ["muted", "surface"], ["muted", "raised"],
  ["accent", "bg"], ["accent", "surface"], ["accent", "raised"], ["on-accent", "accent"],
  ["recon", "bg"], ["recon", "surface"], ["hit", "surface"], ["miss", "surface"],
  ["pending", "surface"], ["warn-fg", "warn-bg"],
  // must-add badges and the LIVE pill; "hot" text on the page's own surfaces
  ["on-hot", "hot"], ["hot-ink", "surface"], ["hot-ink", "bg"],
  // the scoreboard strip (header) and the field banner (home page)
  ["strip-fg", "strip-bg"], ["strip-fg", "strip-raised"], ["strip-muted", "strip-bg"], ["strip-hot", "strip-bg"],
  ["strip-hot", "strip-raised"],
  ["field-fg", "field-a"], ["field-fg", "field-b"], ["field-muted", "field-a"], ["field-muted", "field-b"],
  ["strip-hot", "field-a"], ["strip-hot", "field-b"],
  // position badges
  ...POSITIONS.map((p): [string, string] => ["on-pos", `pos-${p}`]),
];
const GRAPHICS: [string, string][] = [
  ["focus", "bg"], ["focus", "surface"], ["chart-a", "surface"], ["chart-b", "surface"], ["chart-c", "surface"],
  ["live", "bg"], ["recon", "bg"], ["border-strong", "surface"], ["team-fallback", "surface"],
  // the chance meter's fill against its track and the card
  ["meter-fill", "meter-track"], ["meter-fill", "surface"], ["meter-hot", "meter-track"], ["meter-hot", "surface"],
  // focus rings on the dark strip and field are gold (globals.css .site-strip :focus-visible)
  ["strip-hot", "strip-bg"], ["strip-hot", "field-a"], ["strip-hot", "field-b"],
  // position badges and tabs are told apart by colour as well as by their text
  ...POSITIONS.map((p): [string, string] => [`pos-${p}`, "surface"]),
];

const themes = {
  light: block(":root {"),
  "dark (system)": block(':root:not([data-theme="light"]) {'),
  "dark (chosen)": block(':root[data-theme="dark"] {'),
};

test("the two dark blocks are identical", () => {
  assert.deepEqual(themes["dark (system)"], themes["dark (chosen)"]);
});

test("every token is defined in every theme", () => {
  const keys = (t: Record<string, string>) => Object.keys(t).sort();
  assert.deepEqual(keys(themes["dark (system)"]), keys(themes.light));
});

for (const [name, t] of Object.entries(themes)) {
  test(`${name}: text pairs reach 4.5:1, graphics 3:1`, () => {
    const bad: string[] = [];
    for (const [fg, bg] of TEXT) {
      assert.ok(t[fg] && t[bg], `${name}: --${fg} or --${bg} missing`);
      const r = ratio(t[fg], t[bg]);
      if (r < 4.5) bad.push(`--${fg} on --${bg}: ${r.toFixed(2)}`);
    }
    for (const [fg, bg] of GRAPHICS) {
      assert.ok(t[fg] && t[bg], `${name}: --${fg} or --${bg} missing`);
      const r = ratio(t[fg], t[bg]);
      if (r < 3) bad.push(`--${fg} on --${bg}: ${r.toFixed(2)} (graphics)`);
    }
    assert.deepEqual(bad, []);
  });
}

test("the ratio function matches known values", () => {
  assert.equal(ratio("#000000", "#ffffff").toFixed(1), "21.0");
  assert.equal(ratio("#777777", "#ffffff").toFixed(2), "4.48");
});
