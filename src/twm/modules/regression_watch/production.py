"""Regression Watch step D4a: the owner-approved, frozen configuration of the weekly list.

The weekly list (:mod:`twm.modules.regression_watch.weekly`) never estimates anything. It reads
ONE approved parameters file, ``artifacts/production_models/regression_watch/<version>.json``,
pinned with its sha256 in ``config/production_models.yaml`` (``regression_watch``,
``model: params``, like the streamer's D/ST rule), and checked before it is opened. The file
holds, for one season S:

- the **variant** D3's rule picks for S (lowest pooled validation MAE over the headline weeks
  of 2010 .. S-1, :func:`twm.modules.regression_watch.backtest.choose`);
- its **shrinkage table** (D2's signal and noise variances and prior mean per position, with
  and without garbage time), estimated on 2009 .. S-1 only, and the garbage-time scale;
- the **Sell-high / Buy-low X** chosen with that variant on the same validation seasons;
- the **record** of the last backtest season (S-1): the variant, validation MAE and the two X
  the same run picks for S-1. They must equal the committed ``reports/regression_watch/
  backtest.csv`` rows of S-1 (:func:`record_mismatches`), so the approved file and the
  published backtest come from the same code and data (``twm model check`` verifies it).

:func:`build_params` recomputes it from the warehouse (about 10 s; only `twm regression pin`
does); :func:`approve` writes the file and the pin; :func:`load_pinned_params` loads it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import polars as pl

from twm.modules.regression_watch import backtest as bt
from twm.modules.regression_watch import projection as pj
from twm.modules.regression_watch import tags as tg

MODULE = "regression_watch"
PIN_KEY = "regression_watch"
MODEL = "params"  # the pin's `model`: a frozen parameters file, nothing pickled
LABEL = "ppg_ros"  # what the score is: projected points per game over the rest of the season
PARAMS_FORMAT = 2  # 2 (H6-b2): the expected-points source is part of the parameters
ARTIFACT_SUBDIR = "regression_watch"
TOLERANCE = 1.01e-6  # the backtest CSV prints 6 decimals
# The compact shrinkage table: one row per (position, metric); r(g) is computed from it.
SHRINK_SCHEMA: dict[str, pl.DataType] = {
    "position": pl.String(), "metric": pl.String(), "var_signal": pl.Float64(),
    "var_noise": pl.Float64(), "prior_mean": pl.Float64(), "n": pl.Int64(),
    "first_season": pl.Int64(), "last_season": pl.Int64(),
}  # fmt: skip
FEATURES = ("ppg", "xfp_pg", "fpoe_pg", "g_opp", "rw_xfp", "fpoe_ng_pg")
XFP_TITLES = {"own": "own walk-forward, step H6-b2", "ffopportunity": "ffopportunity, nflverse"}

Progress = Callable[[str], None]


class RegressionProductionError(ValueError):
    """The frozen parameters cannot be built, loaded or approved as asked."""


def _threshold(th: tg.Threshold) -> dict[str, Any]:
    return {"x": th.x, "precision": th.precision, "n_tags": th.n_tags, "n_asofs": th.n_asofs,
            "fallback": th.fallback}  # fmt: skip


def _choice(ch: bt.Choice) -> dict[str, Any]:
    """What D3's rule chose for ``ch.season`` on its validation seasons (JSON-ready)."""
    return {
        "season": ch.season, "variant": ch.variant.name,
        "validation_seasons": list(ch.validation_seasons), "validation_mae": ch.val_mae,
        "spec_validation_mae": ch.spec_val_mae, "n_validation_rows": ch.n_val,
        "sell_high": _threshold(ch.sell), "buy_low": _threshold(ch.buy),
    }  # fmt: skip


