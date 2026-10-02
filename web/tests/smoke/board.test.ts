// Smoke tests of the Cliff board (step I2c-b): /board, the home card ("Cliff watch"), and the
// /methodology and /track-record sections. Seed-only checks use tests/seed-board.ts; against a
// real publish only what holds for any data.
import assert from "node:assert/strict";
import { before, describe, test } from "node:test";
import { disagreements, wholePct } from "../../lib/board";
import { BOARD_BANDS, BOARD_DRIVERS, BOARD_MIN_GAMES } from "../../lib/method";
import { BOARD_SEED as B, boardDisagreements, boardOutcomes, boardRows } from "../seed-board";
import { BASE, DATA, EMPTY_BASE, fetchPage, prose, serverUp, text, type Page } from "./dom";
import { foldCheck } from "./fold";
import { routeSet, type RouteSet } from "./routes";

let up = false;
let emptyUp = false;
let set: RouteSet;
const pages = new Map<string, Page>();
const empty = new Map<string, Page>();
const seedOnly = DATA === "seed";
const EXTRA = ["/", "/methodology", "/track-record"];

before(
  async () => {
    up = await serverUp();
    if (!up) return;
    set = await routeSet();
    for (const p of [...set.routes.filter((r) => r.kind === "board").map((r) => r.path), ...EXTRA]) {
      if (!pages.has(p)) pages.set(p, await fetchPage(p));
    }
    if (EMPTY_BASE && seedOnly) {
      emptyUp = await serverUp(EMPTY_BASE);
      if (emptyUp) for (const p of ["/", "/board", "/methodology", "/track-record"]) empty.set(p, await fetchPage(p, EMPTY_BASE));
    }
  },
  { timeout: 300_000 },
);

const main = (path: string, from = pages): Element => {
  const p = from.get(path);
  assert.ok(p, `not fetched: ${path}`);
  const m = p.doc.querySelector("main#main");
  assert.ok(m, `${path}: no <main id=main>`);
  return m;
};
const all = (m: Element, sel: string) => Array.from(m.querySelectorAll(sel));
const PCT = "(?:<1%|>99%|\\d{1,3}%)";
const CLIFF = new RegExp(`^${PCT} Estimated chance of a Cliff$`);
const MISSED = new RegExp(`^${PCT} Estimated chance of missed time$`);
const EFFECT = /\((?:raises|lowers|barely moves) the estimate\)$/;
/** wording the board must never use: estimates, not hype */
const HYPE = /\bwill (?:bust|crash|fall off)|guarantee|sure thing|must[- ]sell|lock\b|🔥|combined chance|total chance/i;

