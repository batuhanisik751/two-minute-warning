import Link from "next/link";
import { FoldList } from "@/components/Fold";
import { MiniLabel } from "@/components/ui";
import { optionName, outcomeWords, situationWords, weekWords, wpPct, wpPoints, type DecisionRow } from "@/lib/decisions";
import { wholePct } from "@/lib/format";

type Props = {
  rows: (DecisionRow & { gain?: number })[];
  /** the list's accessible name, e.g. "Worst calls of 2025" */
  label: string;
  testId: string;
  /** the number on the right: WP lost (worst calls) or WP gained over the next option (best) */
  value: "lost" | "gain";
  /** link the coach (off on the coach's own page) */
  showCoach?: boolean;
  /** show the rank (off for a one-row list, e.g. the home page's card) */
  numbered?: boolean;
};

/** A ranked list of graded decisions: who, when, the situation in words, every option's WP (the
 *  chosen one marked), what happened, and WP lost or gained. Rows are measured by the layout
 *  check ([data-row], [data-cell]); they lay out by their container (narrow: the number under the
 *  call). Folds after 10 (components/Fold.tsx). */
export default function DecisionList({ rows, label, testId, value, showCoach = true, numbered = true }: Props) {
  return (
    <div className="@container">
      <FoldList
        label={label}
        className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface"
        testId={testId}
        items={rows.map((d, i) => (
          <Row key={`${d.gameId}-${d.playId}`} d={d} rank={numbered ? i + 1 : null} value={value} showCoach={showCoach} />
        ))}
      />
    </div>
  );
}

function Row({ d, rank, value, showCoach }: { d: DecisionRow & { gain?: number }; rank: number | null; value: "lost" | "gain"; showCoach: boolean }) {
  const amount = value === "lost" ? d.wpLost : (d.gain ?? 0);
  const outcome = outcomeWords(d.outcome, d.kind);
  return (
    <li
      data-row=""
      data-testid="decision"
      data-kind={d.kind}
      className={`grid gap-x-3 gap-y-2 px-3 py-3 ${
        rank === null ? "grid-cols-1 @xl:grid-cols-[minmax(0,1fr)_8.5rem]" : "grid-cols-[2.25rem_minmax(0,1fr)] @xl:grid-cols-[2.5rem_minmax(0,1fr)_8.5rem]"
      }`}
    >
      {rank !== null ? (
        <span data-cell="rank" aria-hidden="true" className="big-number text-2xl text-muted">
          {rank}
        </span>
      ) : null}
      <div data-cell="call" className="min-w-0 space-y-1">
        <p className="font-semibold break-words">
          {showCoach ? <Link href={`/coach/${d.coachId}`}>{d.coachName}</Link> : null}
          <span className={showCoach ? "font-normal text-muted" : "text-muted"}>
            {showCoach ? " · " : ""}
            {d.posteam} against {d.defteam}, {d.season} {weekWords(d.week, d.seasonType)}
          </span>
        </p>
        <p data-testid="situation">{situationWords(d)}.</p>
        <p className="text-sm">
          Chose: <strong>{optionName(d.chosen)}</strong>
          {d.chosen === d.recommended ? " (the best option)" : <> · best: <strong>{optionName(d.recommended)}</strong></>}
          {outcome ? <> · what happened: {outcome}</> : null}
        </p>
        <ul aria-label="Win probability of each option" className="flex flex-wrap gap-x-4 gap-y-0.5 text-sm text-muted">
          {d.options.map((o) => (
            <li key={o.option} className={o.option === d.chosen ? "text-fg" : undefined}>
              {optionName(o.option)} <span className="tnum font-semibold">{wpPct(o.wp)}</span>
              {o.option === "go" && d.pConvert !== null ? ` (converts ${wholePct(d.pConvert)})` : ""}
              {o.option === "field_goal" && d.pMake !== null ? ` (good ${wholePct(d.pMake)})` : ""}
              {o.option === d.chosen ? " · chosen" : ""}
            </li>
          ))}
        </ul>
      </div>
      <div data-cell="value" className={rank === null ? "@xl:text-right" : "col-start-2 @xl:col-start-3 @xl:text-right"}>
        <span className="big-number block text-[1.75rem] tnum">{value === "gain" ? `+${wpPoints(amount)}` : wpPoints(amount)}</span>
        <MiniLabel>{value === "lost" ? "WP points lost" : "WP points gained"}</MiniLabel>
      </div>
    </li>
  );
}
