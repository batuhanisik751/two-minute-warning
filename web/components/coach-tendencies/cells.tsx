import Term from "@/components/Term";
import { fmtTendency, METRIC_TEXT, percentileText, type TendencyMetric, type TendencyRow } from "@/lib/coach-tendencies";

/** One metric of a coach-team-season: the value, and under it where the offense ranked that
 *  season (pace: "faster than N%"; "not ranked" on a small sample; "not charted" when the metric
 *  has no data that season, e.g. PROE before 2006). */
export function TendencyCell({ metric, row }: { metric: TendencyMetric; row: TendencyRow | undefined }) {
  return (
    <td className="num" data-cell={metric}>
      {row ? (
        <>
          <span className="block">{fmtTendency(metric, row.value)}</span>
          <span className="block text-xs whitespace-nowrap text-muted">{percentileText(metric, row.percentile)}</span>
        </>
      ) : (
        <>
          <span className="block">–</span>
          <span className="block text-xs whitespace-nowrap text-muted">not charted</span>
        </>
      )}
    </td>
  );
}

/** A metric's column header: its short title as a glossary term. */
export function MetricHeader({ metric }: { metric: TendencyMetric }) {
  return (
    <th scope="col" className="num">
      <Term name={metric}>{METRIC_TEXT[metric].short}</Term>
    </th>
  );
}