function checkRows(path: string, m: Element) {
  const rows = all(m, "[data-testid=board-row]");
  const ranks = rows.map((r) => Number(text(r.querySelector("[data-cell=rank]")!)));
  assert.deepEqual(ranks, [...ranks].sort((a, b) => a - b), `${path}: rows by the Cliff rank`);
  for (const r of rows) {
    assert.ok(r.querySelector("[data-cell=player] a[href^='/player/']"), `${path}: player link`);
    assert.match(text(r.querySelector("[data-cell=cliff]")!), CLIFF, `${path}: the Cliff chance in whole percents`);
    assert.match(text(r.querySelector("[data-cell=missed]")!), MISSED, `${path}: the missed-time chance, its own number`);
    const lists = all(r, "[data-testid=drivers]");
    assert.ok(lists.length <= 2, path);
    for (const l of lists) {
      const ds = all(l, ":scope > li");
      assert.ok(ds.length <= BOARD_DRIVERS, `${path}: at most ${BOARD_DRIVERS} drivers a chance`);
      for (const d of ds) assert.match(text(d), EFFECT, `${path}: ${text(d)}`);
    }
    assert.match(text(r.querySelector("[data-testid=last-season]")!), /^Last season, with the .+: [\d.]+ points per game \((QB|RB|WR|TE)\d+\) in \d+ games/, path);
    const d = r.querySelector("[data-testid=disagree]");
    if (d) assert.match(text(d), /^Where we disagree: (We see more risk than the experts|The experts see more risk than we do) \(/, path);
  }
  return rows;
}

describe("/board", () => {
  test("every board page: the season picker, live or reconstructed in words, ranked rows with two separate chances, drivers in words", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const routes = set.routes.filter((r) => r.kind === "board");
    if (!routes.length) return t.skip("no board in this database");
    for (const r of routes) {
      const m = main(r.path);
      assert.equal(m.querySelector("form[action='/board']")?.getAttribute("method"), "get", `${r.path}: season picker`);
      const b = m.querySelector("[data-testid=board]")!;
      assert.match(text(b.querySelector("[data-testid=kind-label]")!), /^(Live board|Reconstructed board)/, r.path);
      assert.ok(checkRows(r.path, m).length > 0, `${r.path}: no rows`);
      assert.ok(m.querySelector("[data-testid=outcomes-pending]") || m.querySelector("[data-testid=outcomes-final]"), `${r.path}: outcomes`);
      assert.doesNotMatch(prose(m), HYPE, `${r.path}: careful wording`);
      const marked = all(m, "[data-testid=disagree]").length;
      if (m.querySelector("[data-testid=no-ecr]")) assert.equal(marked, 0, `${r.path}: no marker without the experts' ranks`);
    }
  });

  test("how to read it: the two chances, the timing, the experts, calibration by band, the disagreement record", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const r = set.routes.find((x) => x.kind === "board");
    if (!r) return t.skip("no board in this database");
    const how = main(r.path).querySelector("[data-testid=how-to-read]")!;
    const p = prose(how);
    assert.match(p, /never added together/);
    assert.match(p, /eve of week 1/);
    assert.match(p, /latest daily depth chart/);
    assert.match(p, /expert consensus rank \(ECR\)/);
    assert.ok(all(how, "[data-testid=board-calibration] table").length >= 1, "calibration tables");
    assert.ok(how.querySelector("[data-testid=record-cliff_main]"), "the Cliff record");
    assert.match(prose(how.querySelector("[data-testid=record-cliff_main]")!), /^On the \d{4}–\d{4} boards, [\d,]+ players were in our top \d+ for a Cliff but not the experts': [\d,]+ of them \(\d+%\) really had a Cliff\./);
  });
});

/** The seed's 2026 live board, as the page must show it. */
const seedLive = () => boardRows(B.season, "live");

describe("seed: the Cliff board", () => {
  test("/board: this season's live board by default, folded after 10, both chances per row, the markers by the rule", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main("/board");
    const b = m.querySelector("[data-testid=board]")!;
    assert.equal(b.getAttribute("data-kind"), "live");
    assert.match(text(b.querySelector("h2")!), new RegExp(`Cliff board ${B.season}`));
    const rows = checkRows("/board", m);
    const want = seedLive();
    assert.equal(rows.length, want.length);
    rows.forEach((r, i) => {
      assert.equal(r.getAttribute("data-player"), want[i].gsisId);
      assert.equal(text(r.querySelector("[data-cell=cliff] .big-number")!), wholePct(want[i].cliffProbability));
      assert.equal(text(r.querySelector("[data-cell=missed] .big-number")!), wholePct(want[i].missedProbability));
    });
    const marks = disagreements(want.map((w) => ({ gsisId: w.gsisId, cliffRank: w.cliffRank, ecrRank: w.ecrRank ?? null, posRankS: w.posRankS })));
    assert.ok(marks.size > 0, "the seed has disagreements");
    const shown = new Map(rows.filter((r) => r.querySelector("[data-disagree]")).map((r) => [r.getAttribute("data-player")!, r.querySelector("[data-disagree]")!.getAttribute("data-disagree")]));
    assert.deepEqual([...shown.entries()].sort(), [...marks.entries()].sort());
    const folds = foldCheck(pages.get("/board")!.doc).folds.filter((f) => f.what.startsWith("Cliff board"));
    assert.deepEqual(folds.map((f) => [f.head, f.rest, f.total]), [[10, want.length - 10, want.length]]);
    assert.equal(all(m, "[data-testid=outcome]").length, 0, "no per-row outcome while pending");
    assert.ok(m.querySelector(`a[href='/board?season=${B.season}&kind=backtest']`), "the reconstructed board of the season");
    const missing = all(m, "[data-testid=drivers] > li").find((d) => /not available/.test(text(d)));
    assert.ok(missing && /^Vacated targets: not available \(the model fills it in\)/.test(text(missing)), "a missing driver in words");
  });
});

