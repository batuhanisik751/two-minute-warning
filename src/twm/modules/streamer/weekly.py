"""The streamer's weekly list (S2a): check the week's data, rank the K and D/ST pools with the
owner-approved methods, store the list and write ``reports/streamer/weekly/<season>-W<nn>.md``.

It mirrors the Radar's weekly run (:mod:`twm.modules.waiver_radar.weekly`) and reuses its
pieces: the same freshness rules (plus the streamer's own two tables), the same clock rule for
'live' vs reconstructed ('backtest') lists, the same store (a live week is never overwritten).

1. **Freshness**: the Radar's checks (:func:`twm.modules.waiver_radar.weekly.check_freshness`)
   and, for every played game, rows in ``fact_kicker_week`` and ``fact_defense_week``. Anything
   missing: refuse (exit code 3) unless ``--allow-incomplete``.
2. **Ranking** (:func:`score_week`, point in time: everything through one
   :class:`~twm.asof.AsOfView`): the S1b pool (``in_pool``), the S1c features; K by the approved
   logistic regression (calibrated probability, then the tie-breakers of
   :func:`twm.modules.streamer.models.rank_scores`); D/ST by the approved next-opponent rule
   (no probability). The chance, its range and the priority come from the frozen backtest of
   the seasons before the list's season (:mod:`twm.modules.streamer.confidence`); the reasons
   from :mod:`twm.modules.streamer.reasons`.
3. **Storing**: every pool row with ``entity_type`` 'kicker' / 'team_defense', the K model's or
   the rule's version, band, tier, reasons, kind, ``incomplete``. No outcomes (the store's
   outcomes table holds the Radar's labels; S2b publishes the streamer's from the labels).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from twm import predictions as pr
from twm.asof import AsOfView
from twm.modules.streamer import confidence as sc
from twm.modules.streamer import models as sm
from twm.modules.streamer import reasons as sr
from twm.modules.streamer.features import features_for
from twm.modules.streamer.pool import candidate_pool
from twm.modules.waiver_radar import confidence as cf
from twm.modules.waiver_radar import weekly as rw

REPORT_DIR = Path("reports/streamer/weekly")
EXIT_NOT_READY = rw.EXIT_NOT_READY
ENTITY_TYPES = {"K": "kicker", "DST": "team_defense"}
POSITIONS = sm.POSITIONS
SCORED_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32(), "week": pl.Int32(), "as_of": pl.Datetime("us", "UTC"),
    "position": pl.String(), "entity_id": pl.String(), "name": pl.String(), "team": pl.String(),
    "score": pl.Float64(), "raw_score": pl.Float64(), "rank": pl.Int32(),
    "chance": pl.Float64(), "band_lo": pl.Float64(), "band_hi": pl.Float64(),
    "band_n": pl.Int64(), "band_hits": pl.Int64(), "band_from": pl.Float64(),
    "band_to": pl.Float64(), "tier": pl.String(), "reasons_json": pl.String(),
    "model_version": pl.String(), "rule_value": pl.Float64(),
}  # fmt: skip


def freshness_inputs(db: Path | str, season: int, week: int) -> dict[str, pl.DataFrame]:
    """The Radar's inputs plus per-game rows of the streamer's two tables."""
    out = rw.freshness_inputs(db, season, week)
    ids = out["games"].get_column("game_id").to_list() or [""]
    con = rw._connect(db)
    try:
        for key, table in (("kickers", "fact_kicker_week"), ("defenses", "fact_defense_week")):
            out[key] = con.execute(
                f"SELECT game_id, count(*) AS n FROM {table} WHERE game_id IN "
                f"({', '.join('?' for _ in ids)}) GROUP BY game_id",
                ids,
            ).pl()
    finally:
        con.close()
    return out


def check_freshness(
    inputs: Mapping[str, pl.DataFrame], season: int, week: int, as_of: datetime, now: datetime
) -> rw.Freshness:
    """The Radar's checks, then: every played game has kicker rows and D/ST rows."""
    fr = rw.check_freshness(inputs, season, week, as_of, now)
    games = inputs["games"]
    if games.height == 0:
        return fr
    played = games.filter(pl.col("has_result"))
    for key, what in (("kickers", "kicker rows (fact_kicker_week)"),
                      ("defenses", "D/ST rows (fact_defense_week)")):  # fmt: skip
        have = set(inputs[key].filter(pl.col("n") > 0).get_column("game_id").to_list())
        missing = played.filter(~pl.col("game_id").is_in(list(have)))
        if missing.height:
            fr.problems.append(
                f"{missing.height} played game(s) have no {what} yet: {rw._matchups(missing)}"
            )
    return fr


