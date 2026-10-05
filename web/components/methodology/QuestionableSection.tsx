import Link from "next/link";
import { BacktestScore } from "@/components/questionable/RecordParts";
import Term from "@/components/Term";
import { EmptyState } from "@/components/ui";
import { pct } from "@/lib/format";
import { getQuestionableModel, getQuestionableTables } from "@/lib/queries/questionable";
import { INACTIVES_NOTE } from "@/lib/questionable";
import { docUrl } from "@/lib/site";

const KEY_WORDS: Record<string, string> = { report_status: "the tag", missed_prev: "whether he missed his team's previous game", position: "his position" };

/** /methodology's Questionable section: what the chance counts, why practice status is not used,
 *  the "if he plays" line, the walk-forward score and the limits. Numbers from the pinned table's
 *  model_versions row and the questionable_* tables. */
export default async function QuestionableSection() {
  const [model, tables] = await Promise.all([getQuestionableModel(), getQuestionableTables()]);
  const ts = model?.trainingSeasons ?? [];
  const seasons = ts.length ? `${Math.min(...ts)}-${Math.max(...ts)}` : null;
  const p = model?.params ?? {};
  const rules = p.plays_rules ?? {};
  return (
    <section aria-labelledby="questionable" data-testid="questionable-method">
      <h2 id="questionable" className="section-title scroll-mt-24">
        Questionable outcomes
      </h2>
      {!model ? (
        <div className="mt-3">
          <EmptyState title="No Questionable table published yet" />
        </div>
      ) : (
        <div className="mt-3 space-y-4">
          <p>
            For a QB, RB, WR or TE tagged <Term name="questionable">Questionable</Term> or <Term name="doubtful">Doubtful</Term> on his team&apos;s final injury report, the{" "}
            <Term name="play_chance">chance he plays</Term> is a counted rate: of the past players with the same{" "}
            {model.featureList.map((k) => KEY_WORDS[k] ?? k).join(", ")} (seasons {seasons}), the share who took at least one offensive snap. Each group is pulled
            toward the larger group it belongs to by {p.pseudo_count ?? "–"} pseudo-players, so small groups do not swing. No machine learning, and the table is frozen
            before the season (<span className="font-mono text-xs">{model.modelVersion}</span>).
          </p>
          <p>
            <Term name="practice_status">Practice status</Term> is shown but not used. Since 2025 the injury data comes from a different source and its practice statuses no
            longer separate players the way they did: on the {p.check_season ?? "2025"} season the grouping with practice status lost to the one without it, the rule
            fixed before the test.
          </p>
          <p>
            <strong>If he plays</strong>: among tagged players who played, with {rules.min_prior_games ?? "–"}+ earlier games and {rules.min_prior_ppg ?? "–"}+ points per
            game so far, the median of that week&apos;s points over his points per game so far, and the <Term name="dud_rate">dud rate</Term> (under{" "}
            {rules.dud !== undefined ? pct(rules.dud) : "–"} of his usual). Healthy players with similar averages are shown next to it, because any player&apos;s
            ratio drifts below 1. A group with fewer than {rules.min_bucket_n ?? "–"} past players shows its tag&apos;s overall line, or nothing.
          </p>
          <BacktestScore rows={tables.backtest} />
          <p>
            Limits: teams use the tags differently; late scratches and game-time decisions are not in the injury report; &quot;played&quot; means one offensive snap
            (three snaps and out counts as played). {INACTIVES_NOTE}
          </p>
          <p className="text-sm">
            <Link href="/questionable">This week&apos;s list</Link> ·{" "}
            <a href={docUrl("docs/questionable.md")}>The full write-up (docs/questionable.md)</a>
          </p>
        </div>
      )}
    </section>
  );
}
