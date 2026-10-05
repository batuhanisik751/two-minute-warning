import Term from "@/components/Term";
import { NotPublished } from "@/components/track/parts";
import { fmtInt, pct } from "@/lib/format";
import { allocationGroups, best, coverageRows, disclosure, type TOAllocationRow, type TOBacktestRow, type TOCoverageRow, type TOEventsRow, type TOParams } from "@/lib/teammate-out";

// Teammate out's record pieces, shared by /teammate-out, /methodology and /track-record: the
// allocation table, the four candidates with the owner-override disclosure, the 80% range's
// coverage. Every number is a published row (teammate_out_allocation, _backtest, _coverage,
// _events) or the pinned version's params.

const share = (x: number | null) => (x === null ? "–" : pct(x));
const OUT_WORDS: Record<string, string> = { RB: "a running back", WR: "a wide receiver", TE: "a tight end" };

/** "Where the work goes": per absent position, the share of his carries and targets each
 *  teammate role takes on average, with the teammate-games behind it. */
export function AllocationTable({ rows, events }: { rows: TOAllocationRow[]; events: TOEventsRow[] }) {
  const groups = allocationGroups(rows);
  if (!groups.length) return <NotPublished what="The allocation table">It appears with the first publish of the Teammate-out table.</NotPublished>;
  const ev = new Map(events.map((e) => [e.outPos, e]));
  return (
    <div className="space-y-6" data-testid="to-allocation">
      {groups.map((g) => {
        const e = ev.get(g.outPos);
        return (
          <div key={g.outPos} className="space-y-2" data-out-pos={g.outPos}>
            <h3 className="font-display text-lg font-bold tracking-wide uppercase">When {OUT_WORDS[g.outPos] ?? g.outPos} starter sits</h3>
            {e ? (
              <p className="text-sm text-muted" data-testid="to-events">
                {fmtInt(e.single)} games with one starter out ({fmtInt(e.events)} in all), seasons {e.seasons}.
              </p>
            ) : null}
            <div className="table-scroll">
              <table className="data-table">
                <caption className="sr-only">Where the work goes when {OUT_WORDS[g.outPos] ?? g.outPos} starter sits: the share of his carries and targets per teammate role</caption>
                <thead>
                  <tr>
                    <th scope="col">
                      <Term name="teammate_role">Role</Term>
                    </th>
                    <th scope="col" className="num">
                      Of his <Term name="carry_share">carries</Term>
                    </th>
                    <th scope="col" className="num">
                      Of his <Term name="target_share">targets</Term>
                    </th>
                    <th scope="col" className="num">Teammate-games</th>
                  </tr>
                </thead>
                <tbody>
                  {g.rows.map((r) => (
                    <tr key={r.role} data-role={r.role}>
                      <th scope="row">{r.role}</th>
                      <td className="num">{g.outPos === "RB" ? share(r.carry) : "–"}</td>
                      <td className="num">{share(r.target)}</td>
                      <td className="num">{fmtInt(r.n)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
      <p className="text-sm text-muted">
        Carries are shared out only when a running back sits (a receiver&apos;s carries are too few to split). A negative share means the role loses work: the
        offense leans elsewhere.
      </p>
    </div>
  );
}

/** The four candidates' pooled walk-forward scores, which one the pre-set rule picked, which one
 *  the site uses and, when they differ, the published reason (the owner's override). */
export function CandidateTable({ rows, params }: { rows: TOBacktestRow[]; params: TOParams | null }) {
  const d = disclosure(rows, params);
  if (!d) return <NotPublished what="The candidates' backtest">It appears with the first publish of the Teammate-out table.</NotPublished>;
  const lo = { points: best(d.rows, "maePoints"), carry: best(d.rows, "maeCarry"), target: best(d.rows, "maeTarget"), top: best(d.rows, "topHit") };
  const cell = (x: number | null, b: number | null, digits: number, asPct = false) => {
    const text = x === null ? "–" : asPct ? pct(x, 1) : x.toFixed(digits);
    return x !== null && x === b ? <strong>{text}</strong> : text;
  };
  const span = d.seasons.length ? `${d.seasons[0]}-${d.seasons[d.seasons.length - 1]}` : "";
  return (
    <div className="space-y-3" data-testid="to-candidates">
      <p>
        Four ways to predict the shares and points, simplest first, each tested <Term name="walk_forward">walk-forward</Term> over {d.seasons.length} seasons ({span},{" "}
        {fmtInt(d.used.n)} teammates in {fmtInt(d.used.events)} games): every season predicted only from the seasons before it. Lower{" "}
        <Term name="mae">MAE</Term> is better; the best value in each column is bold.
      </p>
      <div className="table-scroll">
        <table className="data-table" data-testid="to-candidates-table">
          <caption className="sr-only">The four candidates&apos; pooled walk-forward scores, the rule&apos;s pick and the one used</caption>
          <thead>
            <tr>
              <th scope="col">Candidate</th>
              <th scope="col" className="num">
                Points <Term name="mae">MAE</Term>
              </th>
              <th scope="col" className="num">
                Carry-share <Term name="mae">MAE</Term>
              </th>
              <th scope="col" className="num">
                Target-share <Term name="mae">MAE</Term>
              </th>
              <th scope="col" className="num">Top gainer named</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {d.rows.map((r) => (
              <tr key={r.candidate} data-candidate={r.candidate} data-chosen={r.chosen ? "true" : undefined} data-rule-pick={r.rulePick ? "true" : undefined}>
                <th scope="row">{r.title}</th>
                <td className="num">{cell(r.maePoints, lo.points, 3)}</td>
                <td className="num">{cell(r.maeCarry, lo.carry, 4)}</td>
                <td className="num">{cell(r.maeTarget, lo.target, 4)}</td>
                <td className="num">{cell(r.topHit, lo.top, 1, true)}</td>
                <td>{[r.rulePick ? "the rule's pick" : null, r.chosen ? "used on this site" : null].filter(Boolean).join("; ") || "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Disclosure d={d} rule={params?.rule ?? null} />
    </div>
  );
}

function Disclosure({ d, rule }: { d: NonNullable<ReturnType<typeof disclosure>>; rule: string | null }) {
  if (!d.overridden) {
    return (
      <p data-testid="to-disclosure" data-overridden="false">
        The rule fixed before the backtest ran picked <strong>{d.used.title}</strong>, and the site uses it.
      </p>
    );
  }
  return (
    <div className="space-y-2 rounded-lg border border-line-strong bg-surface p-3" data-testid="to-disclosure" data-overridden="true">
      <p>
        <strong>The rule picked {d.rulePick.title}; the site uses {d.used.title}.</strong> The rule was written down before any test season was scored
        {rule ? <> ({rule})</> : null}. Points MAE: {d.rulePick.title} {d.rulePick.maePoints?.toFixed(3) ?? "–"}, {d.used.title}{" "}
        {d.used.maePoints?.toFixed(3) ?? "–"}.
      </p>
      {d.why ? (
        <p>
          Why {d.used.title} is used anyway, as the owner recorded it: <q data-testid="to-why">{d.why}</q>
        </p>
      ) : null}
      <p className="text-sm text-muted">
        A single player&apos;s points are lopsided (most weeks a little, some weeks a lot), and MAE rewards predicting the typical week, so adding the average gain
        costs a little MAE. That is why the points are shown as an 80% range, not as one number to trust.
      </p>
    </div>
  );
}

/** How often the real points landed inside the 80% range (walk-forward), per position. */
export function CoverageTable({ rows }: { rows: TOCoverageRow[] }) {
  const xs = coverageRows(rows);
  if (!xs.length) return <NotPublished what="The 80% range's coverage">It appears with the first publish of the Teammate-out table.</NotPublished>;
  return (
    <div className="space-y-2" data-testid="to-coverage">
      <p>
        The range is the predicted points plus how far off past predictions for similar teammates were (the 10th to the 90th percentile of the misses, by position
        and predicted-points band). Tested walk-forward, seasons {xs[0].seasons}: did the real points land inside?
      </p>
      <div className="table-scroll">
        <table className="data-table" data-testid="to-coverage-table">
          <caption className="sr-only">The 80% range&apos;s coverage by position: below, inside and above the range</caption>
          <thead>
            <tr>
              <th scope="col">Position</th>
              <th scope="col" className="num">Teammates</th>
              <th scope="col" className="num">Below</th>
              <th scope="col" className="num">Inside</th>
              <th scope="col" className="num">Above</th>
            </tr>
          </thead>
          <tbody>
            {xs.map((r) => (
              <tr key={r.position} data-position={r.position}>
                <th scope="row">{r.position === "all" ? "All" : r.position}</th>
                <td className="num">{fmtInt(r.n)}</td>
                <td className="num">{fmtInt(r.below)}</td>
                <td className="num">
                  <strong>{r.coverage === null ? "–" : pct(r.coverage, 1)}</strong>
                </td>
                <td className="num">{fmtInt(r.above)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