# --------------------------------------------------------------------------------------
# Ranking (the point-in-time function)
# --------------------------------------------------------------------------------------


def empty_scored() -> pl.DataFrame:
    return pl.DataFrame(schema=SCORED_SCHEMA)


def _finish(ranked: pl.DataFrame, band: pl.DataFrame, reasons: list, version: str) -> pl.DataFrame:
    chances, ranks = band.get_column("chance").to_list(), ranked.get_column("rank").to_list()
    tier = [cf.tier_of(c) if r <= sc.TOP_N else None for c, r in zip(chances, ranks, strict=True)]
    return ranked.hstack(band).with_columns(
        pl.Series("tier", tier, dtype=pl.String),
        pl.Series(
            "reasons_json",
            [json.dumps(r, sort_keys=True, separators=(",", ":")) for r in reasons],
            dtype=pl.String,
        ),  # fmt: skip
        pl.lit(version).alias("model_version"),
        pl.col("rank").cast(pl.Int32),
    )


def score_week(
    view: AsOfView,
    season: int,
    week: int,
    *,
    k_model: Any,
    rule: Any,
    k_conf: Any,
    d_conf: Any,
    rules: Any = None,
) -> pl.DataFrame:
    """The ranked K and D/ST pools at the view's as-of (``SCORED_SCHEMA``, K first, by rank).
    Reads nothing but the view (plus the fixed model, rule and backtest bins)."""
    pool = candidate_pool(view, season, week, rules=rules).filter(pl.col("in_pool"))
    if pool.height == 0:
        return empty_scored()
    feats = features_for(view, season, week, pool, rules=rules)
    keys = ["season", "week", "position", "entity_id"]
    info = pool.select(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32), "as_of",
                       "position", "entity_id", "name", "team")  # fmt: skip
    data = info.join(feats, on=keys, how="left", maintain_order="left")
    parts = []
    k = data.filter(pl.col("position") == "K")
    if k.height:
        raw, prob = k_model.predict(k)
        k = sm.rank_scores(k.with_columns(pl.Series("raw_score", raw, dtype=pl.Float64),
                                          pl.Series("score", prob, dtype=pl.Float64)))  # fmt: skip
        band = k_conf.band(k.get_column("score").to_numpy())
        parts.append(_finish(k, band, sr.k_reasons(k_model, k), k_model.model_version)
                     .with_columns(pl.lit(None, dtype=pl.Float64).alias("rule_value")))  # fmt: skip
    d = data.filter(pl.col("position") == "DST")
    if d.height:
        v = pl.col(rule.column).cast(pl.Float64)
        v = v if rule.higher_is_better else -v
        d = sm.rank_scores(d.with_columns(v.alias("raw_score"), v.alias("score")))
        band = d_conf.band(-d.get_column("rank").cast(pl.Float64).to_numpy())
        parts.append(_finish(d, band, sr.dst_reasons(d), rule.model_version).with_columns(
            pl.lit(None, dtype=pl.Float64).alias("score"),  # a rule has no probability
            pl.col(rule.column).cast(pl.Float64).alias("rule_value"),
        ))  # fmt: skip
    if not parts:
        return empty_scored()
    out = pl.concat([p.select(list(SCORED_SCHEMA)) for p in parts], how="vertical")
    return (
        out.cast(SCORED_SCHEMA)
        .with_columns(  # type: ignore[arg-type]
            pl.col("position").replace_strict({"K": 0, "DST": 1}, default=9).alias("_p")
        )
        .sort("_p", "rank")
        .drop("_p")
    )


