// Smoke tests of the Hot-Seat Meter (step H4b): /hot-seat, the coach pages' Hot-Seat section, the
// home card, and the /methodology and /track-record sections. Seed-only checks use
// tests/seed-hot-seat.ts; against a real publish only what holds for any data.
import assert from "node:assert/strict";
import { before, describe, test } from "node:test";
import { HOT_SEAT_DRIVERS, HOT_SEAT_RESEARCH, HOT_SEAT_WINDOW_DAYS } from "../../lib/method";
import { HOT_SEAT_SEED as H, hotSeatListRows, hotSeatLists } from "../seed-hot-seat";
import { BASE, DATA, EMPTY_BASE, fetchPage, prose, serverUp, text, type Page } from "./dom";
import { foldCheck } from "./fold";
import { routeSet, type RouteSet } from "./routes";

let up = false;
let emptyUp = false;
let set: RouteSet;
const pages = new Map<string, Page>();
const empty = new Map<string, Page>();
const seedOnly = DATA === "seed";
const EXTRA = ["/", "/hot-seat", "/methodology", "/track-record", ...(seedOnly ? [`/hot-seat?season=${H.past}&week=3`] : [])];

before(
  async () => {
    up = await serverUp();
    if (!up) return;
    set = await routeSet();
    for (const p of [...set.routes.filter((r) => ["hot-seat", "coach", "coach-hot-seat"].includes(r.kind)).map((r) => r.path), ...EXTRA]) {
      if (!pages.has(p)) pages.set(p, await fetchPage(p));
    }
    if (EMPTY_BASE && seedOnly) {
      emptyUp = await serverUp(EMPTY_BASE);
      if (emptyUp) for (const p of ["/", "/hot-seat", "/methodology", "/track-record"]) empty.set(p, await fetchPage(p, EMPTY_BASE));
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
const WHOLE = /^(?:<1%|>99%|\d{1,3}%) Estimated chance$/;
const EFFECT = /\((?:raises|lowers|barely moves) the estimate\)$/;
/** wording these pages must never use: they are about real people's jobs */
const HARSH = /will be fired|is getting fired|on the hot seat|hot seat is|doomed|axe|🔥/i;

function checkRows(path: string, m: Element) {
  const rows = all(m, "[data-testid=hot-seat-row]");
  const ranks = rows.map((r) => Number(text(r.querySelector("[data-cell=rank]")!)));
  assert.deepEqual(ranks, [...ranks].sort((a, b) => a - b), `${path}: rows by rank`);
  for (const r of rows) {
    assert.ok(r.querySelector("a[href^='/coach/']"), `${path}: coach link`);
    assert.match(text(r.querySelector("[data-cell=estimate]")!), WHOLE, `${path}: a whole percent`);
    const ds = all(r, "[data-testid=drivers] > li");
    assert.ok(ds.length <= HOT_SEAT_DRIVERS, `${path}: at most ${HOT_SEAT_DRIVERS} drivers`);
    for (const d of ds) assert.match(text(d), EFFECT, `${path}: ${text(d)}`);
    assert.match(text(r.querySelector("[data-testid=key-numbers]")!), /^Record /, path);
    assert.match(text(r.querySelector("[data-testid=timeline-words]")!), /^This season: /, path);
  }
  return rows;
}

describe("/hot-seat", () => {
  test("every list page: the picker, live or reconstructed in words, ranked rows in whole percents, signed drivers", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const routes = set.routes.filter((r) => r.kind === "hot-seat");
    if (!routes.length) return t.skip("no Hot-Seat list in this database");
    for (const r of routes) {
      const m = main(r.path);
      assert.equal(m.querySelector("form[action='/hot-seat']")?.getAttribute("method"), "get", `${r.path}: week picker`);
      const week = m.querySelector("[data-testid=hot-seat-week]")!;
      assert.match(text(week.querySelector("[data-testid=kind-label]")!), /^(Live list|Reconstructed list)/, r.path);
      assert.ok(checkRows(r.path, m).length > 0, `${r.path}: no rows`);
      assert.ok(m.querySelector("[data-testid=outcomes-pending]") || m.querySelector("[data-testid=outcomes-final]"), `${r.path}: outcomes`);
      assert.doesNotMatch(prose(m), HARSH, `${r.path}: careful wording`);
      assert.match(prose(m), new RegExp(`let go by ${HOT_SEAT_WINDOW_DAYS} days after the season`), r.path);
    }
  });

  test("this season's weekly lists not started yet: said plainly, with when the first is due", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const note = main("/hot-seat").querySelector("[data-testid=hot-seat-not-started]");
    if (seedOnly) assert.equal(note, null, "the seed's newest list is this season's");
    if (!note) return;
    const due = String.raw`(?:is|was) due at \w{3} \d{1,2} \w{3} \d{4}, \d{2}:\d{2} UTC \(the weekly as-of\)(?: and has not been published yet)?`;
    assert.match(
      prose(note),
      new RegExp(String.raw`^No \d{4} weekly list has been published yet: the next one (?:${due}|is due on the \w+ after week \d+'s games)\.(?: Shown below: the newest published list, from \d{4}\.)?$`),
    );
  });

  test("how to read it: the meaning, the early-season check from the data, the labels' source, the research", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const r = set.routes.find((x) => x.kind === "hot-seat");
    if (!r) return t.skip("no Hot-Seat list in this database");
    const how = main(r.path).querySelector("[data-testid=how-to-read]")!;
    assert.ok(how, "how to read this");
    assert.match(prose(how.querySelector("[data-testid=early-season-caveat]")!), /coaches given .+ in weeks 2–4 \(an average estimate of \d+%\) were really let go \d+% of the time: [\d,]+ of [\d,]+ coach-weeks, [\d,]+ coach-seasons/);
    assert.ok(all(how, "[data-testid=phase-calibration] table").length >= 1, "the phase tables");
    assert.match(prose(how), /accepted that research in bulk/);
    assert.ok(how.querySelector("[data-testid=research-sentence] a[href='/methodology#hot-seat-research']"), "the research link");
  });

  test("coach pages: the Hot-Seat history in whole percents, outcomes in words", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const withSection = set.routes.filter((r) => r.kind === "coach" || r.kind === "coach-hot-seat").map((r) => main(r.path)).filter((m) => m.querySelector("[data-testid=coach-hot-seat]"));
    if (!withSection.length) return t.skip("no coach page with Hot-Seat rows");
    for (const m of withSection) {
      const sec = m.querySelector("[data-testid=coach-hot-seat]")!;
      for (const td of all(sec, "[data-testid=coach-hot-seat-table] tbody td.num:nth-child(2) .font-semibold")) assert.match(text(td), /^(?:<1%|>99%|\d{1,3}%)$/);
      for (const o of all(sec, "[data-outcome]")) assert.match(text(o), /^(Let go|Not let go|Left another way|Pending)$/);
      assert.doesNotMatch(prose(sec), HARSH);
      // record vs expectation (spec 9.1): beside the Hot-Seat history, NULL-safe
      const rec = m.querySelector("[data-testid=coach-record-table]");
      assert.ok(rec, "the record vs expectation table");
      for (const tr of all(rec, "tbody tr")) {
        const [record, expected, vs] = all(tr, "td").map(text);
        // an interim coach's season is the team's record, labelled so (G2.2)
        assert.match(record, /^(\d+–\d+|\d+\.5 wins in \d+ games \(a tie counts half\))( the team's)?$/);
        assert.match(expected, /^(\d+\.\d|not known)$/);
        assert.match(vs, /^([+−]?\d+\.\d|–)$/);
      }
    }
  });
});

/** The seed's early-season cell (weeks 2-4, 50% or more), computed from the seed's own rows. */
function seedEarly() {
  const rows = hotSeatLists()
    .filter((l) => l.kind === "backtest" && l.season !== H.season && l.snapshot === "weekly" && l.week <= 4)
    .flatMap((l) => hotSeatListRows(l.season, l.week, l.snapshot, l.kind))
    .filter((r) => !r.isInterim && r.probability >= 0.5);
  const gone = rows.filter((r) => r.season === H.past && [H.firedAfter, H.firedDuring].includes(r.coachId));
  return { n: rows.length, departed: gone.length, seasons: new Set(rows.map((r) => `${r.season}|${r.coachId}`)).size };
}

describe("seed: the Hot-Seat Meter", () => {
  test("/hot-seat: this season's live list by default, folded after 10, outcomes pending, no per-row outcome", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main("/hot-seat");
    const week = m.querySelector("[data-testid=hot-seat-week]")!;
    assert.equal(week.getAttribute("data-kind"), "live");
    assert.match(text(week.querySelector("h2")!), new RegExp(`Hot-Seat Meter, ${H.season} week ${H.liveWeek}`));
    const rows = checkRows("/hot-seat", m);
    assert.equal(rows.length, 12);
    assert.match(text(rows[0]), /Jamie North/);
    assert.equal(text(rows[0].querySelector("[data-cell=estimate]")!), "72% Estimated chance");
    assert.equal(text(rows[11].querySelector("[data-cell=estimate]")!), "<1% Estimated chance");
    const folds = foldCheck(pages.get("/hot-seat")!.doc).folds.filter((f) => f.what.startsWith("Hot-Seat Meter"));
    assert.deepEqual(folds.map((f) => [f.head, f.rest, f.total]), [[10, 2, 12]]);
    assert.match(text(m.querySelector("[data-testid=outcomes-pending]")!), /pending until the season's departures are labelled/);
    assert.equal(all(m, "[data-testid=outcome]").length, 0);
    const missing = all(m, "[data-testid=drivers] > li").find((d) => /not available yet/.test(text(d)));
    assert.ok(missing && /^Fourth-down WP lost per game: not available yet/.test(text(missing)), "a missing driver in words");
  });

  test("/hot-seat, the past season's snapshot: final outcomes in careful words, the interim coach flagged", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const path = `/hot-seat?season=${H.past}&week=${H.lastWeek}`;
    const m = main(path);
    assert.match(text(m.querySelector("[data-testid=hot-seat-week] h2")!), new RegExp(`${H.past}, end of season`));
    assert.match(prose(m.querySelector("[data-testid=outcomes-final]")!), /1 of these 12 coaches was let go by/);
    const row = (id: string) => m.querySelector(`[data-testid=hot-seat-row][data-coach=${id}]`)!;
    assert.match(text(row(H.firedAfter).querySelector("[data-testid=outcome]")!), /Let go fired after the season, announced 2026-01-05/);
    assert.match(text(row(H.retired).querySelector("[data-testid=outcome]")!), /Left another way retired, announced 2026-01-08: not counted as let go/);
    // G2.4: a departure whose day no source gives (NULL announced) claims no announcement date
    const wk3 = main(`/hot-seat?season=${H.past}&week=3`).querySelector(`[data-testid=hot-seat-row][data-coach=${H.firedDuring}] [data-testid=outcome]`)!;
    assert.match(text(wk3), /Let go fired during the season, announcement date not reported/);
    assert.doesNotMatch(text(wk3), /announced \d/);
    assert.ok(row(H.interim).querySelector("[data-testid=interim-flag]"), "interim flag");
    assert.match(text(row(H.interim)), /the model was not trained on interim coaches/);
    const opts = all(m, "form[action='/hot-seat'] select[name=week] option").map((o) => text(o));
    assert.ok(opts.includes("End of season (reconstructed)"), opts.join(", "));
  });

  test("the early-season caveat is the seed's own rows, aggregated", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const e = seedEarly();
    const caveat = prose(main("/hot-seat").querySelector("[data-testid=early-season-caveat]")!);
    assert.match(caveat, new RegExp(`were really let go ${Math.round((100 * e.departed) / e.n)}% of the time: ${e.departed} of ${e.n} coach-weeks, ${e.seasons} coach-seasons`));
  });
});

