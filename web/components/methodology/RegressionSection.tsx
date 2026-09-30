import Link from "next/link";
import ShrinkTable from "@/components/methodology/ShrinkTable";
import RegressionTrack, { testSeasons } from "@/components/RegressionTrack";
import Term from "@/components/Term";
import { EmptyState } from "@/components/ui";
import { fmtInt, pct, pctRange } from "@/lib/format";
import { getRegressionParams, getRegressionTrack } from "@/lib/queries/regression";
import { TAGS, paramNumber, shrinkRows, signed, tagTitle, trackRow, type RegressionTrackRow } from "@/lib/regression";

const POS = ["QB", "RB", "WR", "TE", "all"];

type Variant = { name?: string; target?: string; half_life?: number | null; garbage?: string };

/** The chosen variant in words (model_versions.params.variant). */
function variantWords(v: Variant | undefined): string {
  if (!v) return "";
  const toward = v.target === "mean" ? "toward his position's average FPOE/game" : "toward zero";
  const recency = typeof v.half_life === "number" ? `recent games count more (a half-life of ${v.half_life} games)` : "every game counts the same";
  const garbage = v.garbage === "all" ? "garbage time kept" : "garbage time left out";
  return `efficiency shrunk ${toward}, ${recency}, ${garbage}`;
}

/** Which variant each test season used (the `choice` rows), as runs of seasons. */
function choices(rows: RegressionTrackRow[]): string {
  const c = rows.filter((r) => r.section === "choice" && r.season !== null && r.method).sort((a, b) => (a.season ?? 0) - (b.season ?? 0));
  const runs: { m: string; from: number; to: number }[] = [];
  for (const r of c) {
    const last = runs[runs.length - 1];
    if (last && last.m === r.method && last.to === (r.season as number) - 1) last.to = r.season as number;
    else runs.push({ m: r.method as string, from: r.season as number, to: r.season as number });
  }
  return runs.map((x) => `${x.m} (${x.from === x.to ? x.from : `${x.from}–${x.to}`})`).join(", ");
}

