import { fmtInt, pct, seasonWeek } from "@/lib/format";
import { AS_OF_WEEKDAY } from "@/lib/method";
import type { LiveCount, LiveWeek } from "@/lib/track-record";

/** "weekly lists" -> "weekly list" */
const one = (plural: string) => plural.replace(/s$/, "");

type Props = {
  summary: { weeks: LiveWeek[]; total: LiveCount };
  /** what is counted, plural ("top-10 picks", "Sell-high tags") */
  what: string;
  /** what a success is called ("hit", "came true") */
  success: string;
  /** what one list is called, plural ("lists", "weekly lists") */
  lists?: string;
  /** when the outcomes become final, in words */
  pendingUntil: string;
  testId: string;
  /** the sentence when nothing is counted (default: no list has been made live yet) */
  emptyText?: string;
};

/** The live results of one module: lists made in real time, graded only once their outcome is
 *  final (a pending outcome is never a miss), with the counts and an honest small-sample note.
 *  "No live results yet" until the first outcome is final. */
export default function LiveResults({ summary, what, success, lists = "lists", pendingUntil, testId, emptyText }: Props) {
  const { weeks, total } = summary;
  if (!total.lists) {
    return (
      <div data-testid={testId} data-live="none">
        <p className="font-semibold">No live results yet.</p>
        <p className="mt-1 text-sm text-muted">
          {emptyText ?? `No list has been made live yet: the first one is made on a ${AS_OF_WEEKDAY} after the week's games.`}
        </p>
      </div>
    );
  }
  const n = weeks.length;
  return (
    <div data-testid={testId} data-live={total.graded ? "graded" : "pending"}>
      {total.graded ? (
        <p>
          <strong className="tnum">{fmtInt(total.hits)}</strong> of the <strong className="tnum">{fmtInt(total.graded)}</strong> graded {what}{" "}
          {success} (<strong className="tnum">{pct(total.hits / total.graded, 1)}</strong>), from {fmtInt(total.lists)} live {total.lists === 1 ? one(lists) : lists} in{" "}
          {fmtInt(n)} {n === 1 ? "week" : "weeks"}.
        </p>
      ) : (
        <p className="font-semibold">No live results yet.</p>
      )}
      <p className="mt-1 text-sm text-muted" data-testid={`${testId}-note`}>
        {total.graded
          ? `A small sample: ${fmtInt(total.graded)} graded ${what} against the backtest's many seasons, so this rate can swing a lot from week to week. Read it as a check on the backtest, not a verdict.`
          : `${fmtInt(total.lists)} live ${total.lists === 1 ? one(lists) : lists} so far (${fmtInt(total.picks)} ${what}); every outcome is still pending until ${pendingUntil}. A pending outcome is never counted as a miss.`}
        {total.graded && total.pending ? ` ${fmtInt(total.pending)} more are still pending.` : ""}
        {total.notGraded ? ` ${fmtInt(total.notGraded)} could not be graded.` : ""}
      </p>
      <ul className="mt-3 divide-y divide-line rounded-md border border-line bg-bg" aria-label={`Live ${lists} by week`}>
        {weeks.map((w) => (
          <li key={`${w.season}-${w.week}`} data-row="" className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-3 py-2 text-sm">
            <span data-cell="week" className="font-semibold">
              {seasonWeek(w.season, w.week)}
            </span>
            <span data-cell="counts" className="tnum text-muted">
              {fmtInt(w.lists)} {w.lists === 1 ? one(lists) : lists} · {fmtInt(w.picks)} {what}
            </span>
            <span data-cell="result" className="tnum">
              {w.graded ? `${fmtInt(w.hits)} of ${fmtInt(w.graded)} ${success}` : "pending"}
              {w.graded && w.pending ? `, ${fmtInt(w.pending)} pending` : ""}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
