import Link from "next/link";
import Term from "@/components/Term";
import { DISCLAIMER } from "@/lib/site";
import { EmptyState, PageHeader, PosBadge } from "@/components/ui";
import { fmtInt, pct, pctRange, points, pointsRange, tierLabel } from "@/lib/format";
import {
  AS_OF_TIME_UTC,
  AS_OF_WEEKDAY,
  BASELINE_LAST_POINTS,
  CHANCE_RANGE_LEVEL,
  LABEL_NAMES,
  POSITIONS,
  TRACK_INTERVAL_LEVEL,
  methodName,
} from "@/lib/method";
import { getGlossary, type GlossaryRow } from "@/lib/queries/glossary";
import { getRadarModel } from "@/lib/queries/radar";
import { getTierStats, getTrackRows, type TrackRow } from "@/lib/queries/track";
import DecisionsSection from "@/components/methodology/DecisionsSection";
import BoardSection from "@/components/methodology/BoardSection";
import ModelCards from "@/components/methodology/ModelCards";
import HotSeatSection from "@/components/methodology/HotSeatSection";
import QuestionableSection from "@/components/methodology/QuestionableSection";
import PlayoffPlannerSection from "@/components/methodology/PlayoffPlannerSection";
import CoachTendenciesSection from "@/components/methodology/CoachTendenciesSection";
import TeammateOutSection from "@/components/methodology/TeammateOutSection";
import RegressionSection from "@/components/methodology/RegressionSection";
import StreamerSection from "@/components/methodology/StreamerSection";
import { headline, select, widestRange } from "@/lib/track";
import { pageMetadata } from "@/lib/seo";
import { THIRD_PARTY_NOTE, showThirdPartyRanks } from "@/lib/third-party";

export const metadata = pageMetadata("/methodology", "Methodology", "How Two-Minute Warning works: the data sources, the point-in-time rule, every model and its backtest, the limits, the disclaimers and the full glossary.");

const SCOPES = ["pooled", "diff", "position", "position_diff", "experts", "experts_diff", "calibration_fixed"];
const MODEL_ORDER = ["logit", "lgbm", "baseline_ecr", "baseline_last_points", "baseline_snap_delta"];
const KIND_TITLES: Record<string, string> = {
  concept: "Concepts",
  feature: "Features (what the model knows at the as-of)",
  label: "Labels (what happened afterwards)",
  metric: "Metrics",
  identifier: "Identifiers (never model inputs)",
};

const order = (m: string) => {
  const i = MODEL_ORDER.indexOf(m);
  return i === -1 ? MODEL_ORDER.length : i;
};

function withInterval(r: TrackRow | undefined, digits = 1) {
  if (!r || r.value === null) return <span className="text-muted">not reported</span>;
  return (
    <>
      <span className="font-semibold">{pct(r.value, digits)}</span>
      {r.low !== null && r.high !== null ? (
        <span className="block text-xs text-muted">{pctRange(r.low, r.high, digits)}</span>
      ) : null}
    </>
  );
}

function diffCell(r: TrackRow | undefined) {
  if (!r || r.value === null) return <span className="text-muted">not reported</span>;
  const spansZero = r.low !== null && r.high !== null && r.low <= 0 && r.high >= 0;
  return (
    <>
      <span className="font-semibold">{points(r.value)}</span>
      {r.low !== null && r.high !== null ? (
        <span className="block text-xs text-muted">{pointsRange(r.low, r.high)}</span>
      ) : null}
      {spansZero ? <span className="block text-xs text-muted">the interval includes 0: no clear difference</span> : null}
    </>
  );
}

