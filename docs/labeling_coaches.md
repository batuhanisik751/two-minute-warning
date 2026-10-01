# Labeling head-coach departures (Hot-Seat Meter, steps H1/H2)

The Hot-Seat Meter (PROJECT_SPEC 8.5) predicts whether a head coach is about to be fired. To
train it we need the true answer for every past departure: **what kind** of departure it was and
**when** it was announced. A program cannot read that reliably from the schedules, so you (the
owner) confirm it by hand. This page tells you how. Budget: about 1 minute per row.

## The two files

| File | Who writes it | What it is |
|---|---|---|
| `data/manual/coach_departures_candidates.csv` | the program (`uv run twm hotseat candidates`) | every head-coach change the nflverse schedules show (as corrected by the cited `data/manual/coach_corrections.csv`, H1b), 2002 onward. Regenerated from the warehouse; never edit it by hand. |
| `data/manual/coach_departures.csv` | you (prefilled by research) | one row per departure: the schedule candidates plus departures the schedules miss. This is the file you check. |

Every row of `coach_departures.csv` was **prefilled** from public pages (mostly each season's
Wikipedia "NFL season" page, section "Head coach changes", and a few coaches' own Wikipedia
pages). A prefilled value is only a suggestion (`prefill = suggested`) until you write `y` in
`verified_by_owner`. When the research could not settle a value it is left empty and
`prefill_note` says why.

## How to edit the file

Any spreadsheet app works; on a Mac, Numbers: open `coach_departures.csv`, edit, then
**File > Export To > CSV...** and save it over the same file (Numbers does not save CSV with a
plain Save). Spreadsheet apps like to rewrite dates (`2003-12-17` becomes `12/17/2003`): if a
date you did not touch changes, type it back as `YYYY-MM-DD`. Do not sort away or delete
columns. After every session run `uv run twm hotseat check-labels`: it reports any date in the
wrong form, an unknown type or a verified row without a URL, so nothing breaks silently.

## Columns you will look at

- `team`, `coach_name`, `last_season`: who left, and the last season he coached there.
- `change_kind`: `in_season` (the schedule shows a new coach between two games of one season) or
  `offseason` (a different coach in week 1 of the next season). Empty on `source_only` rows.
- `last_game_date`, `successor_name`: the departing coach's last game in the schedule and who
  coached the next game. **From 2024 on (and in a few earlier seasons) nflverse's schedule lists
  a fired coach for the whole season**; the warehouse corrects the cases in
  `data/manual/coach_corrections.csv` (H1b), any other one is wrong here: trust the source.
- `interim_suspected`: he took over during a season and left within that season.
- `data_gap_suspected`: the schedule cannot tell whether he was fired during the season (2024+
  seasons with a losing record), or the row was added from a source (`source_only`).
- `departure_type`, `announced_date`, `source_url`: the values you confirm or correct.
- `prefill`, `prefill_note`: where the suggestion came from, with a short quote from the page.
- `verified_by_owner`: write `y` when you have checked the row. Leave empty otherwise.
- `notes`: anything you want to remember (your own words).

## The departure types

Pick the one that best matches what the team and the coach said **at the time**.

| `departure_type` | Use it when | Examples of wording |
|---|---|---|
| `fired_in_season` | the team ended his job before its season was over | "fired after a 1-4 start", "relieved of his duties in November" |
| `fired_after_season` | the team ended his job after its last game (regular season or playoffs). Includes "contract not renewed" and "will not exercise the option" | "fired the day after the finale", "contract will not be renewed" |
| `mutual_parting` | both sides announced they "agreed to part ways" | "mutually agreed to part ways" |
| `resigned_under_pressure` | he resigned, but the source says he was about to be fired or was forced out | "knowing the team was near certain to let him go, he resigned" |
| `resigned` | he resigned and the source gives no sign of pressure | "resigned, citing family", "used the opt-out in his contract" |
| `retired` | he retired from coaching | "announced his retirement" |
| `health_or_death` | he left because of illness or died | "stepped down for health reasons" |
| `left_for_other_job` | he left for another job (college, another NFL team, trade) | "resigned to coach the University of Arkansas", "traded to Kansas City" |
| `interim_not_retained` | an **interim** coach whose stint ended without being kept as the head coach | "finished the season as interim; X was hired" |
| `other` | none of the above (suspension, reassignment to another role, ...) | "suspended for the season", "reassigned to an advisor role" |

Edge cases:

- **Interim coaches.** A coach named "interim" during a season gets `interim_not_retained` when the team
  hires someone else, whatever happened to him next (he may stay as an assistant). If the team
  removes the interim tag and keeps him, there is no departure and no row (the schedule then
  shows him in week 1 of the next season). The model leaves interim coaches out of training
  (spec 8.5) and reports them separately.
- **Resignations under pressure.** Use `resigned_under_pressure` only when the source says so (a
  firing was expected, he was forced out). Spec 8.5 decision point: by default these do **not**
  count as firings; the model reports a sensitivity run with them counted. (This is the one type
  added to the spec 4.2 list: the decision point needs these resignations told apart from the
  others.)
- **Retirement vs. health.** `retired` when the coach chose to stop; `health_or_death` when the source
  says illness forced it. If both are said ("retired due to health concerns"), pick the one the
  source puts first and write the other in `notes`.
- **Leaving for another job** while still employed is `left_for_other_job`, even if the source
  uses the word "resigned". If he was fired first and found the new job later, it is a firing.
- **A temporary absence is not a departure.** Medical leaves (the coach came back) and
  suspensions served inside one season are not departures; if a schedule row shows one, use
  `other` and say so in `notes` (`coach_returns = true` in the candidates file flags them).
