"use client";

import { useSyncExternalStore } from "react";
import { Bar, CartesianGrid, ComposedChart, Legend, Line, Tooltip, XAxis, YAxis } from "recharts";

export type Series = {
  key: string;
  name: string;
  kind: "bar" | "line";
  /** a CSS color, e.g. "var(--chart-a)" */
  color: string;
};

type Props = {
  data: Record<string, number | null>[];
  series: Series[];
  /** the chart's accessible name and description (the figure's caption says the same) */
  title: string;
  desc: string;
  percent?: boolean;
  height?: number;
};

// true on the client after hydration, false on the server: the chart is drawn in the
// browser only (it needs its width); the server renders a box of the same height.
const subscribe = () => () => {};
function useMounted(): boolean {
  return useSyncExternalStore(subscribe, () => true, () => false);
}

const fmt = (percent: boolean) => (v: unknown) =>
  typeof v === "number" ? (percent ? `${Math.round(v * 100)}%` : v.toFixed(1)) : "no data";

export default function WeeklyChart({ data, series, title, desc, percent = false, height = 260 }: Props) {
  const mounted = useMounted();
  if (!mounted) return <div style={{ height }} aria-hidden="true" />;
  const f = fmt(percent);
  return (
    <ComposedChart
      responsive
      width="100%"
      height={height}
      data={data}
      title={title}
      desc={desc}
      margin={{ top: 8, right: 8, bottom: 4, left: 0 }}
    >
      <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 3" vertical={false} />
      <XAxis
        dataKey="week"
        tick={{ fill: "var(--muted)", fontSize: 12 }}
        stroke="var(--border)"
        tickFormatter={(w) => `W${w}`}
        // lines only: keep the first and last points off the chart's edges
        padding={series.some((s) => s.kind === "bar") ? undefined : { left: 24, right: 24 }}
      />
      <YAxis
        tick={{ fill: "var(--muted)", fontSize: 12 }}
        stroke="var(--border)"
        width={44}
        domain={percent ? [0, 1] : [0, "auto"]}
        tickFormatter={(v: number) => (percent ? `${Math.round(v * 100)}%` : String(v))}
      />
      <Tooltip
        formatter={(v) => f(v)}
        labelFormatter={(w) => `Week ${w}`}
        contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", color: "var(--fg)" }}
        labelStyle={{ color: "var(--fg)" }}
      />
      <Legend wrapperStyle={{ color: "var(--fg)", fontSize: 13 }} />
      {series.map((s) =>
        s.kind === "bar" ? (
          <Bar key={s.key} dataKey={s.key} name={s.name} fill={s.color} isAnimationActive={false} />
        ) : (
          <Line
            key={s.key}
            dataKey={s.key}
            name={s.name}
            stroke={s.color}
            strokeWidth={2}
            dot={{ r: 3, fill: s.color }}
            connectNulls={false}
            isAnimationActive={false}
          />
        ),
      )}
    </ComposedChart>
  );
}
