import type { ReactNode } from "react";
import Term from "@/components/Term";
import { fmtInt, fmtNum, pct } from "@/lib/format";
import { STABILITY_INTERVAL_LEVEL, STABILITY_MIN_GAMES, STABILITY_RESAMPLES } from "@/lib/method";
import { extremes, shrinkGames, shrinkTable, splitHalf, studyWindow, type Cell, type StabilityRow } from "@/lib/stability";

// /methodology#stability: the stability study's real numbers (regression_stability, step R1):
// the split-half correlations of opportunity and efficiency, the parts of efficiency, and the
// shrinkage table r(g) the projection uses. Every number is a row of the table.

/** Two decimals, the site's minus sign, and no "-0.00". */
const r2 = (x: number) => fmtNum(x, 2);
const span = (s: string) => s.replace("-", "–");

/** A correlation with its interval under it (and its own n where the column's n differs). */
function Value({ c, n = false }: { c: Cell | null; n?: boolean }) {
  if (!c) return <span className="text-muted">–</span>;
  return (
    <>
      {r2(c.value)}
      {c.lo !== null && c.hi !== null ? (
        <span className="block text-xs whitespace-nowrap text-muted">
          {r2(c.lo)} to {r2(c.hi)}
        </span>
      ) : null}
      {n ? <span className="block text-xs whitespace-nowrap text-muted">n {fmtInt(c.n)}</span> : null}
    </>
  );
}

type Col = { head: ReactNode; metric: string | Record<string, string> };

