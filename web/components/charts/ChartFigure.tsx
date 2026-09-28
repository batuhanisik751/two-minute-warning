import WeeklyChart, { type Series } from "./WeeklyChart";

export type Column = {
  key: string;
  label: string;
  format: (v: number | null) => string;
};

/** A chart as a <figure>: the caption, the chart, and the same numbers as a table inside a
 *  <details> (the accessible alternative; it is in the page even while closed). */
export default function ChartFigure({
  id,
  caption,
  data,
  series,
  columns,
  percent = false,
}: {
  id: string;
  caption: string;
  data: Record<string, number | null>[];
  series: Series[];
  columns: Column[];
  percent?: boolean;
}) {
  return (
    <figure aria-labelledby={`${id}-caption`} className="rounded-lg border border-line bg-surface p-3" data-testid="chart">
      <figcaption id={`${id}-caption`} className="mb-2 text-sm font-semibold">
        {caption}
      </figcaption>
      <WeeklyChart data={data} series={series} title={caption} desc={`${caption}. The same numbers are in the table below.`} percent={percent} />
      <details className="mt-2 text-sm">
        <summary className="inline-flex min-h-11 items-center font-medium text-accent">Show the numbers as a table</summary>
        <div className="table-scroll mt-2">
          <table className="data-table">
            <caption className="sr-only">{caption}</caption>
            <thead>
              <tr>
                <th scope="col">Week</th>
                {columns.map((c) => (
                  <th key={c.key} scope="col" className="num">
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.map((row) => (
                <tr key={String(row.week)}>
                  <th scope="row">{row.week}</th>
                  {columns.map((c) => (
                    <td key={c.key} className="num">
                      {c.format(row[c.key] ?? null)}
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
