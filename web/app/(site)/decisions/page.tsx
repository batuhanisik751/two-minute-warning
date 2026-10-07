import Link from "next/link";
import LeagueTendencies from "@/components/coach-tendencies/LeagueTendencies";
import ClockCaseList from "@/components/decisions/ClockCaseList";
import DecisionList from "@/components/decisions/DecisionList";
import HowItWorks from "@/components/decisions/HowItWorks";
import Leaderboard from "@/components/decisions/Leaderboard";
import SeasonPicker from "@/components/decisions/SeasonPicker";
import { SeasonSummary, TossUps } from "@/components/decisions/SeasonSummary";
import Term from "@/components/Term";
import { EmptyState, Note, PageHeader } from "@/components/ui";
import { parseTendencySort } from "@/lib/coach-tendencies";
import { leagueTotals, rankCoaches } from "@/lib/decisions";
import { fmtInt } from "@/lib/format";
import { parseInt4 } from "@/lib/params";
import { getSeasonTendencies, getTendencyMeta, getTendencyStudies } from "@/lib/queries/coach-tendencies";
import { getBestCalls, getClockCases, getDecisionSeasons, getDecisionsMeta, getSeasonCoaches, getWorstCalls, type DecisionsMeta } from "@/lib/queries/decisions";
import { pageMetadata } from "@/lib/seo";

export const metadata = pageMetadata("/decisions", "Decision Report Card", "The Decision Report Card: NFL head coaches' fourth-down, two-point and clock decisions graded by win probability, the season's best and worst calls, the leaderboard, and how each offense plays.");

/** How many worst and best calls a season shows (the lists fold after 10). */
const WORST = 20;
const BEST = 10;

function Intro() {
  return (
    <PageHeader title="Decision Report Card" kicker="Coaches' calls, graded">
      Every fourth down and every try after a touchdown, priced by our own win-probability model: what each option was
      worth, what the coach chose, and how much win probability a clearly wrong call gave away. Close calls are
      toss-ups and are never held against anyone. Plus three narrow clock-management checks.
    </PageHeader>
  );
}

/** "2026 (through week 3)" for the season being graded, else the season. */
function seasonName(season: number, meta: DecisionsMeta): string {
  return season === meta.season && meta.latestWeek !== null ? `${season} (through week ${meta.latestWeek})` : String(season);
}

