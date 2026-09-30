import XfpScatter, { type ScatterGroup, type ScatterPoint } from "./XfpScatter";

export type ScatterRow = ScatterPoint & { id: string; position: string; team: string; extra: string; groupName: string };

/** The xFP-against-points scatter as a <figure>: caption, chart, and the same numbers as a
 *  table inside a <details> (the accessible alternative; in the page even while closed). */
export default function ScatterFigure({
  id,
  caption,
  rows,
  groups,
  xLabel,
  yLabel,
  extraLabel,
}: {
  id: string;
  caption: string;
  rows: ScatterRow[];
  groups: ScatterGroup[];
  xLabel: string;
  yLabel: string;
  extraLabel: string;
}) {
  const points: ScatterPoint[] = rows.map(({ x, y, name, group }) => ({ x, y, name, group }));
  return (
    <figure aria-labelledby={`${id}-caption`} className="rounded-lg border border-line bg-surface p-3" data-testid="scatter">
      <figcaption id={`${id}-caption`} className="mb-2 font-display text-lg font-bold tracking-wide uppercase">
        {caption}
      </figcaption>
      <XfpScatter
        points={points}
        groups={groups}
        title={caption}
        desc={`${caption}. The dashed diagonal is where points equal xFP. The same numbers are in the table below.`}
        xLabel={xLabel}
        yLabel={yLabel}
      />
      <details className="mt-2 text-sm">
        <summary className="inline-flex min-h-11 items-center font-medium text-accent">Show the numbers as a table</summary>
        <div className="table-scroll mt-2">
          <table className="data-table" data-testid="scatter-table">
            <caption className="sr-only">{caption}</caption>
            <thead>
              <tr>
                <th scope="col">Player</th>
                <th scope="col">Pos.</th>
                <th scope="col">Team</th>
                <th scope="col" className="num">
                  {xLabel}
                </th>
                <th scope="col" className="num">
                  {yLabel}
                </th>
                <th scope="col" className="num">
                  {extraLabel}
                </th>
                <th scope="col">Tag</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <th scope="row">{r.name}</th>
                  <td>{r.position}</td>
                  <td>{r.team}</td>
                  <td className="num">{r.x.toFixed(1)}</td>
                  <td className="num">{r.y.toFixed(1)}</td>
                  <td className="num">{r.extra}</td>
                  <td>{r.groupName}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
