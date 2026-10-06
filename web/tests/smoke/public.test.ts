// The public-site pass (FIX-W, license): run by `npm run test:smoke:run` against a third server
// started with SITE_PUBLIC=true (SMOKE_PUBLIC=1), never in the private pass. On the public site no
// per-player FantasyPros value may reach a page -- neither its HTML nor its RSC payload: no experts'
// rank, no "where we disagree" marker, no reason quoting their ranks -- and each page that would
// show one says so once. The same pages on the private server (SMOKE_PRIVATE_BASE_URL) must show
// the seeded values, so these checks cannot pass by looking at the wrong thing.
import assert from "node:assert/strict";
import { before, describe, test } from "node:test";
import { THIRD_PARTY_NOTE, templatePattern, thirdPartyReasonId } from "../../lib/third-party";
import manifest from "../../lib/third-party-reasons.json";
import { BOARD_SEED as B } from "../seed-board";
import { SEED } from "../seed";
import { MODULES_SEED as M } from "../seed-modules";
import { BASE, DATA, fetchPage, normalise, serverUp, text, type Page } from "./dom";

const ON = process.env.SMOKE_PUBLIC === "1";
const PRIVATE = process.env.SMOKE_PRIVATE_BASE_URL ?? null;
const seedOnly = DATA === "seed";
const LIVE = `season=${SEED.liveWeek.season}&week=${SEED.liveWeek.week}`;
const PRESEASON = `/time-machine?season=${B.season}&week=0`;
/** route -> whether the page hides something there (and so carries the note once) */
const ROUTES: Record<string, boolean> = {
  "/": true,
  "/board": true,
  [`/board?season=${B.past[0]}&kind=backtest`]: true,
  [`/board?season=${B.noEcr}`]: true,
  [`/waivers?${LIVE}&pos=QB&kind=live`]: true,
  [`/waivers?${LIVE}&pos=FLEX&kind=live`]: true,
  [`/waivers?${LIVE}&pos=K&kind=live`]: true,
  [`/waivers?${LIVE}&pos=DST&kind=live`]: true,
  [PRESEASON]: true,
  "/time-machine": false,
  "/methodology": true,
  "/playoff-planner": false,
  "/playoff-planner?pos=RB&sort=total": false,
  // feature #10: the league table of coach tendencies (play-by-play only)
  "/decisions": false,
};
/** an experts' rank as the board and the home card write it ("preseason rank" then "RB12") */
const RANK = /preseason rank.{0,160}?\b(?:QB|RB|WR|TE)\d+\b/is;
/** the "where we disagree" marker's words (lib/board disagreeWords) */
const MARKER = /Where we disagree:|experts (?:did not )?rank him/;
/** any reason quoting FantasyPros, anywhere in the HTML or the payload (lib/third-party-reasons.json) */
const QUOTES = manifest.reasons.map((r) => [r.id, new RegExp(templatePattern(r.template).source.slice(1))] as const);

let up = false;
const pub = new Map<string, Page>();
const priv = new Map<string, Page>();

before(
  async () => {
    if (!ON) return;
    up = await serverUp();
    if (!up) return;
    for (const r of Object.keys(ROUTES)) {
      pub.set(r, await fetchPage(r));
      if (PRIVATE) priv.set(r, await fetchPage(r, PRIVATE));
    }
  },
  { timeout: 300_000 },
);

function leaks(p: Page): string[] {
  const out: string[] = [];
  if (RANK.test(p.html)) out.push(`an experts' rank: ${RANK.exec(p.html)![0].slice(0, 80)}`);
  if (MARKER.test(p.html)) out.push(`a disagreement marker: ${MARKER.exec(p.html)![0]}`);
  if (p.doc.querySelector("[data-disagree]")) out.push("a [data-disagree] element");
  if (p.html.includes("ecrRank")) out.push("an ecrRank field in the payload");
  for (const [id, re] of QUOTES) if (re.test(p.html)) out.push(`a ${id} reason: ${re.exec(p.html)![0].slice(0, 80)}`);
  for (const li of Array.from(p.doc.querySelectorAll("main li"))) {
    const id = thirdPartyReasonId(normalise(li.textContent ?? ""));
    if (id) out.push(`a ${id} reason: ${normalise(li.textContent ?? "")}`);
  }
  return out;
}