@dataclass(frozen=True)
class ProductionParams:
    """The frozen configuration of season ``season``'s weekly list (see the module docstring)."""

    model_version: str
    season: int
    variant: pj.Variant
    x_sell: float
    x_buy: float
    shrinkage: pl.DataFrame  # SHRINK_SCHEMA, sorted by position, metric
    shrinkage_seasons: tuple[int, ...]
    ng_scale: dict[str, float]
    choice: dict[str, Any]  # _choice() of ``season`` (the production choice)
    record: dict[str, Any]  # _choice() of ``season - 1`` (checked against the backtest CSV)
    xfp_source: str  # 'own' or 'ffopportunity' (.xfp_source): what the frame's xFP was

    def priors(self) -> pj.Priors:
        """The projection's :class:`~twm.modules.regression_watch.projection.Priors`, rebuilt
        from the frozen table (nothing is estimated)."""
        return pj.Priors(self.shrinkage_seasons, self.shrinkage, dict(self.ng_scale))

    def describe(self) -> str:
        s = self.shrinkage_seasons
        return (
            f"{self.model_version}: variant {self.variant.name} ({self.variant.describe()}); "
            f"Sell-high X {self.x_sell:g}, Buy-low X {self.x_buy:g} points/game; shrinkage "
            f"estimated on {s[0]}-{s[-1]}; chosen on {_span(self.choice['validation_seasons'])}; "
            f"xFP: {XFP_TITLES[self.xfp_source]}"
        )

    def content(self) -> dict[str, Any]:
        """Everything that determines the list (what the version hashes)."""
        v = self.variant
        return {
            "variant": {"name": v.name, "target": v.target, "half_life": v.half_life,
                        "garbage": v.garbage},
            "x_sell": self.x_sell, "x_buy": self.x_buy,
            "shrinkage": {"seasons": list(self.shrinkage_seasons),
                          "rows": self.shrinkage.to_dicts()},
            "ng_scale": {k: self.ng_scale[k] for k in sorted(self.ng_scale)},
            "decile": tg.DECILE, "min_games": pj.MIN_GAMES, "xfp_source": self.xfp_source,
        }  # fmt: skip

    def definition(self) -> dict[str, Any]:
        """The file's content."""
        return {
            "format": PARAMS_FORMAT, "model": MODEL, "module": MODULE, "label": LABEL,
            "season": self.season, "model_version": self.model_version, **self.content(),
            "description": self.variant.describe(), "choice": self.choice,
            "record": self.record,
        }  # fmt: skip


def _span(seasons: Sequence[int]) -> str:
    return f"{min(seasons)}-{max(seasons)}" if seasons else "-"


def version_of(season: int, content: dict[str, Any]) -> str:
    """``<variant>-<16 hex>`` (:func:`twm.predictions.model_version` of the content)."""
    from twm.predictions import model_version

    return model_version(
        module=MODULE, model=content["variant"]["name"], label=LABEL, features=FEATURES,
        params=content, training_seasons=content["shrinkage"]["seasons"],
        test_season=int(season), dataset_hash="",
    )  # fmt: skip


def params_json(params: ProductionParams) -> str:
    """The file's exact text (sorted keys: the same parameters always give the same bytes)."""
    return json.dumps(params.definition(), sort_keys=True, indent=2) + "\n"


def compact_shrinkage(table: pl.DataFrame) -> pl.DataFrame:
    """D2's shrinkage table (one row per position, metric and g) -> one row per position and
    metric (r(g) depends only on the two variances). Refuses a table whose variances vary
    with g."""
    keys = ["position", "metric"]
    cols = [c for c in SHRINK_SCHEMA if c not in keys]
    varying = table.group_by(keys).agg(pl.col(c).n_unique() for c in cols)
    if varying.select(pl.max_horizontal(cols)).max().item() > 1:
        raise RegressionProductionError("the shrinkage table's variances vary with g")
    return (
        table.group_by(keys)
        .agg(pl.col(c).first() for c in cols)
        .select(list(SHRINK_SCHEMA))
        .cast(SHRINK_SCHEMA)
        .sort(keys)
    )


def make_params(
    season: int, choice: bt.Choice, record: bt.Choice, priors: pj.Priors, xfp_source: str
) -> ProductionParams:
    """The frozen parameters of ``season`` from D3's choice for it, its priors (estimated on
    the seasons before it) and the choice for the last backtest season (``record``), all made
    from a frame whose xFP came from ``xfp_source``."""
    from twm.modules.regression_watch import xfp_source as xs

    shrink = compact_shrinkage(priors.table)
    stub = ProductionParams("", int(season), choice.variant, float(choice.sell.x),
                            float(choice.buy.x), shrink, tuple(int(s) for s in priors.seasons),
                            {k: float(v) for k, v in priors.ng_scale.items()}, _choice(choice),
                            _choice(record), xs.check(xfp_source))  # fmt: skip
    return replace(stub, model_version=version_of(season, stub.content()))


