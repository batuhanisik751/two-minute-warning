"""`twm league odds`: the owner's playoff odds by Monte Carlo (feature #9; local only).

docs/my_league.md "Luck and playoff odds" explains every rule:

- **the team score model** (picked by a rule fixed before scoring, :func:`check_models`): (a)
  each team's season-to-date mean shrunk toward the league mean by :data:`PSEUDO_WEEKS`
  pseudo-weeks, with the pooled within-team sd; a simulated season draws each team's true mean
  once (its uncertainty), then every week's score around it;
- **the regular season**: every remaining matchup on the real (synced) schedule; a week in
  progress keeps the points already scored and simulates only the starters whose NFL games
  have not finished (ESPN's projection times the share of the game left);
- **seeding**: by record (a tie half a win), then ESPN's tiebreak (only TOTAL_POINTS_SCORED,
  checked on the real league, is mapped: total points for; then a coin flip);
- **the playoffs**: one round per playoff week, bracket 1v8, 4v5, 2v7, 3v6 (no reseeding), the
  higher seed advancing on a tie;
- **leverage**: the playoff odds if the owner wins vs loses the next week to be decided.

Seeded: the same data, sims and seed print the same odds.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from twm.league.luck import LeagueSeason, luck_table

SIMS = 20_000
SEED = 2026_10_05  # fixed: the same data always prints the same odds
PSEUDO_WEEKS = 6.0  # (a)'s shrinkage: weeks of the league mean added to each team's mean
MIN_PAIRED = 36  # the selection rule: (b) needs >= 3 full weeks scored alongside (a)
SEEDING_RULES = {"TOTAL_POINTS_SCORED": "record, then total points scored"}


class OddsUnavailableError(LookupError):
    """The odds cannot be computed (no schedule, an unmapped rule): one plain line."""


@dataclass(frozen=True)
class ScoreModel:
    """A normal model of each team's weekly score: true mean ~ N(mean, mean_sd), a week's
    score ~ N(true mean, sigma)."""

    name: str
    means: dict[int, float]
    mean_sd: dict[int, float]
    sigma: float
    league_mean: float
    weeks: tuple[int, ...]  # the final weeks it was fitted on

    def predictive(self, team: int) -> tuple[float, float]:
        """(mean, sd) of one week's score."""
        return self.means[team], math.hypot(self.sigma, self.mean_sd[team])


def _scores(ls: LeagueSeason, weeks: list[int]) -> dict[int, list[float]]:
    scores: dict[int, list[float]] = {t: [] for t in ls.team_ids}
    for w in weeks:
        for s in ls.week_sides(w):
            if s.score is not None:
                scores.setdefault(s.team, []).append(float(s.score))
    return scores


def _sigma(scores: dict[int, list[float]]) -> float | None:
    """The pooled within-team sd (df = sum of weeks - 1 per team); the sd of all scores when
    no team has two weeks; None with fewer than two scores."""
    every = [x for xs in scores.values() for x in xs]
    if len(every) < 2:
        return None
    ss = sum(float(np.sum((np.array(xs) - np.mean(xs)) ** 2)) for xs in scores.values() if xs)
    df = sum(len(xs) - 1 for xs in scores.values() if xs)
    return math.sqrt(ss / df) if df > 0 else float(np.std(every, ddof=1))


def fit_shrunk(ls: LeagueSeason, weeks: list[int], k: float = PSEUDO_WEEKS) -> ScoreModel | None:
    """Model (a) on ``weeks`` (final weeks) with ``k`` pseudo-weeks: None without 2 scores."""
    scores = _scores(ls, weeks)
    sigma = _sigma(scores)
    if sigma is None:
        return None
    mu = float(np.mean([x for xs in scores.values() for x in xs]))
    means, msd = {}, {}
    for t, xs in scores.items():
        means[t] = (sum(xs) + k * mu) / (len(xs) + k)
        msd[t] = sigma / math.sqrt(len(xs) + k)
    return ScoreModel("a", means, msd, sigma, mu, tuple(weeks))


@dataclass(frozen=True)
class KEstimate:
    """Method-of-moments check of the pseudo-weeks (reported, never used by the odds)."""

    sigma: float  # pooled within-team weekly sd
    tau2: float  # between-team variance of true means (floored at TAU2_FLOOR)
    k_hat: float  # sigma^2 / tau2
    weeks: int


TAU2_FLOOR = 1.0  # points^2: keeps k_hat finite when the means spread less than chance


