"""Each module's stored Time Machine rows vs a fresh recomputation (step I3a). Every function
takes a :class:`Context` and returns a :class:`ModuleResult`; nothing here writes the owner's
files (recomputed grades and the xFP folds' copy go under ``Context.work``; the predictions
store is opened read-only)."""

from __future__ import annotations

import json
import shutil
import warnings
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from twm.timemachine.compare import Comparison, compare_lists, sample_seasons


@dataclass
class Context:
    db: Path  # the warehouse (read-only)
    work: Path  # scratch directory for recomputed files
    season: int  # the pinned (current) season
    n: int = 4  # seasons per module (the first and last always among them)
    seed: int = 20261002
    store: Path | None = None  # the predictions store, opened read-only (Radar's extra check)
    progress: Callable[[str], None] = lambda _m: None

    def sample(self, seasons: Iterable[int]) -> list[int]:
        return sample_seasons(seasons, self.n, self.seed)


@dataclass
class ModuleResult:
    module: str
    stored: str  # what the stored rows are
    method: str  # how they were recomputed
    seasons: list[int] = field(default_factory=list)
    comparison: Comparison = field(default_factory=Comparison)
    notes: list[str] = field(default_factory=list)
    error: str | None = None  # why it could not be recomputed

    @property
    def status(self) -> str:
        if self.error:
            return "NOT REPRODUCED"
        if self.comparison.mismatches:
            return "MISMATCH"
        return "REPRODUCED" if self.comparison.rows else "NOTHING COMPARED"


def _first_training(versions: pl.DataFrame) -> int:
    """The first training season of the stored folds (the dataset's first season then)."""
    seasons = [s for t in versions.get_column("training_seasons").to_list() for s in json.loads(t)]
    return min(int(s) for s in seasons)


def _in(df: pl.DataFrame, seasons: list[int]) -> pl.DataFrame:
    return df.filter(pl.col("season").is_in(seasons))


LIST_ARGS = dict(lists=["season", "week", "rank_group"], entity=["entity_id"], rank="rank",
                 numbers=["score", "raw_score"], exact=["model_version"])  # fmt: skip


def _store_rows(store: Path, module: str, versions: list[str], seasons: list[int]) -> pl.DataFrame:
    """The predictions store's rows of ``versions`` (opened read-only, never written)."""
    import duckdb

    con = duckdb.connect(str(store), read_only=True)
    try:
        return con.execute(
            f"""SELECT * FROM predictions WHERE module = ? AND kind = 'backtest'
                AND model_version IN ({", ".join("?" for _ in versions)})
                AND season IN ({", ".join("?" for _ in seasons)})""",
            [module, *versions, *seasons],
        ).pl()
    finally:
        con.close()


def waiver_radar(ctx: Context) -> ModuleResult:
    """The Radar's backtest lists (the pin's snapshot, sha256-checked; also the predictions
    store's rows of the pinned versions) vs each sampled season's walk-forward fold refit on a
    dataset rebuilt from the warehouse (``twm radar backtest``'s own code)."""
    from twm import pins
    from twm.modules.waiver_radar import backtest as wb
    from twm.modules.waiver_radar.dataset import build_dataset
    from twm.modules.waiver_radar.production import LABEL, MODEL

    res = ModuleResult("waiver_radar", "pin snapshot predictions.parquet (sha256-checked)",
                       "fold refit per season on a dataset rebuilt from the warehouse")  # fmt: skip
    frames = pins.load_backtest(pins.get_pin("waiver_radar"))
    stored, vers = frames["predictions"], frames["model_versions"]
    res.seasons = ctx.sample(stored.get_column("season").unique().to_list())
    first, last = _first_training(vers), max(res.seasons)
    ctx.progress(f"waiver_radar: dataset {first}-{last} from the warehouse, folds {res.seasons}")
    df = build_dataset(ctx.db, list(range(first, last + 1)))
    run = wb.run_backtest(df, models=(MODEL,), labels=(LABEL,), test_seasons=res.seasons)
    new = run.runs[(LABEL, MODEL)].predictions.rename(
        {"position": "rank_group", "gsis_id": "entity_id"}
    )
    old = _in(stored, res.seasons)
    res.comparison = compare_lists(old, new, **LIST_ARGS)
    reasons = old.get_column("reasons_json").unique().to_list()
    res.notes.append(f"stored backtest rows carry no band/reasons (reasons_json {reasons})")
    if ctx.store is not None and ctx.store.exists():
        ids = old.get_column("model_version").unique().to_list()
        rows = _store_rows(ctx.store, "waiver_radar", ids, res.seasons)
        extra = compare_lists(old, rows, **LIST_ARGS)
        res.notes.append(f"predictions store (read-only) vs pin: {extra.rows:,} rows, "
                         f"{extra.mismatches} mismatches")  # fmt: skip
        res.comparison.add(Comparison(max_diff=extra.max_diff, mismatches=extra.mismatches,
                                      examples=extra.examples))  # fmt: skip
    return res