function PositionMae({ rows }: { rows: RegressionTrackRow[] }) {
  const q = (position: string, method: string, section = "value") =>
    trackRow(rows, { section, weeks: "headline", position, method: section === "value" ? method : `model-${method}`, metric: "mae" });
  const f = (r: RegressionTrackRow | undefined) => (r ? r.value.toFixed(2) : "–");
  return (
    <div className="table-scroll mt-3">
      <table className="data-table" data-testid="rw-position-mae">
        <caption className="text-left text-sm text-muted">Mean absolute error by position (points per game; lower is better)</caption>
        <thead>
          <tr>
            <th scope="col">Position</th>
            <th scope="col" className="num">Graded</th>
            <th scope="col" className="num">Projection</th>
            <th scope="col" className="num">Season PPG</th>
            <th scope="col" className="num">Last 3</th>
            <th scope="col">Projection minus season PPG</th>
          </tr>
        </thead>
        <tbody>
          {POS.map((p) => {
            const m = q(p, "model");
            const d = q(p, "baseline_ppg", "difference");
            if (!m) return null;
            return (
              <tr key={p}>
                <th scope="row">{p === "all" ? "Pooled" : p}</th>
                <td className="num">{fmtInt(m.n)}</td>
                <td className="num">{f(m)}</td>
                <td className="num">{f(q(p, "baseline_ppg"))}</td>
                <td className="num">{f(q(p, "baseline_last3"))}</td>
                <td className="tnum">{d ? `${signed(d.value, 2)}${d.lo !== null && d.hi !== null ? ` (${signed(d.lo, 2)} to ${signed(d.hi, 2)})` : ""}` : "–"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PositionTags({ rows }: { rows: RegressionTrackRow[] }) {
  const cell = (t: string, p: string, g: string) => trackRow(rows, { section: "tag", weeks: "headline", position: p, metric: t, rowGroup: g });
  const rate = (r: RegressionTrackRow | undefined) =>
    r ? (
      <>
        {pct(r.value, 1)}
        {r.lo !== null && r.hi !== null ? <span className="block text-xs text-muted">{pctRange(r.lo, r.hi, 1)}</span> : null}
      </>
    ) : (
      "–"
    );
  return (
    <div className="table-scroll mt-3">
      <table className="data-table" data-testid="rw-position-tags">
        <caption className="text-left text-sm text-muted">Tag hit rates by position against their base rates</caption>
        <thead>
          <tr>
            <th scope="col">Tag</th>
            <th scope="col">Position</th>
            <th scope="col" className="num">Tags graded</th>
            <th scope="col">Came true</th>
            <th scope="col">Base rate</th>
          </tr>
        </thead>
        <tbody>
          {TAGS.flatMap((t) =>
            POS.map((p) => {
              const tagged = cell(t, p, "tagged");
              if (!tagged) return null;
              return (
                <tr key={`${t}-${p}`}>
                  <th scope="row">{tagTitle(t)}</th>
                  <td>{p === "all" ? "Pooled" : p}</td>
                  <td className="num">{fmtInt(tagged.n)}</td>
                  <td className="tnum">{rate(tagged)}</td>
                  <td className="tnum">{rate(cell(t, p, "base"))}</td>
                </tr>
              );
            }),
          )}
        </tbody>
      </table>
    </div>
  );
}

/** /methodology#regression: the stability study, the projection in words, the backtest
 *  results and the limits (every number from the database). */
export default async function RegressionSection() {
  const [p, rows] = await Promise.all([getRegressionParams(), getRegressionTrack()]);
  const shrink = p ? shrinkRows(p.params) : [];
  const first = shrink.length ? Math.min(...shrink.map((r) => r.first ?? Infinity)) : null;
  const last = shrink.length ? Math.max(...shrink.map((r) => r.last ?? -Infinity)) : null;
  const span = first !== null && last !== null && Number.isFinite(first) && Number.isFinite(last) ? `${first}–${last}` : "the earlier seasons";
  const games = p?.weeks ?? [];
  const xs = p ? paramNumber(p.params, "x_sell") : null;
  const xb = p ? paramNumber(p.params, "x_buy") : null;
  const variant = (p?.params as { variant?: Variant } | null)?.variant;
  const seasons = testSeasons(rows);
  return (
    <section aria-labelledby="regression">
      <h2 id="regression" className="section-title scroll-mt-24">
        How Regression Watch works
      </h2>
      <p className="mt-3">
        A player&apos;s points split into <strong>opportunity</strong> (<Term name="xfp">xFP</Term>: what an average player
        would have scored from his targets and carries) and <strong>efficiency</strong> (<Term name="fpoe">FPOE</Term> = points
        minus xFP). The question is which of the two repeats.
      </p>
      <h3 className="display mt-6 text-xl uppercase">The stability study</h3>
      <p className="mt-2">
        Each player-season is split into two halves (odd and even games). A number that is a real trait shows up in both
        halves; one that was luck does not (<Term name="split_half_correlation">split-half correlation</Term>). Across every
        position opportunity repeated far more than efficiency: a player&apos;s xFP/game in one half says a lot about the
        other half, his FPOE/game very little. The same halves measure how much of FPOE/game is lasting: the{" "}
        <Term name="signal_variance">signal</Term> the halves share against the <Term name="noise_variance">noise</Term> of a
        single game. After g games his FPOE/game deserves the share r(g) = signal / (signal + noise / g) of its value (the{" "}
        <Term name="shrinkage_factor">shrinkage factor</Term>); the rest is expected to fade.
      </p>
      {shrink.length ? (
        <>
          <ShrinkTable rows={shrink} metric="fpoe" games={games} caption={`FPOE/game, every play: estimated on ${span}, the table today's lists use`} />
          <ShrinkTable rows={shrink} metric="fpoe_ng" games={games} caption={`FPOE/game without garbage time, ${span}`} />
          <p className="mt-2 text-sm text-muted">
            What it means: even after a full season&apos;s games only a small share of a player&apos;s points over expected is
            worth keeping; <Term name="games_for_half_weight">games for half weight</Term> is how many games it would take to
            believe half of it. Each backtest season used a table estimated only on the seasons before it.
          </p>
        </>
      ) : (
        <div className="mt-3">
          <EmptyState title="No Regression Watch parameters published yet" />
        </div>
      )}
      <RegressionMethod variant={variant} xs={xs} xb={xb} decile={p ? paramNumber(p.params, "decile") : null} choice={choices(rows)} />
      <h3 className="display mt-6 text-xl uppercase">Results{seasons ? `, ${seasons.from}–${seasons.to}` : ""}</h3>
      {rows.length ? (
        <div className="mt-2 space-y-6">
          <RegressionTrack rows={rows} weeks={games} />
          <PositionMae rows={rows} />
          <PositionTags rows={rows} />
        </div>
      ) : (
        <div className="mt-3">
          <EmptyState title="No Regression Watch track record published yet" />
        </div>
      )}
      <RegressionLimits />
    </section>
  );
}

function RegressionMethod({ variant, xs, xb, decile, choice }: { variant: Variant | undefined; xs: number | null; xb: number | null; decile: number | null; choice: string }) {
  const top = decile !== null ? `top ${pct(decile)}` : "top decile";
  return (
    <>
      <h3 className="display mt-6 text-xl uppercase">The projection and the tags</h3>
      <ul className="mt-2 list-disc space-y-1.5 pl-5">
        <li>
          <strong>Who</strong>: the <Term name="regression_universe">universe</Term>, the fantasy-relevant quarterbacks, running
          backs, receivers and tight ends of the week.
        </li>
        <li>
          <strong>The projection</strong> (<Term name="ppg_ros">rest-of-season points per game</Term>) = his opportunity so far
          (xFP/game) plus the shrinkage factor times his efficiency (FPOE/game): his chances are taken at face value, his luck
          or skill surplus only at the share the stability study says repeats.
          {variant ? ` Today's parameters: ${variantWords(variant)} (${variant.name ?? "unnamed"}).` : ""}
          {choice ? ` Each backtest season used the version that did best on the seasons before it: ${choice}.` : ""}
        </li>
        <li>
          <strong>
            <Term name="sell_high">Sell-high</Term>
          </strong>
          : FPOE/game in the {top} of his position and a projection well below his PPG
          {xs !== null ? ` (by at least ${xs} points per game today)` : ""}.{" "}
          <strong>
            <Term name="buy_low">Buy-low</Term>
          </strong>
          : the mirror image{xb !== null ? ` (projection at least ${xb} points per game above his PPG today)` : ""}.{" "}
          <strong>
            <Term name="legit">Legit</Term>
          </strong>
          : a starter by PPG whose FPOE/game is not in the {top}. The cutoffs (<Term name="tag_threshold_x">X</Term>) are
          chosen each season on earlier seasons only.
        </li>
        <li>
          <strong>Garbage time</strong> (<Term name="is_garbage_time">when the game is decided</Term>): the lists can show PPG,
          xFP/game and FPOE/game without those plays; the projection is the one the parameters chose.
        </li>
      </ul>
      <p className="mt-2 text-sm">
        <Link href="/regression">This week&apos;s tags, the chart and the time machine</Link>
      </p>
    </>
  );
}

function RegressionLimits() {
  return (
    <>
      <h3 className="display mt-8 text-xl uppercase">Limitations</h3>
      <ul className="mt-2 list-disc space-y-1.5 pl-5">
        <li>
          <strong>xFP is another model&apos;s output (PROJECT_SPEC 6.3).</strong> Expected points come from ffopportunity&apos;s
          models, trained across many seasons, later ones included, and the garbage-time flag from nflfastR&apos;s win
          probability model: an old week&apos;s xFP knows a little about the future. A mild, known leak; the entries marked
          model output in the glossary are these.
        </li>
        <li>The outcome counts games played: a player who gets hurt is graded on the games he played; one with too few games left is not graded.</li>
        <li>One set of parameters for every position each season, and one noise size per position: a player with many chances a game swings more than one with few.</li>
        <li>Early-season lists (before the backtested weeks) were never checked this early; their notes say so.</li>
      </ul>
    </>
  );
}