const notes = (p: Page) => Array.from(p.doc.querySelectorAll("[data-testid=third-party-note]")).map((n) => normalise(n.textContent ?? ""));

describe("public site: FantasyPros' per-player values are hidden (license)", () => {
  test("the server is in public mode (robots.txt allows crawling)", async (t) => {
    if (!ON || !up) return t.skip("public pass only (SMOKE_PUBLIC=1)");
    const robots = await (await fetch(`${BASE}/robots.txt`)).text();
    assert.match(robots, /Allow: \//);
  });

  test("no experts' rank, marker or FantasyPros reason in the HTML or the RSC payload", (t) => {
    if (!ON || !up) return t.skip("public pass only (SMOKE_PUBLIC=1)");
    for (const [r, p] of pub) {
      assert.equal(p.status, 200, r);
      assert.deepEqual(leaks(p), [], r);
    }
  });

  test("the license note, once on each page that hides something", (t) => {
    if (!ON || !up) return t.skip("public pass only (SMOKE_PUBLIC=1)");
    for (const [r, hides] of Object.entries(ROUTES)) {
      assert.deepEqual(notes(pub.get(r)!), hides ? [THIRD_PARTY_NOTE] : [], r);
    }
  });

  test("our own estimates, reasons and the aggregate comparison with the experts stay", (t) => {
    if (!ON || !up || !seedOnly) return t.skip("seed, public pass only");
    const board = pub.get("/board")!.doc;
    assert.ok(board.querySelectorAll("[data-testid=board-row] [data-cell=cliff]").length > 0, "the Cliff chances");
    assert.ok(board.querySelector("[data-testid=record-cliff_main]"), "the disagreement record (aggregate)");
    assert.equal(board.querySelector("[data-testid=no-ecr]"), null, "the license note replaces the no-ECR note");
    const qb = pub.get(`/waivers?${LIVE}&pos=QB&kind=live`)!.doc;
    assert.match(text(qb.querySelector("[data-testid=pick]")!), /Seed reason A.*Seed reason B for rank 1/);
    const k = pub.get(`/waivers?${LIVE}&pos=K&kind=live`)!.doc;
    assert.match(text(k.querySelector("main")!), new RegExp(`Seed streamer reason for ${M.kickers[0].name}`));
    assert.match(text(pub.get("/methodology")!.doc.querySelector("main")!), /against the experts/i);
  });

  test("the private server shows what the public one hides (so the checks above look at the right thing)", (t) => {
    if (!ON || !up || !seedOnly || !PRIVATE) return t.skip("seed, public pass with a private server");
    for (const r of ["/", "/board", `/board?season=${B.past[0]}&kind=backtest`, PRESEASON]) {
      const p = priv.get(r)!;
      assert.ok(leaks(p).length > 0, `${r}: nothing to hide on the private site`);
      assert.deepEqual(notes(p), [], `${r}: no license note privately`);
    }
    assert.ok(priv.get("/board")!.doc.querySelector("[data-disagree]"), "markers on the private board");
    assert.ok(priv.get(`/waivers?${LIVE}&pos=QB&kind=live`)!.html.includes(SEED.thirdPartyReason("QB")));
    assert.ok(priv.get(`/waivers?${LIVE}&pos=K&kind=live`)!.html.includes(M.thirdPartyReason(M.kickers[0].name, "K")));
    for (const r of [`/waivers?${LIVE}&pos=FLEX&kind=live`, `/waivers?${LIVE}&pos=DST&kind=live`]) assert.ok(leaks(priv.get(r)!).length > 0, r);
  });
});