# --------------------------------------------------------------------------------------
# A run: check, rank, store
# --------------------------------------------------------------------------------------


@dataclass
class WeeklyRun:
    season: int
    week: int
    as_of: datetime
    now: datetime
    kind: str  # 'live' or 'backtest' (reconstructed)
    freshness: rw.Freshness
    k_model: Any  # the Radar's ProductionModel (logit, y_start)
    rule: Any  # production.ProductionRule
    k_conf: Any
    d_conf: Any
    scored: pl.DataFrame
    next_first_kickoff: datetime | None

    @property
    def incomplete(self) -> bool:
        return not self.freshness.ok


def run_week(
    db: Path | str,
    season: int,
    week: int,
    *,
    k_model: Any,
    rule: Any,
    k_conf: Any,
    d_conf: Any,
    now: datetime,
    allow_incomplete: bool = False,
    real_clock: bool = True,
    rules: Any = None,
) -> WeeklyRun:
    """Check the week's data and rank it (nothing is stored here); :class:`rw.NotReadyError`
    when data is missing and ``allow_incomplete`` is False. Unless ``now`` is the real clock
    (``real_clock``), the run is never 'live'. ``rules``: the league (default config)."""
    from twm.asof import weekly_as_of

    last = rw.last_reg_week(db, season)
    if last is not None and week >= last:
        raise ValueError(
            f"week {week} is the last regular-season week of {season}: no game follows it, so "
            "there is nothing to stream"
        )
    if int(season) != int(k_model.season) or int(season) != int(rule.season):
        raise ValueError(f"the approved K model and D/ST rule score {k_model.season}, not {season}")
    as_of = weekly_as_of(db, season, week)
    fresh = check_freshness(freshness_inputs(db, season, week), season, week, as_of, now)
    if not fresh.ok and not allow_incomplete:
        raise rw.NotReadyError(fresh)
    kickoff = rw.next_kickoff(db, season, week)
    kind = rw.run_kind(as_of, kickoff, now) if real_clock else "backtest"
    with AsOfView(db, as_of) as view:
        scored = score_week(view, season, week, k_model=k_model, rule=rule, k_conf=k_conf,
                            d_conf=d_conf, rules=rules)  # fmt: skip
    return WeeklyRun(season, week, as_of, now, kind, fresh, k_model, rule, k_conf, d_conf, scored,
                     kickoff)  # fmt: skip


def store_frames(run: WeeklyRun, *, created_at: datetime | None = None):
    """(predictions, model_versions) for :func:`twm.predictions.write_predictions`
    (``replace='weeks'``); no outcomes."""
    from twm.modules.streamer.production import version_frame

    created = created_at if created_at is not None else pr.now_utc()
    s = run.scored
    bands = [cf.band_json(r) if r["position"] == "K" else sc.dst_band_json(r)
             for r in s.iter_rows(named=True)]  # fmt: skip
    preds = s.select(
        pl.lit(sm.MODULE).alias("module"),
        pl.col("position").replace_strict(ENTITY_TYPES).alias("entity_type"),
        "entity_id", "season", "week", "as_of",
        pl.lit(1, dtype=pl.Int32).alias("horizon"),
        pl.col("position").alias("rank_group"),
        "score", "raw_score", "rank",
        pl.Series("band", bands, dtype=pl.String),
        "model_version", "reasons_json",
        pl.lit(run.kind).alias("kind"),
        pl.lit(created, dtype=pl.Datetime("us")).alias("created_at"),
        "tier",
        pl.lit(run.incomplete).alias("incomplete"),
    )  # fmt: skip
    versions = pl.concat([version_frame(run.k_model), version_frame(run.rule)], how="vertical")
    return preds, versions


def store_week(run: WeeklyRun, store: Path | str, *, created_at: datetime | None = None) -> dict:
    preds, versions = store_frames(run, created_at=created_at)
    return pr.write_predictions(store, predictions=preds, versions=versions, replace="weeks")


