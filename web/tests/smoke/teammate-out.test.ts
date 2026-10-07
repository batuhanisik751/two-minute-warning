// Smoke tests of Teammate out (feature #5): /teammate-out (this week's list, an older week, a week
// without a list, the empty seed), the disclosure of the owner's override, the player badge, and
// the /methodology and /track-record sections. Seed-only checks use tests/seed-teammate-out.ts;
// against a real publish only what holds for any data.
import assert from "node:assert/strict";
import { before, describe, test } from "node:test";
import { INACTIVES_NOTE, WHEN_LISTS_FILL } from "../../lib/teammate-out";
import { SEED } from "../seed";
import { TEAMMATE_OUT_SEED as T } from "../seed-teammate-out";
import { BASE, DATA, EMPTY_BASE, fetchPage, prose, serverUp, text, type Page } from "./dom";

let up = false;
let emptyUp = false;
const pages = new Map<string, Page>();
const empty = new Map<string, Page>();
const seedOnly = DATA === "seed";
const PAST = `/teammate-out?season=${T.pastWeek.season}&week=${T.pastWeek.week}`;
const NONE = `/teammate-out?season=${T.emptyWeek.season}&week=${T.emptyWeek.week}`;
// G4.10: an impossible week and a past week with no list never get one: no "yet"
const NEVER = [`/teammate-out?season=${T.week.season}&week=99`, `/teammate-out?season=${T.week.season}&week=1`];
const PATHS = ["/teammate-out", "/methodology", "/track-record", ...(seedOnly ? [PAST, NONE, ...NEVER, `/player/${T.featured}`, `/player/${T.absent}`, "/player/00-9000001"] : [])];

before(
  async () => {
    up = await serverUp();
    if (!up) return;
    for (const p of PATHS) pages.set(p, await fetchPage(p));
    if (EMPTY_BASE && seedOnly) {
      emptyUp = await serverUp(EMPTY_BASE);
      if (emptyUp) for (const p of ["/teammate-out", "/methodology", "/track-record"]) empty.set(p, await fetchPage(p, EMPTY_BASE));
    }
  },
  { timeout: 300_000 },
);

const main = (path: string, from = pages): Element => {
  const m = from.get(path)?.doc.querySelector("main#main");
  assert.ok(m, `${path}: no <main id=main>`);
  return m;
};
const all = (m: Element, sel: string) => Array.from(m.querySelectorAll(sel));
const num = (s: string) => Number(/^(\d+\.\d)/.exec(s)?.[1]);

describe("/teammate-out (any data)", () => {
  test("the page, its sections and the honest notes", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const p = pages.get("/teammate-out")!;
    assert.equal(p.status, 200);
    const m = main("/teammate-out");
    assert.equal(prose(m.querySelector("h1")!), "Teammate out");
    assert.equal(p.doc.querySelector("nav[aria-label=Main] a[aria-current=page]")?.getAttribute("href"), "/teammate-out");
    for (const id of ["to-week", "to-allocation-section", "to-quality-section", "to-live-section"]) assert.ok(m.querySelector(`[data-testid=${id}]`), `section ${id}`);
    assert.ok(m.querySelector("[data-testid=to-list]") || m.querySelector("[data-testid=to-empty]"), "neither a list nor the empty state");
    assert.ok(prose(m).includes(INACTIVES_NOTE), "the inactives note");
    for (const r of all(m, "[data-testid=to-row]")) {
      assert.ok(r.querySelector("[data-cell=player] a[href^='/player/']"), "player link");
      assert.match(text(r.querySelector("[data-testid=to-points]")!), /^\d+\.\d \(\d+\.\d–\d+\.\d\)$/);
    }
    // the disclosure: the rule's pick and the one used, from the published rows
    const d = m.querySelector("[data-testid=to-disclosure]");
    if (m.querySelector("[data-testid=to-candidates-table]")) assert.ok(d, "the candidates come with the disclosure");
    assert.ok(m.querySelector("a[href='/methodology#teammate-out']"), "methodology link");
  });

  test("/methodology and /track-record have a Teammate-out section", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    assert.ok(main("/methodology").querySelector("#teammate-out"), "methodology section");
    assert.ok(main("/methodology").querySelector("a[href='#teammate-out']"), "on-this-page link");
    const s = main("/track-record").querySelector("[data-testid=track-teammate-out]");
    assert.ok(s, "track-record section");
    assert.equal(all(s, "[data-testid=panel-backtest]").length, 1);
    assert.equal(all(s, "[data-testid=panel-live]").length, 1);
  });
});

