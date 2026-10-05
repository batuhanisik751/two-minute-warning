import Term from "@/components/Term";

/** The Decision Report Card's honesty notes (docs/decision_metrics.md "Honesty note",
 *  reports/decisions/{submodels,wp_backtest}.md), in plain words. The seasons named come from
 *  the database: the validation seasons from the smoothness rows, the clean test season from
 *  site_meta decisions_season. */
export function HonestyNotes({ validation, cleanSeason }: { validation: string | null; cleanSeason: number | null }) {
  const clean = cleanSeason !== null ? `${cleanSeason} is their first clean test` : "the season in progress is their first clean test";
  return (
    <ul className="mt-3 list-disc space-y-3 pl-5" data-testid="decisions-honesty">
      <li>
        <strong>Sub-models&apos; training windows.</strong> The option to train the conversion and field-goal models on only
        the last ten or five seasons was added after a first backtest had shown recent seasons under-predicted. Each test
        season still picks its window on the season before it only, but because the option itself was suggested by
        test-season results, the published numbers for those two models are slightly optimistic; {clean}.
      </li>
      <li>
        <strong>The smoothed WP model&apos;s candidate rounds.</strong> The smoothness limits were fixed before any fix was
        tried, and every candidate was chosen on the validation seasons{validation ? ` ${validation.replace("-", "–")}` : ""}{" "}
        only. But two later rounds of candidates (the late-game hand-over and the redefined value of the ball before
        halftime) were prompted by looking at regraded seasons, and the pooled test <Term name="log_loss">log loss</Term> of a first refit had been seen
        before them. The WP and grading numbers of the past seasons are therefore slightly optimistic; {clean}.
      </li>
      <li>
        <strong>Clock management.</strong> The definitions and every threshold were written before any season was graded.
        Two things were seen before the full grading and are disclosed: a count of end-of-half candidates on one season
        with the thresholds relaxed (nothing was changed), and a first test run whose one hand-checked case showed that the
        seconds-wasted formula as first written contradicted its own words. The formula was corrected to match the words;
        the timeouts-unused metric was kept exactly as written.
      </li>
      <li>
        <strong>The benchmark.</strong> nfl4th&apos;s models were probably fit on the seasons it is compared on (partly
        in-sample); ours never saw them. Agreement is not accuracy: where the two differ, neither is known to be right.
      </li>
    </ul>
  );
}
