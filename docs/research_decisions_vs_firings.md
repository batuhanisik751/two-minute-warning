# Do bad fourth-down decisions get NFL coaches fired? (step H5)

PROJECT_SPEC 8.5 asks one research question: *controlling for performance vs expectation, does
poor decision quality (8.4) raise firing risk?* This page answers it in plain English. Every
number below is copied from the generated report, **reports/hot_seat/research.md** (every
coefficient: reports/hot_seat/research.csv); rerun with `uv run twm hotseat research`
(code `src/twm/modules/hot_seat/research.py`, tests `tests/test_hot_seat_research.py`).

## The short answer

**No: we find no detectable link.** Once a team's results against expectations and the usual
context (tenure, last year's playoffs, losing streaks, a rookie first-round QB, division rank)
are accounted for, coaches who give away more win probability on fourth down are fired at
season end about as often as the others. The estimate is an odds ratio of **1.01** per
standard deviation of fourth-down WP lost per game, with a classical 95% interval of 0.76 to
1.35 and a season-bootstrap 95% interval of 0.72 to 1.39 (1 = no effect). A permutation check
that shuffles the decision measure among the coaches of the same season gives p = 0.938.

A null result is a valid result, and this one is not "nothing was measured": in the same
model, results against the market's expectation (`wins_vs_expected`, odds ratio 0.31 per SD),
last season's playoff run (0.37) and being in year one (0.45) or two (0.59) of a tenure all
clearly move firing risk (report, "Primary estimate"). The fourth-down measure does not.

## What "one standard deviation" means

- **WP lost** on a fourth down is how much win probability the chosen option (go, field goal,
  punt) gave up against the best option, by this project's own models. Only *clear* calls
  count: the best option beat the second best by more than 1.5 WP points.
- The average coach in the sample gives away 0.26 expected wins a season this way; one
  standard deviation is 0.75 WP points per game, about **0.12 expected wins over a 16.2-game
  season**. So the question is whether giving away about 0.12 more expected wins a season on
  fourth downs changes a coach's fate.
- In probabilities: 14.4% of the coach-seasons in the sample end in a firing. A coach one SD
  worse would be at 14.5%; the two intervals together allow anything from 10.8% to 19.0%. The
  data cannot rule out a modest effect either way, but they show no sign of one.

## The data

- 597 coach-seasons at the end of the regular season, 2006-2025 (the grades start in 2006), 86
  of them fired (after the season, or a mutual parting) within the spec's window; interim
  coaches left out. Coaches fired *during* the season have no end-of-season row: of the 126
  positive coach-seasons, 40 enter only the week-12 and weekly (hazard) versions below.
- The decision grades are the stored, pinned Decision Report Card grades (never regraded).

## Robustness: every version says the same

Each row of the report's robustness table refits the model one way; the fourth-down odds ratio
stays between 0.92 (no controls at all) and 1.11 (the weekly hazard model), and every interval,
classical and bootstrap, includes 1: performance controls only (1.00), the week-12 snapshot
(0.94), the weekly hazard with whole seasons resampled (1.11), censored coach-seasons dropped
(1.01), first-year coaches excluded (1.03), WP lost on every graded fourth down including
toss-ups (1.01), and adding the clock-management cases per game (1.01). Counting
`resigned_under_pressure` as a firing changes nothing: no such departure falls in 2006-2025.

## Honest limits

- **Few firings.** 86 at season end over 20 seasons: the intervals are wide.
- **Owners never see "WP lost".** It is our measure. Owners see records and results against
  expectations, which the model holds fixed; the question is whether decisions matter beyond
  them, and if they matter only *through* results, this design does not credit them.
- **Correlation, not causation.**
- **Labels:** cited public research (step H1) that the owner accepted in bulk on 2026-10-01
  rather than re-checking each row (docs/hot_seat.md, "Where the labels come from").
- **Our own grades:** the decisions are graded by this project's models; noisy grades pull a
  real effect toward 1.
- **The late game is not graded** (since G3b): 4,473 regular-season fourth downs in the last
  120 s of the 4th quarter and in overtime, often the calls people remember.
- **Clock management is thin:** 66 regular-season clock cases in 2006-2025; 44 of the 597
  coach-seasons have one, so that row says little about clock management itself.
