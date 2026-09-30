import type { ReactNode } from "react";
import { FOLD_AT, foldWords, splitFold } from "@/lib/fold";

// Long lists and tables fold after FOLD_AT rows (lib/fold.ts) behind a native <details>
// "Show all N": no JavaScript, the summary is a keyboard control, and the words say what
// opening it does. The layout check (tests/smoke/layout.test.ts) opens every one of them.

/** The "Show all N" control; it says "Show the first 10 only" while open (globals.css .fold). */
export function FoldSummary({ total, label }: { total: number; label?: string }) {
  const w = foldWords(total, FOLD_AT, label);
  return (
    <summary
      className="inline-flex min-h-11 items-center rounded-md border border-line bg-surface px-3 font-medium text-accent"
      data-testid="fold-summary"
    >
      <span className="fold-more">{w.more}</span>{" "}
      <span className="fold-less">{w.less}</span>
      {w.context ? <span className="sr-only">{w.context}</span> : null}
    </summary>
  );
}

type ListProps = {
  /** the rendered <li> rows, in order */
  items: ReactNode[];
  /** the list's accessible name, e.g. "K list, 2026 week 3 (live)" */
  label: string;
  className: string;
  testId: string;
};

/** An ordered list that folds: its first rows, then a <details> holding the rest as the same
 *  list continued (start = 11), so a screen reader meets the new rows right after the control. */
export function FoldList({ items, label, className, testId }: ListProps) {
  const { head, rest } = splitFold(items);
  return (
    <>
      <ol aria-label={label} className={className} data-testid={testId}>
        {head}
      </ol>
      {rest.length ? (
        <details className="fold mt-2" data-fold="" data-total={items.length}>
          <FoldSummary total={items.length} label={label} />
          <ol
            start={head.length + 1}
            aria-label={`${label}, ${head.length + 1} to ${items.length}`}
            className={`${className} mt-2`}
            data-testid={testId}
            data-fold-rest=""
          >
            {rest}
          </ol>
        </details>
      ) : null}
    </>
  );
}

/** A table whose body folds: `rows` are its <tr>s. One table (the columns stay aligned); the rows
 *  after the first 10 are a second <tbody data-fold-rest>, hidden while the <details> under the
 *  table is closed (globals.css .fold-table, CSS only). `table` receives the body to render. */
export function FoldTable({ rows, label, table }: { rows: ReactNode[]; label: string; table: (body: ReactNode) => ReactNode }) {
  const { head, rest } = splitFold(rows);
  const body = (
    <>
      <tbody>{head}</tbody>
      {rest.length ? <tbody data-fold-rest="">{rest}</tbody> : null}
    </>
  );
  return (
    <div className="fold-table">
      {table(body)}
      {rest.length ? (
        <details className="fold mt-2" data-fold="" data-total={rows.length}>
          <FoldSummary total={rows.length} label={label} />
        </details>
      ) : null}
    </div>
  );
}
