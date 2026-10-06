import { HIST_HI, HIST_LO, PRIMARY, SIGNAL_LABELS, histBins, type LTHistRow, type LTSummaryRow, type Signal } from "@/lib/lead-time";

// The lead histogram (feature #8): per lead in weeks, how many of the crowd's adds the Radar's lists
// and the momentum baseline first flagged that early (lead_time_hist, complete seasons; the
// "never" bar is the summary's n_never). Plain CSS bars, hidden from screen readers; the same
// numbers are in the table under it (closed <details>, like the site's other charts).

const SIGNALS: Signal[] = ["listed", "momentum"];
const BAR: Record<string, string> = { listed: "bg-accent", momentum: "bg-muted" };

export default function Histogram({ hist, summary, caption }: { hist: LTHistRow[]; summary: LTSummaryRow[]; caption: string }) {
  const bins = histBins(hist, summary, SIGNALS, PRIMARY);
  const max = Math.max(1, ...bins.flatMap((b) => SIGNALS.map((s) => b.counts[s])));
  return (
    <figure aria-labelledby="lt-hist-caption" className="rounded-lg border border-line bg-surface p-3" data-testid="lt-hist">
      <figcaption id="lt-hist-caption" className="mb-2 font-display text-lg font-bold tracking-wide uppercase">
        {caption}
      </figcaption>
      <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-sm" aria-hidden="true">
        {SIGNALS.map((s) => (
          <li key={s} className="flex items-center gap-1.5">
            <span className={`inline-block size-3 rounded-sm ${BAR[s]}`} />
            {SIGNAL_LABELS[s]}
          </li>
        ))}
      </ul>
      <div className="overflow-x-auto" aria-hidden="true">
        <div className="flex h-40 min-w-[30rem] items-end gap-1 border-b border-line">
          {bins.map((b) => (
            <div key={b.label} className={`flex h-full min-w-0 flex-1 items-end justify-center gap-px ${b.lead === null ? "ml-2" : ""}`}>
              {SIGNALS.map((s) => (
                <div key={s} className={`w-1/2 rounded-t-sm ${BAR[s]}`} style={{ height: `${(100 * b.counts[s]) / max}%` }} title={`${b.label}: ${b.counts[s]}`} />
              ))}
            </div>
          ))}
        </div>
        <div className="flex min-w-[30rem] gap-1 pt-1 text-[0.65rem] text-muted">
          {bins.map((b) => (
            <span key={b.label} className={`min-w-0 flex-1 text-center ${b.lead === null ? "ml-2" : ""}`}>
              {b.lead === null ? "never" : b.lead > 0 ? `+${b.lead}` : b.lead}
            </span>
          ))}
        </div>
      </div>
      <p className="mt-1 text-xs text-muted">Weeks the flag came before the crowd (+) or after it (−); the ends are clipped at {HIST_HI} before and {-HIST_LO} after.</p>
      <details className="mt-2 text-sm">
        <summary className="inline-flex min-h-11 items-center font-medium text-accent">Show the numbers as a table</summary>
        <div className="table-scroll mt-2">
          <table className="data-table" data-testid="lt-hist-table">
            <caption className="sr-only">{caption}</caption>
            <thead>
              <tr>
                <th scope="col">Flagged</th>
                {SIGNALS.map((s) => (
                  <th key={s} scope="col" className="num">
                    {SIGNAL_LABELS[s]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bins.map((b) => (
                <tr key={b.label}>
                  <th scope="row">{b.label}</th>
                  {SIGNALS.map((s) => (
                    <td key={s} className="num">
                      {b.counts[s]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
