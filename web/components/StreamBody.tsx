import Link from "next/link";
import StreamList from "@/components/StreamList";
import Term from "@/components/Term";
import { EmptyState, Note } from "@/components/ui";
import ThirdPartyNote from "@/components/ThirdPartyNote";
import { fmtInt, fmtUtc, kindLabel, pct, pointsRange, points as pointsDiff, seasonWeek } from "@/lib/format";
import { AS_OF_WEEKDAY, CHANCE_RANGE_LEVEL, TRACK_INTERVAL_LEVEL } from "@/lib/method";
import type { ListKey } from "@/lib/params";
import { getGlossary } from "@/lib/queries/glossary";
import { getStreamList, getStreamTrack, type StreamHeader } from "@/lib/queries/stream";
import { isBaselineMethod, starterWeek, streamComparison, streamMethodName, streamMethodOf, streamTopN, verdictWords } from "@/lib/streamer";

/** The K or D/ST list of /waivers: the explanation (once), the list's header, its notes and rows. */
export default async function StreamBody({ chosen, position, currentSeason }: { chosen: ListKey; position: string; currentSeason: number | null }) {
  const [data, gloss] = await Promise.all([getStreamList(chosen.season, chosen.week, position, chosen.kind), getGlossary()]);
  const topN = streamTopN(gloss);
  if (!data) {
    return (
      <div className="mt-4">
        <EmptyState title={`No ${position === "DST" ? "D/ST" : position} list for this week`}>
          <p>{seasonWeek(chosen.season, chosen.week)} has no list for this position. Choose another week or position above.</p>
        </EmptyState>
      </div>
    );
  }
  const { header, picks } = data;
  const k = kindLabel(chosen.kind);
  const who = position === "DST" ? "team defenses" : "kickers";
  return (
    <>
      <StreamMeta header={header} shown={picks.length} who={who} />
      <div className="mt-4">
        <StreamExplainer position={position} model={header.model} topN={topN} />
      </div>
      <div className="mt-4 space-y-3">
        {header.incomplete ? <Note tone="warn">This list was made before all of the week&apos;s data had arrived (published as incomplete).</Note> : null}
        {header.note ? (
          <Note>
            <strong>Published with this list: </strong>
            {header.note}
          </Note>
        ) : null}
        {picks.length && picks.every((p) => p.chance === null) ? (
          <Note>
            <strong>Chance and priority: not available for this list.</strong> The chance is how often similar picks
            started in earlier seasons&apos; backtests; the first backtest season has no earlier season to learn it from.
          </Note>
        ) : null}
        {picks.length && picks.every((p) => p.reasons.length === 0) ? (
          <Note>
            <strong>No reasons for this list.</strong> Reasons are written by the weekly list; the backtest that
            reconstructed this list did not generate them.
          </Note>
        ) : null}
        {picks.some((p) => p.reasons.length > 0) ? <ThirdPartyNote /> : null}
        {chosen.season < (currentSeason ?? chosen.season) ? (
          <p className="text-sm text-muted">
            <Term name="current_franchise">Teams</Term> are shown by today&apos;s franchise code and name.
          </p>
        ) : null}
      </div>
      <p className="mt-4 text-sm text-muted">
        <Term name="stream_chance">Chance</Term>: how often similar picks had a {starterWeek(topN, position)} the next week, with
        its {pct(CHANCE_RANGE_LEVEL)} range. <Term name="priority">Priority</Term>: the suggested pickup priority.{" "}
        <Term name="y_start" showFormula>
          Outcome
        </Term>
        : hit (a {starterWeek(topN, position)}), no hit, or pending until next week&apos;s games are final.
      </p>
      <div className="mt-3">
        {picks.length ? (
          <StreamList picks={picks} topN={topN} label={`${position === "DST" ? "D/ST" : position} list, ${seasonWeek(chosen.season, chosen.week)}, ${k.short.toLowerCase()}`} />
        ) : (
          <EmptyState title="This list has no picks" />
        )}
      </div>
    </>
  );
}

