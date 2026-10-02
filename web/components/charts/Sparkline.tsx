"use client";

import { useSyncExternalStore } from "react";
import { Line, LineChart, ReferenceLine, XAxis, YAxis } from "recharts";

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

// Drawn in the browser only (as the other charts); the server renders a box of the same size.
const subscribe = () => () => {};
function useMounted(): boolean {
  return useSyncExternalStore(subscribe, () => true, () => false);
}

/** A tiny line of one coach's weekly estimate (0-100% on every row, the same weeks across), with
 *  no text of its own: the row says the same numbers in words. Hidden from screen readers. */
export default function Sparkline({ data, weeks, reference = 0.5, width = 128, height = 36 }: Props) {
  const mounted = useMounted();
  if (!mounted) return <div style={{ width, height }} aria-hidden="true" />;
  const one = data.length === 1;
  return (
    <div aria-hidden="true" style={{ width, height }}>
      <LineChart width={width} height={height} data={data} margin={{ top: 3, right: 3, bottom: 3, left: 3 }}>
        <XAxis dataKey="week" type="number" domain={weeks} hide />
        <YAxis domain={[0, 1]} hide />
        {reference !== null ? <ReferenceLine y={reference} stroke="var(--chart-grid)" strokeDasharray="2 2" /> : null}
        <Line dataKey="value" stroke="var(--chart-a)" strokeWidth={2} dot={one ? { r: 2.5, fill: "var(--chart-a)" } : false} isAnimationActive={false} />
      </LineChart>
    </div>
  );
}
