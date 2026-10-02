// Smoke tests of the time machine (step I3b): /time-machine. For any data: the picker, one section
// per module (covered: framed and badged like the module's page, with a link; not covered: why),
// and the stored lists themselves (each compact list = the top of the module page's list for that
// week). Seed-only: which modules cover which seeded week.
import assert from "node:assert/strict";
import { before, describe, test } from "node:test";
import { MODULES } from "../../lib/time-machine";
import { BOARD_SEED } from "../seed-board";
import { HOT_SEAT_SEED } from "../seed-hot-seat";
import { SEED } from "../seed";
import { DATA, fetchPage, serverUp, text, type Page } from "./dom";
import { routeSet } from "./routes";

let up = false;
const pages = new Map<string, Page>();
const seedOnly = DATA === "seed";

before(
  async () => {
    up = await serverUp();
    if (!up) return;
    const set = await routeSet();
    for (const r of set.routes.filter((x) => x.kind === "time-machine")) pages.set(r.path, await fetchPage(r.path));
  },
  { timeout: 300_000 },
);

const main = (path: string): Element => {
  const p = pages.get(path);
  assert.ok(p, `not fetched: ${path}`);
  assert.equal(p.status, 200, path);
  const m = p.doc.querySelector("main#main");
  assert.ok(m, `${path}: no <main id=main>`);
  return m;
};
const all = (m: Element, sel: string) => Array.from(m.querySelectorAll(sel));
const section = (m: Element, mod: string) => m.querySelector(`[data-testid=tm-section][data-module=${mod}]`);
const names = (root: Element, sel: string) => all(root, sel).map((a) => text(a));

describe("/time-machine", () => {
  test("every week: the picker, one section per module, live or reconstructed badges, links or reasons", (t) => {
    if (!up) return t.skip("no server");
    assert.ok(pages.size >= 1, "no time-machine route");
    for (const path of pages.keys()) {
      const m = main(path);
      assert.equal(text(m.querySelector("h1")!), "Time machine", path);
      assert.match(text(m), /Nothing here is recomputed: these are the stored lists, exactly as published/, path);
      const form = m.querySelector("form[action='/time-machine']");
      assert.ok(form?.querySelector("select[name=season]") && form.querySelector("select[name=week]"), `${path}: the picker`);
      const secs = all(m, "[data-testid=tm-section]");
      assert.deepEqual(secs.map((s) => s.getAttribute("data-module")).sort(), [...MODULES].sort(), `${path}: one section per module`);
      const covered = secs.filter((s) => s.getAttribute("data-covered") === "yes");
      const cov = text(m.querySelector("[data-testid=tm-coverage]")!);
      assert.match(cov, new RegExp(`^\\d{4} (preseason|week \\d+): ${covered.length} of the ${MODULES.length} modules have something stored`), path);
      for (const s of covered) {
        const kind = s.getAttribute("data-kind");
        const mod = s.getAttribute("data-module");
        if (kind === "live") assert.ok(s.classList.contains("frame-live") && s.querySelector(".live-pill"), `${path} ${mod}: live frame and badge`);
        else if (kind === "backtest") assert.ok(s.classList.contains("frame-recon") && s.querySelector(".recon-pill"), `${path} ${mod}: reconstructed frame and badge`);
        else assert.equal(mod, "decisions", `${path}: only the Report Card is neither live nor reconstructed`);
        if (mod === "decisions") assert.match(text(s), /Grades are retrospective: every call was priced after the game/, path);
        assert.ok(s.querySelector("a[href^='/']:last-of-type"), `${path} ${mod}: a link to its page`);
        assert.ok(all(s, "[data-row]").length > 0 || /no players|no picks|No .* player this week/.test(text(s)) || mod === "decisions", `${path} ${mod}: rows`);
      }
      for (const s of secs.filter((x) => x.getAttribute("data-covered") === "no")) {
        assert.match(text(s), /^.+ Not covered then\. .+\.( Go to \d{4} (preseason|week \d+))?$/, `${path}: ${text(s)}`);
      }
    }
  });

  test("each compact list is the top of the module page's stored list for that week", async (t) => {
    if (!up) return t.skip("no server");
    for (const path of pages.keys()) {
      const m = main(path);
      const radar = section(m, "radar");
      const card = radar?.querySelector("section[data-pos]");
      if (card) {
        const href = card.querySelector("a[href^='/waivers']")!.getAttribute("href")!;
        const full = (await fetchPage(href)).doc.querySelector("[data-testid=radar-list]")!;
        const ours = names(card, "[data-cell=player] a[href^='/player/']");
        assert.deepEqual(ours, names(full, "[data-cell=player] a[href^='/player/']").slice(0, ours.length), `${path}: Radar = ${href}`);
      }
      for (const [mod, row] of [["hot_seat", "[data-testid=hot-seat-row] [data-cell=coach] a"], ["board", "[data-testid=board-row] [data-cell=player] a"]] as const) {
        const s = section(m, mod);
        if (!s || s.getAttribute("data-covered") !== "yes") continue;
        const href = Array.from(s.querySelectorAll("a")).pop()!.getAttribute("href")!;
        const ours = names(s, row);
        const theirs = names((await fetchPage(href)).doc.querySelector("main")!, row);
        assert.deepEqual(ours, theirs.slice(0, ours.length), `${path}: ${mod} = ${href}`);
      }
    }
  });
});

