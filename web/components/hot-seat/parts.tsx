// Small shared pieces of the Hot-Seat Meter (server components). Neutral on purpose: a grey-blue
// bar, no heat colours, no icons that judge; the text is always the source of truth.
import Term from "@/components/Term";
import { MiniLabel, Note } from "@/components/ui";
import { fmtInt } from "@/lib/format";
import { outcomeWords, wholePct, type OutcomeRow, type hotSeatTally } from "@/lib/hot-seat";
import { HOT_SEAT_WINDOW_DAYS } from "@/lib/method";

/** The estimate as a whole percent with a bar under it (hidden from screen readers). */
export function EstimateCell({ probability, size = "big", label = "Estimated chance" }: { probability: number; size?: "big" | "small"; label?: string }) {
  const w = `${Math.max(0, Math.min(1, probability)) * 100}%`;
  return (
    <span className="block tnum">
      <span className={`big-number block ${size === "big" ? "text-[1.875rem]" : "text-xl"}`}>{wholePct(probability)}</span>
      <span className="meter mt-1 block" aria-hidden="true">
        <span className="meter-fill meter-estimate" style={{ width: w }} />
      </span>
      <MiniLabel className="mt-1">{label}</MiniLabel>
    </span>
  );
}

/** What really happened to the coach (final) or "Pending" (live seasons), as a quiet tag. */
export function OutcomeTag({ outcome, long = false }: { outcome: OutcomeRow | null; long?: boolean }) {
  const o = outcomeWords(outcome);
  const cls = o.tone === "pending" ? "border-pending text-pending" : o.tone === "let-go" ? "border-line-strong text-fg font-semibold" : "border-line text-muted";
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-2">
      <span data-outcome={o.tone} className={`inline-flex items-center rounded border-[1.5px] px-1.5 py-0.5 text-xs whitespace-nowrap ${cls}`}>
        {o.short}
      </span>
      {long ? <span className="text-sm text-muted">{o.long}</span> : null}
    </span>
  );
}

/** "Interim" with the reason, for coaches who took over during the season. */
export function InterimFlag() {
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-2 text-sm" data-testid="interim-flag">
      <span className="inline-flex items-center rounded border-[1.5px] border-line-strong px-1.5 py-0.5 text-xs font-semibold whitespace-nowrap">Interim</span>
      <span className="text-muted">the model was not trained on interim coaches</span>
    </span>
  );
}

/** A list's "What happened" line: pending until the season's departures are labelled, else how
 *  many of its coaches were let go (lib/hot-seat hotSeatTally). */
export function HotSeatWhatHappened({ tally }: { tally: ReturnType<typeof hotSeatTally> }) {
  const { final, letGo, pending } = tally;
  return pending ? (
    <Note>
      <span data-testid="outcomes-pending">
        What happened: pending until the season&apos;s departures are labelled. The list is not graded before then.
      </span>
    </Note>
  ) : (
    <p className="text-sm" data-testid="outcomes-final">
      <strong>What happened:</strong> {fmtInt(letGo)} of these {fmtInt(final)} coaches {letGo === 1 ? "was" : "were"}{" "}
      <Term name="hot_seat_let_go">let go</Term> by {HOT_SEAT_WINDOW_DAYS} days after the season.
    </p>
  );
}
