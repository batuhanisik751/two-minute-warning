import Term from "@/components/Term";
import { fmtInt, pct, pctRange } from "@/lib/format";
import { TRACK_INTERVAL_LEVEL } from "@/lib/method";
import { TAGS, droppedRates, rwMethodName, signed, tagTitle, tagVerdict, testSeasons, trackRow, type RegressionTrackRow } from "@/lib/regression";

const pts = (x: number) => x.toFixed(2);
const range = (lo: number | null, hi: number | null, f: (x: number) => string) => (lo !== null && hi !== null ? `${f(lo)} to ${f(hi)}` : null);

const HIT_WORDS: Record<(typeof TAGS)[number], React.ReactNode> = {
  sell_high: (
    <>
      his rest-of-season PPG was below his <Term name="ppg">PPG</Term> at the list
    </>
  ),
  buy_low: (
    <>
      his rest-of-season PPG was above his <Term name="ppg">PPG</Term> at the list
    </>
  ),
};

/** The honest track record: the projection's error against the two simple baselines, and each
 *  tag's hit rate against its base rate, from regression_track_record (headline weeks). */
export default function RegressionTrack({ rows, weeks, position = "all" }: { rows: RegressionTrackRow[]; weeks: number[]; position?: string }) {
  const q = { weeks: "headline", position };
  const model = trackRow(rows, { section: "value", ...q, method: "model", metric: "mae" });
  const seasons = testSeasons(rows);
  if (!model) return <p className="text-muted">No track record is published yet.</p>;
  const methods = ["model", "baseline_ppg", "baseline_last3"].map((m) => ({ m, v: trackRow(rows, { section: "value", ...q, method: m, metric: "mae" }) }));
  const diffs = ["baseline_ppg", "baseline_last3"].map((m) => ({ m, d: trackRow(rows, { section: "difference", ...q, method: `model-${m}`, metric: "mae" }) }));
  const when = `${seasons ? `the ${seasons.from}–${seasons.to} test seasons` : "the test seasons"}${weeks.length ? `, as-of weeks ${weeks.join(", ")}` : ""}`;
  return (
    <div className="space-y-6" data-testid="rw-track">
      <div>
        <p>
          Graded on {fmtInt(model.n)} player-weeks of {when} (the <Term name="walk_forward">walk-forward backtest</Term>): the{" "}
          <Term name="ppg_ros">projection</Term> missed his real <Term name="rest_of_season_ppg">rest-of-season PPG</Term> by{" "}
          <strong className="tnum">{pts(model.value)}</strong> points per game on average (<Term name="mae">mean absolute error</Term>; lower is better).
        </p>
        <div className="table-scroll mt-3">
          <table className="data-table" data-testid="rw-mae-table">
            <caption className="sr-only">Mean absolute error of the rest-of-season projection and the baselines</caption>
            <thead>
              <tr>
                <th scope="col">Method</th>
                <th scope="col" className="num">
                  Error (<Term name="mae">MAE</Term>)
                </th>
                <th scope="col">{pct(TRACK_INTERVAL_LEVEL)} interval</th>
                <th scope="col">Projection minus it</th>
              </tr>
            </thead>
            <tbody>
              {methods.map(({ m, v }) => {
                const d = diffs.find((x) => x.m === m)?.d;
                return (
                  <tr key={m}>
                    <th scope="row">{rwMethodName(m)}</th>
                    <td className="num">{v ? pts(v.value) : "–"}</td>
                    <td className="tnum">{v ? range(v.lo, v.hi, pts) ?? "–" : "–"}</td>
                    <td className="tnum">
                      {d ? `${signed(d.value, 2)} (${range(d.lo, d.hi, (x) => signed(x, 2)) ?? "no interval"})${d.hi !== null && d.hi < 0 ? ": closer" : d.lo !== null && d.lo > 0 ? ": further off" : ": not clearly different"}` : m === "model" ? "" : "–"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      <TagRates rows={rows} position={position} />
    </div>
  );
}

function TagRates({ rows, position }: { rows: RegressionTrackRow[]; position: string }) {
  const q = { section: "tag", weeks: "headline", position };
  const tags = TAGS.map((t) => ({
    t,
    tagged: trackRow(rows, { ...q, metric: t, rowGroup: "tagged" }),
    base: trackRow(rows, { ...q, metric: t, rowGroup: "base" }),
  })).filter((x): x is { t: (typeof TAGS)[number]; tagged: RegressionTrackRow; base: RegressionTrackRow } => !!x.tagged && !!x.base);
  if (!tags.length) return null;
  const dropped = droppedRates(rows, position);
  return (
    <div>
      <p>When a tag came true:</p>
      <ul className="mt-1 list-disc space-y-0.5 pl-5">
        {tags.map((x) => (
          <li key={x.t}>
            <strong>{tagTitle(x.t)}</strong>: {HIT_WORDS[x.t]}.
          </li>
        ))}
      </ul>
      <p className="mt-2">
        The base rate is how often that happened to every comparable player (every universe player): a tag is only
        useful if it beats it.
      </p>
      <div className="table-scroll mt-3">
        <table className="data-table" data-testid="rw-tag-table">
          <caption className="sr-only">Tag hit rates against base rates</caption>
          <thead>
            <tr>
              <th scope="col">Tag</th>
              <th scope="col" className="num">
                Tags graded
              </th>
              <th scope="col">Came true</th>
              <th scope="col">Base rate</th>
              <th scope="col">Verdict</th>
            </tr>
          </thead>
          <tbody>
            {tags.map(({ t, tagged, base }) => {
              const v = tagVerdict(tagged, base);
              return (
                <tr key={t} data-tag={t} data-verdict={v}>
                  <th scope="row">{tagTitle(t)}</th>
                  <td className="num">{fmtInt(tagged.n)}</td>
                  <td className="tnum">
                    {pct(tagged.value, 1)}
                    {tagged.lo !== null && tagged.hi !== null ? <span className="block text-xs text-muted">{pctRange(tagged.lo, tagged.hi, 1)}</span> : null}
                  </td>
                  <td className="tnum">
                    {pct(base.value, 1)}
                    {base.lo !== null && base.hi !== null ? <span className="block text-xs text-muted">{pctRange(base.lo, base.hi, 1)}</span> : null}
                  </td>
                  <td>{v === "above" ? "Above the base rate" : v === "below" ? "Below the base rate" : "The same as the base rate"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {dropped.map((d, i) => (
        // keyed by position: the dropped tag's name is not shown anywhere, not even in the payload
        <p key={i} className="mt-3" data-testid="dropped-tag">
          A third tag was also tested and dropped: it came true for {pct(d.tagged.value, 1)} of the{" "}
          {fmtInt(d.tagged.n)} players it tagged, against {pct(d.base.value, 1)} for every comparable player,{" "}
          {d.verdict === "above" ? "above the base rate." : d.verdict === "below" ? "below the base rate." : "so it predicted nothing better than the base rate."}
        </p>
      ))}
    </div>
  );
}