function StreamMeta({ header, shown, who }: { header: StreamHeader; shown: number; who: string }) {
  const t = header.trainingSeasons;
  const rule = isBaselineMethod(streamMethodOf(header.model));
  return (
    <p className="mt-2 text-sm text-muted">
      As of <time dateTime={header.asOf}>{fmtUtc(header.asOf)}</time> (the <Term name="as_of">as-of time</Term>);{" "}
      {header.kind === "live" ? "made" : "reconstructed"} on <time dateTime={header.generatedAt}>{fmtUtc(header.generatedAt)}</time>. Ranked from{" "}
      {fmtInt(header.nPool)} {who} in the pool; {shown === header.nPool ? "all are shown" : `the top ${shown} are shown`}.
      {rule ? (
        " Ranked by a simple rule, not a model."
      ) : t.length ? (
        <>
          {" "}
          The model learned from {Math.min(...t)}
          {t.length > 1 ? `–${Math.max(...t)}` : ""} only.
        </>
      ) : null}
    </p>
  );
}

/** Once per list: what streaming is, why no betting lines, and the backtest in one sentence. */
export async function StreamExplainer({ position, model, topN, compact = false }: { position: string; model: string; topN: Record<string, number>; compact?: boolean }) {
  const rows = await getStreamTrack();
  const c = streamComparison(rows, position, streamMethodOf(model));
  const who = position === "DST" ? "team defenses" : "kickers";
  const week = starterWeek(topN, position);
  const top = c?.metric.match(/^p_at_(\d+)$/)?.[1] ?? null;
  const sentence = c && top ? (
    <p className="mt-2" data-testid="stream-verdict">
      {isBaselineMethod(c.ours.method) ? (
        <>
          The {position === "DST" ? "D/ST" : "K"} list uses a simple rule, not a model: it ranks by {streamMethodName(c.ours.method, position)}. In the{" "}
          {c.seasons.replace("-", "–")} <Term name="walk_forward">backtest</Term> the rule&apos;s top {top} had a {week}{" "}
          <strong className="tnum">{pct(c.ours.value, 1)}</strong> of the time, against{" "}
          <strong className="tnum">{pct(c.other.value, 1)}</strong> for the best model ({streamMethodName(c.other.method, position)})
        </>
      ) : (
        <>
          In the {c.seasons.replace("-", "–")} <Term name="walk_forward">backtest</Term> the model&apos;s top {top} {who} had a {week}{" "}
          <strong className="tnum">{pct(c.ours.value, 1)}</strong> of the time, against{" "}
          <strong className="tnum">{pct(c.other.value, 1)}</strong> for the best simple rule ({streamMethodName(c.other.method, position)})
        </>
      )}
      {c.diff && c.verdict ? (
        <>
          : {pointsDiff(c.diff.value)}
          {c.diff.lo !== null && c.diff.hi !== null ? ` (${pct(TRACK_INTERVAL_LEVEL)} interval ${pointsRange(c.diff.lo, c.diff.hi)})` : ""}, {verdictWords(c.verdict)}
        </>
      ) : null}
      .{c.baseRate !== null ? ` A random pick from the pool had one ${pct(c.baseRate, 1)} of the time.` : ""}
    </p>
  ) : null;
  if (compact) return sentence;
  return (
    <div className="rounded-lg border border-line bg-surface p-4 text-sm" data-testid="stream-explainer">
      <p>
        <strong>A one-week pick (streaming).</strong> Each {AS_OF_WEEKDAY} the {who} probably still on waivers (the{" "}
        <Term name="stream_pool">pool</Term>) are ranked by their chance of a {week} in the very next game: pick one up,
        start it once, and look again next week.
      </p>
      <p className="mt-2">
        <strong>No betting lines, by design.</strong> The lines in the data are set just before kickoff; on the Tuesday of a
        waiver claim they do not exist yet, so using them would peek at the future. The list uses the next opponent&apos;s
        season so far instead (and no weather, for the same reason).
      </p>
      {sentence}
      <p className="mt-2">
        <Link href="/methodology#streamer">How the streamer works and all its results</Link>
      </p>
    </div>
  );
}