describe("/teammate-out (seed)", () => {
  test("this week's newest snapshot: by kickoff, then team; absent starters and why; teammates by points", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main("/teammate-out");
    assert.equal(prose(m.querySelector("#to-week-heading")!), `This week, ${T.week.season} week ${T.week.week}`);
    assert.match(prose(m.querySelector("[data-testid=to-asof]")!), /^The newest list: as of Sat 3 Oct 2026, 12:00 UTC .* 3 starters out on 2 teams .* 6 teammates listed/);
    const groups = all(m, "[data-testid=to-list] > section");
    assert.deepEqual(groups.map((g) => g.querySelector("time")!.getAttribute("dateTime")), T.kickoffs);
    const teams = all(m, "[data-testid=to-team]");
    assert.deepEqual(teams.map((x) => x.getAttribute("data-team")), ["NHG", "EVP"]);
    const nhg = teams[0];
    assert.match(prose(nhg.querySelector("[data-testid=to-absent]")!), /^Starter out: .+ \(RB\), Out \(injury report\)$/);
    assert.ok(nhg.querySelector(`[data-testid=to-absent] a[href='/player/${T.absent}']`), "absent starter links his page");
    assert.match(prose(nhg), /Up for grabs: 56% of the carries and 10% of the targets/);
    const evp = prose(teams[1].querySelector("[data-testid=to-absent]")!);
    assert.match(evp, /\(WR\), on a reserve list \(injured reserve and similar\)/);
    assert.match(evp, /\(TE\), Doubtful \(injury report\)/);
    const pts = (x: Element) => all(x, "[data-testid=to-points]").map((c) => num(text(c)));
    assert.deepEqual(pts(nhg), [14.2, 13.1, 4.1], "by predicted points, highest first");
    const featured = all(nhg, "[data-testid=to-row]")[0];
    assert.ok(featured.querySelector(`a[href='/player/${T.featured}']`));
    assert.match(prose(featured), /Role RB2/);
    assert.match(prose(featured), /Carry share 25% → 38% \(\+13\)/);
    assert.match(prose(featured), /PPR points: 14\.2 \(6\.1–22\.0\) 80% range · usually 9\.0 a game; the share change is worth \+4\.5; his points per carry or target pulled toward the position average: \+0\.7/);
    for (const term of ["starter_out", "vacated_share", "teammate_role", "carry_share", "target_share", "allocation", "mae"]) {
      assert.ok(m.querySelector(`a[href='/methodology#term-${term}']`), `term ${term}`);
    }
  });

  test("where the work goes, the candidates with the override disclosed, the coverage, the live record", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main("/teammate-out");
    assert.deepEqual(all(m, "[data-testid=to-allocation] [data-out-pos]").map((x) => x.getAttribute("data-out-pos")), ["RB", "WR", "TE"]);
    const rb = m.querySelector("[data-testid=to-allocation] [data-out-pos=RB]")!;
    assert.deepEqual(all(rb, "tbody tr").map((r) => r.getAttribute("data-role")), ["RB2", "RB3", "RB+", "WR1"]);
    assert.match(prose(rb.querySelector("tr[data-role=RB2]")!), /^RB2 45% 41% 408$/);
    assert.match(prose(rb.querySelector("[data-testid=to-events]")!), /^420 games with one starter out \(500 in all\), seasons 2013-2025\.$/);
    const rows = all(m, "[data-testid=to-candidates-table] tbody tr");
    assert.deepEqual(rows.map((r) => r.getAttribute("data-candidate")), ["nothing", "pro_rata", "group", "role"]);
    assert.match(prose(rows[0]), /^Nothing changes 4\.360 .* the rule's pick$/);
    assert.match(prose(rows[3]), /^Role 4\.393 .* used on this site$/);
    const d = m.querySelector("[data-testid=to-disclosure]")!;
    assert.equal(d.getAttribute("data-overridden"), "true");
    assert.match(prose(d), /^The rule picked Nothing changes; the site uses Role\./);
    assert.ok(prose(d).includes(T.rule), "the rule's published text");
    assert.match(prose(d), /Points MAE: Nothing changes 4\.360, Role 4\.393\./);
    assert.equal(prose(d.querySelector("[data-testid=to-why]")!), T.why);
    assert.match(prose(d), /shown as an 80% range/);
    assert.deepEqual(all(m, "[data-testid=to-coverage-table] tbody tr").map((r) => r.getAttribute("data-position")), ["all", "RB", "WR", "TE"]);
    assert.match(prose(m.querySelector("[data-testid=to-coverage-table] tr[data-position=all]")!), /^All 1,400 150 77\.9% 160$/);
    const live = m.querySelector("[data-testid=to-live]")!;
    assert.equal(live.getAttribute("data-live"), "graded");
    assert.match(prose(live), /^2 of the 3 graded teammates landed inside their 80% range \(66\.7%\), from 1 live week: the predicted PPR points missed by 3\.40 on average, against 3\.90 for "nothing changes"/);
    assert.match(prose(live), /6 more are waiting for their game\./);
    assert.match(prose(m.querySelector("#to-live-heading")!), /^2026 so far$/);
  });

  test("an older week shows its own newest snapshot; a week without one, the empty state", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const past = main(PAST);
    assert.equal(prose(past.querySelector("#to-week-heading")!), `The list, ${T.pastWeek.season} week ${T.pastWeek.week}`);
    assert.equal(all(past, "[data-testid=to-row]").length, 3);
    const none = main(NONE);
    const e = none.querySelector("[data-testid=to-empty]");
    assert.ok(e, "the empty state");
    assert.match(prose(e), new RegExp(`No starter listed out for ${T.emptyWeek.season} week ${T.emptyWeek.week} yet`));
    assert.ok(prose(e).includes(WHEN_LISTS_FILL) && prose(e).includes(INACTIVES_NOTE), "when lists fill in, and the inactives note");
    assert.equal(all(none, "[data-testid=to-weeks] a").length, 2, "links to the two weeks with a list");
  });

  test("an impossible or past week without a list says there is none, not \"yet\"", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    for (const [path, week] of [[NEVER[0], 99], [NEVER[1], 1]] as const) {
      const e = main(path).querySelector("[data-testid=to-empty]");
      assert.ok(e, `${path}: the empty state`);
      assert.match(prose(e), new RegExp(`^No list for ${T.week.season} week ${week} A list is made only for the week whose games are next; the weeks that have one are linked above\\.$`), path);
    }
  });

  test("the player badge: a predicted gainer or an absent starter on this week's newest list", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const g = main(`/player/${T.featured}`).querySelector("[data-testid=to-badge]");
    assert.ok(g, "gainer badge");
    assert.equal(g.getAttribute("data-kind"), "gainer");
    assert.match(prose(g), /^Teammate out \(.+\): predicted 14\.2 \(6\.1–22\.0\) PPR points as the RB2; his bigger share is worth \+4\.5; his points per carry or target pulled toward the position average: \+0\.7 \(2026 week 4 list\)$/);
    assert.equal(g.querySelector("a")?.getAttribute("href"), `/teammate-out?season=${T.week.season}&week=${T.week.week}`);
    assert.equal(T.featured, SEED.featured, "the badge's player is the main seed's featured one");
    const a = main(`/player/${T.absent}`).querySelector("[data-testid=to-badge]");
    assert.equal(a?.getAttribute("data-kind"), "absent");
    assert.match(prose(a!), /^Starter out: Out \(injury report\); see who gets his work/);
    assert.equal(main("/player/00-9000001").querySelector("[data-testid=to-badge]"), null, "no badge for a player not listed");
  });

  test("the empty seed: no week, no list, the notes; the other sections say not published", (t) => {
    if (!emptyUp) return t.skip("no empty server");
    const m = main("/teammate-out", empty);
    const e = m.querySelector("[data-testid=to-empty]");
    assert.ok(e, "the empty state");
    assert.match(prose(e), /^No week is being played right now/);
    assert.ok(prose(e).includes(WHEN_LISTS_FILL));
    assert.match(prose(m.querySelector("[data-testid=to-live-empty]")!), /^No live results yet\./);
    assert.equal(all(m, "[data-testid=not-published]").length, 3, "allocation, candidates and coverage: not published yet");
    assert.ok(main("/methodology", empty).querySelector("[data-testid=teammate-out-method]"));
  });
});
