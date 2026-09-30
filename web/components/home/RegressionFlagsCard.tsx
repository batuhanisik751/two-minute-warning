import Link from "next/link";
import RegressionList from "@/components/RegressionList";
import Term from "@/components/Term";
import { KindBadge } from "@/components/ui";
import { seasonWeek } from "@/lib/format";
import { getLatestRegressionLive } from "@/lib/queries/regression";
import { regressionHref, tagRows, tagTitle, type Tag } from "@/lib/regression";

const TOP = 3;
const SHOWN: Tag[] = ["sell_high", "buy_low"];

/** The home page's Regression flags card: the newest live list's top 3 Sell-high and top 3
 *  Buy-low (the weekly report's order), else a line saying there is none yet. */
export default async function RegressionFlagsCard() {
  const live = await getLatestRegressionLive();
  const h = live?.header;
  return (
    <section aria-labelledby="regression-card" className="min-w-0 rounded-xl border border-line bg-surface p-4 sm:p-5" data-testid="regression-card">
      <p className="kicker">Regression Watch</p>
      <div className="mt-1 flex flex-wrap items-center gap-3">
        <h2 id="regression-card" className="section-title">
          Regression flags{h ? `, ${seasonWeek(h.season, h.week)}` : ""}
        </h2>
        {h ? <KindBadge kind="live" /> : null}
      </div>
      {live && h ? (
        <>
          <p className="mt-2 text-sm text-muted">
            Players scoring far from what their chances were worth (<Term name="fpoe">points over expected</Term>), and
            whose <Term name="ppg_ros">rest-of-season projection</Term> says it will not last.
          </p>
          {h.note ? <p className="mt-2 text-sm font-medium">{h.note}</p> : null}
          <div className="mt-4 grid gap-5">
            {SHOWN.map((t) => {
              const rows = tagRows(live.rows, t);
              return (
                <section key={t} aria-labelledby={`flag-${t}`} className="min-w-0">
                  <h3 id={`flag-${t}`} className="display mb-2 text-xl uppercase">
                    <Term name={t}>{tagTitle(t)}</Term>{" "}
                    <span className="text-base text-muted">
                      (top {Math.min(TOP, rows.length)} of {rows.length})
                    </span>
                  </h3>
                  {rows.length ? (
                    <RegressionList rows={rows.slice(0, TOP)} withGarbage reasons={false} outcomes={false} label={`${tagTitle(t)} top ${TOP}, ${seasonWeek(h.season, h.week)}, live`} />
                  ) : (
                    <p className="text-muted">No {tagTitle(t)} player this week.</p>
                  )}
                </section>
              );
            })}
          </div>
          <p className="mt-3 text-sm">
            <Link href={regressionHref({ season: h.season, week: h.week, kind: "live" })}>All tags with reasons, the chart and the track record</Link>
          </p>
        </>
      ) : (
        <p className="mt-2 text-muted">
          No live Regression Watch list yet: the first one appears after a week&apos;s games and data have arrived.{" "}
          <Link href="/regression">What Regression Watch does</Link>
        </p>
      )}
    </section>
  );
}
