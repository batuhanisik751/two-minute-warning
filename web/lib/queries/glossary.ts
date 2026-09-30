import "server-only";
import { asc } from "drizzle-orm";
import { db } from "@/db/client";
import { glossary } from "@/db/schema";
import { cached } from "@/lib/cache";
import { SITE_TERMS, type TermEntry } from "@/lib/site-terms";
import { DROPPED_TAGS } from "@/lib/regression";

export type GlossaryRow = {
  name: string;
  title: string;
  kind: string;
  unit: string;
  formula: string;
  explanation: string;
  verified: string | null;
  modules: string[];
  modelOutput: boolean;
};

async function getGlossaryRaw(): Promise<GlossaryRow[]> {
  return db()
    .select({
      name: glossary.name,
      title: glossary.title,
      kind: glossary.kind,
      unit: glossary.unit,
      formula: glossary.formula,
      explanation: glossary.explanation,
      verified: glossary.verified,
      modules: glossary.modules,
      modelOutput: glossary.modelOutput,
    })
    .from(glossary)
    .orderBy(asc(glossary.kind), asc(glossary.name))
    // tags the owner removed from the product (2026-09-30) are not shown, not even as terms
    .then((rows) => rows.filter((r) => !Object.hasOwn(DROPPED_TAGS, r.name)));
}

export const getGlossary = cached("glossary.getGlossary", getGlossaryRaw);

/** A term for a tooltip: the glossary table's entry, else the site's own (lib/site-terms). */
export async function lookupTerm(name: string): Promise<TermEntry | null> {
  const rows = await getGlossary();
  const row = rows.find((r) => r.name === name);
  if (row) return { name: row.name, title: row.title, explanation: row.explanation, formula: row.formula };
  return SITE_TERMS[name] ?? null;
}
