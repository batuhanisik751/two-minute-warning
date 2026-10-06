import Term from "@/components/Term";
import { fmtInt } from "@/lib/format";
import { H2H_KINDS, LEVELS, PRIMARY, SECONDARY, SIGNAL_LABELS, h2hOf, pooledRow, reverseOf, share, weeks, type LTCoverageRow, type LTH2hRow, type LTReverseRow, type LTSummaryRow, type Signal } from "@/lib/lead-time";
import { LEAD_TIME_LICENSE, LEAD_TIME_SOURCE } from "@/lib/third-party";

// Lead time vs the crowd's comparisons (feature #8): the 25% result, the head to head with the
// momentum baseline, the reverse view, the coverage note and the source credit. Every number is a
// published lead_time_* row; "–" = fewer than 5 cases.

/** The secondary threshold in one sentence: the Radar's lists against the momentum baseline. */
export function AtSecondary({ summary }: { summary: LTSummaryRow[] }) {
  const listed = pooledRow(summary, "listed", SECONDARY);
  const mom = pooledRow(summary, "momentum", SECONDARY);
  if (!listed || !mom) return null;
  return (
    <p data-testid="lt-t25">
      <strong>At the {SECONDARY}% mark</strong> ({fmtInt(listed.nAdds)} crowd adds, the same seasons), the Radar&apos;s lists flagged{" "}
      <strong className="tnum">{share(listed.shareBefore)}</strong> before the crowd and the momentum baseline{" "}
      <strong className="tnum">{share(mom.shareBefore)}</strong>; the baseline mostly fires the same week ({share(mom.shareSame)}), when the rise and
      the crossing are one and the same scrape.
    </p>
  );
}

const H2H_HEAD: Record<(typeof H2H_KINDS)[number], string> = { both: "Both", radar_only: "Radar only", momentum_only: "Momentum only", neither: "Neither" };

/** Radar level vs the momentum baseline on the same crowd adds (crowd at 50%). */
export function H2hTable({ h2h }: { h2h: LTH2hRow[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table" data-testid="lt-h2h">
        <caption className="text-left text-sm text-muted">Head to head on the same crowd adds (crowd at {PRIMARY}%): who flagged him before the crowd</caption>
        <thead>
          <tr>
            <th scope="col">Radar level</th>
            {H2H_KINDS.map((k) => (
              <th key={k} scope="col" className="num">
                {H2H_HEAD[k]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {LEVELS.map((lv) => {
            const d = h2hOf(h2h, lv);
            return (
              <tr key={lv} data-row="" data-level={lv}>
                <th scope="row">{SIGNAL_LABELS[lv]}</th>
                {H2H_KINDS.map((k) => (
                  <td key={k} className="num">
                    {d[k] ? fmtInt(d[k].n) : "0"}
                    {k === "both" && d.both ? <span className="block text-xs text-muted">Radar earlier in {fmtInt(d.both.nRadarEarlier)}</span> : null}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const STATE_HEAD = { already: "Already rostered", added_later: "Added later", never: "Never added" } as const;

/** The reverse view: what the crowd did with each Radar level's first flags, and how often the
 *  Radar's own label called it a hit. */
export function ReverseTable({ reverse }: { reverse: LTReverseRow[] }) {
  const cell = (r: LTReverseRow | undefined, later: boolean) =>
    r ? (
      <>
        {fmtInt(r.n)}
        <span className="block text-xs text-muted">
          {r.hitRate === null ? "hit rate –" : `${share(r.hitRate)} hit`}
          {later && r.leadMedian !== null ? `, crowd ${weeks(r.leadMedian)} wk later` : ""}
        </span>
      </>
    ) : (
      "0"
    );
  return (
    <div className="table-scroll">
      <table className="data-table" data-testid="lt-reverse">
        <caption className="text-left text-sm text-muted">
          The Radar&apos;s first flags of a player (crowd at {PRIMARY}%) by what the crowd did, and how often the flag was a hit by the Radar&apos;s own
          definition
        </caption>
        <thead>
          <tr>
            <th scope="col">
              Radar <Term name="priority">level</Term>
            </th>
            {(Object.keys(STATE_HEAD) as (keyof typeof STATE_HEAD)[]).map((k) => (
              <th key={k} scope="col" className="num">
                {STATE_HEAD[k]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {(["must_add", "spec_plus", "listed"] as Signal[]).map((s) => {
            const d = reverseOf(reverse, s);
            return (
              <tr key={s} data-row="" data-signal={s}>
                <th scope="row">{SIGNAL_LABELS[s]}</th>
                <td className="num">{cell(d.already, false)}</td>
                <td className="num">{cell(d.added_later, true)}</td>
                <td className="num">{cell(d.never, false)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Which seasons the numbers stand on, from lead_time_coverage. */
export function CoverageNote({ coverage, span }: { coverage: LTCoverageRow[]; span: string | null }) {
  const partial = coverage.filter((c) => c.inStudy && !c.complete);
  const excluded = coverage.filter((c) => !c.inStudy);
  return (
    <p className="text-sm text-muted" data-testid="lt-coverage">
      {span ? `The pooled numbers are the complete seasons ${span}. ` : null}
      {partial.map((c) => `${c.season} is partial: its roster percentages start after week ${c.baselinePeriod}, so its leads run long; it is never pooled. `).join("")}
      {excluded.map((c) => `${c.season} is left out: ${c.inSeasonDays ? "it is incomplete" : "the warehouse has no in-season roster percentages for it"}. `).join("")}
      Weekly numbers, so a lead is counted in whole weeks.
    </p>
  );
}

/** The source credit and the license note (lib/third-party.ts): shown on the private and the public site. */
export function SourceNote() {
  return (
    <p className="rounded-md border border-dashed border-line px-3 py-2 text-sm text-muted" data-testid="lt-source">
      {LEAD_TIME_SOURCE} {LEAD_TIME_LICENSE}
    </p>
  );
}

/** The reverse view in one sentence: the must-adds the crowd never added, against those it added later. */
export function IgnoredLine({ reverse }: { reverse: LTReverseRow[] }) {
  const d = reverseOf(reverse, "must_add");
  if (!d.never || !d.added_later) return null;
  return (
    <p data-testid="lt-ignored">
      Of the Radar&apos;s must-adds, the crowd never added <strong className="tnum">{fmtInt(d.never.n)}</strong>: they were hits only{" "}
      <strong className="tnum">{share(d.never.hitRate)}</strong> of the time, against <strong className="tnum">{share(d.added_later.hitRate)}</strong> for the{" "}
      {fmtInt(d.added_later.n)} it added later.
      {d.never.hitRate !== null && d.never.hitRate < 0.5 ? " Where a must-add never caught on with the crowd, the crowd was usually right." : ""}
    </p>
  );
}
