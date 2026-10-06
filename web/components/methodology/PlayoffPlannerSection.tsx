import Link from "next/link";
import Term from "@/components/Term";
import { EmptyState } from "@/components/ui";
import { PP_POSITIONS } from "@/lib/playoff-planner";
import { positionShort } from "@/lib/positions";
import { getPlannerModel, getPlannerTables } from "@/lib/queries/playoff-planner";
import { docUrl } from "@/lib/site";

/** /methodology's playoff-planner section: how a matchup rating is counted, the shrinkage (the
 *  pseudo-games per position), the walk-forward test and the fixed rule with its pick per
 *  position, and the limits. Numbers from the pinned version's model_versions row and the
 *  playoff_planner_choice table. */
export default async function PlayoffPlannerSection() {
  const [model, tables] = await Promise.all([getPlannerModel(), getPlannerTables()]);
  const p = model?.params ?? {};
  const tests = p.test_seasons ?? [];
  const span = tests.length ? `${Math.min(...tests)}-${Math.max(...tests)}` : "–";
  const choice = new Map(tables.choice.map((c) => [c.position, c]));
  return (
    <section aria-labelledby="playoff-planner" data-testid="playoff-planner-method">
      <h2 id="playoff-planner" className="section-title scroll-mt-24">
        Playoff planner
      </h2>
      {!model ? (
        <div className="mt-3">
          <EmptyState title="No playoff-planner rule published yet" />
        </div>
      ) : (
        <div className="mt-3 space-y-4">
          <p>
            A <Term name="matchup_rating">matchup rating</Term> is a ratio of counted sums, no machine learning: the fantasy points players at a position scored against a
            team per game, over the league&apos;s average per team-game at that position (1.00 is average). For a D/ST it is the opposing offense: the points it gives up
            to defenses. A few games are mostly noise, so the rating is pulled toward 1.00 by <Term name="pseudo_games">pseudo-games</Term>:{" "}
            {PP_POSITIONS.filter((x) => choice.has(x))
              .map((x) => `${positionShort(x)} ${choice.get(x)!.pseudoGames}`)
              .join(", ")}{" "}
            average games added to each team&apos;s own. The schedule-adjusted version also weighs who the team faced.
          </p>
          <p>
            Walk-forward test, seasons {span}: as of weeks {(p.horizons ?? []).join(", ")}, each player&apos;s points per game so far times his opponent&apos;s rating
            predicted his points in weeks {(p.playoff_weeks ?? []).join(", ")}. {p.rule ? `The rule, fixed before any result was seen: ${p.rule}.` : null} It picked{" "}
            {PP_POSITIONS.filter((x) => choice.has(x))
              .map((x) => `${positionShort(x)} ${choice.get(x)!.ruleChoice}`)
              .join(", ")}{" "}
            ({p.chosen_by === "rule" ? "used as picked" : `the site overrides it: ${p.chosen_by ?? "–"}`}; <span className="font-mono text-xs">{model.modelVersion}</span>).
            Where it picked none, every matchup counts as 1.00 and the planner shows the schedule only.
          </p>
          <p>
            Limits: injuries, weather and role changes move a player&apos;s points far more than his opponent; a rating is about a team&apos;s season so far, and trades
            and injuries on a defense change it; players who did not play are not scored, so the test says nothing about whether a hard schedule makes a player sit.
          </p>
          <p className="text-sm">
            <Link href="/playoff-planner">The planner</Link> · <a href={docUrl("docs/playoff_planner.md")}>The full write-up (docs/playoff_planner.md)</a>
          </p>
        </div>
      )}
    </section>
  );
}
