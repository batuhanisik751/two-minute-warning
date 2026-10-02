import Link from "next/link";
import Sparkline from "@/components/charts/Sparkline";
import { FoldList } from "@/components/Fold";
import { driverEffect, driverValue, recordWords, signedNum, tenureWords, timelineWords, type TimelinePoint } from "@/lib/hot-seat";
import type { HotSeatEntry } from "@/lib/queries/hot-seat";
import { EstimateCell, InterimFlag, OutcomeTag } from "./parts";

type Props = {
  rows: HotSeatEntry[];
  /** each coach's weekly estimates this season (lib/hot-seat mergeTimeline) */
  timelines: Map<string, TimelinePoint[]>;
  /** the season's first and last list week (one scale for every sparkline) */
  weeks: [number, number];
  /** the list's accessible name, e.g. "Hot-Seat Meter, 2026 week 3, reconstructed" */
  label: string;
  /** show what really happened (final) or "Pending" */
  showOutcome?: boolean;
  /** the time machine's compact form: who, the estimate and what happened (no key numbers,
   *  drivers or timeline) */
  compact?: boolean;
};

/** One row per head coach, by estimated chance: who, the key numbers, the three drivers in plain
 *  words, the season's timeline and (when known) what happened. Rows are measured by the layout
 *  check ([data-row], [data-cell]) and lay out by their container; the list folds after 10. */
export default function HotSeatList({ rows, timelines, weeks, label, showOutcome = true, compact = false }: Props) {
  return (
    <div className="@container">
      <FoldList
        label={label}
        className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface"
        testId="hot-seat-list"
        items={rows.map((r) => (
          <Row key={r.coachId} r={r} points={timelines.get(r.coachId) ?? []} weeks={weeks} showOutcome={showOutcome} compact={compact} />
        ))}
      />
    </div>
  );
}

function KeyNumbers({ r }: { r: HotSeatEntry }) {
  const parts: React.ReactNode[] = [
    <>
      Record <span className="tnum font-semibold">{recordWords(r.regWins, r.regGamesPlayed)}</span>
      {r.expectedWins !== null && r.winsVsExpected !== null ? (
        <>
          {" "}
          (the market expected <span className="tnum">{r.expectedWins.toFixed(1)}</span> wins:{" "}
          <span className="tnum font-semibold">{signedNum(r.winsVsExpected)}</span>)
        </>
      ) : null}
    </>,
  ];
  if (r.pointDiffPerGame !== null) parts.push(<>Point differential <span className="tnum font-semibold">{signedNum(r.pointDiffPerGame)}</span> per game</>);
  parts.push(<>{tenureWords(r.tenureSeasons)}</>);
  return (
    <p className="text-sm" data-testid="key-numbers">
      {parts.map((p, i) => (
        <span key={i}>
          {i ? " · " : ""}
          {p}
        </span>
      ))}
    </p>
  );
}

function Row({ r, points, weeks, showOutcome, compact }: { r: HotSeatEntry; points: TimelinePoint[]; weeks: [number, number]; showOutcome: boolean; compact: boolean }) {
  return (
    <li
      data-row=""
      data-testid="hot-seat-row"
      data-coach={r.coachId}
      data-interim={r.isInterim ? "" : undefined}
      className="grid grid-cols-[2.25rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 px-3 py-3 @2xl:grid-cols-[2.5rem_minmax(0,1fr)_9rem]"
    >
      <span data-cell="rank" aria-hidden="true" className="big-number col-start-1 row-start-1 text-2xl text-muted">
        {r.rank}
      </span>
      <div data-cell="coach" className="col-start-2 row-start-1 min-w-0 space-y-1.5">
        <p className="font-semibold break-words">
          <Link href={`/coach/${r.coachId}`}>{r.name}</Link>
          <span className="font-normal text-muted"> · {r.teamName ?? r.team}</span>
        </p>
        {r.isInterim ? <InterimFlag /> : null}
      </div>
      {/* narrow: the estimate right under the name; wide: in its own column */}
      <div data-cell="estimate" className="col-start-2 row-start-2 max-w-56 @2xl:max-w-none @2xl:col-start-3 @2xl:row-span-2 @2xl:row-start-1 @2xl:text-right">
        <EstimateCell probability={r.probability} />
      </div>
      <div data-cell="details" className="col-start-2 row-start-3 min-w-0 space-y-1.5 @2xl:row-start-2">
        {compact ? null : <KeyNumbers r={r} />}
        {!compact && r.drivers.length ? (
          <ul aria-label={`What moves ${r.name}'s estimate most`} className="space-y-0.5 text-sm" data-testid="drivers">
            {r.drivers.map((d) => (
              <li key={d.feature} className="break-words">
                <span className="font-medium">{d.label.replace(/ \(missing\)$/, "")}</span>: {driverValue(d)}{" "}
                <span className="text-muted">({driverEffect(d)})</span>
              </li>
            ))}
          </ul>
        ) : null}
        {compact ? null : (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
            {points.length ? <Sparkline data={points.map((p) => ({ week: p.week, value: p.probability }))} weeks={weeks} /> : null}
            <span className="min-w-0 text-muted" data-testid="timeline-words">
              This season: {timelineWords(points)}
            </span>
          </div>
        )}
        {showOutcome ? (
          <p className="text-sm" data-testid="outcome">
            <span className="font-medium">What happened: </span>
            <OutcomeTag outcome={r.outcome} long />
          </p>
        ) : null}
      </div>
    </li>
  );
}
