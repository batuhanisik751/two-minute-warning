// lib/site.ts: the repository links and the model-card list that /methodology shows (step I6a).
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { MODEL_CARDS, MODEL_CARDS_INDEX, REPO_URL, docUrl } from "../../lib/site";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const CARDS = join(ROOT, "docs", "model_cards");

test("doc links point at the main branch of the public repository", () => {
  assert.equal(REPO_URL, "https://github.com/batuhanisik751/two-minute-warning");
  assert.equal(docUrl("docs/timemachine.md"), `${REPO_URL}/blob/main/docs/timemachine.md`);
});

test("every model card in docs/model_cards is listed once, in the index's order, and nothing else", () => {
  const files = readdirSync(CARDS).filter((f) => f.endsWith(".md") && f !== "README.md");
  const listed = MODEL_CARDS.map((c) => c.file);
  assert.deepEqual([...listed].sort(), [...files].sort());
  const index = readFileSync(join(ROOT, MODEL_CARDS_INDEX), "utf8");
  const order = [...index.matchAll(/\]\(([a-z_]+\.md)\)/g)].map((m) => m[1]);
  assert.deepEqual(listed, order);
  for (const c of MODEL_CARDS) assert.ok(c.title.trim(), `${c.file} has a title`);
});
