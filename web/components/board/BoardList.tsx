import Link from "next/link";
import { FoldList } from "@/components/Fold";
import { EstimateCell } from "@/components/hot-seat/parts";
import { PosBadge } from "@/components/ui";
import { boardDriverValue, driverEffect, posRank, type Disagree, type Driver } from "@/lib/board";
import type { BoardEntry } from "@/lib/queries/board";
import { BoardOutcomeTag, DisagreeTag } from "./parts";

type Props = {
  rows: BoardEntry[];
  /** the "where we disagree" markers of the whole board (lib/board disagreements) */
  marks: Map<string, Disagree>;
  /** whether this board has the experts' ranks (2020 on) */
  ecr: boolean;
  /** the list's accessible name, e.g. "Cliff board 2026, reconstructed" */
  label: string;
  /** show what really happened (final) or "Pending" */
  showOutcome: boolean;
};

/** One row per player, by the Cliff chance: who, the two chances side by side (never added), the
 *  experts' rank, last season, the drivers in plain words and (when known) what happened. Rows are
 *  measured by the layout check ([data-row], [data-cell]) and lay out by their container; the
 *  list folds after 10. */
export default function BoardList({ rows, marks, ecr, label, showOutcome }: Props) {
  return (
    <div className="@container">
      <FoldList
        label={label}
        className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface"
        testId="board-list"
        items={rows.map((r) => (
          <Row key={r.gsisId} r={r} mark={marks.get(r.gsisId) ?? null} ecr={ecr} showOutcome={showOutcome} />
        ))}
      />
    </div>
  );
}

function Drivers({ title, drivers, who }: { title: string; drivers: Driver[]; who: string }) {
  if (!drivers.length) return null;
  return (
    <div className="text-sm">
      <p className="font-medium">{title}</p>
      <ul aria-label={`${title}, ${who}`} className="space-y-0.5" data-testid="drivers">
        {drivers.map((d) => (
          <li key={d.feature} className="break-words">
            <span className="font-medium">{d.label.replace(/ \(missing\)$/, "")}</span>: {boardDriverValue(d)}{" "}
            <span className="text-muted">({driverEffect(d)})</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function LastSeason({ r, ecr }: { r: BoardEntry; ecr: boolean }) {
  const parts: React.ReactNode[] = [
    <>
      Last season, with the {r.teamName ?? r.team}: <span className="tnum font-semibold">{r.ppgS.toFixed(1)}</span> points per game ({posRank(r.position, r.posRankS)}) in{" "}
      <span className="tnum">{r.gamesS}</span> games
    </>,
  ];
  if (r.age !== null) parts.push(<>Age {Math.floor(r.age)} after last season</>);
  if (ecr) parts.push(<>Experts&apos; preseason rank <span className="tnum font-semibold">{posRank(r.position, r.ecrRank)}</span></>);
  return (
    <p className="text-sm" data-testid="last-season">
      {parts.map((p, i) => (
        <span key={i}>
          {i ? " · " : ""}
          {p}
        </span>
      ))}
    </p>
  );
}

function Row({ r, mark, ecr, showOutcome }: { r: BoardEntry; mark: Disagree | null; ecr: boolean; showOutcome: boolean }) {
  return (
    <li
      data-row=""
      data-testid="board-row"
      data-player={r.gsisId}
      data-pos={r.position}
      className="grid grid-cols-[2.25rem_minmax(0,1fr)] gap-x-3 gap-y-2 px-3 py-3 @2xl:grid-cols-[2.5rem_minmax(0,1fr)_17rem]"
    >
      <span data-cell="rank" aria-hidden="true" className="big-number col-start-1 row-start-1 text-2xl text-muted">
        {r.cliffRank}
      </span>
      <div data-cell="player" className="col-start-2 row-start-1 min-w-0 space-y-1">
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 font-semibold break-words">
          <PosBadge pos={r.position} />
          <Link href={`/player/${r.gsisId}`} className="min-w-0 break-words">
            {r.name}
          </Link>
        </p>
        {mark ? <DisagreeTag d={mark} position={r.position} r={r} /> : null}
      </div>
      {/* narrow: the two chances side by side under the name; wide: their own column */}
      <div className="col-start-2 row-start-2 grid max-w-sm grid-cols-2 gap-3 @2xl:col-start-3 @2xl:row-span-2 @2xl:row-start-1 @2xl:max-w-none">
        <div data-cell="cliff" className="min-w-0">
          <EstimateCell probability={r.cliffProbability} label="Estimated chance of a Cliff" />
        </div>
        <div data-cell="missed" className="min-w-0">
          <EstimateCell probability={r.missedProbability} label="Estimated chance of missed time" />
        </div>
      </div>
      <div data-cell="details" className="col-start-2 row-start-3 min-w-0 space-y-2 @2xl:row-start-2">
        <LastSeason r={r} ecr={ecr} />
        <Drivers title="What moves the Cliff chance most" drivers={r.cliffDrivers} who={r.name} />
        <Drivers title="What moves the missed-time chance most" drivers={r.missedDrivers} who={r.name} />
        {showOutcome ? (
          <p className="text-sm" data-testid="outcome">
            <span className="font-medium">What happened: </span>
            <BoardOutcomeTag outcome={r.outcome} ppgS={r.ppgS} long />
          </p>
        ) : null}
      </div>
    </li>
  );
}
