import CalibrationChart from "@/components/charts/CalibrationChart";
import { fmtInt, pct, pctRange } from "@/lib/format";
import { binEdges, calMarks, type CalPoint } from "@/lib/track-record";

type Props = {
  id: string;
  caption: string;
  points: CalPoint[];
  /** what happened, in words ("Really hit", "Really won") */
  observedLabel: string;
  /** a sentence under the caption (who the predictions are, the seasons) */
  children?: React.ReactNode;
};

/** A calibration plot as a <figure>: caption, chart, and the same numbers as a table inside a
 *  <details> (the accessible alternative; in the page even while closed). */
export default function CalibrationFigure({ id, caption, points, observedLabel, children }: Props) {
  const marks = calMarks(points);
  const anyMid = marks.some((m) => m.mid);
  const anyRange = points.some((p) => p.observedLo !== null && p.observedHi !== null);
  const xLabel = anyMid ? "Predicted (group)" : "Average predicted";
  const desc = `${caption}. Each mark is a group of predictions: across, ${anyMid ? "the group's middle" : "the average prediction"}; up, how often it really happened. On the dashed diagonal the two are equal; a bigger mark is a bigger group. The same numbers are in the table below.`;
  return (
    <figure aria-labelledby={`${id}-caption`} className="rounded-lg border border-line bg-surface p-3" data-testid="calibration">
      <figcaption id={`${id}-caption`} className="mb-1 font-display text-lg font-bold tracking-wide uppercase">
        {caption}
      </figcaption>
      {children ? <div className="mb-2 text-sm text-muted">{children}</div> : null}
      {anyMid ? (
        <p className="mb-2 text-sm text-muted" data-testid="calibration-mid">
          The average prediction of each group is not published: each mark sits at the middle of its group.
        </p>
      ) : null}
      <CalibrationChart
        marks={marks.map((m, i) => ({ x: m.x, y: m.y, n: m.n, group: `Predicted ${pctRange(points[i].lo, points[i].hi)}` }))}
        ticks={binEdges(points)}
        title={caption}
        desc={desc}
        xLabel={xLabel}
        yLabel={observedLabel}
      />
      <details className="mt-2 text-sm">
        <summary className="inline-flex min-h-11 items-center font-medium text-accent">Show the numbers as a table</summary>
        <div className="table-scroll mt-2">
          <table className="data-table" data-testid="calibration-table">
            <caption className="sr-only">{caption}</caption>
            <thead>
              <tr>
                <th scope="col">Probability group</th>
                <th scope="col" className="num">
                  Average predicted
                </th>
                <th scope="col" className="num">
                  {observedLabel}
                </th>
                <th scope="col" className="num">
                  Predictions
                </th>
              </tr>
            </thead>
            <tbody>
              {points.map((p) => (
                <tr key={p.key}>
                  <th scope="row" className="tnum">
                    {pctRange(p.lo, p.hi)}
                  </th>
                  <td className="num">{p.predicted !== null ? pct(p.predicted, 1) : "not published"}</td>
                  <td className="num">
                    {pct(p.observed, 1)}
                    {anyRange && p.observedLo !== null && p.observedHi !== null ? (
                      <span className="block text-xs text-muted">{pctRange(p.observedLo, p.observedHi, 1)}</span>
                    ) : null}
                  </td>
                  <td className="num">{p.n !== null ? fmtInt(p.n) : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
