// Colour contrast of the theme tokens in app/globals.css, in both themes (axe cannot measure
// contrast in jsdom, which has no layout). WCAG 2.2 AA: 4.5:1 for text, 3:1 for graphics and
// focus indicators.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

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

function lum(hex: string): number {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const l = c.map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2];
}

export function ratio(a: string, b: string): number {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

const TEXT: [string, string][] = [
  ["fg", "bg"], ["fg", "surface"], ["fg", "raised"], ["fg", "accent-soft"], ["fg", "warn-bg"],
  ["muted", "bg"], ["muted", "surface"], ["muted", "raised"],
  ["accent", "bg"], ["accent", "surface"], ["on-accent", "accent"],
  ["recon", "bg"], ["recon", "surface"], ["hit", "surface"], ["miss", "surface"],
  ["pending", "surface"], ["warn-fg", "warn-bg"],
];
const GRAPHICS: [string, string][] = [
  ["focus", "bg"], ["focus", "surface"], ["chart-a", "surface"], ["chart-b", "surface"], ["chart-c", "surface"],
  ["live", "bg"], ["recon", "bg"],
];

const themes = {
  light: block(":root {"),
  "dark (system)": block(':root:not([data-theme="light"]) {'),
  "dark (chosen)": block(':root[data-theme="dark"] {'),
};

test("the two dark blocks are identical", () => {
  assert.deepEqual(themes["dark (system)"], themes["dark (chosen)"]);
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
