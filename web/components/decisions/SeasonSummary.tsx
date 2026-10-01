import Term from "@/components/Term";
import { leagueTotals, wpPoints } from "@/lib/decisions";
import { fmtInt, pct } from "@/lib/format";
import { TOSS_UP_MARGIN } from "@/lib/method";

type League = ReturnType<typeof leagueTotals>;

const share = (n: number, of: number) => (of > 0 ? ` (${pct(n / of)})` : "");

/** The league's season in a few sentences, every number summed from coach_season. */
export function SeasonSummary({ name, league }: { name: string; league: League }) {
  const clear = league.fourthGraded + league.twoPointGraded;
  const tossUps = league.fourthTossUps + league.twoPointTossUps;
  return (
    <section aria-labelledby="season-heading" className="rounded-xl border border-line bg-surface p-4 sm:p-5" data-testid="season-summary">
      <h2 id="season-heading" className="section-title">
        The league, {name}
      </h2>
      <p className="mt-3">
        {fmtInt(league.coaches)} head coaches, {fmtInt(league.teamGames)} team-games. {fmtInt(clear + tossUps)}{" "}
        <Term name="decisions_graded">decisions</Term> priced: <strong className="tnum">{fmtInt(clear)}</strong>{" "}
        <Term name="clear_call">clear calls</Term>, of which <strong className="tnum">{fmtInt(league.wrong)}</strong> were
        wrong{share(league.wrong, clear)}, and <strong className="tnum">{fmtInt(tossUps)}</strong>{" "}
        <Term name="toss_up">toss-ups</Term>.
      </p>
      <p className="mt-2">
        Going for it on fourth down was clearly best {fmtInt(league.goClear)} times; coaches went {fmtInt(league.goClearWent)}
        {share(league.goClearWent, league.goClear)}. Clearly wrong calls gave away{" "}
        <strong className="tnum">{wpPoints(league.wpLost)}</strong> <Term name="wp_points">WP points</Term> in all
        {league.teamGames > 0 ? <>, {wpPoints(league.wpLost / league.teamGames, 2)} per team-game</> : null}.
      </p>
    </section>
  );
}

/** Toss-ups explained, with the season's counts. */
export function TossUps({ name, league }: { name: string; league: League }) {
  const fourthAll = league.fourthGraded + league.fourthTossUps;
  const triesAll = league.twoPointGraded + league.twoPointTossUps;
  return (
    <section aria-labelledby="tossups-heading" className="mt-10" data-testid="toss-ups">
      <h2 id="tossups-heading" className="section-title mb-2 scroll-mt-24">
        Toss-ups, {name}
      </h2>
      <div className="max-w-3xl space-y-2">
        <p>
          <strong className="tnum">{fmtInt(league.fourthTossUps)}</strong> of {fmtInt(fourthAll)} fourth downs
          {share(league.fourthTossUps, fourthAll)} and <strong className="tnum">{fmtInt(league.twoPointTossUps)}</strong> of{" "}
          {fmtInt(triesAll)} tries after a touchdown{share(league.twoPointTossUps, triesAll)} were{" "}
          <Term name="toss_up">toss-ups</Term>: the best option beat the second best by {wpPoints(TOSS_UP_MARGIN)} WP points
          or less.
        </p>
        <p className="text-muted">
          Options that close are within the model&apos;s own error, so a toss-up is counted but never graded, whatever the
          coach chose: going for it on a toss-up is not a mistake, and neither is punting. A coach&apos;s WP lost comes from
          the clear calls only.
        </p>
      </div>
    </section>
  );
}
