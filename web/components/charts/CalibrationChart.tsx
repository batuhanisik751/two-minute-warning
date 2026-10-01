"use client";

import { useSyncExternalStore } from "react";
import { CartesianGrid, ReferenceLine, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { fmtInt, pct } from "@/lib/format";

export type CalMark = { x: number; y: number; group: string; n: number | null };

type Props = {
  marks: CalMark[];
  /** the probability groups' edges (from the published bins): the axes' ticks and range */
  ticks: number[];
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

const asPct = (v: number) => pct(v);

/** A calibration plot: one mark per probability group, the prediction across and how often it
 *  really happened up; on the dashed diagonal the two are equal. A mark's size follows its
 *  group's number of predictions. */
export default function CalibrationChart({ marks, ticks, title, desc, xLabel, yLabel, height = 320 }: Props) {
  const mounted = useMounted();
  if (!mounted) return <div style={{ height }} aria-hidden="true" />;
  const lo = ticks[0];
  const hi = ticks[ticks.length - 1];
  return (
    <ScatterChart responsive width="100%" height={height} title={title} desc={desc} margin={{ top: 8, right: 12, bottom: 24, left: 4 }}>
      <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 3" />
      <XAxis
        type="number"
        dataKey="x"
        name={xLabel}
        domain={[lo, hi]}
        ticks={ticks}
        tickFormatter={asPct}
        tick={{ fill: "var(--muted)", fontSize: 12 }}
        stroke="var(--border)"
        label={{ value: xLabel, position: "insideBottom", offset: -14, fill: "var(--muted)", fontSize: 12 }}
      />
      <YAxis
        type="number"
        dataKey="y"
        name={yLabel}
        domain={[lo, hi]}
        ticks={ticks}
        tickFormatter={asPct}
        width={64}
        tick={{ fill: "var(--muted)", fontSize: 12 }}
        stroke="var(--border)"
        label={{ value: yLabel, angle: -90, position: "insideLeft", offset: 10, fill: "var(--muted)", fontSize: 12 }}
      />
      {/* a mark's area grows with its group's predictions: a small group is a small, noisy mark */}
      <ZAxis type="number" dataKey="z" range={[20, 260]} />
      <ReferenceLine
        segment={[
          { x: lo, y: lo },
          { x: hi, y: hi },
        ]}
        stroke="var(--border-strong)"
        strokeDasharray="6 4"
      />
      <Tooltip
        cursor={{ strokeDasharray: "3 3" }}
        content={({ active, payload }) => {
          const p = active ? (payload?.[0]?.payload as CalMark | undefined) : undefined;
          if (!p) return null;
          return (
            <div className="rounded border border-line bg-surface px-2 py-1 text-sm text-fg">
              <strong>{p.group}</strong>
              <br />
              {xLabel}: {pct(p.x, 1)}
              <br />
              {yLabel}: {pct(p.y, 1)}
              {p.n !== null ? (
                <>
                  <br />
                  {fmtInt(p.n)} predictions
                </>
              ) : null}
            </div>
          );
        }}
      />
      <Scatter data={marks.map((m) => ({ ...m, z: m.n ?? 1 }))} fill="var(--chart-a)" shape="circle" isAnimationActive={false} />
    </ScatterChart>
  );
}