def _streamer_dataset(ctx: Context, first: int, last: int) -> pl.DataFrame:
    from twm.modules.streamer.dataset import build_dataset

    ctx.progress(f"streamer: dataset {first}-{last} from the warehouse")
    return build_dataset(ctx.db, list(range(first, last + 1)))


def streamer_k(ctx: Context) -> ModuleResult:
    """The K model's backtest lists (pin snapshot) vs each sampled season's fold refit on a
    streamer dataset rebuilt from the warehouse (``twm streamer backtest``'s own code)."""
    from twm.modules.streamer import backtest as sb
    from twm.modules.streamer import production as sp

    res = ModuleResult("streamer_k", "pin snapshot predictions.parquet (sha256-checked)",
                       "fold refit per season on a dataset rebuilt from the warehouse")  # fmt: skip
    _, kpin = sp.load_pinned_k(ctx.season)
    frames = sp.load_k_snapshot(kpin)
    stored = frames["predictions"]
    res.seasons = ctx.sample(stored.get_column("season").unique().to_list())
    df = _streamer_dataset(ctx, _first_training(frames["model_versions"]), max(res.seasons))
    run = sb.run_backtest(df, methods=(sp.MODEL_K,), positions=("K",), test_seasons=res.seasons)
    new = run.runs[(sp.MODEL_K, "K")].predictions.rename({"position": "rank_group"})
    res.comparison = compare_lists(_in(stored, res.seasons), new, **LIST_ARGS)
    res.notes.append("stored backtest rows carry no band/reasons; the site's chance is computed "
                     "at publish from earlier seasons' stored rows")  # fmt: skip
    return res


def streamer_dst(ctx: Context) -> ModuleResult:
    """The D/ST rule (nothing fitted): the pin keeps only its hit-rate table and the publish
    re-applies the rule to the cached streamer dataset (data/streamer/dataset.parquet) for the
    lists. Both are compared with the rule applied to a dataset rebuilt from the warehouse."""
    from twm.config import ROOT
    from twm.modules.streamer import backtest as sb
    from twm.modules.streamer import models as sm
    from twm.modules.streamer import production as sp

    res = ModuleResult("streamer_dst", "pin hit_rates.parquet + the lists the publish re-derives",
                       "the rule re-applied to a dataset rebuilt from the warehouse")  # fmt: skip
    _, dpin = sp.load_pinned_rule(ctx.season)
    rates = sp.load_hit_rates(dpin)
    res.seasons = ctx.sample(rates.get_column("season").unique().to_list())
    df = _streamer_dataset(ctx, min(sb.DEFAULT_TESTS) - 1, max(res.seasons))  # from 2012

    def lists(data: pl.DataFrame) -> pl.DataFrame:
        graded = sm.graded_rows(data).filter(pl.col("season").is_in(res.seasons))
        run = sb.run_baseline(graded, sp.RULE_METHOD, "DST")
        return run.predictions.with_columns(pl.col("position").alias("rank_group"))

    new = lists(df)
    got = sp.hit_rate_table(sp.with_labels(new, df))
    res.comparison = compare_lists(_in(rates, res.seasons), got, lists=["season", "list_length"],
                                   entity=["rank"], exact=["lists", "starts"])  # fmt: skip
    cached = ROOT / "data/streamer/dataset.parquet"
    if cached.exists():
        served = lists(pl.read_parquet(cached))
        extra = compare_lists(served, new, **LIST_ARGS)
        res.comparison.add(extra)
        res.notes.append(f"served D/ST lists (rule on the cached dataset) vs rebuilt: "
                         f"{extra.rows:,} rows, {extra.mismatches} mismatches")  # fmt: skip
    else:
        res.notes.append(f"{cached} absent: only the hit-rate table compared")
    return res


