// Smoke tests of the K and D/ST streamer and Regression Watch pages (step W2): /waivers?pos=K
// and ?pos=DST, /regression, the home page's cards, the player page's toggle and history, the
// methodology sections. Seed-only checks use tests/seed-modules.ts; against a real publish only
// what holds for any data.
import assert from "node:assert/strict";
import { before, describe, test } from "node:test";
import { SEED } from "../seed";
import { MODULES_SEED as M } from "../seed-modules";
import { BASE, DATA, fetchPage, normalise, prose, serverUp, text, type Page } from "./dom";
import { routeSet, type RouteSet } from "./routes";

let up = false;
let set: RouteSet;
const pages = new Map<string, Page>();
const seedOnly = DATA === "seed";
const LIVE = `season=${M.liveWeek.season}&week=${M.liveWeek.week}`;
const SB = M.streamBacktest;
const RB = M.rwBacktest[1];

before(
  async () => {
    up = await serverUp();
    if (!up) return;
    set = await routeSet();
    const extra = ["/", "/methodology", `/player/${SEED.featured}?season=${RB.season}`];
    for (const p of [...set.routes.map((r) => r.path), ...(seedOnly ? extra : [])]) if (!pages.has(p)) pages.set(p, await fetchPage(p));
  },
  { timeout: 300_000 },
);

const main = (path: string): Element => {
  const p = pages.get(path);
  assert.ok(p, `not fetched: ${path}`);
  const m = p.doc.querySelector("main#main");
  assert.ok(m, `${path}: no <main id=main>`);
  return m;
};
const rows = (m: Element, id = "stream-pick") => Array.from(m.querySelectorAll(`[data-testid=${id}]`));

describe("/waivers?pos=K and ?pos=DST", () => {
  test("every K and D/ST page: list, label, explanation once, rows with chance, priority and next game", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const stream = set.routes.filter((r) => r.kind === "waivers-stream");
    if (!stream.length) return t.skip("no K or D/ST list in this database");
    for (const r of stream) {
      const m = main(r.path);
      assert.ok(m.querySelector("[data-testid=radar-list]"), r.path);
      assert.equal(m.querySelectorAll("[data-testid=stream-explainer]").length, 1, `${r.path}: explained once`);
      assert.match(text(m.querySelector("[data-testid=stream-explainer]")!), /No betting lines, by design/);
      assert.ok(m.querySelector("[data-testid=stream-verdict]"), `${r.path}: no backtest sentence`);
      const rs = rows(m);
      assert.ok(rs.length > 0, `${r.path}: no rows`);
      for (const row of rs) assert.match(text(row), /Next game: /, r.path);
      assert.equal(m.querySelectorAll("[data-testid=pick]").length, 0, `${r.path}: Radar rows on a streamer page`);
    }
  });

  test("seed: the live K list, its note, the verdict sentence and the starter cutoff from the glossary", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main(`/waivers?${LIVE}&pos=K&kind=live`);
    assert.equal(m.querySelector("[data-testid=radar-list]")?.getAttribute("data-kind"), "live");
    const rs = rows(m);
    assert.equal(rs.length, M.kickers.length);
    assert.match(text(rs[0]), new RegExp(`${M.kickers[0].name}.*NHG · North Harbor Gulls.*Next game: vs SRO \\(home\\), South Ridge Owls`));
    assert.match(text(rs[0]), /Seed streamer reason for Kade Longfoot/);
    assert.match(text(rs[0]), /46% similar picks started 42–50%/);
    assert.match(text(rs[0]), /Speculative.*Pending/);
    assert.match(text(rs[rs.length - 1]), /Next game: not known yet/);
    assert.match(text(m), new RegExp(`Published with this list: ${M.kNote}`));
    assert.equal(
      prose(m.querySelector("[data-testid=stream-verdict]")!),
      "In the 2014–2025 backtest the model's top 5 kickers had a top-10 week 40.0% of the time, against 37.0% for the best simple rule (last game's points): +3.0 points (95% interval \u22121.0 to +7.0), ahead, but not clearly (the interval includes zero). A random pick from the pool had one 25.0% of the time.",
    );
    assert.match(text(m), /Ranked from 5 kickers in the pool; all are shown\. The model learned from 2012 ?–2025 only\./);
  });

  test("seed: the D/ST list says it is a rule, not a model", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main(`/waivers?${LIVE}&pos=DST&kind=live`);
    assert.equal(rows(m).length, 4);
    assert.match(text(m.querySelector("#list-heading")!), /Defense and special teams \(D\/ST\), 2026 week 3/);
    assert.match(
      prose(m.querySelector("[data-testid=stream-verdict]")!),
      /^The D\/ST list uses a simple rule, not a model: it ranks by the next opponent's points scored per game, fewer first\. In the 2014–2025 backtest the rule's top 5 had a top-10 week 41\.0% of the time, against 39\.0% for the best model \(our model, a logistic regression\): \+2\.0 points \(95% interval \u22121\.0 to \+5\.0\), ahead, but not clearly/,
    );
    assert.match(text(m), /Ranked by a simple rule, not a model\./);
  });

  test("seed: reconstructed weeks show outcomes, and the first season says the chance is not available", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main(`/waivers?season=${SB[0].season}&week=${SB[0].week}&pos=K`);
    const rs = rows(m);
    assert.match(text(rs[0]), /Hit.*Next week: top 10, 12\.0 pts/);
    assert.match(text(rs[1]), /No hit.*Next week: outside the top 10, no points recorded/);
    assert.match(text(m), /No reasons for this list/);
    assert.match(text(m.querySelector("[data-testid=kind-label]")!), /what the streamer would have said then/);
    const first = main(`/waivers?season=${SB[1].season}&week=${SB[1].week}&pos=K`);
    assert.match(text(first), /Chance and priority: not available for this list/);
    for (const row of rows(first)) assert.ok(!/Chance 0%/.test(text(row)));
    const seasons = Array.from(first.querySelectorAll<HTMLOptionElement>("select[name=season] option")).map((o) => o.value);
    assert.deepEqual(seasons, ["2026", "2025", "2024"], "the K time machine has the streamer's seasons");
    assert.equal(first.querySelector("form[action='/waivers'] input[name=pos]")?.getAttribute("value"), "K");
  });
});