describe("seed: the Hot-Seat Meter elsewhere", () => {
  test("home: the top 5 of this week's live list, linking to the full list", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const card = main("/").querySelector("[data-testid=hot-seat-card]")!;
    assert.ok(card, "the Hot-Seat card");
    assert.equal(all(card, "[data-testid=hot-seat-top] > li").length, 5);
    for (const e of all(card, "[data-cell=estimate]")) assert.match(text(e), WHOLE);
    assert.ok(card.querySelector(".live-pill"), "the live pill");
    assert.ok(card.querySelector(`a[href='/hot-seat?season=${H.season}&week=${H.liveWeek}&kind=live']`), "the link");
  });

  test("coach pages: this season's weeks with a data table, past seasons' last estimate and outcome", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const sec = main(`/coach/${H.featured}`).querySelector("[data-testid=coach-hot-seat]")!;
    assert.match(prose(sec.querySelector("[data-testid=coach-hot-seat-season]")!), new RegExp(`${H.season}: week ${H.reconWeek}: \\d+%, week ${H.liveWeek}: \\d+%`));
    assert.equal(all(sec, "[data-testid=hot-seat-chart] details table tbody tr").length, 2);
    const past = all(sec, "[data-testid=coach-hot-seat-table] tbody tr").map((r) => text(r.querySelector("th a")!));
    assert.deepEqual(past, [String(H.past), String(H.older)]);
    // record vs expectation: this season through the newest week, past seasons at their end
    const rec = all(main(`/coach/${H.featured}`), "[data-testid=coach-record-table] tbody tr").map((r) => prose(r.querySelector("th")!));
    assert.equal(rec.length, 3, rec.join(" | "));
    assert.match(rec[0], new RegExp(`^${H.season} [A-Z]{2,3}, through week ${H.liveWeek}$`));
    assert.match(rec[1], new RegExp(`^${H.past} [A-Z]{2,3}$`));
    assert.match(rec[2], new RegExp(`^${H.older} [A-Z]{2,3}$`));
    const gone = main(`/coach/${H.firedAfter}`).querySelector("[data-testid=coach-hot-seat]")!;
    assert.match(prose(gone), /Let go fired after the season, announced 2026-01-05/);
    assert.equal(gone.querySelector("[data-testid=coach-hot-seat-season]"), null, "no row this season");
    const interim = main(`/coach/${H.interim}`).querySelector("[data-testid=coach-hot-seat-table] tbody tr")!;
    assert.match(prose(interim), /interim/);
    assert.match(prose(interim), /interim coach not retained, announced 2026-01-06: not counted as let go/);
    // G2.2: an interim coach's record is the team's season, and says so
    const irec = main(`/coach/${H.interim}`).querySelector("[data-testid=coach-record-table] tbody tr")!;
    assert.match(prose(irec.querySelector("th")!), /interim coach: the team's season/);
    assert.match(prose(irec.querySelectorAll("td")[0]), /^(\d+–\d+|\d+\.5 wins in \d+ games \(a tie counts half\)) the team's$/);
    assert.equal(all(main(`/coach/${H.featured}`), "[data-testid=coach-record-interim]").length, 0, "a head coach's own seasons carry no label");
    // G2.5: a coach without a graded season says when grading starts instead of reading as a clean record
    const ungraded = main(`/coach/${H.firedAfter}`);
    assert.equal(prose(ungraded.querySelector("[data-testid=coach-ungraded]")!), "Nothing graded: the Decision Report Card grades the seasons from 2024 on.");
    assert.ok(!/No clearly wrong call|No clock case|No graded season yet/.test(prose(ungraded)), "no clean-record wording");
  });

  test("methodology and track record: the models, the firings, the research numbers, live kept apart", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main("/methodology").querySelector("[data-testid=hot-seat-method]")!;
    assert.equal(all(m, "[data-testid=hot-seat-models] tbody tr").length, 5);
    assert.equal(all(m, "[data-testid=hot-seat-features] li").length, H.features.length);
    const fir = all(m, "[data-testid=hot-seat-firings] tbody tr").map((r) => text(r.querySelector("th")!));
    assert.deepEqual(fir, ["2025", "2024", "2023 training only", "2022 training only"]);
    assert.match(prose(m.querySelector("[data-testid=hot-seat-research]")!), new RegExp(`odds ratio per standard deviation of fourth-down WP lost per game is ${HOT_SEAT_RESEARCH.oddsRatio.toFixed(2)}`));
    assert.match(prose(m), /penalty strength C = 0\.1/);
    const tr = main("/track-record").querySelector("[data-testid=track-hot-seat]")!;
    // the top-5 hit rate at week 12 beside the season-end one (tiles and the models table)
    assert.equal(all(tr, "[data-testid=hot-seat-headline] > li").length, 4);
    assert.match(prose(tr.querySelector("[data-testid=hot-seat-headline]")!), /top 5 at week 12 .*top 5 at season end/);
    // G4.9: the top-5 hit rates are percentages of the coaches let go (the slice's positives), not of its rows
    const t5 = all(tr, "[data-testid=hot-seat-headline] > li").slice(2);
    assert.ok(t5.every((li) => /^\d{1,3}%$/.test(text(li.querySelector("[data-cell=value]")!))), "top-5 tiles in percent");
    assert.deepEqual(t5.map((li) => text(li.querySelector("[data-cell=note]")!)), ["4 coaches let go, 2 test seasons", "3 coaches let go, 2 test seasons"]);
    assert.ok(all(m, "[data-testid=hot-seat-models] tbody td:nth-last-child(-n+2) .font-semibold").every((c) => /^\d{1,3}%$/.test(text(c))), "top-5 columns in percent");
    const heads = all(m, "[data-testid=hot-seat-models] thead th").map(text);
    assert.deepEqual(heads.slice(-2), ["Top-5 hit rate, week 12", "Top-5 hit rate, season end"]);
    assert.equal(tr.querySelector("[data-testid=hot-seat-live]")!.getAttribute("data-live"), "pending");
    assert.match(prose(tr.querySelector("[data-testid=hot-seat-live]")!), /^No live results yet\. 1 live list \(12 coach-seasons\): outcomes pending until the season's departures are labelled/);
  });

  test("empty database: an empty state on /hot-seat, nothing on the home page, 'not published' elsewhere", (t) => {
    if (!emptyUp) return t.skip("no empty-database server");
    assert.ok(main("/hot-seat", empty).querySelector("[data-testid=empty-state]"));
    assert.equal(main("/", empty).querySelector("[data-testid=hot-seat-card]"), null);
    assert.ok(main("/methodology", empty).querySelector("[data-testid=hot-seat-method] [data-testid=empty-state]"));
    assert.match(prose(main("/track-record", empty).querySelector("[data-testid=track-hot-seat]")!), /not published yet/);
  });
});