def _xfp_copy(ctx: Context) -> Path:
    """A scratch copy of the own xFP folds (data/regression_watch/own_xfp/): the history
    reader rewrites its inputs and refits folds not current, never in the owner's copy."""
    from twm.config import ROOT
    from twm.modules.regression_watch import own_xfp as ox

    src, dst = ROOT / ox.OUT_DIR, ctx.work / "own_xfp"
    if dst.exists():
        shutil.rmtree(dst)
    if src.exists():
        shutil.copytree(src, dst)
    return dst


def regression_watch(ctx: Context) -> ModuleResult:
    """The frozen backtest lists (pin snapshot) vs the snapshot rebuilt from the warehouse by
    the code that froze it (:func:`twm.modules.regression_watch.frozen.build_snapshot`: each
    season's variant chosen walk-forward, own xFP from each season's fold)."""
    from twm.config import league
    from twm.modules.regression_watch import frozen as fz
    from twm.modules.regression_watch import production as rprod

    res = ModuleResult("regression_watch", "pin snapshot predictions + model_versions + "
                       "player_xfp (sha256)",
                       "frozen.build_snapshot from the warehouse (xFP folds: a copy)")  # fmt: skip
    params, pin = rprod.load_pinned_params(ctx.season)
    stored = fz.load_snapshot(pin)
    res.seasons = ctx.sample(stored["predictions"].get_column("season").unique().to_list())
    xdir = _xfp_copy(ctx)
    ctx.progress(f"regression_watch: rebuilding 2006-{ctx.season - 1} (own xFP folds in {xdir})")
    new = fz.build_snapshot(ctx.db, ctx.season, league(), xfp_source=params.xfp_source,
                            progress=ctx.progress, xfp_dir=xdir)  # fmt: skip
    res.comparison = compare_lists(
        _in(stored["predictions"], res.seasons), _in(new["predictions"], res.seasons),
        lists=["season", "week", "rank_group"], entity=["entity_id"], rank="rank",
        numbers=["score"], exact=["band", "model_version", "reasons_json", "horizon", "team",
                                  "as_of"])  # fmt: skip
    pick = pl.col("test_season").is_in(res.seasons)
    res.comparison.add(compare_lists(
        stored["model_versions"].filter(pick), new["model_versions"].filter(pick),
        lists=["test_season"], entity=["model_version"],
        exact=["params", "notes", "training_seasons"]))  # fmt: skip
    res.notes.append("own xFP per play from the stored walk-forward folds when current by "
                     "content hash (else refit into the scratch copy)")  # fmt: skip
    # step PXFP: the player pages' own-xFP history frozen in the same pin, every season of it
    stored_px = fz.load_player_xfp(pin)
    new_px = fz.build_player_xfp(ctx.db, ctx.season, xfp_dir=xdir, progress=ctx.progress)
    res.comparison.add(compare_lists(stored_px, new_px, lists=["season"],
                                     entity=["week", "game_id", "gsis_id"],
                                     numbers=list(fz.PLAYER_XFP_COLUMNS[4:])))  # fmt: skip
    px, n = stored_px.get_column("season"), stored_px.height
    res.notes.append(f"player pages' own xFP (pin {fz.PLAYER_XFP}): every season "
                     f"{px.min()}-{px.max()}, {n:,} player-games, from the same folds")  # fmt: skip
    return res