# --------------------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------------------


def report_path(season: int, week: int, root: Path | None = None) -> Path:
    from twm.config import ROOT

    return (root if root is not None else ROOT) / REPORT_DIR / f"{season}-W{week:02d}.md"


def _kind_text(run: WeeklyRun) -> str:
    from twm.asof import to_utc

    nxt = run.next_first_kickoff
    after = f"week {run.week + 1}'s first game" + (
        f" ({to_utc(nxt):%a %Y-%m-%d %H:%M} UTC)" if nxt else ""
    )
    if run.kind == "live":
        return f"**Live list.** Made in real time, after the Tuesday as-of and before {after}."
    if to_utc(run.now) < to_utc(run.as_of):
        return "**Early list (stored as 'backtest').** Made before the Tuesday as-of."
    if nxt is not None and to_utc(run.now) < to_utc(nxt):
        return (
            "**Reproduced list (stored as 'backtest').** Run with a set clock inside the live "
            "window; only a run on the real clock is stored as 'live'."
        )
    return (
        f"**Reconstructed list (stored as 'backtest').** Made after {after}, from the data as "
        "it stood at the as-of: what the streamer would have said that Tuesday."
    )


def _why(text: str | None) -> str:
    return "; ".join(r["text"] for r in json.loads(text or "[]")) or "-"


def _chance(r: Mapping[str, Any]) -> str:
    return sc.band_text(r["chance"], r["band_lo"], r["band_hi"], r["position"])


def _tier_lines(conf: Any, position: str) -> list[str]:
    first, last = conf.seasons
    head = ["priority", "chance", "picks", "started", "start rate", "per list"]
    rows = [[f"**{r['tier']}**", {"must-add": "50% or more", "speculative": "25% to 50%",
                                  "watch": "below 25%"}[r["tier"]],
             f"{r['rows']:,}", f"{r['hits']:,}", rw._pct(r["rate"], 1),
             "-" if r["per_list"] is None else f"{r['per_list']:.1f}"]
            for r in conf.tiers.iter_rows(named=True)]  # fmt: skip
    return [f"How the priorities did on the {position} lists of the {first}-{last} backtest "
            "(every pick of every weekly list):", "", *rw._table(head, rows), ""]  # fmt: skip


def method_notes(csv_path: Path, season: int) -> dict[str, str]:
    """Per position, the plain words on why this method ranks the list, with the numbers read
    from reports/streamer/backtest.csv (pooled precision@5); empty when the file is missing or
    covers ``season`` or later (a reconstructed list never quotes outcomes after its as-of)."""
    if not csv_path.exists():
        return {}
    ev = pl.read_csv(csv_path, infer_schema_length=0).filter(
        (pl.col("train_on") == "pool") & (pl.col("metric") == "p_at_5")
    )

    def get(pos: str, method: str, scope: str = "pooled", key: str = "") -> dict | None:
        hit = ev.filter(
            (pl.col("position") == pos) & (pl.col("method") == method)
            & (pl.col("scope") == scope) & (pl.col("key").fill_null("") == key)
        )  # fmt: skip
        return hit.row(0, named=True) if hit.height else None

    k, kb = get("K", "logit"), get("K", "logit", "diff", "baseline_last_points")
    d, dm = get("DST", "baseline_opponent"), get("DST", "logit")
    if not (k and kb and d and dm) or int(str(k["seasons"]).split("-")[-1]) >= int(season):
        return {}
    span = k["seasons"]
    f = float
    return {
        "K": (f"In the {span} backtest the model's top 5 kickers started {f(k['value']):.1%} of "
              f"the time, {100 * f(kb['value']):+.1f} points more than a list of last week's top "
              f"scorers (95% interval {100 * f(kb['lo']):+.1f} to {100 * f(kb['hi']):+.1f}): "
              "ahead, but not clearly."),
        "DST": (f"The D/ST list uses a simple rule, not a model: in the {span} backtest the "
                f"rule's top 5 started {f(d['value']):.1%} of the time and our model's "
                f"{f(dm['value']):.1%}, so the rule is what we use."),
    }  # fmt: skip