/** One split-half table: a row per position, a column per metric. */
function SplitTable({ rows, split, cols, caption, testId, perCellN = false }: { rows: StabilityRow[]; split: string; cols: Col[]; caption: string; testId: string; perCellN?: boolean }) {
  const table = splitHalf(rows, split, cols.map((c) => c.metric));
  if (!table.length) return null;
  return (
    <div className="table-scroll mt-3">
      <table className="data-table" data-testid={testId}>
        <caption className="text-left text-sm text-muted">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Position</th>
            {perCellN ? null : (
              <th scope="col" className="num">
                <Term name="player_season">Player-seasons</Term>
              </th>
            )}
            {cols.map((c, i) => (
              <th key={i} scope="col" className="num">
                {c.head}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.map((t) => (
            <tr key={t.position}>
              <th scope="row">{t.position}</th>
              {perCellN ? null : <td className="num">{t.n === null ? "–" : fmtInt(t.n)}</td>}
              {t.cells.map((c, i) => (
                <td key={i} className="num">
                  <Value c={c} n={perCellN} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The shrinkage table of one metric: signal, noise, average, games for half weight, r(g). */
function ShrinkStudy({ rows, metric, seasons, games, caption }: { rows: StabilityRow[]; metric: string; seasons: string; games: number[]; caption: string }) {
  const lines = shrinkTable(rows, metric, seasons, games);
  if (!lines.length) return null;
  return (
    <div className="table-scroll mt-3">
      <table className="data-table" data-testid={`stability-shrink-${metric}`}>
        <caption className="text-left text-sm text-muted">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Position</th>
            <th scope="col" className="num">
              <Term name="player_season">Player-seasons</Term>
            </th>
            <th scope="col" className="num">
              <Term name="signal_variance">Signal</Term>
            </th>
            <th scope="col" className="num">
              <Term name="noise_variance">Noise per game</Term>
            </th>
            <th scope="col" className="num">
              <Term name="prior_mean">Average per game</Term>
            </th>
            <th scope="col" className="num">
              <Term name="games_for_half_weight">Games for half weight</Term>
            </th>
            {games.map((g, i) => (
              <th key={g} scope="col" className="num">
                {i === 0 ? <Term name="reliability">{`r(${g})`}</Term> : `r(${g})`}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {lines.map((l) => (
            <tr key={l.position}>
              <th scope="row">{l.position}</th>
              <td className="num">{fmtInt(l.n)}</td>
              <td className="num">{r2(l.signal)}</td>
              <td className="num">{l.noise.toFixed(1)}</td>
              <td className="num">{l.prior === null ? "–" : r2(l.prior)}</td>
              <td className="num">{l.halfWeight === null ? "–" : fmtInt(Math.round(l.halfWeight))}</td>
              {l.r.map((c, i) => (
                <td key={games[i]} className="num">
                  <Value c={c} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const MAIN: Col[] = [
  { head: <><Term name="xfp">xFP</Term>/game (opportunity)</>, metric: "xfp" },
  { head: <><Term name="fpoe">FPOE</Term>/game (efficiency)</>, metric: "fpoe" },
  { head: <><Term name="fantasy_points">Points</Term>/game</>, metric: "fantasy_points" },
];
const NO_GARBAGE: Col[] = [
  { head: <><Term name="xfp_ng">xFP</Term>/game (opportunity)</>, metric: "xfp_ng" },
  { head: <><Term name="fpoe_ng">FPOE</Term>/game (efficiency)</>, metric: "fpoe_ng" },
  { head: <><Term name="points_ng">Points</Term>/game</>, metric: "points_ng" },
];
const PARTS: Col[] = [
  { head: <Term name="td_rate_over_expected">TD rate over expected</Term>, metric: "td_rate_over_expected" },
  {
    head: (
      <>
        <Term name="catch_rate_over_expected">Catch rate over expected</Term> (QB:{" "}
        <Term name="completion_rate_over_expected">completion rate</Term>)
      </>
    ),
    metric: { QB: "completion_rate_over_expected", "*": "catch_rate_over_expected" },
  },
  { head: <Term name="yac_over_expected">YAC over expected</Term>, metric: "yac_over_expected" },
];

/** The study: what it does, what it found in one paragraph, and its tables. */
export default function StabilityStudy({ rows, weeks }: { rows: StabilityRow[]; weeks: number[] }) {
  const w = studyWindow(rows);
  if (!w) return null;
  const pick = (split: string) => splitHalf(rows, split, ["xfp", "fpoe"]);
  const [xo, fo, xf, ff] = [extremes(pick("odd_even"), 0), extremes(pick("odd_even"), 1), extremes(pick("first_second"), 0), extremes(pick("first_second"), 1)];
  const games = shrinkGames(rows, weeks);
  const last = games[games.length - 1];
  const full = last !== undefined ? extremes(shrinkTable(rows, "fpoe", w, [last]).map((l) => ({ position: l.position, n: l.n, cells: l.r })), 0) : null;
  const sticky = xo && fo && fo.high.value < xo.low.value;
  const at = (e: NonNullable<typeof xo>) => `${r2(e.low.value)} (${e.low.position}) to ${r2(e.high.value)} (${e.high.position})`;
  return (
    <div data-testid="stability">
      <p className="mt-2">
        Every <Term name="player_season">player-season</Term> of {span(w)} with at least {STABILITY_MIN_GAMES} games is split
        into two halves, his odd games against his even games. A number that is a real trait (a role, a skill) shows up in
        both halves; one that was luck does not. The <Term name="split_half_correlation">split-half correlation</Term> r says
        how much: 1 when the halves rank the players the same, 0 when one half says nothing about the other. Under each r is
        its {pct(STABILITY_INTERVAL_LEVEL)} <Term name="stability_interval">interval</Term> ({fmtInt(STABILITY_RESAMPLES)}{" "}
        resamples of the player-seasons).
      </p>
      {xo && fo ? (
        <p className="mt-3" data-testid="stability-meaning">
          <strong>{sticky ? "Opportunity repeats; efficiency mostly does not." : "What the halves show."}</strong> A
          player&apos;s xFP/game in one half of a season correlates {at(xo)} with the other half, his FPOE/game only {at(fo)}.
          {xf && ff ? ` The harder test, the first half of his games against the second (roles change in between), gives xFP/game ${r2(xf.low.value)} to ${r2(xf.high.value)} and FPOE/game ${r2(ff.low.value)} to ${r2(ff.high.value)}.` : ""}{" "}
          So the projection takes his chances at face value and keeps only a share r(g) of his FPOE/game
          {full ? `: after ${last} games ${r2(full.low.value)} to ${r2(full.high.value)} of it, by position` : ""}; the rest is
          expected to fade.
        </p>
      ) : null}
      <SplitTable rows={rows} split="odd_even" cols={MAIN} testId="split-half" caption={`Split-half correlation r, odd against even games, every play (${span(w)})`} />
      <SplitTable rows={rows} split="odd_even" cols={NO_GARBAGE} testId="split-half-ng" caption={`The same without garbage time (only plays while the game was in doubt)`} />
      <p className="mt-3 text-sm text-muted">
        The parts of efficiency, each a rate over expected per chance (per pass, carry or target for touchdowns, per target
        for catches, per catch for yards after the catch). A player-season counts for a rate only with enough chances in
        each half, so each rate has its own n. The parts have no garbage-time split: their weekly source does not separate it.
      </p>
      <SplitTable rows={rows} split="odd_even" cols={PARTS} testId="split-half-parts" perCellN caption={`The parts of efficiency, odd against even games (${span(w)})`} />
      <p className="mt-4">
        The same halves measure how much of FPOE/game lasts: the <Term name="signal_variance">signal</Term> the halves share
        against the <Term name="noise_variance">noise</Term> of a single game. After g games his FPOE/game deserves the share
        r(g) = signal / (signal + noise / g) of its value (the <Term name="shrinkage_factor">shrinkage factor</Term>).
      </p>
      <ShrinkStudy rows={rows} metric="fpoe" seasons={w} games={games} caption={`FPOE/game, every play: the shrinkage table (${span(w)})`} />
      <ShrinkStudy rows={rows} metric="fpoe_ng" seasons={w} games={games} caption={`FPOE/game without garbage time (${span(w)})`} />
    </div>
  );
}
