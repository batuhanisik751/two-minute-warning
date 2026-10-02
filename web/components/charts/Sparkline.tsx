type Props = {
  /** the points, by week; values are probabilities 0-1 */
  data: { week: number; value: number }[];
  /** the weeks the axis spans (the season's lists), so every coach's line shares one scale */
  weeks: [number, number];
  /** a reference level drawn as a faint line (e.g. 0.5), or none */
  reference?: number | null;
  width?: number;
  height?: number;
};

const PAD = 3;

/** A tiny line of one coach's weekly estimate (0-100% on every row, the same weeks across), with
 *  no text of its own: the row says the same numbers in words. Hidden from screen readers.
 *  Plain SVG drawn on the server (I5): a charting library for a 128 x 36 line cost every page
 *  with the Hot-Seat list about 100 KB of compressed JavaScript. */
export default function Sparkline({ data, weeks, reference = 0.5, width = 128, height = 36 }: Props) {
  const [lo, hi] = weeks;
  const x = (week: number) => PAD + (hi > lo ? ((week - lo) / (hi - lo)) * (width - 2 * PAD) : (width - 2 * PAD) / 2);
  const y = (v: number) => PAD + (1 - Math.min(1, Math.max(0, v))) * (height - 2 * PAD);
  const pts = [...data].sort((a, b) => a.week - b.week);
  return (
    <svg aria-hidden="true" focusable="false" width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="block">
      {reference !== null ? (
        <line x1={PAD} x2={width - PAD} y1={y(reference)} y2={y(reference)} stroke="var(--chart-grid)" strokeDasharray="2 2" />
      ) : null}
      {pts.length === 1 ? (
        <circle cx={x(pts[0].week)} cy={y(pts[0].value)} r={2.5} fill="var(--chart-a)" />
      ) : (
        <polyline
          points={pts.map((p) => `${x(p.week).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ")}
          fill="none"
          stroke="var(--chart-a)"
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      )}
    </svg>
  );
}