def _position_lines(run: WeeklyRun, position: str) -> list[str]:
    s = run.scored.filter(pl.col("position") == position)
    if position == "K":
        head = ["rank", "kicker", "team", "chance (range)", "model probability", "priority", "why"]
        rows = [
            [
                r["rank"],
                r["name"],
                r["team"],
                _chance(r),
                rw._pct(r["score"], 1),
                r["tier"] or "-",
                _why(r["reasons_json"]),
            ]
            for r in s.iter_rows(named=True)
        ]
        title = "## Kickers"
    else:
        head = ["rank", "D/ST", "next opponent's points per game", "chance (range)", "priority",
                "why"]  # fmt: skip
        rows = [[r["rank"], r["name"], "-" if r["rule_value"] is None else f"{r['rule_value']:.1f}",
                 _chance(r), r["tier"] or "-", _why(r["reasons_json"])]
                for r in s.iter_rows(named=True)]  # fmt: skip
        title = "## Team defenses (D/ST)"
    if not rows:
        return [title, "", f"No {position} in the pool at this as-of.", ""]
    return [title, "", *rw._table(head, rows), ""]


def build_report(
    run: WeeklyRun, *, generated: str, command: str, notes: Mapping[str, str] | None = None
) -> str:
    """The weekly markdown (deterministic apart from ``generated``)."""
    from twm.asof import to_utc
    from twm.backtest.walkforward import season_span
    from twm.modules.streamer.pool import StreamerRules

    notes = notes or {}
    m, rule = run.k_model, run.rule
    starts = StreamerRules.from_config().start_thresholds
    (k0, k1), (d0, d1) = run.k_conf.seasons, run.d_conf.seasons
    lines = [
        f"# K and D/ST streamer: {run.season} week {run.week}", "",
        f"{generated} Command: `{command}`.", "",
        f"- {_kind_text(run)}",
        f"- **As-of** {to_utc(run.as_of):%a %Y-%m-%d %H:%M} UTC: only data public then is used; "
        f"the picks are for week {run.week + 1}.",
        f"- **Pool**: kickers and D/STs probably on waivers in a {starts['K']}-team league "
        "(reports/streamer/pool_labels.md). A pick **started** when it finished in the top "
        f"{starts['K']} of its position the next week.",
    ]  # fmt: skip
    if run.incomplete:
        lines.append("- **WARNING: incomplete data** (scored with --allow-incomplete): "
                     + "; ".join(run.freshness.problems))  # fmt: skip
    lines += [
        "", "## How these lists are made", "",
        f"- **Kickers**: a logistic regression ({m.model_version}, trained on "
        f"{season_span(m.training_seasons)}, tuned and calibrated on {m.fold.val_season}). Picks "
        "with the same model score are ordered by last game's points, then points per game, "
        f"then preseason rank. **Chance** = how often kickers the model scored alike in the "
        f"{k0}-{k1} backtest started, with its 90% range. {notes.get('K', '')}".rstrip(),
        f"- **D/ST: a simple rule, not a model.** Ranked by next week's opponent's points per "
        "game (fewer is better; ties by last game's points, then points per game, then "
        "preseason rank). "
        f"{notes.get('DST', '')} **Chance** = how often the rule's pick at that rank started in "
        f"the {d0}-{d1} backtest, with its 90% range; there is no model probability "
        f"({rule.model_version}).",
        "- **Priority** from the chance: must-add 50% or more, speculative 25% to 50%, watch "
        "below 25%.", "",
    ]  # fmt: skip
    for pos, conf in (("K", run.k_conf), ("DST", run.d_conf)):
        lines += _position_lines(run, pos) + _tier_lines(conf, pos)
    return "\n".join(lines).rstrip() + "\n"


def write_report(text: str, path: Path) -> Path:
    return rw.write_report(text, path)
