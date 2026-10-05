// A tooltip's text: the published glossary table's row (lib/queries/glossary.ts), else the
// generated fallback lib/glossary-fallback.json. That file is written from src/twm/registry.py by
// `uv run twm glossary --write` (never by hand; tests/test_registry_site.py fails when it is out
// of date), so a term added to the registry has its tooltip as soon as the site deploys, before
// the next publish refreshes the table. Pure (no database): unit-tested in
// tests/unit/glossary-fallback.test.ts.
import data from "./glossary-fallback.json";
import { DROPPED_TAGS } from "./regression";

/** A term for a tooltip: its glossary name, title, plain-English explanation and formula. */
export type TermEntry = { name: string; title: string; explanation: string; formula?: string };

type TermText = { title: string; explanation: string; formula: string };

/** Every registry entry's tooltip text, by name (generated). */
export const GLOSSARY_FALLBACK: Readonly<Record<string, TermText>> = data.terms;

/** The term `name`: the published row when the table has it, else the fallback's; null for a
 *  name neither has and for a tag the owner removed from the product (DROPPED_TAGS). */
export function resolveTerm(
  rows: readonly TermEntry[],
  name: string,
  fallback: Readonly<Record<string, TermText>> = GLOSSARY_FALLBACK,
): TermEntry | null {
  if (Object.hasOwn(DROPPED_TAGS, name)) return null;
  const row = rows.find((r) => r.name === name);
  if (row) return { name: row.name, title: row.title, explanation: row.explanation, formula: row.formula };
  const text = Object.hasOwn(fallback, name) ? fallback[name] : undefined;
  return text ? { name, title: text.title, explanation: text.explanation, formula: text.formula } : null;
}