def from_definition(d: dict[str, Any]) -> ProductionParams:
    """The parameters a file's JSON describes (its version is recomputed from the content)."""
    v = d["variant"]
    variant = pj.Variant(str(v["target"]), v["half_life"], str(v["garbage"]))
    if variant.name != v["name"] or variant not in pj.VARIANTS:
        raise RegressionProductionError(f"unknown variant {v['name']!r}")
    shrink = pl.DataFrame(d["shrinkage"]["rows"], schema=SHRINK_SCHEMA, orient="row")
    stub = ProductionParams("", int(d["season"]), variant, float(d["x_sell"]), float(d["x_buy"]),
                            shrink, tuple(int(s) for s in d["shrinkage"]["seasons"]),
                            {k: float(x) for k, x in d["ng_scale"].items()}, d["choice"],
                            d["record"], str(d["xfp_source"]))  # fmt: skip
    if stub.xfp_source not in XFP_TITLES:
        raise RegressionProductionError(f"unknown xFP source {stub.xfp_source!r}")
    return replace(stub, model_version=version_of(stub.season, stub.content()))


def load_params(path: Path) -> ProductionParams:
    """Read a parameters file and check it is exactly what the code would write for its
    content (format, module, version = the hash of its content, canonical text)."""
    if not path.exists():
        raise RegressionProductionError(f"parameters file not found: {path}")
    text = path.read_text()
    try:
        d = json.loads(text)
    except ValueError as e:
        raise RegressionProductionError(f"{path} is not a parameters file: {e}") from e
    if not isinstance(d, dict) or d.get("format") != PARAMS_FORMAT or d.get("model") != MODEL \
            or d.get("module") != MODULE:  # fmt: skip
        raise RegressionProductionError(f"{path} is not a Regression Watch parameters file")
    try:
        params = from_definition(d)
    except (KeyError, TypeError, ValueError) as e:
        raise RegressionProductionError(f"{path}: {e}") from e
    if params.model_version != d.get("model_version") or params_json(params) != text:
        raise RegressionProductionError(
            f"{path} does not hash to the version it names ({d.get('model_version')}; its "
            f"content is {params.model_version}): it was edited by hand"
        )
    return params


def build_params(
    db: Path | str, season: int, league: Any, *, xfp_source: str,
    progress: Progress | None = None,
) -> ProductionParams:  # fmt: skip
    """Recompute the parameters of ``season`` from the warehouse exactly as D3's walk-forward
    does for a test season: the universe rows of 2010 .. season-1 at the headline as-ofs, the
    variant and X chosen on them, the priors estimated on 2009 .. season-1, and the same choice
    for season-1 (the record), with the xFP of ``xfp_source`` (own: the walk-forward history,
    each season from its own fold). Only approving (`twm regression pin`) calls this."""
    from twm.modules.regression_watch import xfp_source as xs
    from twm.modules.regression_watch.player_week import player_games_history

    if season - 1 < bt.FIRST_TEST_SEASON:
        raise RegressionProductionError(f"season {season}: no earlier backtest season to check")
    earlier = list(range(pj.FIRST_DATA_SEASON, season))
    xfp = xs.history(db, season - 1, xfp_source, progress=progress)
    history = player_games_history(db, earlier, xfp=xfp)
    asofs = bt.asof_table(db, earlier, bt.HEADLINE_WEEKS)
    val = list(range(bt.FIRST_VALIDATION_SEASON, season))
    rows, _ = bt.build_rows(history, asofs, league, val, progress=progress)
    priors = pj.estimate_priors(history, earlier)
    return make_params(season, bt.choose(rows, season), bt.choose(rows, season - 1), priors,
                       xfp_source)  # fmt: skip


def record_rows(record: dict[str, Any]) -> list[dict[str, str]]:
    """The backtest CSV rows (reports/regression_watch/backtest.csv, table 'choice' and
    'threshold') that ``record`` must reproduce, formatted like the report writes them."""

    def r6(x: Any) -> str:
        return "" if x is None else str(round(float(x), 6))

    s = str(record["season"])
    val = ",".join(map(str, record["validation_seasons"]))
    out = [{"table": "choice", "season": s, "method": record["variant"],
            "metric": "validation_mae", "value": r6(record["validation_mae"]),
            "n": str(record["n_validation_rows"]),
            "detail": f"validation seasons {val}; spec formula "
                      f"{r6(record['spec_validation_mae'])}"}]  # fmt: skip
    for tag in ("sell_high", "buy_low"):
        th = record[tag]
        per = round(th["n_tags"] / th["n_asofs"], 3) if th["n_asofs"] else None
        out.append({"table": "threshold", "season": s, "metric": tag, "value": str(th["x"]),
                    "group": "fallback" if th["fallback"] else "chosen",
                    "n": str(th["n_tags"]), "per_asof": "" if per is None else str(per),
                    "detail": f"validation precision {r6(th['precision'])}"})  # fmt: skip
    return out


