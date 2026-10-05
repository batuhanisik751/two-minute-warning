// Shared pieces of /track-record (server components). The backtest and the live results sit in
// separate panels, framed like the site's lists: reconstructed = dashed, live = solid with a gold
// top edge; the pill says which in words.
import Term from "@/components/Term";
import { KindBadge } from "@/components/ui";

export type Stat = {
  /** what the number is ("Radar's top 10 that hit") */
  label: string;
  /** a glossary term in the label: its first `text` becomes a <Term name={name}> */
  term?: { name: string; text: string };
  /** the number, formatted */
  value: string;
  /** its interval, formatted ("95% interval 46.2–49.5%"), when published */
  interval?: string | null;
  /** a short line under it (lists, seasons) */
  note?: React.ReactNode;
};

/** A tile's label, its metric (when it names one) a glossary term. */
function StatLabel({ s }: { s: Stat }) {
  const i = s.term ? s.label.indexOf(s.term.text) : -1;
  if (!s.term || i < 0) return <>{s.label}</>;
  const end = i + s.term.text.length;
  return (
    <>
      {s.label.slice(0, i)}
      <Term name={s.term.name}>{s.term.text}</Term>
      {s.label.slice(end)}
    </>
  );
}

/** Up to three headline numbers as tiles; one column on a narrow container. The tiles are
 *  measured by the layout check ([data-row], [data-cell]). */
export function StatTiles({ stats, testId }: { stats: Stat[]; testId: string }) {
  return (
    <div className="@container">
      <ul className="grid grid-cols-1 gap-3 @xl:grid-cols-3" data-testid={testId}>
        {stats.map((s) => (
          <li key={s.label} data-row="" className="min-w-0 rounded-md border border-line bg-bg px-3 py-2.5">
            <p data-cell="label" className="text-sm font-medium break-words text-muted">
              <StatLabel s={s} />
            </p>
            <p data-cell="value" className="big-number text-3xl">
              {s.value}
            </p>
            {s.interval ? (
              <p data-cell="interval" className="text-xs break-words text-muted">
                {s.interval}
              </p>
            ) : null}
            {s.note ? (
              <p data-cell="note" className="mt-1 text-xs break-words text-muted">
                {s.note}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A panel of the backtest ("backtest") or of the live results ("live"); `context` names the
 *  module for screen readers when the title alone repeats on the page. */
export function Panel({ kind, title, id, context, children }: { kind: "backtest" | "live"; title: string; id: string; context?: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={id} data-testid={`panel-${kind}`} className={`mt-5 rounded-lg bg-surface p-4 ${kind === "live" ? "frame-live" : "frame-recon"}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <KindBadge kind={kind} />
        <h3 id={id} className="display text-xl uppercase">
          {/* the module's name for screen readers: every region's name is unique on the page */}
          {context ? <span className="sr-only">{context}: </span> : null}
          {title}
        </h3>
      </div>
      <div className="mt-3 space-y-5">{children}</div>
    </section>
  );
}

/** The honest note for what is not published (no new data is made up for it). */
export function NotPublished({ what, children }: { what: string; children?: React.ReactNode }) {
  return (
    <p className="rounded-md border border-dashed border-line px-3 py-2 text-sm text-muted" data-testid="not-published">
      <strong className="font-semibold text-fg">{what}: not published yet.</strong> {children}
    </p>
  );
}

export const H4 = ({ children }: { children: React.ReactNode }) => <h4 className="font-display text-lg font-bold tracking-wide uppercase">{children}</h4>;