/** The seed's calibration cell of the Cliff's highest band, from its own rows and outcomes. */
function seedTopCliffBand() {
  const lo = BOARD_BANDS[BOARD_BANDS.length - 1];
  const rows = [B.noEcr, ...B.past].flatMap((season) => {
    const out = new Map(boardOutcomes(season).map((o) => [o.gsisId, o]));
    return boardRows(season, "backtest").map((r) => ({ p: r.cliffProbability, y: out.get(r.gsisId)!.yCliff }));
  });
  const band = rows.filter((r) => r.y !== null && r.p >= lo);
  return { n: band.length, hits: band.filter((r) => r.y).length };
}

describe("seed: the Cliff board, past seasons and elsewhere", () => {
  test("a past board: final outcomes in words, counted from the seed's own rows", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const season = B.past[1];
    const m = main(`/board?season=${season}`);
    const o = boardOutcomes(season);
    const judged = o.filter((x) => x.yCliff !== null);
    const sentence = `${judged.filter((x) => x.yCliff).length} of the ${judged.length} players who played ${BOARD_MIN_GAMES} or more games had a Cliff; ${o.filter((x) => x.yMissed).length} of all ${o.length} missed time.`;
    assert.match(prose(m.querySelector("[data-testid=outcomes-final]")!), new RegExp(`What happened: ${sentence.replace(/[.()]/g, "\\$&")}$`));
    const tags = all(m, "[data-testid=outcome] [data-outcome]").map((e) => text(e));
    assert.deepEqual([...new Set(tags)].sort(), ["Cliff", "Missed time", "No Cliff"]);
    assert.match(text(m.querySelector("[data-testid=outcome]")!), /^What happened: Cliff [\d.]+ points per game in \d+ games \(−40% from [\d.]+\): a drop of 30% or more$/);
  });

  test("one position, an unranked player, and the board before the experts' ranks", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const te = main(`/board?season=${B.past[0]}&pos=TE`);
    assert.match(text(te.querySelector("[data-testid=board] h2")!), /, TE$/);
    const rows = all(te, "[data-testid=board-row]");
    assert.ok(rows.length > 0 && rows.every((r) => r.getAttribute("data-pos") === "TE"));
    assert.equal(te.querySelector("nav[aria-label=Position] a[aria-current=page]")?.getAttribute("data-pos"), "TE");
    const un = te.querySelector(`[data-testid=board-row][data-player='${B.unranked}']`)!;
    assert.match(text(un.querySelector("[data-testid=last-season]")!), /Experts' preseason rank not ranked$/);
    const old = main(`/board?season=${B.noEcr}`);
    assert.ok(old.querySelector("[data-testid=no-ecr]"), "the no-ECR note");
    assert.equal(all(old, "[data-testid=disagree]").length, 0);
    assert.doesNotMatch(text(old.querySelector("[data-testid=board-list]")!), /Experts' preseason rank/);
  });

  test("calibration and the record are the seed's own rows, aggregated", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const how = main("/board").querySelector("[data-testid=how-to-read]")!;
    const c = seedTopCliffBand();
    const cell = how.querySelector(`[data-chance=cliff] tr[data-band='${BOARD_BANDS.length - 1}']`)!;
    assert.match(text(cell), new RegExp(`^50% or more \\d+% ${Math.round((100 * c.hits) / c.n)}% ${c.hits} of ${c.n} players`));
    const d = boardDisagreements().filter((x) => x.variant === "cliff_main");
    const sum = (g: string, k: "players" | "hits") => d.filter((x) => x.pickGroup === g).reduce((a, x) => a + x[k], 0);
    assert.match(prose(how.querySelector("[data-testid=record-cliff_main]")!), new RegExp(`^On the ${B.past[0]}–${B.past[1]} boards, ${sum("model_only", "players")} players were in our top 10 for a Cliff but not the experts': ${sum("model_only", "hits")} of them`));
    assert.equal(all(how, "[data-testid=record-cliff_main] tbody tr").length, B.past.length);
  });
});

