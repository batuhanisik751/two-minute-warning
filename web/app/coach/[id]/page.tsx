import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import ClockCaseList from "@/components/decisions/ClockCaseList";
import CoachTendencies from "@/components/coach-tendencies/CoachTendencies";
import CoachChart from "@/components/decisions/CoachChart";
import DecisionList from "@/components/decisions/DecisionList";
import { CountCells, CountHeaders } from "@/components/decisions/Leaderboard";
import { FoldTable } from "@/components/Fold";
import CoachHotSeat from "@/components/hot-seat/CoachHotSeat";
import CoachRecord from "@/components/hot-seat/CoachRecord";
import Term from "@/components/Term";
import { PageHeader } from "@/components/ui";
import { isCoachId } from "@/lib/decisions";
import { getBestCalls, getClockCases, getCoach, getDecisionsMeta, getWorstCalls, type CoachSeasonRow } from "@/lib/queries/decisions";
import { getCoachHotSeat, getHotSeatMeta } from "@/lib/queries/hot-seat";
import { pageMetadata } from "@/lib/seo";

// No loading.tsx above this route on purpose (as /player/[id]): an unknown id must answer with a
// real 404 status, which needs notFound() before the response starts streaming.

const WORST = 20;
const BEST = 10;

export async function generateMetadata({ params }: PageProps<"/coach/[id]">): Promise<Metadata> {
  const { id } = await params;
  if (!isCoachId(id)) return { title: "Coach not found" };
  const c = await getCoach(id);
  if (!c) return { title: "Coach not found" };
  return pageMetadata(
    `/coach/${id}`,
    c.name,
    `${c.name}: graded fourth-down, two-point and clock decisions, the worst and best calls, how his offense plays, record vs expectation and the Hot-Seat Meter's history.`,
  );
}

function SeasonsTable({ rows, name, current }: { rows: CoachSeasonRow[]; name: string; current: { season: number | null; week: number | null } }) {
  const newest = [...rows].reverse();
  return (
    <FoldTable
      label={`${name}'s seasons`}
      rows={newest.map((r) => (
        <tr key={r.season}>
          <th scope="row">
            <Link href={`/decisions?season=${r.season}`}>{r.season}</Link>
            {r.season === current.season && current.week !== null ? <span className="block text-xs font-normal text-muted">through week {current.week}</span> : null}
          </th>
          <td>{r.team}</td>
          <CountCells r={r} />
        </tr>
      ))}
      table={(body) => (
        <>
          <p className="mb-2 text-sm font-medium text-muted">Newest first; WP lost per game in WP points.</p>
          <div className="table-scroll">
          <table className="data-table" data-testid="coach-seasons">
            <caption className="sr-only">{name}&apos;s seasons as head coach</caption>
            <thead>
              <tr>
                <th scope="col">Season</th>
                <th scope="col">Team</th>
                <CountHeaders />
              </tr>
            </thead>
            {body}
          </table>
          </div>
        </>
      )}
    />
  );
}

export default async function CoachPage({ params }: PageProps<"/coach/[id]">) {
  const { id } = await params;
  if (!isCoachId(id)) notFound();
  const coach = await getCoach(id);
  if (!coach) notFound();
  const [meta, worst, best, clock, hotSeat, hotSeatMeta] = await Promise.all([
    getDecisionsMeta(),
    getWorstCalls(null, id, null, WORST),
    getBestCalls(null, id, null, BEST),
    getClockCases(null, id),
    getCoachHotSeat(id),
    getHotSeatMeta(),
  ]);
  const s = coach.seasons;
  const span = s.length ? (s[0].season === s[s.length - 1].season ? `${s[0].season}` : `${s[0].season}–${s[s.length - 1].season}`) : null;
  const teams = [...new Set(s.map((r) => r.team))];
  return (
    <>
      <PageHeader title={coach.name} kicker="Head coach · Decision Report Card">
        {span ? (
          <>
            Graded seasons {span} ({s.length} {s.length === 1 ? "season" : "seasons"}; {teams.join(", ")}). Every fourth down
            and try after a touchdown is credited to the head coach of the team with the ball.{" "}
            <Link href="/decisions">All coaches</Link>
          </>
        ) : (
          <>No graded season yet.</>
        )}
      </PageHeader>
      <section aria-labelledby="seasons-heading" data-testid="coach-seasons-section">
        <h2 id="seasons-heading" className="section-title mb-3 scroll-mt-24">
          Season by season
        </h2>
        {s.length ? (
          <>
            <SeasonsTable rows={s} name={coach.name} current={{ season: meta.season, week: meta.latestWeek }} />
            <div className="mt-6">
              <CoachChart rows={s} name={coach.name} />
            </div>
          </>
        ) : (
          <p className="text-muted">No season row published for this coach.</p>
        )}
      </section>
      <CoachTendencies coachId={id} name={coach.name} />
      <CoachRecord rows={hotSeat} name={coach.name} />
      <CoachHotSeat rows={hotSeat} name={coach.name} season={hotSeatMeta.latest?.season ?? null} />
      <section aria-labelledby="coach-worst-heading" className="mt-10">
        <h2 id="coach-worst-heading" className="section-title mb-2 scroll-mt-24">
          Worst calls
        </h2>
        <p className="mb-3 max-w-3xl text-sm text-muted">
          The <Term name="clear_call">clear calls</Term> that gave away the most win probability (
          <Term name="wp_lost">WP lost</Term>), every season.
        </p>
        {worst.length ? (
          <DecisionList rows={worst} label={`${coach.name}: worst calls`} testId="worst-calls" value="lost" showCoach={false} />
        ) : (
          <p className="text-muted">No clearly wrong call.</p>
        )}
      </section>
      <section aria-labelledby="coach-best-heading" className="mt-10">
        <h2 id="coach-best-heading" className="section-title mb-2 scroll-mt-24">
          Best calls against convention
        </h2>
        <p className="mb-3 max-w-3xl text-sm text-muted">
          <Term name="against_convention">Against convention</Term>: going for it (or for two) was clearly best and the
          coach went; the number is the win probability gained over the best kicking option.
        </p>
        {best.length ? (
          <DecisionList rows={best} label={`${coach.name}: best calls against convention`} testId="best-calls" value="gain" showCoach={false} />
        ) : (
          <p className="text-muted">No clear go call that was taken.</p>
        )}
      </section>
      <section aria-labelledby="coach-clock-heading" className="mt-10">
        <h2 id="coach-clock-heading" className="section-title mb-2 scroll-mt-24">
          Clock cases
        </h2>
        <p className="mb-3 max-w-3xl text-sm text-muted">
          <Term name="clock_case">Clock cases</Term> under the three narrow definitions (
          <Link href="/methodology#decisions-clock">in plain words</Link>): facts, not always mistakes.
        </p>
        {clock.length ? (
          <ClockCaseList rows={clock} label={`${coach.name}: clock cases`} showCoach={false} />
        ) : (
          <p className="text-muted" data-testid="no-clock-cases">
            No clock case.
          </p>
        )}
      </section>
    </>
  );
}
