import Link from "next/link";
import { FoldTable } from "@/components/Fold";
import Term from "@/components/Term";
import { fmtInt, pct, pctRange, points, pointsRange } from "@/lib/format";
import { AS_OF_WEEKDAY, BASELINE_LAST_POINTS, TRACK_INTERVAL_LEVEL, methodName } from "@/lib/method";
import { getRadarModel } from "@/lib/queries/radar";
import { getTrackRows, type TrackRow } from "@/lib/queries/track";
import { getRadarLivePicks } from "@/lib/queries/track-record";
import { headline } from "@/lib/track";
import { liveSummary, radarCalibration, radarSeasons, topNOf, type RadarSeason } from "@/lib/track-record";
import CalibrationFigure from "./CalibrationFigure";
import LiveResults from "./LiveResults";
import { H4, NotPublished, Panel, StatTiles } from "./parts";

const LABEL = "y_hit";
const iv = (r: { low: number | null; high: number | null }) =>
  r.low !== null && r.high !== null ? `${pct(TRACK_INTERVAL_LEVEL)} interval ${pctRange(r.low, r.high, 1)}` : null;
const excludesZero = (r: { low: number | null; high: number | null }) => (r.low !== null && r.low > 0) || (r.high !== null && r.high < 0);
const rate = (r: TrackRow | null) => (r && r.value !== null ? pct(r.value, 1) : "–");

/** /track-record#radar: the Waiver Radar's walk-forward backtest (headline, seasons, calibration)
 *  and its live lists, every number from track_record and the live picks' outcomes. */
export default async function RadarTrack() {
  const [rows, model, live] = await Promise.all([getTrackRows(["pooled", "diff", "season", "calibration_fixed"]), getRadarModel(), getRadarLivePicks()]);
  const radar = model ?? "logit";
  const head = headline(rows, radar);
  const topN = head ? topNOf(head.radar.metric) : null;
  const seasons = head ? radarSeasons(rows, radar, BASELINE_LAST_POINTS, LABEL, head.radar.metric) : [];
  const cal = radarCalibration(rows, radar, LABEL);
  return (
    <>
      <Panel kind="backtest" title="The backtest" context="Waiver Radar" id="radar-backtest">
        {head && head.radar.value !== null && head.baseline.value !== null && topN ? (
          <>
            <p>
              Every week of {head.range.seasonFrom}&ndash;{head.range.seasonTo}, rebuilt as it stood that {AS_OF_WEEKDAY} and ranked by a model
              trained only on earlier seasons: how many of each list&apos;s top {topN} were{" "}
              <Term name="y_hit" showFormula>
                hits
              </Term>{" "}
              (<Term name="precision_at_10">precision@{topN}</Term>), against a list of last week&apos;s top scorers.
            </p>
            <StatTiles
              testId="radar-headline"
              stats={[
                { label: `The Radar's top ${topN} that hit`, value: pct(head.radar.value, 1), interval: iv(head.radar), note: head.radar.nLists !== null ? `${fmtInt(head.radar.nLists)} weekly lists` : null },
                { label: methodName(BASELINE_LAST_POINTS), value: pct(head.baseline.value, 1), interval: iv(head.baseline), note: "the simple rule to beat" },
                ...(head.diff && head.diff.value !== null
                  ? [{ label: "The Radar minus the rule", value: points(head.diff.value), interval: head.diff.low !== null && head.diff.high !== null ? `${pct(TRACK_INTERVAL_LEVEL)} interval ${pointsRange(head.diff.low, head.diff.high)}` : null, note: excludesZero(head.diff) ? "the interval excludes 0: a clear difference" : "the interval includes 0: no clear difference" }]
                  : []),
              ]}
            />
            <H4>Season by season</H4>
            {seasons.length ? <RadarSeasons rows={seasons} topN={topN} /> : <NotPublished what="The per-season results" />}
          </>
        ) : (
          <NotPublished what="The Waiver Radar's backtest" />
        )}
        <H4>Calibration</H4>
        {cal.length ? (
          <CalibrationFigure id="radar-calibration" caption="Is the Radar's probability right?" points={cal} observedLabel="Really hit">
            Every pool player of every backtest list, grouped by the model&apos;s probability. The site shows a{" "}
            <Term name="chance">chance</Term> instead of the raw probability because the probabilities ran high for the likeliest
            players.
          </CalibrationFigure>
        ) : (
          <NotPublished what="The Radar's calibration" />
        )}
        <p className="text-sm">
          <Link href="/methodology#results">Every result table, by position and against the experts</Link>
        </p>
      </Panel>
      <Panel kind="live" title="Live this season" context="Waiver Radar" id="radar-live">
        {topN || !live.length ? (
          <LiveResults summary={liveSummary(live, topN ?? 0)} what={`top-${topN} picks`} success="hit" pendingUntil="every game of the weeks after the list has been played" testId="radar-live" />
        ) : (
          <NotPublished what="The live results">Live lists exist, but without a published backtest metric they are not counted here.</NotPublished>
        )}
      </Panel>
    </>
  );
}

function RadarSeasons({ rows, topN }: { rows: RadarSeason[]; topN: number }) {
  const label = "The Waiver Radar's backtest, season by season";
  return (
    <FoldTable
      label={label}
      rows={rows.map((r) => (
        <tr data-row="" key={r.season}>
          <th scope="row" data-cell="season">{r.season}</th>
          <td data-cell="value" className="num">{rate(r.ours)}</td>
          <td data-cell="value" className="num">{rate(r.other)}</td>
          <td data-cell="value" className="num">{rate(r.baseRate)}</td>
          <td data-cell="value" className="num">{r.ours?.nLists != null ? fmtInt(r.ours.nLists) : "–"}</td>
          <td data-cell="value" className="num whitespace-nowrap">{r.ours?.nTopHits != null && r.ours.nTop ? `${fmtInt(r.ours.nTopHits)} of ${fmtInt(r.ours.nTop)}` : "–"}</td>
        </tr>
      ))}
      table={(body) => (
        <div className="table-scroll">
          <table className="data-table" data-testid="radar-seasons">
            <caption className="text-left text-sm text-muted">
              Share of each list&apos;s top {topN} that hit, newest season first ({pct(TRACK_INTERVAL_LEVEL)} intervals are pooled only)
            </caption>
            <thead>
              <tr>
                <th scope="col">Season</th>
                <th scope="col" className="num">The Radar</th>
                <th scope="col" className="num">{methodName(BASELINE_LAST_POINTS)}</th>
                <th scope="col" className="num">A random pool player</th>
                <th scope="col" className="num">Lists</th>
                <th scope="col" className="num">Top-{topN} picks that hit</th>
              </tr>
            </thead>
            {body}
          </table>
        </div>
      )}
    />
  );
}