def estimate_k(ls: LeagueSeason, weeks: list[int]) -> KEstimate | None:
    """tau^2 = var(team means, ddof 1) - sigma^2 / (mean weeks per team), floored."""
    scores = {t: xs for t, xs in _scores(ls, weeks).items() if xs}
    sigma = _sigma(scores)
    if sigma is None or len(scores) < 3 or not any(len(xs) > 1 for xs in scores.values()):
        return None
    means = [float(np.mean(xs)) for xs in scores.values()]
    n_bar = float(np.mean([len(xs) for xs in scores.values()]))
    tau2 = max(float(np.var(means, ddof=1)) - sigma**2 / n_bar, TAU2_FLOOR)
    return KEstimate(sigma, tau2, sigma**2 / tau2, len(weeks))


@dataclass(frozen=True)
class LiveWeek:
    """The week in progress at the sync: points already scored and what is still to come."""

    week: int
    already: dict[int, float]  # team -> points scored so far (ESPN's live total)
    rem_mean: dict[int, float]  # ESPN's projection of the starters still to play, times the
    # share of their game left
    rem_share: dict[int, float]  # that remainder's share of the starters' whole projection
    note: str = ""


def game_fraction_left(kickoff: datetime | None, end: datetime | None, synced: datetime) -> float:
    """Share of an NFL game still to be played at ``synced`` (naive UTC): 1 before kickoff, 0
    after the estimated end, linear in between."""
    if kickoff is None or end is None or synced <= kickoff:
        return 1.0
    if synced >= end:
        return 0.0
    total = (end - kickoff).total_seconds()
    return max(0.0, min(1.0, (end - synced).total_seconds() / total)) if total > 0 else 0.0


def live_from_rows(
    week: int, starters: list[tuple], games: dict[str, tuple] | None, synced: datetime,
    live_totals: dict[int, float | None],
) -> LiveWeek | None:  # fmt: skip
    """``starters``: (team, NFL team code or None, points, projected, on_bye); ``games``: NFL
    team -> (kickoff, estimated end), None when the warehouse is missing (then a starter with no
    points yet counts as not played). None when no starter's game has started."""
    already: dict[int, float] = {}
    rem: dict[int, float] = {}
    whole: dict[int, float] = {}
    started = False
    for team, pro, pts, proj, bye in starters:
        k_e = None if games is None else games.get(pro or "")
        if bye or (games is not None and k_e is None):
            left = 0.0  # no game this week: nothing to come
        elif games is None:
            left = 1.0 if not pts else 0.0
            started = started or left < 1.0
        else:
            left = game_fraction_left(k_e[0], k_e[1], synced)
            started = started or left < 1.0
        p = float(proj or 0.0)
        rem[team] = rem.get(team, 0.0) + p * left
        whole[team] = whole.get(team, 0.0) + p
        already[team] = already.get(team, 0.0) + float(pts or 0.0)
    if not started:
        return None
    for t, total in live_totals.items():
        if total is not None and t in already:
            already[t] = float(total)  # ESPN's own live total of the side
    share = {t: (rem[t] / whole[t] if whole[t] > 0 else 0.0) for t in rem}
    note = "" if games is not None else ("game times unknown (no warehouse): a starter with no "
                                         "points yet counts as not played")  # fmt: skip
    return LiveWeek(week, already, rem, share, note)


def bracket_order(n: int) -> list[int]:
    """Seeds in bracket order without reseeding: 8 -> [1, 8, 4, 5, 2, 7, 3, 6] (neighbours meet
    in round 1, winners of neighbouring pairs in round 2)."""
    if n < 2 or n & (n - 1):
        raise OddsUnavailableError(f"My League: a playoff of {n} teams is not supported (a "
                                   "power of two without byes only)")  # fmt: skip
    order = [1, 2]
    while len(order) < n:
        m = 2 * len(order) + 1
        order = [x for s in order for x in (s, m - s)]
    return order


@dataclass
class TeamOdds:
    team: int
    playoffs: float  # P(top playoff_teams after the regular season)
    seed1: float
    title: float
    mean_wins: float  # simulated final win points (a tie half)


@dataclass
class Leverage:
    week: int
    p_win: float  # P(the owner wins that week)
    playoffs_if_win: float | None  # None: too few simulated seasons with that result
    playoffs_if_loss: float | None
    title_if_win: float | None
    title_if_loss: float | None


@dataclass
class Odds:
    sims: int
    seed: int
    through: int | None  # the last final week counted (None: none)
    live: LiveWeek | None
    remaining: tuple[int, ...]  # regular-season weeks simulated
    teams: dict[int, TeamOdds]
    leverage: Leverage | None  # the first week whose result is still open for the owner
    model: ScoreModel
    notes: list[str] = field(default_factory=list)
    decided: list[Leverage] = field(default_factory=list)  # owner's weeks already settled


MIN_BRANCH = 200  # simulated seasons a leverage branch needs to be printed