describe("seed: the board on the home page, /methodology and /track-record", () => {
  test("home: the top 5 of this season's live board with both chances, linking to the full board", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const card = main("/").querySelector("[data-testid=board-card]")!;
    assert.ok(card, "the Cliff watch card");
    const rows = all(card, "[data-testid=board-top] > li");
    assert.equal(rows.length, 5);
    rows.forEach((r, i) => {
      assert.equal(text(r.querySelector("[data-cell=cliff] .big-number")!), wholePct(seedLive()[i].cliffProbability));
      assert.ok(r.querySelector("[data-cell=missed]"), "the missed-time chance beside it");
    });
    assert.ok(card.querySelector(".live-pill"), "the live pill");
    assert.ok(card.querySelector(`a[href='/board?season=${B.season}&kind=live']`), "the link");
  });

  test("methodology: the two models, the backtest with intervals, Breakout stated from the research rows; track record apart", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main("/methodology").querySelector("[data-testid=board-method]")!;
    assert.ok(main("/methodology").querySelector("nav a[href='#board']"), "in the table of contents");
    assert.equal(all(m, "[data-testid=board-backtest] tbody tr").length, 3);
    assert.equal(all(m, "[data-testid=board-cliff-features] li").length, B.cliffFeatures.length);
    assert.equal(all(m, "[data-testid=board-missed-features] li").length, B.missedFeatures.length);
    assert.match(prose(m), /penalty strength C = 0\.01/);
    const br = m.querySelector("[data-testid=board-breakout]")!;
    assert.equal(all(br, "[data-testid=board-breakout-table] tbody tr").length, 2);
    assert.match(prose(br.querySelector("li[data-variant=breakout_wr_te]")!), /shows no clear difference from last season's PPG rank/);
    assert.match(prose(br.querySelector("li[data-variant=breakout_rb]")!), /is ahead of last season's PPG rank \(PR-AUC difference \+0\.088; 95% interval \+0\.009 to \+0\.178\)/);
    assert.match(prose(br), /it stays research/);
    assert.match(prose(m.querySelector("[data-testid=board-limits]")!), /every other board is reconstructed/);
    const tr = main("/track-record").querySelector("[data-testid=track-board]")!;
    assert.equal(all(tr, "[data-testid=board-headline] > li").length, 3);
    assert.equal(tr.querySelector("[data-variant^=breakout]"), null, "Breakout is research only, not in the track record");
    assert.equal(tr.querySelector("[data-testid=board-live]")!.getAttribute("data-live"), "pending");
    assert.match(prose(tr.querySelector("[data-testid=board-live]")!), new RegExp(`^No live results yet\\. 1 live board \\(${B.ids.length} players\\): outcomes pending until the season is over`));
  });

  test("empty database: an empty state on /board, nothing on the home page, 'not published' elsewhere", (t) => {
    if (!emptyUp) return t.skip("no empty-database server");
    assert.ok(main("/board", empty).querySelector("[data-testid=empty-state]"));
    assert.equal(main("/", empty).querySelector("[data-testid=board-card]"), null);
    assert.ok(main("/methodology", empty).querySelector("[data-testid=board-method] [data-testid=empty-state]"));
    assert.match(prose(main("/track-record", empty).querySelector("[data-testid=track-board]")!), /not published yet/);
  });
});
