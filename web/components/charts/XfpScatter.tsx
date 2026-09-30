"use client";

import { useSyncExternalStore } from "react";
import { CartesianGrid, Legend, ReferenceLine, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";

export type ScatterPoint = { x: number; y: number; name: string; group: string };

export type ScatterGroup = {
  key: string;
  name: string;
  /** a CSS color, e.g. "var(--chart-a)" */
  color: string;
  shape: "circle" | "triangle" | "diamond" | "square" | "cross";
};

type Props = {
  points: ScatterPoint[];
  groups: ScatterGroup[];
  /** the chart's accessible name and description (the figure's caption says the same) */
  title: string;
  desc: string;
  xLabel: string;
  yLabel: string;
  height?: number;
};

// drawn in the browser only (it needs its width); the server renders a box of the same height
const subscribe = () => () => {};
function useMounted(): boolean {
  return useSyncExternalStore(subscribe, () => true, () => false);
}

/** Points per game against expected points per game, one mark per player, with the diagonal
 *  where points = xFP: above it a player scored more than his chances were worth. */
export default function XfpScatter({ points, groups, title, desc, xLabel, yLabel, height = 360 }: Props) {
  const mounted = useMounted();
  if (!mounted) return <div style={{ height }} aria-hidden="true" />;
  const all = points.flatMap((p) => [p.x, p.y]);
  const hiVal = all.length ? Math.max(...all) : 5;
  const step = hiVal > 30 ? 10 : 5;
  // a little room above the highest mark, and round ticks on both axes
  const top = Math.ceil((hiVal + step / 5) / step) * step;
  const low = all.length ? Math.min(0, Math.floor(Math.min(...all) / step) * step) : 0;
  const ticks = Array.from({ length: Math.round((top - low) / step) + 1 }, (_, i) => low + i * step);
  return (
    <ScatterChart responsive width="100%" height={height} title={title} desc={desc} margin={{ top: 8, right: 12, bottom: 24, left: 4 }}>
      <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 3" />
      <XAxis
        type="number"
        dataKey="x"
        name={xLabel}
        domain={[low, top]}
        ticks={ticks}
        tick={{ fill: "var(--muted)", fontSize: 12 }}
        stroke="var(--border)"
        label={{ value: xLabel, position: "insideBottom", offset: -14, fill: "var(--muted)", fontSize: 12 }}
      />
      <YAxis
        type="number"
        dataKey="y"
        name={yLabel}
        domain={[low, top]}
        ticks={ticks}
        width={48}
        tick={{ fill: "var(--muted)", fontSize: 12 }}
        stroke="var(--border)"
        label={{ value: yLabel, angle: -90, position: "insideLeft", offset: 10, fill: "var(--muted)", fontSize: 12 }}
      />
      <ReferenceLine
        segment={[
          { x: low, y: low },
          { x: top, y: top },
        ]}
        stroke="var(--border-strong)"
        strokeDasharray="6 4"
        label={{ value: "points = xFP", position: "insideTopLeft", fill: "var(--muted)", fontSize: 12 }}
      />
      <Tooltip
        cursor={{ strokeDasharray: "3 3" }}
        content={({ active, payload }) => {
          const p = active ? (payload?.[0]?.payload as ScatterPoint | undefined) : undefined;
          if (!p) return null;
          return (
            <div className="rounded border border-line bg-surface px-2 py-1 text-sm text-fg">
              <strong>{p.name}</strong>
              <br />
              {xLabel}: {p.x.toFixed(1)}
              <br />
              {yLabel}: {p.y.toFixed(1)}
            </div>
          );
        }}
      />
      <Legend verticalAlign="top" itemSorter={null} wrapperStyle={{ color: "var(--fg)", fontSize: 13, paddingBottom: 6 }} />
      {groups.map((g) => (
        <Scatter
          key={g.key}
          name={g.name}
          data={points.filter((p) => p.group === g.key)}
          fill={g.color}
          shape={g.shape}
          legendType={g.shape}
          isAnimationActive={false}
        />
      ))}
    </ScatterChart>
  );
}