describe("home: the Streamers and Regression flags cards", () => {
  test("seed: top 3 K and D/ST, top 3 Sell-high and Buy-low of the live week", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main("/");
    const sc = m.querySelector("[data-testid=streamers-card]")!;
    const lists = Array.from(sc.querySelectorAll("[data-testid=stream-list]"));
    assert.equal(lists.length, 2);
    for (const l of lists) assert.equal(l.querySelectorAll("[data-testid=stream-pick]").length, 3);
    assert.match(text(sc), /Streamers, 2026 week 3/);
    assert.match(text(sc), /top-10 K or top-10 D\/ST week/);
    const rc = m.querySelector("[data-testid=regression-card]")!;
    const rl = Array.from(rc.querySelectorAll("[data-testid=rw-list]"));
    assert.equal(rl.length, 2);
    // prose: the tags are glossary terms (their hidden panels are not what the card reads)
    assert.match(prose(rc), /Sell-high \(top 3 of 3\)/);
    assert.match(prose(rc), /Buy-low \(top 3 of 3\)/);
    assert.match(text(rc), new RegExp(M.rwNote));
    assert.ok(!/Seed reason/.test(text(rc)), "the card shows no reasons");
  });
});

describe("/regression (seed)", () => {
  test("the live week: tables in the weekly report's order, the note, pending said once", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main("/regression");
    assert.equal(m.querySelector("[data-testid=rw-week]")?.getAttribute("data-kind"), "live");
    const ids = (tag: string) => rows(m.querySelector(`[data-testid=tag-${tag}]`)!, "rw-row").map((r) => r.querySelector("a")?.getAttribute("href"));
    assert.deepEqual(ids("sell_high"), ["/player/00-9000001", "/player/00-9000014", `/player/${SEED.longName.gsisId}`]);
    assert.equal(ids("buy_low").length, 3);
    // the seed's live list carries Legit tags like the frozen live 2026-W03 list: the site drops them
    assert.equal(m.querySelector("[data-testid=tag-legit]"), null);
    assert.ok(!/legit/i.test(pages.get("/regression")!.html), "Legit in the page");
    const both = rows(m.querySelector("[data-testid=tag-buy_low]")!, "rw-row").find((r) => r.querySelector("a")?.getAttribute("href") === "/player/00-9000015");
    assert.ok(both, "the Buy-low and Legit player stays in the Buy-low table");
    assert.equal(both.getAttribute("data-tags"), "buy_low");
    assert.match(text(both), /Buy-low: seed reason for 00-9000015\.$/);
    // a Legit-only player is untagged: in the scatter's "No tag" group, in no table
    const scatter = Array.from(m.querySelectorAll("[data-testid=scatter-table] tbody tr")).map((r) => text(r));
    assert.ok(scatter.some((r) => /No tag/.test(r)) && !scatter.some((r) => /legit/i.test(r)));
    assert.equal(m.querySelectorAll("details[data-fold]").length, 0, "short tables do not fold");
    assert.match(text(m), new RegExp(M.rwNote));
    assert.ok(m.querySelector("[data-testid=rw-pending]"), "pending is said once");
    assert.equal(m.querySelectorAll("[data-testid=rw-outcome]").length, 0);
    const first = rows(m.querySelector("[data-testid=tag-sell_high]")!, "rw-row")[0];
    assert.match(text(first), /Games 3 PPG 20\.0 xFP\/game 13\.0 FPOE\/game \+7\.0 Projection 14\.0/);
    assert.match(text(first), /Sell-high: seed reason for 00-9000001\./);
    assert.doesNotMatch(text(first), /include garbage time/);
    // the live week-3 list has no 80% range (published before feature #4): no brackets, no term
    assert.equal(m.querySelectorAll("[data-testid=rw-range]").length, 0);
    assert.doesNotMatch(text(m), /80% range/);
    assert.equal(m.querySelector("[data-testid=gt-toggle] a[aria-current=page]")?.textContent, "With garbage time");
  });

  test("without garbage time: the _ng values, the toggle and the time machine keep gt=off", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main("/regression?gt=off");
    const first = rows(m.querySelector("[data-testid=tag-sell_high]")!, "rw-row")[0];
    assert.match(text(first), /PPG 19\.0 xFP\/game 12\.5 FPOE\/game \+6\.5 Projection 14\.0/);
    // the reason sentence quotes the numbers with garbage time (the tag's): it says so
    assert.match(text(first), /Sell-high: seed reason for 00-9000001\. \(These numbers include garbage time: the tag is made from them\.\)/);
    assert.equal(m.querySelector("[data-testid=gt-toggle] a[aria-current=page]")?.textContent, "Without garbage time");
    assert.equal(m.querySelector("form[action='/regression'] input[name=gt]")?.getAttribute("value"), "off");
    assert.match(text(m.querySelector("figure[data-testid=scatter] figcaption")!), /without garbage time/);
    const table = m.querySelector("[data-testid=scatter-table]")!;
    assert.equal(table.querySelectorAll("tbody tr").length, 24, "every universe player in the table");
  });

  test("a reconstructed week: outcomes per row and the honest track record", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main(`/regression?season=${RB.season}&week=${RB.week}`);
    assert.equal(m.querySelector("[data-testid=rw-week]")?.getAttribute("data-kind"), "backtest");
    assert.match(text(m), /Rest of the season: \d+\.\d PPG in 10 games\./);
    assert.match(text(m), /Rest of the season: no games played\./);
    const verdicts = Array.from(m.querySelectorAll("[data-testid=rw-tag-table] tbody tr")).map((r) => r.getAttribute("data-verdict"));
    assert.deepEqual(verdicts, ["above", "above"], "Sell-high and Buy-low only");
    assert.equal(
      prose(m.querySelector("[data-testid=dropped-tag]")!),
      "A third tag was also tested and dropped: it came true for 60.5% of the 120 players it tagged, against 61.0% for every comparable player, so it predicted nothing better than the base rate.",
    );
    // the long Buy-low table: 10 rows, then "Show all 27" holding the other 17
    const bl = m.querySelector("[data-testid=tag-buy_low]")!;
    assert.equal(rows(bl, "rw-row").length, M.rwLong.buyLow);
    const fold = bl.querySelector("details[data-fold]")!;
    assert.match(text(fold.querySelector("summary")!), new RegExp(`^Show all ${M.rwLong.buyLow} `));
    assert.equal(rows(fold, "rw-row").length, M.rwLong.buyLow - 10);
    assert.equal(bl.querySelector("ol[data-testid=rw-list]:not([data-fold-rest])")!.children.length, 10);
    assert.equal(m.querySelector("[data-testid=tag-sell_high] details"), null, "Sell-high (3) does not fold");
    // the 80% range under every projection: seed-modules.ts rwRows (projection - 3 or - 0.5, + 3)
    assert.match(prose(m.querySelector("[data-testid=tag-sell_high]")!), /in brackets its 80% range\./);
    const ranged = rows(m, "rw-row");
    assert.equal(m.querySelectorAll("[data-testid=rw-range]").length, ranged.length);
    for (const r of ranged) {
      const got = /Projection (\d+\.\d) \((\d+\.\d)–(\d+\.\d)\)/.exec(text(r));
      assert.ok(got, text(r));
      const [p, lo, hi] = got.slice(1).map(Number);
      assert.ok(Math.abs(hi - p - 3) < 0.051 && [3, 0.5].some((d) => Math.abs(p - lo - d) < 0.051), text(r));
    }
    assert.match(prose(m.querySelector("[data-testid=rw-track]")!), /Graded on 900 player-weeks of the 2024–2025 test seasons, as-of weeks 4, 6 \(/);
    const weeks = Array.from(m.querySelectorAll<HTMLOptionElement>("select[name=week] option")).map((o) => normalise(o.textContent ?? ""));
    assert.deepEqual(weeks, ["Week 4 (reconstructed)", "Week 6 (reconstructed)"]);
  });
});

