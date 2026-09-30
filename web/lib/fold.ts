// Long lists and tables fold (owner decision, 2026-09-30): the first FOLD_AT rows show, the
// rest sit behind a native <details> "Show all N" (components/Fold.tsx; no JavaScript needed).
// Pure helpers, unit-tested in tests/unit/fold.test.ts.

/** How many rows a list or table shows before it folds. */
export const FOLD_AT = 10;

/** The rows shown at first and the folded rest (empty when the list is short enough). */
export function splitFold<T>(rows: readonly T[], at: number = FOLD_AT): { head: T[]; rest: T[] } {
  if (rows.length <= at) return { head: [...rows], rest: [] };
  return { head: rows.slice(0, at), rest: rows.slice(at) };
}

/** The control's words: closed, open, and the accessible context (the list's own label). */
export function foldWords(total: number, at: number = FOLD_AT, label?: string): { more: string; less: string; context: string } {
  return { more: `Show all ${total}`, less: `Show the first ${at} only`, context: label ? ` (${label})` : "" };
}
