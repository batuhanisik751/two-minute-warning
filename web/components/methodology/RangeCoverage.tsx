import Term from "@/components/Term";
import { EmptyState } from "@/components/ui";
import { fmtInt, pct } from "@/lib/format";
import { getRegressionRangeCoverage } from "@/lib/queries/regression";
import { coverageRows } from "@/lib/regression";

/** The 80% range of the projection (feature #4) and its coverage check, per position, read
 *  from the published backtest rows and outcomes (lib/queries/regression.ts): nothing typed. */
export default async function RangeCoverage() {
  const rows = coverageRows(await getRegressionRangeCoverage());
  const all = rows.find((r) => r.position === "All");
  return (
    <section aria-labelledby="rw-range" data-testid="rw-range-section">
      <h3 id="rw-range" className="display mt-6 scroll-mt-24 text-xl uppercase">
        The 80% range
      </h3>
      <p className="mt-2">
        Each projection comes with an <Term name="projection_range">80% range</Term>: the projection plus the 10th and the
        90th percentile of how far the projection missed (actual rest-of-season points per game minus the projection) for
        players at his position with about as many weeks left, in the backtest&apos;s earlier seasons only. It covers how
        well he scores per game, not games he misses.
      </p>
      {all ? (
        <>
          <p className="mt-2">
            Checked on the backtest lists of {all.first === all.last ? all.first : `${all.first}–${all.last}`} (the first backtest season has no earlier season, so no
            range): the actual rest-of-season points per game fell inside the range for {pct(all.coverage, 1)} of{" "}
            {fmtInt(all.n)} graded players.
          </p>
          <div className="table-scroll mt-3">
            <table className="data-table" data-testid="rw-range-coverage">
              <caption className="text-left text-sm text-muted">
                Coverage of the 80% range by position (graded backtest rows; the target is 80%)
              </caption>
              <thead>
                <tr>
                  <th scope="col">Position</th>
                  <th scope="col" className="num">
                    Graded
                  </th>
                  <th scope="col" className="num">
                    Inside
                  </th>
                  <th scope="col" className="num">
                    Below
                  </th>
                  <th scope="col" className="num">
                    Above
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.position}>
                    <th scope="row">{r.position === "All" ? "Pooled" : r.position}</th>
                    <td className="num">{fmtInt(r.n)}</td>
                    <td className="num">{pct(r.coverage, 1)}</td>
                    <td className="num">{pct(r.below / r.n, 1)}</td>
                    <td className="num">{pct(r.above / r.n, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div className="mt-3">
          <EmptyState title="No 80% range is published yet" />
        </div>
      )}
    </section>
  );
}