describe("player page and methodology (seed)", () => {
  test("the player page: garbage-time toggle on the points chart and the Regression Watch history", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const on = main(`/player/${SEED.featured}?season=${RB.season}`);
    const off = main(`/player/${SEED.featured}?season=${RB.season}&gt=off`);
    const cell = (m: Element) => normalise(m.querySelector("figure[data-testid=chart] details tbody tr td")?.textContent ?? "");
    assert.equal(Number(cell(on)) - Number(cell(off)), 1, "points without garbage time");
    assert.match(text(off.querySelector("figure[data-testid=chart] figcaption")!), /without garbage time/);
    assert.equal(off.querySelector("[data-testid=gt-toggle] a[aria-current=page]")?.textContent, "Without garbage time");
    const hist = on.querySelector("[data-testid=regression-history]")!;
    assert.equal(hist.querySelectorAll("tbody tr").length, 2, "weeks 4 and 6 of 2025");
    assert.doesNotMatch(text(hist), /Legit/, "his Legit tags are dropped");
    assert.match(text(hist), /No tag/);
    assert.match(text(hist), /PPG in 10 games/);
    // the 80% range beside each backtest projection, with its term in the header
    assert.match(prose(hist.querySelector("thead")!), /Projection \(80% range/);
    const proj = Array.from(hist.querySelectorAll("[data-testid=rw-history-projection]")).map((c) => normalise(c.textContent ?? ""));
    assert.equal(proj.length, 2);
    assert.ok(proj.every((x) => /^\d+\.\d \(\d+\.\d–\d+\.\d\)$/.test(x)), proj.join(" | "));
    const season = Array.from(off.querySelectorAll<HTMLAnchorElement>("nav[aria-label=Season] a")).map((a) => a.getAttribute("href"));
    assert.ok(season.every((h) => h?.endsWith("gt=off")), "the season links keep the view");
  });

  test("methodology: the streamer and Regression Watch sections read their numbers from the database", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main("/methodology");
    assert.ok(m.querySelector("#streamer") && m.querySelector("#regression"));
    const k = m.querySelector("[data-testid=stream-results-K]")!;
    assert.equal(k.querySelectorAll("tbody tr").length, 6, "five methods and the base rate");
    assert.match(text(k), /our model, a logistic regression on the site 40\.0% 38\.0–42\.0%/);
    assert.match(text(k), /25\.0% \(250 of 1,000 pool picks\)/);
    // the stability study from regression_stability (seed-modules.ts rwStability, fictional)
    const st = m.querySelector("[data-testid=stability]")!;
    const split = st.querySelector("[data-testid=split-half]")!;
    assert.equal(split.querySelectorAll("tbody tr").length, 4);
    assert.equal(text(split.querySelector("tbody tr")!), "QB 100 0.70 0.65 to 0.75 0.05 0.00 to 0.10 0.60 0.55 to 0.65");
    assert.match(text(split.querySelector("caption")!), /odd against even games, every play \(2012–2025\)/);
    assert.equal(text(st.querySelector("[data-testid=split-half-ng] tbody tr")!), "QB 100 0.67 0.62 to 0.72 0.03 −0.02 to 0.08 0.57 0.52 to 0.62");
    const parts = Array.from(st.querySelectorAll("[data-testid=split-half-parts] tbody tr")).map((r) => text(r));
    assert.equal(parts[0], "QB 0.01 −0.04 to 0.06 n 95 0.30 0.25 to 0.35 n 92 –", "QB: completion rate, no YAC");
    assert.equal(parts[1], "RB 0.01 −0.04 to 0.06 n 235 0.14 0.09 to 0.19 n 200 0.17 0.12 to 0.22 n 180");
    assert.equal(
      prose(st.querySelector("[data-testid=stability-meaning]")!),
      "Opportunity repeats; efficiency mostly does not. A player's xFP/game in one half of a season correlates 0.70 (QB) to 0.80 (RB) with the other half, his FPOE/game only 0.05 (QB) to 0.12 (RB). The harder test, the first half of his games against the second (roles change in between), gives xFP/game 0.60 to 0.70 and FPOE/game 0.05 to 0.12. So the projection takes his chances at face value and keeps only a share r(g) of his FPOE/game: after 17 games 0.25 to 0.36 of it, by position; the rest is expected to fade.",
    );
    const sh = st.querySelector("[data-testid=stability-shrink-fpoe]")!;
    assert.deepEqual(Array.from(sh.querySelectorAll("thead th")).slice(-3).map((th) => normalise(th.textContent ?? "").replace(/ \(explain\).*/, "")), ["r(4)", "r(6)", "r(17)"]);
    assert.equal(text(sh.querySelector("tbody tr")!), "QB 100 0.50 20.0 −0.50 40 0.09 0.05 to 0.13 0.13 0.09 to 0.17 0.30");
    assert.ok(st.querySelector("[data-testid=stability-shrink-fpoe_ng] tbody tr"));
    assert.equal(m.querySelectorAll("[data-testid=rw-position-mae] tbody tr").length, 2);
    // the 80% range's coverage check, computed from the published rows (seed: M.rwCoverage)
    const cov = Array.from(m.querySelectorAll("[data-testid=rw-range-coverage] tbody tr")).map((r) => text(r));
    const want = Object.entries(M.rwCoverage).map(([p, [n, inside]]) =>
      `${p} ${n} ${((inside / n) * 100).toFixed(1)}% ${(((n - inside) / n) * 100).toFixed(1)}% 0.0%`);
    assert.deepEqual(cov, want);
    assert.match(prose(m.querySelector("[data-testid=rw-range-section]")!), /backtest lists of 2025 .* inside the range for 88\.6% of 70 graded players/);
    assert.match(text(m.querySelector("#regression")!.parentElement!), /PROJECT_SPEC 6\.3/);
    assert.match(text(m), /mean_flat_all/);
  });
});