def record_mismatches(params: ProductionParams, csv_path: Path) -> list[str]:
    """Where the parameters' record of the last backtest season disagrees with the committed
    backtest CSV ([] = the file and the published backtest come from the same run)."""
    import csv

    if not csv_path.exists():
        return [f"the backtest report is missing: {csv_path}"]
    with csv_path.open(newline="", encoding="utf-8") as f:
        have = [r for r in csv.DictReader(f) if r["table"] in ("choice", "threshold")]
    problems = []
    for want in record_rows(params.record):
        key = (want["table"], want["season"], want["metric"])
        hit = [r for r in have if (r["table"], r["season"], r["metric"]) == key]
        what = " ".join(key)
        if len(hit) != 1:
            problems.append(f"{what}: not in the report")
            continue
        for col, w in want.items():
            g = hit[0][col]
            if col in ("value", "per_asof") and g and w:
                if abs(float(g) - float(w)) > TOLERANCE:
                    problems.append(f"{what}: {col} {w} in the parameters, {g} in the report")
            elif g != w:
                problems.append(f"{what}: {col} {w!r} in the parameters, {g!r} in the report")
    return problems


# --------------------------------------------------------------------------------------
# The pin: checked before the file is opened
# --------------------------------------------------------------------------------------


def _rel(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return str(path)


def artifact_dir(root: Path | None = None) -> Path:
    from twm import pins
    from twm.config import ROOT

    return (root if root is not None else ROOT) / pins.ARTIFACT_DIR / ARTIFACT_SUBDIR


def load_pinned_params(season: int, *, path: Path | None = None, root: Path | None = None):
    """(the approved parameters, their pin) for ``season``; :class:`twm.pins.PinError` when the
    pin is missing, is not a parameters pin, scores another season, or its file is missing,
    not the approved bytes (sha256, checked before the file is read) or another version."""
    from twm import pins

    pin = pins.get_pin(PIN_KEY, path)
    if pin.model != MODEL:
        raise pins.PinError(f"the pin of {PIN_KEY} says model {pin.model!r}, not {MODEL!r}")
    if pin.season != int(season):
        raise pins.PinError(
            f"the approved {PIN_KEY} parameters score season {pin.season}, not {season}: "
            "approve them for this season (`uv run twm regression pin`), review and commit"
        )
    file = pin.path(root)
    if not file.exists():
        raise pins.PinError(f"the approved file is missing: {pin.file}")
    digest = pins.sha256_of(file)
    if digest != pin.sha256:
        raise pins.PinError(
            f"{pin.file} is not the approved file (sha256 {digest[:12]}..., the pin says "
            f"{pin.sha256[:12]}...); it was not opened"
        )
    try:
        params = load_params(file)
    except RegressionProductionError as e:
        raise pins.PinError(str(e)) from e
    if params.model_version != pin.model_version or params.season != int(season):
        raise pins.PinError(f"{pin.file} holds {params.model_version}, the pin says "
                            f"{pin.model_version}")  # fmt: skip
    _check_xfp_pin(pin, params, root)
    return params, pin


def _check_xfp_pin(pin: Any, params: ProductionParams, root: Path | None) -> None:
    """The pin's ``xfp`` entry agrees with the parameters' source; for 'own', the live fold is
    the pinned season and every model file exists with the pinned sha256 (not opened here)."""
    from twm import pins
    from twm.modules.regression_watch import own_xfp as ox

    xp = pin.xfp
    if xp is None:
        raise pins.PinError(f"the pin of {PIN_KEY} names no xFP source (`xfp:`): approve with "
                            "`uv run twm regression pin --xfp own`, review and commit")  # fmt: skip
    if xp.source != params.xfp_source:
        raise pins.PinError(f"the pin's xFP source is {xp.source!r}, the parameters were made "
                            f"with {params.xfp_source!r}")  # fmt: skip
    if xp.source != "own":
        return
    if xp.fold != params.season or set(xp.models) != set(ox.COMPONENT_BY_NAME):
        raise pins.PinError(f"the pinned own xFP must be the {params.season} fold with the "
                            f"models {sorted(ox.COMPONENT_BY_NAME)}")  # fmt: skip
    for name, m in xp.models.items():
        file = m.path(root)
        if not file.exists():
            raise pins.PinError(f"the approved own xFP model is missing: {m.file}")
        digest = pins.sha256_of(file)
        if digest != m.sha256:
            raise pins.PinError(f"{m.file} is not the approved {name} model (sha256 "
                                f"{digest[:12]}..., the pin says {m.sha256[:12]}...); it was "
                                "not opened")  # fmt: skip


def load_pinned_xfp(season: int, *, path: Path | None = None, root: Path | None = None):
    """(the live own xFP models or None for 'ffopportunity', the parameters, their pin): the
    pin and the parameters as :func:`load_pinned_params` checks them, then every model file
    opened only after its sha256 matched. Nothing is fit."""
    from twm import pins
    from twm.modules.regression_watch import own_xfp as ox

    params, pin = load_pinned_params(season, path=path, root=root)
    if pin.xfp.source != "own":
        return None, params, pin
    files = {name: m.path(root) for name, m in pin.xfp.models.items()}
    try:
        live = ox.load_live(files, version=pin.xfp.version, fold=int(season))
    except (ox.LiveModelError, OSError, ValueError) as e:
        raise pins.PinError(str(e)) from e
    return live, params, pin


def version_frame(params: ProductionParams, *, created_at: Any = None) -> pl.DataFrame:
    """The ``model_versions`` row of the parameters (the store's record of what made a list)."""
    from twm import predictions as pr

    notes = {"kind": "production params", "choice": params.choice,
             "description": params.variant.describe()}  # fmt: skip
    return pl.DataFrame([{
        "model_version": params.model_version, "module": MODULE, "model": params.variant.name,
        "label": LABEL, "feature_list": json.dumps(list(FEATURES)),
        "params": json.dumps(params.content(), sort_keys=True),
        "training_seasons": json.dumps(list(params.shrinkage_seasons)),
        "test_season": params.season, "dataset_hash": "", "code_version": pr.code_version(),
        "notes": json.dumps(notes, sort_keys=True),
        "created_at": created_at if created_at is not None else pr.now_utc(),
    }], schema={"model_version": pl.String, "module": pl.String, "model": pl.String,
                "label": pl.String, "feature_list": pl.String, "params": pl.String,
                "training_seasons": pl.String, "test_season": pl.Int32, "dataset_hash": pl.String,
                "code_version": pl.String, "notes": pl.String,
                "created_at": pl.Datetime("us")})  # fmt: skip


def approve(
    params: ProductionParams,
    *,
    csv_path: Path,
    root: Path | None = None,
    path: Path | None = None,
    today: Any = None,
    live: Any = None,
) -> Any:
    """Approve ``params`` (from :func:`build_params`): refuse unless its record reproduces
    ``csv_path`` (reports/regression_watch/backtest.csv), write
    ``artifacts/production_models/regression_watch/<version>.json`` and pin it in
    config/production_models.yaml (other pins keep theirs) with its xFP source; for 'own',
    ``live`` (:func:`.own_xfp.fit_live` of the season) is written as
    ``artifacts/production_models/regression_watch/<live version>/<component>.joblib`` and
    every file pinned with its sha256. Returns the new pin. Review, then commit the files, the
    pin and the reports together."""
    from datetime import UTC, datetime

    from twm import pins
    from twm.config import ROOT

    problems = record_mismatches(params, csv_path)
    if problems:
        raise RegressionProductionError(
            f"the parameters disagree with {csv_path} in {len(problems)} places (e.g. "
            f"{problems[0]}): run `uv run twm regression backtest` on the same warehouse so "
            "the report and the parameters agree"
        )
    if params.xfp_source == "own" and (live is None or live.fold != params.season):
        raise RegressionProductionError(
            f"own xFP parameters for {params.season} need the live models of that season"
        )
    base = root if root is not None else ROOT
    target = artifact_dir(base) / f"{params.model_version}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(params_json(params))
    tmp.replace(target)
    try:
        rel = target.relative_to(base).as_posix()
    except ValueError:
        rel = str(target)
    day = (today or datetime.now(UTC)).astimezone(UTC).date().isoformat()
    xfp = pins.XfpPin(params.xfp_source)
    if params.xfp_source == "own":
        from twm.modules.regression_watch import own_xfp as ox

        files = ox.save_live(live, artifact_dir(base))
        seasons = live.train_seasons
        xfp = pins.XfpPin(
            "own",
            live.fold,
            live.version,
            f"{seasons[0]}-{seasons[-1]}",
            {n: pins.ModelFile(_rel(p, base), pins.sha256_of(p)) for n, p in files.items()},
        )
    pin = pins.Pin(PIN_KEY, params.season, params.model_version, rel, pins.sha256_of(target),
                   day, model=MODEL, xfp=xfp)  # fmt: skip
    current = pins.read_pins(path)
    current[PIN_KEY] = pin
    pins.write_pins(current, path)
    return pin
