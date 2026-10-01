"use client";

import { useSyncExternalStore } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, Tooltip, XAxis, YAxis } from "recharts";

type Props = {
  /** one row per season: { season, value } (NULL: no value that season) */
  data: { season: number; value: number | null }[];
  kind: "bar" | "line";
  /** a CSS color, e.g. "var(--chart-a)" */
  color: string;
  /** the series' name (tooltip) */
  name: string;
  /** the chart's accessible name and description (the figure's caption says the same) */
  title: string;
  desc: string;
  /** values are shares 0-1 (axis 0-100%); else WP points (the value x 100, one decimal) */
  percent?: boolean;
  height?: number;
};

// Drawn in the browser only (it needs its width); the server renders a box of the same height.
const subscribe = () => () => {};
function useMounted(): boolean {
  return useSyncExternalStore(subscribe, () => true, () => false);
}

/** A small chart of one number per season (the coach page: WP lost per game, aggressiveness). */
export default function SeasonChart({ data, kind, color, name, title, desc, percent = false, height = 200 }: Props) {
  const mounted = useMounted();
  if (!mounted) return <div style={{ height }} aria-hidden="true" />;
  const f = (v: unknown) => (typeof v === "number" ? (percent ? `${Math.round(v * 100)}%` : (v * 100).toFixed(1)) : "no data");
  return (
    <ComposedChart responsive width="100%" height={height} data={data} title={title} desc={desc} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
      <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 3" vertical={false} />
      <XAxis
        dataKey="season"
        tick={{ fill: "var(--muted)", fontSize: 12 }}
        stroke="var(--border)"
        padding={kind === "bar" ? undefined : { left: 16, right: 16 }}
      />
      <YAxis
        tick={{ fill: "var(--muted)", fontSize: 12 }}
        stroke="var(--border)"
        width={40}
        domain={percent ? [0, 1] : [0, "auto"]}
        tickFormatter={(v: number) => (percent ? `${Math.round(v * 100)}%` : (v * 100).toFixed(1))}
      />
      <Tooltip
        formatter={(v) => f(v)}
        labelFormatter={(s) => `Season ${s}`}
        contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", color: "var(--fg)" }}
        labelStyle={{ color: "var(--fg)" }}
      />
      {kind === "bar" ? (
        <Bar dataKey="value" name={name} fill={color} isAnimationActive={false} />
      ) : (
        <Line dataKey="value" name={name} stroke={color} strokeWidth={2} dot={{ r: 3, fill: color }} connectNulls={false} isAnimationActive={false} />
      )}
    </ComposedChart>
  );
}
