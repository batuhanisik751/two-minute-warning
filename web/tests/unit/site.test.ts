// lib/site.ts: the repository links and the model-card list that /methodology shows (step I6a).
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { LOCAL_ONLY_MODULES, MODEL_CARDS, MODEL_CARDS_INDEX, REPO_URL, docUrl, siteGlossary } from "../../lib/site";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const CARDS = join(ROOT, "docs", "model_cards");
/** Cards of local-only features: in the docs, not on the site (G5.1: the owner's start/sit report). */
const LOCAL_ONLY_CARDS = ["startsit.md"];

test("doc links point at the main branch of the public repository", () => {
  assert.equal(REPO_URL, "https://github.com/batuhanisik751/two-minute-warning");
  assert.equal(docUrl("docs/timemachine.md"), `${REPO_URL}/blob/main/docs/timemachine.md`);
});

test("every model card in docs/model_cards is listed once, in the index's order, and nothing else (local-only cards left out)", () => {
  for (const f of LOCAL_ONLY_CARDS) assert.ok(readdirSync(CARDS).includes(f), `${f} is still in the docs`);
  const files = readdirSync(CARDS).filter((f) => f.endsWith(".md") && f !== "README.md" && !LOCAL_ONLY_CARDS.includes(f));
  const listed = MODEL_CARDS.map((c) => c.file);
  assert.deepEqual([...listed].sort(), [...files].sort());
  const index = readFileSync(join(ROOT, MODEL_CARDS_INDEX), "utf8");
  const order = [...index.matchAll(/\]\(([a-z_]+\.md)\)/g)].map((m) => m[1]).filter((f) => !LOCAL_ONLY_CARDS.includes(f));
  assert.deepEqual(listed, order);
  for (const c of MODEL_CARDS) assert.ok(c.title.trim(), `${c.file} has a title`);
});

test("the site's glossary leaves out the rows of local-only modules alone (G5.1)", () => {
  assert.deepEqual([...LOCAL_ONLY_MODULES], ["my_league"]);
  const rows = [
    { name: "start_sit_odds", modules: ["my_league"] },
    { name: "ppr", modules: ["waiver_radar", "my_league"] },
    { name: "as_of", modules: [] },
    { name: "y_hit", modules: ["waiver_radar"] },
  ];
  assert.deepEqual(siteGlossary(rows).map((r) => r.name), ["ppr", "as_of", "y_hit"]);
});