function PooledTable({ rows, label }: { rows: TrackRow[]; label: string }) {
  const pooled = rows.filter((r) => r.scope === "pooled" && r.label === label && r.metric === "p_at_10");
  const range = widestRange(pooled);
  if (!range) return null;
  const all = select(rows, { scope: "pooled", metric: "p_at_10", label, exclRostered: false, range });
  const excl = select(rows, { scope: "pooled", metric: "p_at_10", label, exclRostered: true, range });
  const base = select(rows, { scope: "pooled", metric: "base_rate", label, exclRostered: false, range })[0];
  const models = [...new Set(all.map((r) => r.model))].sort((a, b) => order(a) - order(b));
  const seasons = `${range.seasonFrom}–${range.seasonTo}`;
  return (
    <div className="table-scroll">
      <table className="data-table" data-testid={`pooled-${label}`}>
        <caption>
          Precision@10 for &ldquo;{LABEL_NAMES[label] ?? label}&rdquo;, {seasons}, every weekly list pooled (
          {pct(TRACK_INTERVAL_LEVEL)} intervals under each number)
        </caption>
        <thead>
          <tr>
            <th scope="col">Method</th>
            <th scope="col" className="num">
              All pool players
            </th>
            <th scope="col" className="num">
              Without players most real leagues had rostered
            </th>
            <th scope="col" className="num">
              Lists
            </th>
            <th scope="col" className="num">
              Top-10 picks that hit
            </th>
          </tr>
        </thead>
        <tbody>
          {models.map((m) => {
            const a = all.find((r) => r.model === m);
            const e = excl.find((r) => r.model === m);
            return (
              <tr key={m}>
                <th scope="row">{methodName(m)}</th>
                <td className="num">{withInterval(a)}</td>
                <td className="num">{withInterval(e)}</td>
                <td className="num">{a?.nLists !== null && a?.nLists !== undefined ? fmtInt(a.nLists) : "–"}</td>
                <td className="num">
                  {a?.nTopHits !== null && a?.nTopHits !== undefined && a?.nTop ? `${fmtInt(a.nTopHits)} of ${fmtInt(a.nTop)}` : "–"}
                </td>
              </tr>
            );
          })}
          {base && base.value !== null ? (
            <tr>
              <th scope="row">{methodName("base_rate")}</th>
              <td className="num">
                <span className="font-semibold">{pct(base.value, 1)}</span>
              </td>
              <td className="num">
                {(() => {
                  const b = select(rows, { scope: "pooled", metric: "base_rate", label, exclRostered: true, range })[0];
                  return b && b.value !== null ? <span className="font-semibold">{pct(b.value, 1)}</span> : "–";
                })()}
              </td>
              <td className="num">{base.nLists !== null ? fmtInt(base.nLists) : "–"}</td>
              <td className="num">
                {base.nPositives !== null && base.nRows !== null
                  ? `${fmtInt(base.nPositives)} of ${fmtInt(base.nRows)} pool players`
                  : "–"}
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

function DiffTable({ rows, model }: { rows: TrackRow[]; model: string }) {
  const diffs = rows.filter((r) => r.scope === "diff" && r.label === "y_hit" && !r.exclRostered && r.model === model);
  const range = widestRange(diffs);
  if (!range) return null;
  const inR = diffs.filter((r) => r.seasonFrom === range.seasonFrom && r.seasonTo === range.seasonTo);
  const others = [...new Set(inR.map((r) => r.scopeValue))].sort((a, b) => order(a) - order(b));
  return (
    <div className="table-scroll">
      <table className="data-table" data-testid="diff-table">
        <caption>
          The Radar minus each alternative, on the same lists ({range.seasonFrom}&ndash;{range.seasonTo}, all pool
          players, precision@10 in percentage points)
        </caption>
        <thead>
          <tr>
            <th scope="col">Compared with</th>
            <th scope="col" className="num">
              Difference ({pct(TRACK_INTERVAL_LEVEL)} interval)
            </th>
            <th scope="col" className="num">
              Seasons the Radar was ahead
            </th>
          </tr>
        </thead>
        <tbody>
          {others.map((o) => {
            const d = inR.find((r) => r.scopeValue === o && r.metric === "p_at_10_diff");
            const won = inR.find((r) => r.scopeValue === o && r.metric === "seasons_won");
            return (
              <tr key={o}>
                <th scope="row">{methodName(o)}</th>
                <td className="num">{diffCell(d)}</td>
                <td className="num">
                  {won && won.value !== null ? `${fmtInt(won.value)} of ${won.nLists !== null ? fmtInt(won.nLists) : "?"}` : "–"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PositionTable({ rows, model }: { rows: TrackRow[]; model: string }) {
  const pos = rows.filter((r) => r.scope === "position" && r.label === "y_hit" && !r.exclRostered && r.metric === "p_at_10");
  const range = widestRange(pos);
  if (!range) return null;
  const inR = (r: TrackRow) => r.seasonFrom === range.seasonFrom && r.seasonTo === range.seasonTo;
  const diffs = rows.filter(
    (r) => r.scope === "position_diff" && r.label === "y_hit" && !r.exclRostered && r.metric === "p_at_10_diff" && r.model === model && inR(r),
  );
  return (
    <div className="table-scroll">
      <table className="data-table" data-testid="position-table">
        <caption>
          By position ({range.seasonFrom}&ndash;{range.seasonTo}, all pool players, precision@10)
        </caption>
        <thead>
          <tr>
            <th scope="col">Position</th>
            <th scope="col" className="num">
              {methodName(model)}
            </th>
            <th scope="col" className="num">
              {methodName(BASELINE_LAST_POINTS)}
            </th>
            <th scope="col" className="num">
              Difference
            </th>
          </tr>
        </thead>
        <tbody>
          {POSITIONS.map((p) => {
            const a = pos.find((r) => r.model === model && r.scopeValue === p && inR(r));
            const b = pos.find((r) => r.model === BASELINE_LAST_POINTS && r.scopeValue === p && inR(r));
            const d = diffs.find((r) => r.scopeValue === `${p} vs ${BASELINE_LAST_POINTS}`);
            if (!a && !b) return null;
            return (
              <tr key={p}>
                <th scope="row">
                  <PosBadge pos={p} />
                </th>
                <td className="num">{withInterval(a)}</td>
                <td className="num">{withInterval(b)}</td>
                <td className="num">{diffCell(d)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ExpertsTable({ rows, model }: { rows: TrackRow[]; model: string }) {
  const ex = rows.filter((r) => r.scope === "experts" && r.label === "y_hit" && !r.exclRostered && r.metric === "p_at_10");
  const range = widestRange(ex);
  if (!range) return null;
  const a = ex.find((r) => r.model === model);
  const e = ex.find((r) => r.model === "baseline_ecr");
  const d = rows.find(
    (r) => r.scope === "experts_diff" && r.label === "y_hit" && !r.exclRostered && r.model === model && r.metric === "p_at_10_diff",
  );
  if (!a || !e) return null;
  return (
    <div className="table-scroll">
      <table className="data-table" data-testid="experts-table">
        <caption>
          Against the experts, where their ranks exist ({range.seasonFrom}&ndash;{range.seasonTo},{" "}
          {a.nLists !== null ? `${fmtInt(a.nLists)} lists` : "lists with expert ranks"})
        </caption>
        <thead>
          <tr>
            <th scope="col">Method</th>
            <th scope="col" className="num">
              Precision@10
            </th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">{methodName(model)}</th>
            <td className="num">{withInterval(a)}</td>
          </tr>
          <tr>
            <th scope="row">{methodName("baseline_ecr")}</th>
            <td className="num">{withInterval(e)}</td>
          </tr>
          <tr>
            <th scope="row">Difference</th>
            <td className="num">{diffCell(d)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function CalibrationTable({ rows, model }: { rows: TrackRow[]; model: string }) {
  const cal = rows.filter((r) => r.scope === "calibration_fixed" && r.label === "y_hit" && !r.exclRostered && r.model === model);
  if (!cal.length) return null;
  const bins = [...new Set(cal.map((r) => r.scopeValue))].sort((a, b) => parseFloat(a) - parseFloat(b));
  const range = widestRange(cal)!;
  return (
    <div className="table-scroll">
      <table className="data-table" data-testid="calibration-table">
        <caption>
          <Term name="calibration">Calibration</Term> of the model&apos;s probability ({range.seasonFrom}&ndash;
          {range.seasonTo}): predictions grouped by probability
        </caption>
        <thead>
          <tr>
            <th scope="col">Probability group</th>
            <th scope="col" className="num">
              Average predicted
            </th>
            <th scope="col" className="num">
              Really hit
            </th>
            <th scope="col" className="num">
              Predictions
            </th>
          </tr>
        </thead>
        <tbody>
          {bins.map((b) => {
            const pred = cal.find((r) => r.scopeValue === b && r.metric === "mean_pred");
            const obs = cal.find((r) => r.scopeValue === b && r.metric === "observed");
            const [lo, hi] = b.split("-").map(Number);
            return (
              <tr key={b}>
                <th scope="row" className="tnum">
                  {Number.isFinite(lo) && Number.isFinite(hi) ? pctRange(lo, hi) : b}
                </th>
                <td className="num">{pred?.value !== null && pred?.value !== undefined ? pct(pred.value, 1) : "–"}</td>
                <td className="num">{obs?.value !== null && obs?.value !== undefined ? pct(obs.value, 1) : "–"}</td>
                <td className="num">{obs?.nRows !== null && obs?.nRows !== undefined ? fmtInt(obs.nRows) : "–"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function GlossaryList({ rows }: { rows: GlossaryRow[] }) {
  const kinds = [...new Set(rows.map((r) => r.kind))].sort(
    (a, b) => Object.keys(KIND_TITLES).indexOf(a) - Object.keys(KIND_TITLES).indexOf(b),
  );
  return (
    <>
      {kinds.map((k) => (
        <section key={k} aria-labelledby={`gloss-${k}`} className="mt-6">
          <h3 id={`gloss-${k}`} className="display text-xl uppercase">
            {KIND_TITLES[k] ?? k}
          </h3>
          <dl className="mt-2 divide-y divide-line rounded-lg border border-line bg-surface">
            {rows
              .filter((r) => r.kind === k)
              .map((r) => (
                <div key={r.name} id={`term-${r.name}`} className="scroll-mt-24 px-4 py-3">
                  <dt className="font-semibold">
                    {r.title} <span className="font-mono text-xs font-normal text-muted">{r.name}</span>
                    {r.modelOutput ? (
                      <span className="ml-2 rounded border border-pending px-1.5 py-0.5 text-xs font-normal text-pending">
                        model output
                      </span>
                    ) : null}
                  </dt>
                  <dd className="mt-1 text-sm">
                    <p>{r.explanation}</p>
                    <p className="mt-1 text-muted">
                      Unit: {r.unit}. How it is computed: {r.formula}
                    </p>
                    {r.verified ? <p className="mt-1 text-muted">Checked: {r.verified}</p> : null}
                  </dd>
                </div>
              ))}
          </dl>
        </section>
      ))}
    </>
  );
}

export default async function MethodologyPage() {
  const [rows, gloss, tiers, model] = await Promise.all([
    getTrackRows(SCOPES),
    getGlossary(),
    getTierStats(),
    getRadarModel(),
  ]);
  const radar = model ?? "logit";
  const head = headline(rows, radar);
  const modelOutputs = gloss.filter((g) => g.modelOutput && g.kind === "feature");
  const myTiers = tiers.filter((t) => t.model === radar && t.label === "y_hit");

  return (
    <>
      <PageHeader title="Methodology" kicker="How it works">
        How the Waiver Radar, the K and D/ST streamer, Regression Watch, Questionable outcomes, Teammate out, the Playoff planner, the Decision Report Card, Coach
        tendencies, the Hot-Seat Meter and the Cliff board work, what data they use, how they were tested and what the tests found. Every result on this page is read from the published track records, the priority table,
        the frozen parameters and the glossary; none is typed in by hand. Each module&apos;s results season by season, its
        calibration and its live lists are on <Link href="/track-record">the Track record page</Link>.
      </PageHeader>

      <nav aria-label="On this page" className="mb-10 rounded-lg border border-line bg-surface p-4 text-sm">
        <p className="mb-2 font-display text-sm font-bold tracking-wider text-muted uppercase">On this page</p>
        <ul className="grid gap-x-4 gap-y-1 sm:grid-cols-2 lg:grid-cols-4">
          <li>
            <a href="#sources">Data sources and credits</a>
          </li>
          <li>
            <a href="#point-in-time">The point-in-time rule</a>
          </li>
          <li>
            <a href="#leakage">Leakage safeguards</a>
          </li>
          <li>
            <a href="#radar">How the Waiver Radar works</a>
          </li>
          <li>
            <a href="#results">Results</a>
          </li>
          <li>
            <a href="#streamer">The K and D/ST streamer</a>
          </li>
          <li>
            <a href="#regression">Regression Watch</a>
          </li>
          <li>
            <a href="#questionable">Questionable outcomes</a>
          </li>
          <li>
            <a href="#teammate-out">Teammate out</a>
          </li>
          <li>
            <a href="#playoff-planner">Playoff planner</a>
          </li>
          <li>
            <a href="#decisions">The Decision Report Card</a>
          </li>
          <li>
            <a href="#coach-tendencies">Coach tendencies</a>
          </li>
          <li>
            <a href="#hot-seat">The Hot-Seat Meter</a>
          </li>
          <li>
            <a href="#board">The Cliff board</a>
          </li>
          <li>
            <a href="#model-cards">Model cards</a>
          </li>
          <li>
            <a href="#glossary">Glossary</a>
          </li>
          <li>
            <a href="#disclaimers">Disclaimers</a>
          </li>
        </ul>
      </nav>

      <div className="max-w-4xl space-y-12">
        <section aria-labelledby="sources">
          <h2 id="sources" className="section-title scroll-mt-24">
            Data sources and credits
          </h2>
          <ul className="mt-3 list-disc space-y-2 pl-6">
            <li>
              <a href="https://nflverse.nflverse.com/">nflverse</a>, loaded with nflreadpy: play-by-play (built by
              nflfastR, whose win probability the Decision Report Card is compared with), player stats, schedules and
              results with their betting lines (the Hot-Seat Meter&apos;s market expectation), weekly rosters, injury
              reports, depth charts, snap counts, player details, and the NFL Combine results and Next Gen Stats (the
              Cliff board). Every page depends on it.
            </li>
            <li>
              ffverse&apos;s <a href="https://github.com/ffverse/ffopportunity">ffopportunity</a> (through nflreadpy):
              the expected fantasy points (xFP) behind several Radar features, and the per-play rows that Regression
              Watch and the player pages&apos; charts value with our own walk-forward models (the player pages showed
              ffopportunity&apos;s xFP until October 2, 2026).
            </li>
            <li>
              <a href="https://github.com/dynastyprocess/data">DynastyProcess</a>: the cross-platform player id map and
              the archive of <a href="https://www.fantasypros.com/">FantasyPros</a> expert rankings. The preseason
              expert ranks decide part of the candidate pool (from the 2020 season on), the in-season ranks are the
              experts&apos; baseline in the results
              {showThirdPartyRanks() ? (
                <>, and a &ldquo;ranked before the season&rdquo; reason quotes them.</>
              ) : (
                <>
                  .{" "}<span data-testid="third-party-note">{THIRD_PARTY_NOTE}</span>
                </>
              )}
            </li>
            <li>
              <a href="https://www.pro-football-reference.com/">Pro Football Reference</a>: the original source of
              nflverse&apos;s snap counts (snap share).
            </li>
            <li>
              Head-coach departures (the Hot-Seat Meter&apos;s labels): every change nflverse&apos;s schedules show,
              researched from cited public pages, mostly{" "}
              <a href="https://en.wikipedia.org/">Wikipedia</a>&apos;s &ldquo;NFL season&rdquo; pages and the coaches&apos;
              own pages, with the source kept for every row. The project&apos;s owner accepted that research in bulk
              rather than re-checking every row, so a mistake in it is possible.
            </li>
            <li>
              A benchmark only (no published number is computed from it): the{" "}
              <a href="https://github.com/nflverse/nfl4th">nfl4th</a> R package&apos;s fourth-down recommendations, which
              the Decision Report Card&apos;s grades are compared with.
            </li>
          </ul>
          <p className="mt-3 text-sm text-muted">
            Not used: the league data of any fantasy platform (the owner&apos;s own league stays on the owner&apos;s
            computer), and no NFL or team logos. FTN&apos;s charting and the participation data are downloaded by the pipeline but no
            published number uses them.
          </p>
        </section>

        <section aria-labelledby="point-in-time">
          <h2 id="point-in-time" className="section-title scroll-mt-24">
            The point-in-time rule
          </h2>
          <p className="mt-3">
            Every list is made at an <Term name="as_of">as-of time</Term>: the {AS_OF_WEEKDAY} at {AS_OF_TIME_UTC} UTC
            after a week&apos;s games, once Monday night&apos;s game is final and before most fantasy waivers are
            processed. Every row of data carries the moment it became public (<Term name="available_at">available at</Term>
            ), and a list may use only rows public at or before its as-of time. The same rule rebuilds every past
            Tuesday, which is what makes the reconstructed lists honest: they see only what was public then.
          </p>
          <p className="mt-3">
            When the exact publication time of some data is unknown, the estimate errs late: a late estimate costs a
            little information, an early one would let the future leak in. That is why some data waits: next
            week&apos;s injury reports, for example, come out from Wednesday on, so a {AS_OF_WEEKDAY} list sees only the
            reports of the week just played, and a roster move made on Monday shows up in the next week&apos;s roster.
          </p>
        </section>

        <section aria-labelledby="leakage">
          <h2 id="leakage" className="section-title scroll-mt-24">
            Leakage safeguards
          </h2>
          <ul className="mt-3 list-disc space-y-2 pl-6">
            <li>
              <strong>Walk-forward by season.</strong> To grade a season, the model learns only from the seasons before
              it; its settings are tuned on the last of those seasons. The training code refuses any row of the test
              season (<Term name="walk_forward">walk-forward backtest</Term>).
            </li>
            <li>
              <strong>Labels come strictly after the as-of.</strong> What happened next is built only from data that
              became public after the list was made.
            </li>
            <li>
              <strong>Preprocessing is learned from training rows only</strong> (filling in missing values, scaling).
            </li>
            <li>
              <strong>No player, team or coach identifiers as inputs</strong>, and no labels or expert ranks.
            </li>
            <li>
              <strong>A leakage test</strong> re-runs features with future rows deleted and scrambled: a feature whose
              value changes fails it, and deliberately leaky features are shown to fail.
            </li>
            <li>
              <strong>Model-derived source columns.</strong> Some inputs are themselves outputs of models that were
              trained on many seasons, including seasons after some backtest weeks: a mild, known leak that the backtest
              cannot remove. It is allowed in this first version and stated here. Regression Watch re-estimates its
              expected points walk-forward since October 1, 2026 (its lists, stability study and backtest); the
              Radar&apos;s features still use the model columns.
              {modelOutputs.length ? (
                <>
                  {" "}
                  The Radar features that are model outputs:{" "}
                  {modelOutputs.map((g, i) => (
                    <span key={g.name}>
                      {i > 0 ? ", " : ""}
                      <a href={`#term-${g.name}`}>{g.title}</a>
                    </span>
                  ))}
                  .
                </>
              ) : null}
            </li>
          </ul>
        </section>

        <section aria-labelledby="radar">
          <h2 id="radar" className="section-title scroll-mt-24">
            How the Waiver Radar works
          </h2>
          <h3 className="display mt-5 text-xl uppercase">Who it ranks: the candidate pool</h3>
          <p className="mt-2">{gloss.find((g) => g.name === "candidate_pool")?.explanation}</p>
          <p className="mt-2 text-sm text-muted">
            The rule: {gloss.find((g) => g.name === "candidate_pool")?.formula}
          </p>

          <h3 className="display mt-7 text-xl uppercase">What counts as a hit</h3>
          <dl className="mt-2 space-y-2">
            {["y_hit", "y_sustained", "starter_threshold"].map((n) => {
              const g = gloss.find((x) => x.name === n);
              return g ? (
                <div key={n}>
                  <dt className="font-semibold">{g.title}</dt>
                  <dd className="text-sm">
                    {g.explanation} <span className="text-muted">{g.formula}</span>
                  </dd>
                </div>
              ) : null;
            })}
          </dl>
          <p className="mt-2 text-sm">
            Outcomes stay <strong>pending</strong> until every game of the weeks after the list has been played; a pending
            outcome is never counted as a miss.
          </p>

          <h3 className="display mt-7 text-xl uppercase">Chance, not the model&apos;s probability</h3>
          <p className="mt-2">
            The model (a regularized logistic regression, calibrated on the season before the one it predicts) gives
            each player a probability. The site shows his <Term name="chance">chance</Term> instead: how often players
            the Radar rated like him really hit in earlier seasons&apos; backtests, with a {pct(CHANCE_RANGE_LEVEL)}{" "}
            range. The calibration table below shows why: the probabilities ran too high for the most likely players.
            A reconstructed list without a chance (it was not computed for that list) shows the rank and the outcome
            only, never the raw probability in its place.
          </p>

          <h3 className="display mt-7 text-xl uppercase">Suggested priority</h3>
          {myTiers.length ? (
            <div className="table-scroll mt-2">
              <table className="data-table" data-testid="tier-table">
                <caption>
                  How each priority did in the backtest ({myTiers[0].seasonFrom}&ndash;{myTiers[0].seasonTo}, the
                  players ranked on {fmtInt(myTiers[0].lists)} weekly lists)
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Priority</th>
                    <th scope="col" className="num">
                      When similar players hit
                    </th>
                    <th scope="col" className="num">
                      Players
                    </th>
                    <th scope="col" className="num">
                      Hits
                    </th>
                    <th scope="col" className="num">
                      Hit rate
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {myTiers.map((t) => (
                    <tr key={t.tier}>
                      <th scope="row">{tierLabel(t.tier)}</th>
                      <td className="num">{pctRange(t.chanceLow, t.chanceHigh)}</td>
                      <td className="num">{fmtInt(t.players)}</td>
                      <td className="num">{fmtInt(t.hits)}</td>
                      <td className="num">{t.hitRate !== null ? pct(t.hitRate, 1) : "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="mt-2 text-muted">No priority table has been published.</p>
          )}

          <h3 className="display mt-7 text-xl uppercase">Live and reconstructed lists</h3>
          <p className="mt-2">
            A <strong>live</strong> list is made in real time, between the as-of time and the next week&apos;s first
            kickoff, and is never changed afterwards. Every other list is <strong>reconstructed</strong> (a backtest):
            made later, from the data as it stood that {AS_OF_WEEKDAY}, by a model trained only on earlier seasons. The
            site labels each list in words and draws live lists with a solid frame, reconstructed ones with a dashed
            frame. See <Link href="/waivers">the Waivers page</Link> to browse both.
          </p>
        </section>

        <section aria-labelledby="results">
          <h2 id="results" className="section-title scroll-mt-24">
            Results
          </h2>
          {head && head.radar.value !== null && head.baseline.value !== null ? (
            <p className="mt-3" data-testid="results-headline">
              {head.range.seasonFrom}&ndash;{head.range.seasonTo}: on average {pct(head.radar.value, 1)} of the
              Radar&apos;s weekly top 10 were hits, against {pct(head.baseline.value, 1)} for last week&apos;s top
              scorers
              {head.diff && head.diff.value !== null && head.diff.low !== null && head.diff.high !== null
                ? ` (${points(head.diff.value)}, ${pct(TRACK_INTERVAL_LEVEL)} interval ${pointsRange(head.diff.low, head.diff.high)})`
                : ""}
              . <Term name="precision_at_10">Precision@10</Term> is the share of a list&apos;s top 10 who hit. The{" "}
              <Term name="interval">intervals</Term> come from redrawing whole seasons.
            </p>
          ) : null}
          {rows.length ? (
            <div className="mt-4 space-y-8">
              <PooledTable rows={rows} label="y_hit" />
              <DiffTable rows={rows} model={radar} />
              <PositionTable rows={rows} model={radar} />
              <ExpertsTable rows={rows} model={radar} />
              <PooledTable rows={rows} label="y_sustained" />
              <CalibrationTable rows={rows} model={radar} />
            </div>
          ) : (
            <div className="mt-4">
              <EmptyState title="No track record published yet" />
            </div>
          )}
        </section>

        <StreamerSection />

        <RegressionSection />

        <QuestionableSection />

        <TeammateOutSection />

        <PlayoffPlannerSection />

        <DecisionsSection />

        <CoachTendenciesSection />

        <HotSeatSection />

        <BoardSection />

        <ModelCards />

        <section aria-labelledby="glossary">
          <h2 id="glossary" className="section-title scroll-mt-24">
            Glossary
          </h2>
          <p className="mt-3">
            Every feature, label, metric and concept the pipeline knows, from its registry. Entries marked{" "}
            <em>model output</em> come from another model (see the leakage safeguards above).
          </p>
          {gloss.length ? <GlossaryList rows={gloss} /> : <EmptyState title="The glossary has not been published" />}
        </section>

        <section aria-labelledby="disclaimers">
          <h2 id="disclaimers" className="section-title scroll-mt-24">
            Disclaimers
          </h2>
          <p className="mt-3">{DISCLAIMER}</p>
          <p className="mt-2">
            A chance is a track record of similar players, not a promise: most players on any list do not become
            starters.
          </p>
        </section>
      </div>
    </>
  );
}
