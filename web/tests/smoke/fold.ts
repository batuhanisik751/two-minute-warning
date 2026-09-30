// The fold rule on a rendered page (components/Fold.tsx, lib/fold.ts): no ranked list (<ol>)
// or table body shows more than FOLD_AT rows before a native "Show all N" <details>, and every
// such control counts its rows right. Charts' data tables are folded whole already (inside
// their own <details>), and the glossary and navigation are not lists of rows: not checked.
import { FOLD_AT } from "../../lib/fold";
import { normalise } from "./dom";

export type Fold = { what: string; head: number; rest: number; total: number; summary: string };

const name = (el: Element) => el.getAttribute("aria-label") ?? el.closest("[data-testid]")?.getAttribute("data-testid") ?? el.tagName;

function checkFold(what: string, head: number, rest: number, fold: Element, folds: Fold[], problems: string[]) {
  const total = Number(fold.getAttribute("data-total"));
  const summary = normalise(fold.querySelector(":scope > summary")?.textContent ?? "");
  folds.push({ what, head, rest, total, summary });
  if (head !== FOLD_AT) problems.push(`${what}: ${head} rows before the fold, not ${FOLD_AT}`);
  if (head + rest !== total) problems.push(`${what}: ${head} + ${rest} rows, but the control says ${total}`);
  if (!summary.startsWith(`Show all ${total}`)) problems.push(`${what}: the control says "${summary}"`);
  if (fold.hasAttribute("open")) problems.push(`${what}: open on arrival`);
}

export function foldCheck(doc: Document): { folds: Fold[]; problems: string[] } {
  const root = doc.querySelector("main#main") ?? doc.body;
  const folds: Fold[] = [];
  const problems: string[] = [];
  for (const ol of Array.from(root.querySelectorAll("ol"))) {
    if (ol.hasAttribute("data-fold-rest")) continue;
    const head = ol.querySelectorAll(":scope > li").length;
    const next = ol.nextElementSibling;
    if (next?.matches("details[data-fold]")) {
      const rest = next.querySelector(":scope > ol[data-fold-rest]");
      if (!rest) problems.push(`${name(ol)}: a fold without its rows`);
      else if (rest.getAttribute("start") !== String(head + 1)) problems.push(`${name(ol)}: the folded rows do not continue the list`);
      checkFold(name(ol), head, rest ? rest.querySelectorAll(":scope > li").length : 0, next, folds, problems);
    } else if (head > FOLD_AT) problems.push(`${name(ol)}: ${head} rows and no fold`);
  }
  for (const body of Array.from(root.querySelectorAll("tbody:not([data-fold-rest])"))) {
    const table = body.closest("table")!;
    if (table.closest("details")) continue;
    const head = body.querySelectorAll(":scope > tr").length;
    const rest = table.querySelector(":scope > tbody[data-fold-rest]");
    const fold = table.closest(".fold-table")?.querySelector(":scope > details[data-fold]");
    if (rest && fold) checkFold(name(table), head, rest.querySelectorAll(":scope > tr").length, fold, folds, problems);
    else if (rest || fold) problems.push(`${name(table)}: half a fold`);
    else if (head > FOLD_AT) problems.push(`${name(table)}: ${head} rows and no fold`);
  }
  return { folds, problems };
}
