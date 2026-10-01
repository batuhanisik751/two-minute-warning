import Link from "next/link";
import { FoldList } from "@/components/Fold";
import { MiniLabel } from "@/components/ui";
import { CLOCK_TITLES, clockAmountWords, clockSituation, weekWords } from "@/lib/decisions";
import type { ClockCaseRow } from "@/lib/queries/decisions";

/** The clock-management cases (decision_clock rows with is_case): the metric, the team and
 *  game, the key snap in words and the amount (timeouts unused, expected points left, seconds
 *  wasted). Rows are measured by the layout check; the list folds after 10. */
export default function ClockCaseList({ rows, label, showCoach = true }: { rows: ClockCaseRow[]; label: string; showCoach?: boolean }) {
  return (
    <div className="@container">
      <FoldList
        label={label}
        className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface"
        testId="clock-cases"
        items={rows.map((c) => {
          const amount = clockAmountWords(c.metric, c.amount);
          return (
            <li
              key={`${c.metric}-${c.gameId}-${c.team}`}
              data-row=""
              data-testid="clock-case"
              data-metric={c.metric}
              className="grid grid-cols-1 gap-x-3 gap-y-1 px-3 py-3 @xl:grid-cols-[minmax(0,1fr)_11rem]"
            >
              <div data-cell="case" className="min-w-0 space-y-1">
                <p className="font-semibold break-words">
                  {CLOCK_TITLES[c.metric]}
                  <span className="font-normal text-muted">
                    {" · "}
                    {showCoach ? (
                      <>
                        <Link href={`/coach/${c.coachId}`}>{c.coachName}</Link>,{" "}
                      </>
                    ) : null}
                    {c.team} against {c.opp}, {c.season} {weekWords(c.week, c.seasonType)}
                  </span>
                </p>
                <p className="text-sm">{clockSituation(c)}.</p>
              </div>
              <div data-cell="amount" className="@xl:text-right">
                <span className="font-display text-xl font-bold">{amount}</span>
                {c.metric === "half_passivity" && c.wpLeft !== null ? (
                  <MiniLabel>{(c.wpLeft * 100).toFixed(1)} WP points left</MiniLabel>
                ) : null}
              </div>
            </li>
          );
        })}
      />
    </div>
  );
}