- **Which ones count as "fired" for the model** (spec 8.5): `fired_in_season`,
  `fired_after_season`, `mutual_parting`. `retired`, `health_or_death` and `left_for_other_job` count as
  "not fired" (censored). Your job is only to pick the right type; the model applies these rules.

## What "announced" means

`announced_date` is the **calendar day (US Eastern time) on which the team, or the coach, made the
departure public**: the press release, the press conference, or the first report that the team
confirmed that day. Not the day of his last game, not the day his successor was hired.

- A firing announced the evening of the season finale is that Sunday's date.
- A retirement or resignation announced ahead of time ("he will step down after the season") is
  the day of that announcement, even if it is weeks before his last game.
- A coach who went on leave and was fired later: the day of the firing (and say so in `notes`).
- If a source gives only "January 4" under a heading for the 2010-11 offseason, the year comes
  from that context (`prefill_note` says "year from context" in that case).

Why it matters: the model's label (spec 8.5) is "a firing announced after the as-of date and no
later than 30 days after the team's final game". A wrong date can move a firing into or out of
that window. Whether the window is anchored on the Monday after the season or on the last
kickoff is decided in H3 (some firings are announced on the Sunday evening of the finale).

## How to check one row (about a minute)

1. Open `source_url`. The quote in `prefill_note` tells you what to look for (use the browser's
   find, e.g. Cmd-F for the coach's last name).
2. Confirm or correct `departure_type` using the table above.
3. Confirm or correct `announced_date` (format `YYYY-MM-DD`). If it is empty, a quick web search
   for "<coach> fired <year>" usually finds it in a news story; put that story's link in
   `source_url` (or add it to `notes` if the first page still settles the type).
4. Write `y` in `verified_by_owner`. A verified row needs a type, a date and a URL.

If you cannot settle a row, leave `verified_by_owner` empty and write why in `notes`.

## Adding a departure the schedule misses

The schedules miss some departures (see "Known schedule problems" below). Add one row:

- `candidate_id`: `src_<season>_<team>_<name>`, e.g. `src_2024_NYJ_jeff_ulbrich` (lowercase,
  underscores); `origin`: `source_only`; `team`: the code used everywhere in the app (`LV`, `LAC`,
  `LA`, `WAS`, ...); `last_season`: the season he last coached; `coach_name`.
- `last_game_date` / `successor_first_game_date` if you know them (optional);
  `data_gap_suspected`: `true`; `interim_suspected`: `true` for an interim coach.
- `departure_type`, `announced_date`, `source_url`, then `y` in `verified_by_owner`.

The prefill had added 11 such rows (interim coaches the schedule never names: 2015 MIA, 2016
LA, 2019 CAR, 2024 CHI/NO/NYJ, 2025 NYG/TEN; and ARI, ATL, BUF, whose fired coaches the 2026
schedule still lists). Since H1b the corrected schedule shows all 11, so they are now
`schedule` rows (their suggestions, sources and notes carried over; `prefill_note` says so).

## Checking the whole file

```bash
uv run twm hotseat check-labels
```

It prints how many rows there are, how many you verified, and how many are still suggestions,
then every problem. **Errors** (exit code 1) must be fixed: an unknown type, a date that is not
`YYYY-MM-DD` or not between June 1 of `last_season` and September 30 of the next year, a
verified row without a type, date or URL, `verified_by_owner` other than `y`, a duplicate
`candidate_id`, or the same coach and date twice. **Warnings** are worth a look but may be
right: a schedule candidate with no row, a firing dated before his last listed game (e.g. a
contract non-renewal announced before the finale), an in-season change announced after the
successor's first game (e.g. a leave of absence followed by a firing).

## Regenerating the candidates

`uv run twm hotseat candidates` rebuilds `coach_departures_candidates.csv` from the warehouse
(same input, same file). It never touches your file. New candidates (a new season) show up in
`check-labels` as "schedule candidate has no row": add a row for each, with `origin` =
`schedule` and the `candidate_id` from the candidates file.

The candidates file also has `none_recorded` rows: teams in the latest season with a losing record
so far and no change in the schedule. They are reminders to check the news, not departures; they
do not need a row unless the coach actually left.

## Known schedule problems (why the sources win)

The cases below are corrected in the warehouse by `data/manual/coach_corrections.csv` (H1b,
every row cited; docs/warehouse.md), so the candidates already show them; any new one goes into
that file with its page and quote, then `twm build` and `twm hotseat candidates`.

- From 2024 on nflverse lists one coach per team for the whole season, so in-season firings
  appear as offseason changes (2024 CHI, NO, NYJ; 2025 NYG, TEN) and interim coaches are absent.
- Some earlier in-season firings are missing too: 2015 MIA (Philbin) and TEN (Whisenhunt), 2016
  LA (Fisher), 2019 CAR (Rivera).
- The 2026 schedule still lists Jonathan Gannon (ARI), Raheem Morris (ATL) and Sean McDermott
  (BUF), whom the 2026 season page reports fired in January 2026.
- Spellings: the schedule has "Klint Kubliak" (LV 2026; the sources write Kubiak) and "Jay
  Rosburg" (DEN 2022; the sources write Jerry Rosburg), and "Jim Mora" for two men (IND
  1999-2001 is Jim E. Mora).
- 2007 ATL: the schedule credits Emmitt Thomas with Bobby Petrino's 13th game (12-10).
- Medical leaves are not in the schedule (e.g. 2012 IND, 2013 DEN), which is fine: they are not
  departures.
