// Shared plumbing for the smoke and accessibility tests: they read the RENDERED pages of a
// running app (`next start` on a seeded database, started by tests/run-smoke.ts), over HTTP,
// parsed with jsdom. Without a server they skip loudly; with SMOKE_REQUIRE=1 (CI, and the
// runner) a missing server is a failure, never a skip.
import { JSDOM } from "jsdom";

export const BASE = process.env.SMOKE_BASE_URL ?? "http://localhost:3000";
export const EMPTY_BASE = process.env.SMOKE_EMPTY_BASE_URL ?? null;
export const REQUIRE = process.env.SMOKE_REQUIRE === "1";
/** "seed" (tests/seed.ts, exact assertions) or "real" (a real publish: only assertions that
 *  hold for any data). */
export const DATA = process.env.SMOKE_DATA === "real" ? "real" : "seed";

export async function serverUp(base: string = BASE): Promise<boolean> {
  let up = false;
  let why = "";
  try {
    const r = await fetch(`${base}/robots.txt`, { signal: AbortSignal.timeout(8000) });
    up = r.ok;
    if (!up) why = `${base}/robots.txt returned ${r.status}`;
  } catch (err) {
    why = err instanceof Error ? err.message : String(err);
  }
  if (!up && REQUIRE) {
    throw new Error(
      `SMOKE_REQUIRE=1 and no server is answering at ${base} (${why}). ` +
        "Refusing to skip: a suite that cannot reach the app has not tested it.",
    );
  }
  if (!up) {
    console.warn(
      `\n  SKIPPING: no server at ${base} (${why}).\n  Run \`npm run build && npm run test:smoke:run\` ` +
        "to start a seeded server and run these tests.\n",
    );
  }
  return up;
}

export type Page = { status: number; html: string; doc: Document; dom: JSDOM };

export async function fetchPage(route: string, base: string = BASE): Promise<Page> {
  const r = await fetch(base + route, { signal: AbortSignal.timeout(60_000), redirect: "manual" });
  const html = await r.text();
  const dom = parse(html, base);
  return { status: r.status, html, doc: dom.window.document, dom };
}

export function parse(html: string, base: string = BASE): JSDOM {
  // "outside-only" does NOT run the page's scripts: it only gives a window to evaluate axe
  // in. The audit is of the server render, which must work before any JavaScript arrives.
  const dom = new JSDOM(html, { url: base, pretendToBeVisual: true, runScripts: "outside-only" });
  settleStreamedBoundaries(dom.window.document);
  return dom;
}

/** A loading.tsx boundary makes the server STREAM: the fallback first, the page later as
 *  <div hidden id="S:n"> plus React's inline swap script. Nothing here runs scripts, so the
 *  swap is replayed by hand (the F1 app's approach), or the audit would be of the spinner. */
export function settleStreamedBoundaries(doc: Document): void {
  for (let pass = 0; pass < 8; pass++) {
    const segs = Array.from(doc.querySelectorAll<HTMLTemplateElement>("template[id^='P:']"));
    let moved = 0;
    for (const tpl of segs) {
      const content = doc.getElementById("S:" + tpl.id.slice(2));
      const parent = tpl.parentNode;
      if (!content || !parent) continue;
      while (content.firstChild) parent.insertBefore(content.firstChild, tpl);
      parent.removeChild(tpl);
      let wrapper: Node | null = content.parentNode;
      content.remove();
      while (wrapper && wrapper !== doc.body && wrapper.childNodes.length === 0) {
        const up: Node | null = wrapper.parentNode;
        (wrapper as ChildNode).remove();
        wrapper = up;
      }
      moved++;
    }
    if (moved === 0) break;
  }
  for (const tpl of Array.from(doc.querySelectorAll<HTMLTemplateElement>("template[id^='B:']"))) {
    const content = doc.getElementById("S:" + tpl.id.slice(2));
    const parent = tpl.parentNode;
    if (!content || !parent) continue;
    let depth = 0;
    let node: Node | null = tpl.nextSibling;
    while (node) {
      const next: Node | null = node.nextSibling;
      if (node.nodeType === 8) {
        const v = node.nodeValue ?? "";
        if (v === "/$") {
          if (depth === 0) break;
          depth--;
        } else if (v === "$" || v === "$?" || v === "$!") depth++;
      }
      parent.removeChild(node);
      node = next;
    }
    while (content.firstChild) parent.insertBefore(content.firstChild, node);
    parent.removeChild(tpl);
    content.remove();
  }
}

export const normalise = (s: string): string => s.replace(/\s+/g, " ").trim();

/** normalise() plus no space before closing punctuation or after an opening bracket (text
 *  nodes are joined with spaces, so "(<b>x</b>)" would read "( x )"). */
export const tidy = (s: string): string =>
  normalise(s).replace(/\s+([),.;:!?])/g, "$1").replace(/([(])\s+/g, "$1");

/** Every word under `root`, text nodes joined with spaces (hidden tooltips included). */
export function text(root: Element | Document): string {
  const el = "body" in root ? root.body : root;
  const clone = el.cloneNode(true) as HTMLElement;
  for (const x of Array.from(clone.querySelectorAll("script,style,noscript,template"))) x.remove();
  const parts: string[] = [];
  const walk = clone.ownerDocument.createTreeWalker(clone, 4);
  let n: Node | null;
  while ((n = walk.nextNode())) parts.push(n.nodeValue ?? "");
  return tidy(parts.join(" "));
}

/** Text without the tooltips' hidden panels (what a reader sees before opening one). */
export function visibleText(root: Element | Document): string {
  const el = "body" in root ? root.body : root;
  const clone = el.cloneNode(true) as HTMLElement;
  for (const x of Array.from(clone.querySelectorAll("[hidden],script,style,noscript,template"))) x.remove();
  return text(clone);
}

/** The sentence a sighted reader reads: no closed tooltip panels, no screen-reader-only
 *  hints (such as a term button's "(explain)"). */
export function prose(root: Element | Document): string {
  const el = "body" in root ? root.body : root;
  const clone = el.cloneNode(true) as HTMLElement;
  for (const x of Array.from(clone.querySelectorAll("[hidden],.sr-only,script,style,noscript,template"))) x.remove();
  return text(clone);
}