describe("/time-machine (seed)", () => {
  const live = SEED.liveWeek;
  const bt = SEED.backtestWeeks[1];
  const covered = (m: Element) => all(m, "[data-testid=tm-section][data-covered=yes]").map((s) => `${s.getAttribute("data-module")}:${s.getAttribute("data-kind")}`);

  test("the default is the live week; the board points to the preseason", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main("/time-machine");
    assert.equal(m.querySelector<HTMLOptionElement>("select[name=week] option[selected]")?.textContent, `Week ${live.week} (live)`);
    assert.deepEqual(covered(m), ["radar:live", "stream:live", "regression:live", "decisions:graded", "hot_seat:live"]);
    const board = section(m, "board")!;
    assert.match(text(board), /The Cliff board is made once a season, on the eve of week 1: see the 2026 preseason\./);
    assert.equal(board.querySelector("a")?.getAttribute("href"), `/time-machine?season=${BOARD_SEED.season}&week=0`);
    for (const c of all(section(m, "radar")!, "section[data-pos]")) assert.ok(all(c, "[data-testid=pick-list] > li, ol > li").length <= 5);
  });

  test("the preseason holds the board only; a past week shows the reconstructed lists", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const pre = main(`/time-machine?season=${BOARD_SEED.season}&week=0`);
    assert.deepEqual(covered(pre), ["board:live"]);
    assert.match(text(section(pre, "radar")!), /The Waiver Radar has nothing for the preseason\./);
    const past = main(`/time-machine?season=${bt.season}&week=${bt.week}`);
    assert.deepEqual(covered(past).filter((c) => c !== "decisions:graded"), ["radar:backtest", "stream:backtest", "regression:backtest"]);
    const hs = section(past, "hot_seat")!;
    assert.match(text(hs), new RegExp(`in ${HOT_SEAT_SEED.past} its lists cover weeks ${HOT_SEAT_SEED.pastWeeks[0]}–${HOT_SEAT_SEED.lastWeek}\\.`));
    assert.equal(hs.querySelector("a")?.getAttribute("href"), `/time-machine?season=${HOT_SEAT_SEED.past}&week=${HOT_SEAT_SEED.lastWeek}`);
  });

  test("final outcomes are overlaid; an unknown week falls back with a note", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const eos = section(main(`/time-machine?season=${HOT_SEAT_SEED.past}&week=${HOT_SEAT_SEED.lastWeek}`), "hot_seat")!;
    assert.match(text(eos), /\(the end-of-season snapshot\)/);
    assert.match(text(eos.querySelector("[data-testid=outcomes-final]")!), /^What happened: \d+ of these \d+ coaches (was|were) let go/);
    assert.ok(all(eos, "[data-testid=outcome]").length > 0);
    const board = section(main(`/time-machine?season=${BOARD_SEED.past[0]}&week=0`), "board")!;
    assert.match(text(board.querySelector("[data-testid=outcomes-final]")!), /^What happened: \d+ of the \d+ players who played/);
    assert.equal(all(board, "[data-testid=drivers]").length, 0, "compact: no drivers");
    assert.match(text(main("/time-machine?season=1999&week=1")), /Nothing was published for that week; showing 2026 week 3 instead\./);
  });
});