def _check(ls: LeagueSeason, through: int | None) -> tuple[list[int], list[int]]:
    """(final weeks counted, regular-season weeks to simulate) after the setting checks."""
    if ls.seeding_rule not in SEEDING_RULES:
        raise OddsUnavailableError(
            f"My League: ESPN's playoff seeding rule {ls.seeding_rule or '(not synced)'} is not "
            f"mapped (known: {', '.join(SEEDING_RULES)}): sync again or extend twm.league.odds"
        )
    if not 2 <= ls.playoff_teams <= len(ls.team_ids):
        raise OddsUnavailableError(f"My League: {ls.playoff_teams} playoff teams for "
                                   f"{len(ls.team_ids)} teams")  # fmt: skip
    if ls.reseed:
        raise OddsUnavailableError("My League: a reseeded bracket is not supported")
    final = ls.final_weeks(through)
    remaining = [w for w in ls.weeks() if w not in final]
    early = [w for w in remaining if through is not None and w <= through]
    if early:
        raise OddsUnavailableError(f"My League: week {early[0]} is not final yet")
    return final, remaining


def _week_scores(model: ScoreModel, theta: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    return theta + model.sigma * rng.standard_normal(theta.shape)


def _live_scores(
    sc: np.ndarray, live: LiveWeek, model: ScoreModel, idx: dict[int, int],
    z: np.ndarray,
) -> np.ndarray:  # fmt: skip
    """The week in progress: points already scored + a normal remainder (ESPN's projection of
    what is left, sd = sigma x sqrt(share left)), never below 0."""
    out = sc.copy()
    for t, i in idx.items():
        if t in live.already:
            sd = model.sigma * math.sqrt(live.rem_share.get(t, 0.0))
            rest = np.maximum(live.rem_mean.get(t, 0.0) + sd * z[:, i], 0.0)
            out[:, i] = live.already[t] + rest
    return out


def simulate(
    ls: LeagueSeason, model: ScoreModel, *, through: int | None = None,
    live: LiveWeek | None = None, sims: int = SIMS, seed: int = SEED,
) -> Odds:  # fmt: skip
    """Simulate the rest of the regular season and the playoffs ``sims`` times."""
    final, remaining = _check(ls, through)
    teams = ls.team_ids
    idx = {t: i for i, t in enumerate(teams)}
    table = luck_table(ls, through)
    rng = np.random.default_rng(seed)
    mean = np.array([model.means.get(t, model.league_mean) for t in teams])
    msd = np.array([model.mean_sd.get(t, model.sigma) for t in teams])
    theta = mean + msd * rng.standard_normal((sims, len(teams)))
    wp = np.tile(np.array([table[t].win_points for t in teams], dtype=float), (sims, 1))
    pf = np.tile(np.array([table[t].points_for for t in teams], dtype=float), (sims, 1))
    me = idx.get(ls.my_team) if ls.my_team is not None else None
    my_win: dict[int, np.ndarray] = {}  # week -> the owner won it, per simulated season
    for w in remaining:
        sc = _week_scores(model, theta, rng)
        z = rng.standard_normal(sc.shape)  # drawn every week: the stream never depends on live
        if live is not None and w == live.week:
            sc = _live_scores(sc, live, model, idx, z)
        done: set[int] = set()
        for s in ls.week_sides(w):
            if s.team not in idx:
                continue
            pf[:, idx[s.team]] += sc[:, idx[s.team]]
            if s.opponent is None or s.opponent not in idx or s.team in done:
                continue
            done |= {s.team, s.opponent}
            a, b = idx[s.team], idx[s.opponent]
            win_a, tie = sc[:, a] > sc[:, b], sc[:, a] == sc[:, b]
            wp[:, a] += win_a + 0.5 * tie
            wp[:, b] += (~win_a & ~tie) + 0.5 * tie
            if me in (a, b):
                my_win[w] = win_a if me == a else (~win_a & ~tie)
    key = wp * 1e6 + pf + rng.random(wp.shape) * 1e-6  # record, total points, a coin flip
    order = np.argsort(-key, axis=1, kind="stable")
    made = np.zeros(wp.shape, dtype=bool)
    made[np.arange(sims)[:, None], order[:, : ls.playoff_teams]] = True
    champ = _playoffs(ls, model, theta, order, rng)
    T = len(teams)  # noqa: N806
    seed1 = np.bincount(order[:, 0], minlength=T) / sims
    title = np.bincount(champ, minlength=T) / sims
    res = {t: TeamOdds(t, float(made[:, i].mean()), float(seed1[i]), float(title[i]),
                       float(wp[:, i].mean())) for t, i in idx.items()}  # fmt: skip
    lev, decided = None, []
    for w in sorted(my_win) if me is not None else []:
        lev = _leverage(w, my_win[w], made[:, me], champ == me)
        if lev.playoffs_if_win is not None and lev.playoffs_if_loss is not None:
            break
        decided.append(lev)  # (practically) decided already: the next open week instead
        lev = None
    return Odds(sims, seed, final[-1] if final else None, live, tuple(remaining), res, lev,
                model, [f"Seeding: {SEEDING_RULES[ls.seeding_rule]} (ESPN {ls.seeding_rule})"],
                decided)  # fmt: skip


def _playoffs(
    ls: LeagueSeason, model: ScoreModel, theta: np.ndarray, order: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:  # fmt: skip
    """The champion (team index) of every simulated season: one round per playoff_length
    weeks (their scores summed), the higher seed advancing on a tie."""
    bracket = np.array(bracket_order(ls.playoff_teams))
    alive = order[:, bracket - 1]
    seeds = np.tile(bracket, (order.shape[0], 1))
    while alive.shape[1] > 1:
        sc = sum(_week_scores(model, theta, rng) for _ in range(ls.playoff_length))
        a, b = alive[:, 0::2], alive[:, 1::2]
        sa, sb = np.take_along_axis(sc, a, axis=1), np.take_along_axis(sc, b, axis=1)
        da, db = seeds[:, 0::2], seeds[:, 1::2]
        a_on = (sa > sb) | ((sa == sb) & (da < db))
        alive, seeds = np.where(a_on, a, b), np.where(a_on, da, db)
    return alive[:, 0]


def _leverage(week: int, won: np.ndarray, made: np.ndarray, champ: np.ndarray) -> Leverage:
    def share(mask: np.ndarray, x: np.ndarray) -> float | None:
        return float(x[mask].mean()) if int(mask.sum()) >= MIN_BRANCH else None

    lost = ~won
    return Leverage(week, float(won.mean()), share(won, made), share(lost, made),
                    share(won, champ), share(lost, champ))  # fmt: skip


Predictor = Callable[[int], dict[int, tuple[float, float]] | None]  # week -> team (mean, sd)


@dataclass
class ModelScore:
    name: str
    n: int  # team-weeks scored
    nll: float | None  # mean Gaussian negative log-likelihood (lower is better)
    mae: float | None
    rmse: float | None


@dataclass
class ModelCheck:
    a: ModelScore
    b: ModelScore
    paired: int  # team-weeks both models predicted
    diff: float | None  # mean NLL(b) - NLL(a) on them
    se: float | None  # its standard error
    pick: str  # the selection rule's pick: 'a' or 'b'


def predict_a(ls: LeagueSeason, k: float = PSEUDO_WEEKS) -> Predictor:
    """Model (a) for week w from the final weeks before w."""

    def f(w: int) -> dict[int, tuple[float, float]] | None:
        m = fit_shrunk(ls, [x for x in ls.final_weeks() if x < w], k)
        return None if m is None else {t: m.predictive(t) for t in ls.team_ids}

    return f


def _score(name: str, rows: dict[tuple[int, int], tuple[float, float]]) -> ModelScore:
    if not rows:
        return ModelScore(name, 0, None, None, None)
    nll = [v[0] for v in rows.values()]
    err = np.array([v[1] for v in rows.values()])
    return ModelScore(name, len(rows), float(np.mean(nll)), float(np.mean(np.abs(err))),
                      float(np.sqrt(np.mean(err**2))))  # fmt: skip


def check_models(ls: LeagueSeason, b: Predictor | None, through: int | None = None) -> ModelCheck:
    """Score (a) and (b) on every final week (to ``through``), each week predicted from data
    before it, and apply the selection rule fixed before any scoring: (b) only when both
    scored the same >= MIN_PAIRED team-weeks and (b)'s mean NLL is lower by more than one
    standard error of the paired difference; otherwise (a), the simpler model."""
    rows: dict[str, dict[tuple[int, int], tuple[float, float]]] = {"a": {}, "b": {}}
    for w in ls.final_weeks(through):
        actual = {s.team: s.score for s in ls.week_sides(w)}
        for name, pred in (("a", predict_a(ls)), ("b", b)):
            got = pred(w) if pred is not None else None
            for t, (mu, sd) in (got or {}).items():
                x = actual.get(t)
                if x is None or sd <= 0:
                    continue
                nll = 0.5 * math.log(2 * math.pi * sd * sd) + (x - mu) ** 2 / (2 * sd * sd)
                rows[name][(w, t)] = (nll, x - mu)
    paired = sorted(set(rows["a"]) & set(rows["b"]))
    d = np.array([rows["b"][k][0] - rows["a"][k][0] for k in paired])
    diff = float(d.mean()) if len(d) else None
    se = float(d.std(ddof=1) / math.sqrt(len(d))) if len(d) > 1 else None
    win = len(d) >= MIN_PAIRED and diff is not None and se is not None and diff < -se
    return ModelCheck(_score("a", rows["a"]), _score("b", rows["b"]), len(paired), diff, se,
                      "b" if win else "a")  # fmt: skip
