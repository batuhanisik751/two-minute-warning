import Term from "@/components/Term";
import { fmtInt, pct } from "@/lib/format";
import { nfl4th, submodelLines, verdict, type Cell, type SubmodelLine, type TrackRow } from "@/lib/decisions-track";

const WHAT: Record<SubmodelLine["key"], { name: React.ReactNode; rows: string; base: string }> = {
  conversion: { name: <Term name="p_convert">Go for it: the chance to convert</Term>, rows: "fourth downs", base: "the earlier seasons' rate by down and distance" },
  fieldgoal: { name: <Term name="p_fg_make">Field goal: the chance it is good</Term>, rows: "kicks", base: "the earlier seasons' rate by distance band" },
  punt: { name: <Term name="punt_expected_wp">Punt: where the other team starts</Term>, rows: "punts", base: "the raw history from the same yardline" },
  pat: { name: <Term name="pat_rate">Extra point rate</Term>, rows: "kicks", base: "every earlier season pooled" },
  two_point: { name: <Term name="two_point_rate">Two-point rate</Term>, rows: "tries", base: "every earlier season pooled" },
};

const v = (c: Cell | null, d = 4) => (c ? c.value.toFixed(d) : "–");
const signed = (c: Cell | null, d = 4) => (c ? `${c.value > 0 ? "+" : ""}${c.value.toFixed(d)}` : "–");
const ci = (c: Cell | null, d = 4) => (c && c.lo !== null && c.hi !== null ? ` (95%: ${c.lo.toFixed(d)} to ${c.hi.toFixed(d)})` : "");

/** One line per sub-model, with its honest metric against a simple baseline. */
export function Submodels({ rows }: { rows: TrackRow[] }) {
  const lines = submodelLines(rows).filter((l) => l.model);
  if (!lines.length) return <p className="mt-3 text-muted">The sub-models&apos; backtest has not been published.</p>;
  return (
    <ul className="mt-3 list-disc space-y-2 pl-5" data-testid="submodels">
      {lines.map((l) => {
        const w = WHAT[l.key];
        const name = l.metric === "log_score" ? "mean log score (higher is better)" : "log loss (lower is better)";
        const d = verdict(l);
        return (
          <li key={l.key} data-submodel={l.key}>
            <strong>{w.name}</strong> ({l.span?.replace("-", "–")}, {l.model?.n !== null && l.model?.n !== undefined ? fmtInt(l.model.n) : "–"}{" "}
            {w.rows}): {name} {v(l.model)} against {v(l.base)} for {w.base}; difference {signed(l.diff)}
            {ci(l.diff)}
            {d ? `: ${d === "no clear difference" ? "no clear difference from the simple rate" : `${d} than the baseline`}` : ""}.
          </li>
        );
      })}
    </ul>
  );
}

/** The nfl4th benchmark: agreement, go rates, where and why we differ. */
export function Nfl4thBenchmark({ rows }: { rows: TrackRow[] }) {
  const b = nfl4th(rows);
  if (!b || !b.agreement.all) return <p className="mt-3 text-muted">The benchmark against nfl4th has not been published.</p>;
  const a = b.agreement;
  const seasons = b.seasons.join(" and ");
  const rate = (x: { value: number; n: number | null } | null) => (x ? `${pct(x.value)}${x.n !== null ? ` of ${fmtInt(x.n)}` : ""}` : "–");
  return (
    <div className="mt-3 space-y-2" data-testid="nfl4th">
      <p>
        On every graded fourth down of {seasons} that nfl4th evaluates (regulation, outside the last seconds), our recommended
        option matched the nfl4th R package&apos;s on <strong className="tnum">{rate(a.all)}</strong>; on our{" "}
        <Term name="clear_call">clear calls</Term> on <strong className="tnum">{rate(a.clear)}</strong>, and on our{" "}
        <Term name="toss_up">toss-ups</Term> on {rate(a.tossUp)}.
        {b.goRate.ours && b.goRate.nfl4th && b.goRate.real
          ? ` We recommend going for it on ${pct(b.goRate.ours.value)} of these fourth downs, nfl4th on ${pct(b.goRate.nfl4th.value)}; coaches went on ${pct(b.goRate.real.value)}.`
          : ""}
      </p>
      <p>
        <strong>Where we differ:</strong> mostly late in games. The two models assume different clocks (how many seconds a
        fourth-down play and what follows take, and nfl4th&apos;s fixed end-of-game rules), and our win-probability model
        hands over to a separate model in the fourth quarter; the next most common cause is the punt model. nfl4th goes for it
        more often late in the fourth quarter; we go more often in the opponent&apos;s territory and at short to medium distances. nfl4th&apos;s
        models were fit on many seasons, probably including these (partly in-sample); ours never saw the season it grades.
        The likely cause of each disagreement is a heuristic, not a decomposition.
      </p>
    </div>
  );
}
