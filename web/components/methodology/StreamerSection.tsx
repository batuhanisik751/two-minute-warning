import Term from "@/components/Term";
import { StreamExplainer } from "@/components/StreamBody";
import { EmptyState } from "@/components/ui";
import { fmtInt, pct, pctRange } from "@/lib/format";
import { TRACK_INTERVAL_LEVEL } from "@/lib/method";
import { positionLabel, positionShort, sortPositions } from "@/lib/positions";
import { getGlossary } from "@/lib/queries/glossary";
import { getStreamModels, getStreamTrack } from "@/lib/queries/stream";
import { isBaselineMethod, starterWeek, streamMethodName, streamMethodOf, streamTopN, type StreamTrackRow } from "@/lib/streamer";

const METRICS = ["p_at_5", "p_at_3", "p_at_1"];
const METRIC_TITLES: Record<string, string> = { p_at_5: "Top 5", p_at_3: "Top 3", p_at_1: "No. 1 pick" };
const ORDER = ["logit", "lgbm", "baseline_last_points", "baseline_ppg", "baseline_opponent"];

function ResultsTable({ rows, position, published, topN }: { rows: StreamTrackRow[]; position: string; published: string | null; topN: Record<string, number> }) {
  const pooled = rows.filter((r) => r.position === position && r.scope === "pooled" && r.trainOn === "pool");
  const methods = [...new Set(pooled.map((r) => r.method))].sort((a, b) => ORDER.indexOf(a) - ORDER.indexOf(b));
  const any = pooled[0];
  if (!any) return null;
  const base = any.nRows && any.nPos !== null ? any.nPos / any.nRows : null;
  const cell = (m: string, metric: string) => pooled.find((r) => r.method === m && r.metric === metric);
  return (
    <div className="table-scroll mt-3">
      <table className="data-table" data-testid={`stream-results-${position}`}>
        <caption className="text-left text-sm text-muted">
          {positionLabel(position)}, {any.seasons.replace("-", "–")}: share of picks with a {starterWeek(topN, position)} the next
          week ({fmtInt(any.nGroups ?? 0)} weekly lists, {pct(TRACK_INTERVAL_LEVEL)} intervals)
        </caption>
        <thead>
          <tr>
            <th scope="col">Method</th>
            {METRICS.map((m) => (
              <th key={m} scope="col">
                {METRIC_TITLES[m]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {methods.map((m) => (
            <tr key={m} data-method={m}>
              <th scope="row">
                {streamMethodName(m, position)}
                {isBaselineMethod(m) ? " (simple rule)" : ""}
                {m === published ? <span className="ml-2 rounded border border-accent px-1.5 text-xs font-normal">on the site</span> : null}
              </th>
              {METRICS.map((metric) => {
                const r = cell(m, metric);
                return (
                  <td key={metric} className="tnum">
                    {r?.value != null ? pct(r.value, 1) : "–"}
                    {r?.lo != null && r?.hi != null ? <span className="block text-xs text-muted">{pctRange(r.lo, r.hi, 1)}</span> : null}
                  </td>
                );
              })}
            </tr>
          ))}
          {base !== null ? (
            <tr>
              <th scope="row">A random pick from the pool (base rate)</th>
              <td className="tnum" colSpan={METRICS.length}>
                {pct(base, 1)} ({fmtInt(any.nPos ?? 0)} of {fmtInt(any.nRows ?? 0)} pool picks)
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

/** /methodology#streamer: the K and D/ST streamer in words, and its results table from
 *  stream_track_record (every number from the database). */
export default async function StreamerSection() {
  const [rows, models, gloss] = await Promise.all([getStreamTrack(), getStreamModels(), getGlossary()]);
  const topN = streamTopN(gloss);
  const positions = sortPositions([...new Set(rows.map((r) => r.position))]);
  return (
    <section aria-labelledby="streamer">
      <h2 id="streamer" className="section-title scroll-mt-24">
        The K and D/ST streamer
      </h2>
      <p className="mt-3">
        Kickers and team defenses score very differently from one week to the next, and much of it depends on the opponent,
        so many players do not keep one all season: each week they pick up a free one with a good matchup and drop him the
        week after. That is <strong>streaming</strong>, and the streamer ranks the candidates every Tuesday.
      </p>
      <ul className="mt-3 list-disc space-y-1.5 pl-5">
        <li>
          <strong>Who is on the list</strong> (the <Term name="stream_pool">streaming pool</Term>): the kickers and team
          defenses probably still on waivers, estimated the same way as the Waiver Radar&apos;s pool, because past waiver
          wires are not public. The kicker pool includes kickers on practice squads or released, who almost never kick; a good
          list puts them last.
        </li>
        <li>
          <strong>The target</strong> (<Term name="y_start">a starter next week</Term>): a{" "}
          {positions.length ? positions.map((p) => `${starterWeek(topN, p).replace(" week", "")} ${positionShort(p)}`).join(" or ") : "starter"} week in
          the very next game. One week only: a streamer is picked up for one game.
        </li>
        <li>
          <strong>What it knows on Tuesday</strong>: his own points so far (per game, last game, rank) and the experts&apos;
          preseason rank; for kickers, attempts per game, long attempts, accuracy by distance and whether he kicked in his
          team&apos;s last game; for defenses, sacks, takeaways, return touchdowns and points allowed; his team&apos;s offense
          (points, red-zone trips, drives that stall); next week&apos;s opponent so far; home or away, dome or roof; the
          experts&apos; weekly rank where it exists. Every feature is in the glossary below.
        </li>
        <li>
          <strong>No betting lines and no weather, by design.</strong> The lines in the data are set just before kickoff and
          the forecast is not known on Tuesday: using either in a backtest would peek at the future.
        </li>
        <li>
          <strong>Tested the same honest way</strong> as the Radar: every season predicted by a method that learned only from
          earlier seasons, and compared with simple rules (last game&apos;s points, points per game so far, and the next
          opponent). Where a simple rule did as well as the model, the site uses the rule.
        </li>
      </ul>
      {rows.length ? (
        <div className="mt-6 space-y-8">
          {positions.map((p) => (
            <div key={p}>
              <h3 className="display text-xl uppercase">{positionLabel(p)}</h3>
              {models[p] ? <StreamExplainer position={p} model={models[p]} topN={topN} compact /> : null}
              <ResultsTable rows={rows} position={p} published={models[p] ? streamMethodOf(models[p]) : null} topN={topN} />
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-4">
          <EmptyState title="No streamer track record published yet" />
        </div>
      )}
      <h3 className="display mt-8 text-xl uppercase">Limitations</h3>
      <ul className="mt-2 list-disc space-y-1.5 pl-5">
        <li>Tuesday knowledge only: no injury news, depth-chart changes, weather or lines after Tuesday. A Friday look still matters.</li>
        <li>Small samples: each season gives only a few hundred pool picks per position, so every rate above has a range of a few points.</li>
        <li>The pool is an estimate of a waiver wire, and the scoring and league shape are the site&apos;s defaults: other settings change who counts as a starter.</li>
        <li>Chances are track records of similar picks, not promises: most weeks, most streaming picks are coin flips at best.</li>
      </ul>
    </section>
  );
}
