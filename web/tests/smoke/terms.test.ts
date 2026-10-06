// Every metric a page names in a table header or a headline label is a glossary term (T1,
// 2026-10-04: ROC-AUC, PR-AUC, Brier score, log loss, MAE, PPG, G had no tooltip). A Term is a
// button whose panel links its glossary entry (components/TermPopover.tsx), so each check looks
// for that link inside the header. The texts come from the published table or, for a term the
// table lacks (most of them in the seed), from lib/glossary-fallback.json, generated from the
// registry: the seeded pages show the registry's real explanations.
import assert from "node:assert/strict";
import { before, describe, test } from "node:test";
import { DATA, fetchPage, serverUp, type Page } from "./dom";

const seedOnly = DATA === "seed";
let up = false;
const pages = new Map<string, Page>();
const PATHS = ["/track-record", "/methodology", "/regression"];

before(
  async () => {
    up = await serverUp();
    if (!up) return;
    for (const p of PATHS) pages.set(p, await fetchPage(p));
  },
  { timeout: 120_000 },
);

/** [page, the header or label's selector, the glossary terms it must explain] */
const HEADERS: [string, string, string[]][] = [
  ["/track-record", "[data-testid=wp-seasons] thead", ["brier", "log_loss"]],
  ["/track-record", "[data-testid=decisions-headline]", ["log_loss", "brier"]],
  ["/track-record", "[data-testid=hot-seat-headline]", ["roc_auc", "brier"]],
  ["/track-record", "[data-testid=board-headline]", ["pr_auc"]],
  ["/track-record", "[data-testid=track-teammate-out] [data-testid=to-candidates-table] thead", ["mae"]],
  ["/track-record", "[data-testid=q-backtest-table] thead", ["log_loss", "brier"]],
  ["/track-record", "[data-testid=lt-summary] thead", ["lead_time"]],
  ["/track-record", "[data-testid=lt-headline]", ["crowd_add", "momentum_baseline"]],
  ["/methodology", "[data-testid=hot-seat-models] thead", ["roc_auc", "pr_auc", "brier"]],
  ["/methodology", "[data-testid=wp-table] thead", ["brier", "log_loss"]],
  ["/methodology", "[data-testid=board-backtest] thead", ["pr_auc", "ppg"]],
  ["/methodology", "[data-testid=rw-position-mae] thead", ["ppg"]],
  ["/methodology", "[data-testid=rw-position-mae] caption", ["mae"]],
  ["/regression", "[data-row=header]", ["games", "ppg", "xfp", "fpoe", "ppg_ros"]],
];

const link = (name: string) => `a[href='/methodology#term-${name}']`;

describe("metric headers are glossary terms", () => {
  test("each metric header and headline label explains its metric", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    for (const [path, sel, terms] of HEADERS) {
      const els = Array.from(pages.get(path)!.doc.querySelectorAll(`main ${sel}`));
      assert.ok(els.length, `${path}: no ${sel}`);
      for (const el of els) {
        for (const name of terms) assert.ok(el.querySelector(link(name)), `${path} ${sel}: no term ${name}`);
        assert.ok(el.querySelector("[role=button][aria-expanded=false]"), `${path} ${sel}: no term button`);
      }
    }
  });

  test("the Regression Watch track record names its error (MAE) as a term", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const mae = pages.get("/track-record")!.doc.querySelector("main [data-testid=rw-mae-table] thead");
    if (!mae) return t.skip("no Regression Watch track record in this database");
    assert.ok(mae.querySelector(link("mae")));
  });

  test("a term the published table lacks is explained from the generated fallback, in real words", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    // the seed's glossary has no roc_auc row: the panel's title is the registry's
    const head = pages.get("/methodology")!.doc.querySelector("main [data-testid=hot-seat-models] thead")!;
    const panel = head.querySelector(link("roc_auc"))!.closest("[role=note]")!;
    assert.match(panel.textContent ?? "", /^ROC-AUC.*0\.5 is no better than guessing/s);
  });
});
