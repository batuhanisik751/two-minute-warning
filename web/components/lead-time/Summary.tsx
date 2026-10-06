import Term from "@/components/Term";
import { StatTiles } from "@/components/track/parts";
import { fmtInt } from "@/lib/format";
import { PRIMARY, SIGNAL_LABELS, pooled, pooledRow, share, weeks, type LTSummaryRow, type Signal } from "@/lib/lead-time";

// Lead time vs the crowd: the headline tiles and the summary table (feature #8). Every number is a
// published lead_time_summary row (complete seasons pooled); "–" = fewer than 5 adds.

const ahead = (r: LTSummaryRow | undefined) => (r?.leadMedian === null || r?.leadMedian === undefined ? null : `median ${weeks(r.leadMedian)} ${Math.abs(r.leadMedian) === 1 ? "week" : "weeks"} ahead`);

/** Three tiles: the Radar's lists, its must-adds and the momentum baseline, flagged before the crowd. */
export function Headline({ summary, span }: { summary: LTSummaryRow[]; span: string | null }) {
  const row = (s: Signal) => pooledRow(summary, s);
  const listed = row("listed");
  const must = row("must_add");
  const mom = row("momentum");
  const n = listed?.nAdds ?? must?.nAdds ?? mom?.nAdds ?? 0;
  return (
    <StatTiles
      testId="lt-headline"
      stats={[
        { label: "Crowd adds the Radar listed first", term: { name: "crowd_add", text: "Crowd adds" }, value: share(listed?.shareBefore), note: [ahead(listed), `${fmtInt(n)} adds${span ? `, ${span}` : ""}`].filter(Boolean).join("; ") },
        { label: "Flagged as must-adds first", value: share(must?.shareBefore), note: must?.shareNever !== null && must?.shareNever !== undefined ? `never a must-add: ${share(must.shareNever)}` : null },
        { label: "Momentum baseline first", term: { name: "momentum_baseline", text: "Momentum baseline" }, value: share(mom?.shareBefore), note: ahead(mom) },
      ]}
    />
  );
}

/** Before / same week / after / never per signal, with the lead's median and quartiles. */
export function SummaryTable({ summary, threshold = PRIMARY, testId = "lt-summary", caption }: { summary: LTSummaryRow[]; threshold?: number; testId?: string; caption: string }) {
  const rows = pooled(summary, threshold);
  return (
    <div className="table-scroll">
      <table className="data-table" data-testid={testId}>
        <caption className="text-left text-sm text-muted">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Signal</th>
            <th scope="col" className="num">Before</th>
            <th scope="col" className="num">Same week</th>
            <th scope="col" className="num">After</th>
            <th scope="col" className="num">Never</th>
            <th scope="col" className="num">
              <Term name="lead_time">Lead</Term>, median (middle half)
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.signal} data-row="" data-signal={r.signal}>
              <th scope="row">{SIGNAL_LABELS[r.signal as Signal]}</th>
              <td className="num">{share(r.shareBefore)}</td>
              <td className="num">{share(r.shareSame)}</td>
              <td className="num">{share(r.shareAfter)}</td>
              <td className="num">{share(r.shareNever)}</td>
              <td className="num">{r.leadMedian === null ? "–" : `${weeks(r.leadMedian)} (${weeks(r.leadQ1)} to ${weeks(r.leadQ3)})`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