export default async function DecisionsPage({ searchParams }: PageProps<"/decisions">) {
  const sp = await searchParams;
  const [seasons, meta] = await Promise.all([getDecisionSeasons(), getDecisionsMeta()]);
  if (!seasons.length) {
    return (
      <>
        <Intro />
        <EmptyState title="No decisions published yet">
          <p>The Report Card appears here after the first publish that includes graded decisions.</p>
        </EmptyState>
      </>
    );
  }
  const asked = parseInt4(sp.season);
  const season = asked !== null && seasons.includes(asked) ? asked : seasons[0];
  const [coaches, worst, best, clock, tMeta, studies] = await Promise.all([
    getSeasonCoaches(season),
    getWorstCalls(season, null, null, WORST),
    getBestCalls(season, null, null, BEST),
    getClockCases(season, null),
    getTendencyMeta(),
    getTendencyStudies(),
  ]);
  const tSeason = tMeta.season;
  const tRows = tSeason !== null ? await getSeasonTendencies(tSeason) : [];
  const { ranked, fewer, minGames } = rankCoaches(coaches);
  const league = leagueTotals(coaches);
  const name = seasonName(season, meta);
  return (
    <>
      <Intro />
      <SeasonPicker seasons={seasons} chosen={season} />
      {asked !== null && asked !== season ? (
        <div className="mt-4">
          <Note tone="warn">There are no graded decisions for {asked}; showing {season} instead.</Note>
        </div>
      ) : null}
      <div className="mt-8 grid items-start gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <SeasonSummary name={name} league={league} />
        <HowItWorks />
      </div>

      <section aria-labelledby="leaderboard-heading" className="mt-10" data-testid="leaderboard-section">
        <h2 id="leaderboard-heading" className="section-title mb-2 scroll-mt-24">
          Leaderboard, {name}
        </h2>
        <p className="mb-3 max-w-3xl text-sm text-muted">
          Least <Term name="wp_lost_per_game">WP lost per game</Term> first: the win probability a coach&apos;s clearly
          wrong fourth-down and two-point calls gave away, per game coached. <Term name="aggressiveness">Aggressiveness</Term>{" "}
          is how often the coach went for it when going was clearly best.
        </p>
        {ranked.length ? <Leaderboard rows={ranked} season={season} minGames={minGames} /> : <p className="text-muted">No coach has enough games yet.</p>}
        {fewer.length ? (
          <p className="mt-3 text-sm" data-testid="fewer-games">
            Fewer than {minGames} games, not ranked:{" "}
            {fewer.map((c, i) => (
              <span key={c.coachId}>
                {i ? ", " : ""}
                <Link href={`/coach/${c.coachId}`}>{c.name}</Link> ({c.team}, {fmtInt(c.games)})
              </span>
            ))}
            .
          </p>
        ) : null}
      </section>
      <TossUps name={name} league={league} />
      {/* the sort links keep the season only when it was asked for and graded: an ungraded ?season= would repeat its warning (G2.7) */}
      {tSeason !== null && tRows.length ? (
        <LeagueTendencies season={tSeason} rows={tRows} link={studies.link} sort={parseTendencySort(sp.tsort)} query={asked === season ? { season: String(season) } : {}} />
      ) : null}
      <section aria-labelledby="worst-heading" className="mt-10">
        <h2 id="worst-heading" className="section-title mb-2 scroll-mt-24">
          Worst calls, {name}
        </h2>
        <p className="mb-3 max-w-3xl text-sm text-muted">
          The <Term name="clear_call">clear calls</Term> that gave away the most win probability (
          <Term name="wp_lost">WP lost</Term>), fourth downs and tries together, with every option&apos;s win probability as
          the model saw it before the snap.
        </p>
        {worst.length ? <DecisionList rows={worst} label={`Worst calls, ${season}`} testId="worst-calls" value="lost" /> : <p className="text-muted">No clearly wrong call this season.</p>}
      </section>
      <section aria-labelledby="best-heading" className="mt-10">
        <h2 id="best-heading" className="section-title mb-2 scroll-mt-24">
          Best calls against convention, {name}
        </h2>
        <p className="mb-3 max-w-3xl text-sm text-muted">
          <Term name="against_convention">Against convention</Term>: going for it was clearly best and the coach went (or went
          for two). The number is the win probability it gained over the best kicking option.
        </p>
        {best.length ? <DecisionList rows={best} label={`Best calls against convention, ${season}`} testId="best-calls" value="gain" /> : <p className="text-muted">No such call this season.</p>}
      </section>
      <section aria-labelledby="clock-heading" className="mt-10">
        <h2 id="clock-heading" className="section-title mb-2 scroll-mt-24">
          Clock cases, {name}
        </h2>
        <p className="mb-3 max-w-3xl text-sm text-muted">
          Three narrow checks with exact definitions (<Link href="/methodology#decisions-clock">in plain words</Link>):{" "}
          <Term name="timeouts_unused">timeouts unused in a lost one-score game</Term>,{" "}
          <Term name="half_passivity">end-of-half passivity</Term> and{" "}
          <Term name="timeout_seconds_wasted">seconds wasted with timeouts in hand</Term>. A case is a fact, not always a
          mistake.
        </p>
        {clock.length ? <ClockCaseList rows={clock} label={`Clock cases, ${season}`} /> : <p className="text-muted" data-testid="no-clock-cases">No clock case this season.</p>}
      </section>
    </>
  );
}