DECISION_KEYS = {"fourth_downs": ["game_id", "play_id"], "two_point": ["game_id", "play_id"],
                 "clock_cases": ["metric", "game_id", "team", "play_id"],
                 "team_games": ["game_id", "team"], "season_inputs": []}  # fmt: skip


def compare_table(stored: pl.DataFrame, new: pl.DataFrame, entity: list[str]) -> Comparison:
    """A table without a rank, by season: every float column within the tolerance, every other
    column equal, the same rows in the same order."""
    floats = [c for c, t in stored.schema.items() if t.is_float() and c not in entity]
    exact = [c for c in stored.columns if c not in floats and c not in (*entity, "season")]
    return compare_lists(stored, new.select(stored.columns), lists=["season"], entity=entity,
                         numbers=floats, exact=exact)  # fmt: skip


def decisions(ctx: Context) -> ModuleResult:
    """The Decision Report Card's frozen history (pin) vs each sampled season regraded from the
    warehouse (G3 ``grade_season`` + G4 ``clock_season``, the stored fold models of that season
    from models/decisions/, written under the scratch directory) and turned into the site's
    rows by the code that froze them (:func:`twm.modules.decisions.frozen.season_tables`)."""
    from twm import pins
    from twm.modules.decisions import clock as ck
    from twm.modules.decisions import frozen as fz
    from twm.modules.decisions import grade as gr

    res = ModuleResult("decisions", "pin history snapshot (5 tables, sha256-checked)",
                       "regraded from the warehouse with the stored fold models")  # fmt: skip
    stored = fz.load_snapshot(pins.get_pin("decisions"))
    res.seasons = ctx.sample(stored["season_inputs"].get_column("season").to_list())
    gdir, cdir = ctx.work / "decisions" / "graded", ctx.work / "decisions" / "clock"
    for s in res.seasons:
        try:
            gr.grade_season(ctx.db, s, out_dir=gdir, progress=ctx.progress)
            ck.clock_season(ctx.db, s, out_dir=cdir, progress=ctx.progress)
        except (gr.GradeError, ValueError, OSError) as e:  # e.g. a fold model not on disk
            res.error = f"season {s} cannot be regraded: {e}"
            return res
        new = fz.season_tables(s, graded_dir=gdir, clock_dir=cdir)
        for table, key in DECISION_KEYS.items():
            old = stored[table].filter(pl.col("season") == s)
            got = compare_table(old, new[table], key)
            res.comparison.add(got)
    n = {t: _in(stored[t], res.seasons).height for t in DECISION_KEYS}
    res.notes.append("rows per table: " + ", ".join(f"{t} {v:,}" for t, v in n.items()))
    return res
    res.notes.append("fold WP/sub-models loaded by version from models/decisions/ (the stored "
                     "fold models, not refit)")  # fmt: skip
    return res


def compare_auto(stored: pl.DataFrame, new: pl.DataFrame, lists: list[str], entity: list[str],
                 rank: str) -> Comparison:  # fmt: skip
    """compare_lists with every float column a number and every other column exact."""
    skip = {*lists, *entity, rank}
    floats = [c for c, t in stored.schema.items() if t.is_float() and c not in skip]
    exact = [c for c in stored.columns if c not in skip and c not in floats]
    return compare_lists(stored, new, lists=lists, entity=entity, rank=rank, numbers=floats,
                         exact=exact)  # fmt: skip


