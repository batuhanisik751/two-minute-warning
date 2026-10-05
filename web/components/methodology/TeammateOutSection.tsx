import Link from "next/link";
import { CandidateTable } from "@/components/teammate-out/RecordParts";
import Term from "@/components/Term";
import { EmptyState } from "@/components/ui";
import { fmtInt, pct } from "@/lib/format";
import { getTeammateOutModel, getTeammateOutTables } from "@/lib/queries/teammate-out";
import { docUrl } from "@/lib/site";
import { INACTIVES_NOTE } from "@/lib/teammate-out";

/** /methodology's Teammate-out section: who counts as a starter, how the allocation table is
 *  counted, the prediction, the four candidates with the rule's pick and the owner's override,
 *  the 80% range and the limits. Numbers from the pinned version's model_versions row and the
 *  teammate_out_* tables. */
export default async function TeammateOutSection() {
  const [model, tables] = await Promise.all([getTeammateOutModel(), getTeammateOutTables()]);
  const ts = model?.trainingSeasons ?? [];
  const seasons = ts.length ? `${Math.min(...ts)}-${Math.max(...ts)}` : null;
  const all = tables.events.find((e) => e.outPos === "all");
  const cov = tables.coverage.find((c) => c.position === "all");
  const r = model?.params.event_rule ?? {};
  const q = model?.params.ranges ?? {};
  const pc = (x: number | undefined) => (x === undefined ? "–" : pct(x));
  return (
    <section aria-labelledby="teammate-out" data-testid="teammate-out-method">
      <h2 id="teammate-out" className="section-title scroll-mt-24">
        Teammate out
      </h2>
      {!model ? (
        <div className="mt-3">
          <EmptyState title="No Teammate-out table published yet" />
        </div>
      ) : (
        <div className="mt-3 space-y-4">
          <p>
            A <Term name="starter_out">starter</Term> before a game is a RB with {pc(r.rb_carry_share)} or more of his team&apos;s carries, or a WR or TE with{" "}
            {pc(r.target_share)} or more of its targets, over the games he played among his team&apos;s last {r.window ?? "–"}. He is <em>out</em> when he takes no offensive snap while still with the team (traded, released and
            retired players are gone, not out). Seasons {seasons}
            {all ? `: ${fmtInt(all.sat)} starters sat, ${fmtInt(all.kept)} were still with their team, ${fmtInt(all.events)} games had teammates to grade` : ""}.
          </p>
          <p>
            The <Term name="allocation">allocation</Term> table is a ratio of counted sums, no machine learning: of the <Term name="vacated_share">vacated share</Term>{" "}
            a starter leaves, the share each teammate <Term name="teammate_role">role</Term> took over his usual games, pulled toward its position group so thin cells
            do not swing. A teammate&apos;s predicted share is his usual share plus his role&apos;s part of the vacated share; his predicted points are his predicted
            carries and targets (the team&apos;s usual volume) times his <Term name="points_per_opportunity">points per opportunity</Term>. The table is frozen before
            the season (<span className="font-mono text-xs">{model.modelVersion}</span>).
          </p>
          <CandidateTable rows={tables.backtest} params={model.params} />
          <p>
            The {pc(q.level)} range is the predicted points plus the {pc(q.q_lo)} and {pc(q.q_hi)} quantiles of past misses for similar teammates (the low end at
            least 0)
            {cov?.coverage != null ? `; walk-forward, ${(cov.coverage * 100).toFixed(1)}% of the real points landed inside (seasons ${cov.seasons})` : ""}.
          </p>
          <p>
            Limits: a single player&apos;s points are noisy, so the range is wide on purpose; the team&apos;s volume is kept at its usual level (it barely moves when a
            starter sits); roles come from usage, not depth charts, and a newly signed player with fewer than {r.min_base_games ?? "–"} games is not listed; quarterbacks are not
            modelled. {INACTIVES_NOTE}
          </p>
          <p className="text-sm">
            <Link href="/teammate-out">This week&apos;s list</Link> · <a href={docUrl("docs/teammate_out.md")}>The full write-up (docs/teammate_out.md)</a>
          </p>
        </div>
      )}
    </section>
  );
}