def _hot_seat_targets(ctx: Context, last: int) -> pl.DataFrame:
    """The verified labelled rows of 2002 .. ``last`` with features rebuilt from the warehouse
    (``twm hotseat features``' batch path) into the scratch directory."""
    from datetime import UTC, datetime

    from twm.config import settings
    from twm.modules.hot_seat import backtest as hb
    from twm.modules.hot_seat import features as hf
    from twm.modules.hot_seat import labels as hl
    from twm.modules.hot_seat import production as hp
    from twm.modules.hot_seat.cli import CANDIDATES_CSV, LABELS_CSV

    man = settings().path("manual")
    ctx.progress(f"hot_seat: features {hb.FIRST_SEASON}-{last} from the warehouse")
    feats = hf.features_history(ctx.db, list(range(hb.FIRST_SEASON, last + 1)),
                                now=datetime.now(UTC))  # fmt: skip
    feats = hf.add_interim_flag(feats, hl.read_labels(man / LABELS_CSV),
                                hl.read_candidates(man / CANDIDATES_CSV))  # fmt: skip
    path = ctx.work / "hot_seat_features.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    feats.write_parquet(path)
    return hp.verified_targets(ctx.db, path, man / LABELS_CSV, man / CANDIDATES_CSV)


def hot_seat(ctx: Context) -> ModuleResult:
    """The frozen walk-forward backtest (pin) vs each sampled season's fold refit with the
    production code (:func:`twm.modules.hot_seat.production.fit_live`, the backtest's fold
    harness) on targets rebuilt from the warehouse: probability, rank, drivers, version."""
    import numpy as np

    from twm.modules.hot_seat import production as hp
    from twm.modules.hot_seat import targets as ht
    from twm.modules.hot_seat.models import FEATURES, KEYS

    res = ModuleResult("hot_seat", "pin snapshot predictions.parquet (sha256-checked)",
                       "fold refit per season on targets rebuilt from the warehouse")  # fmt: skip
    _, pin = hp.load_pinned(ctx.season)
    stored = hp.load_snapshot(pin)["predictions"]
    res.seasons = ctx.sample(stored.get_column("season").unique().to_list())
    rows = _hot_seat_targets(ctx, max(res.seasons))
    names = dict(ht.load_coach_names(ctx.db).iter_rows())
    parts = []
    for s in res.seasons:
        fr, train = hp.fit_live(rows, s)
        version = hp.version_record(fr, train, "backtest")["model_version"]
        test = rows.filter(pl.col("season") == s).sort(list(KEYS))
        x = test.select(list(FEATURES))
        with warnings.catch_warnings():  # early folds: no fourth-down grades before 2006
            warnings.filterwarnings("ignore", message="Skipping features without any")
            prob = np.asarray(fr.model.predict(x), dtype=np.float64)
            drv = hp.drivers(fr.model.inner, x)
        parts.append(hp.list_frame(test, prob, [version] * test.height, drv, names))
    new = pl.concat(parts)
    lists, entity = ["season", "snapshot", "week"], ["team", "coach_id"]
    old = _in(stored, res.seasons)
    res.comparison = compare_auto(old, new.select(stored.columns), lists, entity, "rank")
    return res


def _board_dataset(ctx: Context, anchor: str) -> pl.DataFrame:
    """The preseason board dataset rebuilt from the warehouse (``twm board dataset --snapshot
    preseason``' code), the own xFP from the scratch copy of the folds; never written."""
    from twm.config import settings
    from twm.modules.board import dataset as bd
    from twm.modules.board import features as bf
    from twm.modules.board.cli import DEPARTURES_CSV
    from twm.modules.regression_watch import own_xfp as ox

    last = max(bd.complete_seasons(ctx.db))
    ctx.progress(f"board: preseason dataset to {last} from the warehouse (anchor {anchor})")
    pg = ox.history_player_games(ctx.db, last, out_dir=_xfp_copy(ctx), progress=ctx.progress)
    xfp = pg.select("game_id", "gsis_id", pl.col("season").cast(pl.Int32), "xfp")
    deps = bf.read_departures(settings().path("manual") / DEPARTURES_CSV)
    return bd.build_preseason_dataset(ctx.db, xfp_games=xfp, departures=deps, anchor=anchor,
                                      progress=ctx.progress)  # fmt: skip


def board(ctx: Context) -> ModuleResult:
    """The frozen walk-forward boards (pin) vs each sampled board's two folds (Cliff, missed
    time) refit with the production code (:func:`twm.modules.board.production.fit_live`) on
    the preseason dataset rebuilt from the warehouse: chances, ranks, drivers, ECR, versions.
    Also the season's board frozen in the pin re-scored by the pinned models (model check)."""
    import numpy as np

    from twm.modules.board import production as bp
    from twm.modules.board.models import KEYS

    res = ModuleResult("board", "pin snapshot predictions.parquet (sha256-checked)",
                       "both folds refit per board on a rebuilt dataset")  # fmt: skip
    models, spec, pin = bp.load_pinned(ctx.season)
    stored = bp.load_snapshot(pin)["predictions"]
    res.seasons = ctx.sample(stored.get_column("season").unique().to_list())
    df = _board_dataset(ctx, spec["anchor"])
    parts = []
    for b in res.seasons:
        pop = bp.population(df).filter(pl.col("season") == b - 1).sort(list(KEYS))
        pop = bp.ecr_columns(ctx.db, pop, bp.snapshot_as_of(pop))
        scored = {}
        for role in bp.ROLES:
            _, rspec = bp.role_spec(role)
            fr, train = bp.fit_live(df, role, b - 1)
            version = bp.version_record(fr, train, rspec, "backtest")["model_version"]
            x = pop.select(list(rspec.features))
            with warnings.catch_warnings():  # early folds: no NGS / snap / xFP seasons
                warnings.filterwarnings("ignore", message="Skipping features without any")
                prob = np.asarray(fr.model.predict(x), dtype=np.float64)
                scored[role] = (prob, [version] * pop.height, bp.drivers(fr.model.inner, x))
        parts.append(bp.board_frame(pop, scored))
    new = pl.concat(parts).select(stored.columns)
    res.comparison = compare_auto(_in(stored, res.seasons), new, ["season"], ["gsis_id"],
                                  "cliff_rank")  # fmt: skip
    current = bp.load_current(pin)
    if current is not None:
        problems = bp.current_mismatches(models, current)
        res.comparison.flag(len(problems), "; ".join(problems[:2]))
        res.notes.append(
            f"{ctx.season} board frozen in the pin ({current['current_board'].height}"
            f" rows) re-scored by the pinned models: {len(problems)} problems"
        )
    return res


MODULES: dict[str, Callable[[Context], ModuleResult]] = {
    "waiver_radar": waiver_radar, "streamer_k": streamer_k, "streamer_dst": streamer_dst,
    "regression_watch": regression_watch, "decisions": decisions, "hot_seat": hot_seat,
    "board": board,
}  # fmt: skip
ALIASES = {"all": tuple(MODULES), "streamer": ("streamer_k", "streamer_dst")}


def chosen(module: str) -> tuple[str, ...]:
    """The module keys of a ``--module`` value (a key, 'streamer' or 'all')."""
    m = module.strip().lower().replace("-", "_")
    if m in ALIASES:
        return ALIASES[m]
    if m not in MODULES:
        raise ValueError(f"unknown module {module!r}: one of {', '.join([*MODULES, *ALIASES])}")
    return (m,)


def run(ctx: Context, modules: Iterable[str]) -> list[ModuleResult]:
    """Every module's result; a module that cannot be recomputed (a pin or file missing, a
    fold model not on disk) is reported NOT REPRODUCED with the reason, never skipped."""
    from twm import pins

    out = []
    for m in modules:
        try:
            out.append(MODULES[m](ctx))
        except (pins.PinError, OSError, ValueError, LookupError) as e:
            out.append(ModuleResult(m, "?", "?", error=f"{type(e).__name__}: {e}"))
    return out
