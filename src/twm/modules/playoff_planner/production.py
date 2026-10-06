"""The frozen playoff-planner spec (docs/playoff_planner.md, "Frozen and pinned").

Built once on the owner's Mac from the warehouse (``twm playoff_planner build``), approved with
``twm playoff_planner pin`` and committed: one JSON file
``artifacts/production_models/playoff_planner/matchup-<16 hex>.json`` holding the rating rule
(pseudo-games, candidates), the walk-forward backtest of every candidate (test seasons before
the pinned one), the chosen candidate per position (the fixed rule's, unless the pin was
explicitly overridden: ``chosen_by``), the effect sizes, the rating stability and the late-week
table. The pin (config/production_models.yaml, key ``playoff_planner``, ``model: matchup``)
names it with its sha256; the weekly grid checks it before reading and never rebuilds it.
``twm model check playoff_planner`` (and ``all``) also compares it with reports/playoff_planner/.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from twm.modules.playoff_planner import backtest as bt
from twm.modules.playoff_planner import history as hs
from twm.modules.playoff_planner import ratings as rt

MODULE = "playoff_planner"
PIN_KEY = "playoff_planner"
MODEL = "matchup"  # a JSON rating rule, nothing pickled
LABEL = "unit_points_weeks_15_17"
SPEC_FORMAT = 1
ARTIFACT_SUBDIR = "playoff_planner"
DECIMALS = 6
TOLERANCE = 1.01e-6
REPORT_DIR = Path("reports") / "playoff_planner"
REPORTS = ("backtest", "effects", "stability", "late_weeks")
REPORT_COLUMNS = {  # the JSON stores rows with sorted keys: the reports' column order
    "backtest": ("candidate", "position", "horizon", "season", "n", "mae", "chosen"),
    "effects": ("position", "horizon", "n_worst", "n_best", "rated_gap", "shrunk_gap",
                "realized_gap", "survived"),
    "stability": ("position", "horizon", "seasons", "rho_mean", "rho_min", "rho_max"),
    "late_weeks": ("era", "position", "week", "based", "played_share", "vs_base"),
}  # fmt: skip
RULE = ("per position, pooled over the horizons: from 'none', the next candidate (raw, shrunk, "
        "adjusted) replaces the choice only if its pooled points MAE is lower and lower in at "
        f"least {bt.SEASON_WINS_NEEDED} test seasons")  # fmt: skip


class PlayoffPlannerProductionError(ValueError):
    """The spec cannot be built, approved or read."""


def _r(x: Any) -> Any:
    return round(x, DECIMALS) if isinstance(x, float) else x


def records(df: pl.DataFrame) -> list[dict[str, Any]]:
    return [{k: _r(v) for k, v in row.items()} for row in df.iter_rows(named=True)]


@dataclass(frozen=True)
class Built:
    season: int
    tests: tuple[int, ...]
    selection: bt.Selection
    metrics: pl.DataFrame
    effects: pl.DataFrame
    stability: pl.DataFrame
    late: pl.DataFrame
    chosen: dict[str, str]  # the rule's choice unless overridden (content: chosen_by)


def build(
    units: pl.DataFrame,
    totals: pl.DataFrame,
    season: int,
    overrides: Mapping[str, str] | None = None,
) -> Built:
    """Backtest every candidate walk-forward on the test seasons before ``season``, choose per
    position by the fixed rule (``overrides``: position -> candidate, explicit) and measure the
    effect sizes, the stability and the late weeks."""
    for pos, cand in (overrides or {}).items():
        if pos not in hs.POSITIONS or cand not in rt.CANDIDATES:
            raise PlayoffPlannerProductionError(
                f"bad override {pos}={cand}: positions {hs.POSITIONS}, candidates {rt.CANDIDATES}"
            )
    tests = tuple(s for s in bt.TEST_SEASONS if s < season)
    if not tests:
        raise PlayoffPlannerProductionError(f"no test season before {season}")
    u = units.filter(pl.col("season").is_in(tests))
    t = totals.filter(pl.col("season").is_in(tests))
    rows = bt.all_rows(u, t)
    m = bt.metrics(rows)
    sel = bt.select(m)
    chosen = {**sel.chosen, **dict(overrides or {})}
    return Built(int(season), tests, sel, m, bt.effects(rows), bt.stability(t),
                 bt.late_weeks(u), chosen)  # fmt: skip


def content(b: Built, reason: str | None = None) -> dict[str, Any]:
    """The JSON's content (floats with 6 decimals). ``chosen_by`` is 'rule' when the rule's
    choice is used for every position, else ``reason`` (an override must say who decided it
    and why)."""
    overridden = b.chosen != b.selection.chosen
    if overridden and not (reason or "").strip():
        raise PlayoffPlannerProductionError(
            f"{b.chosen} overrides the rule's {b.selection.chosen}: give the reason"
        )
    m = b.metrics.with_columns(
        (pl.col("candidate") == pl.col("position").replace_strict(b.chosen, default=None))
        .fill_null(False).alias("chosen"))  # fmt: skip
    return {
        "format": SPEC_FORMAT, "module": MODULE, "season": b.season,
        "test_seasons": list(b.tests), "horizons": list(bt.HORIZONS),
        "playoff_weeks": list(bt.PLAYOFF_WEEKS), "positions": list(hs.POSITIONS),
        "candidates": list(rt.CANDIDATES), "rule": RULE,
        "season_wins_needed": bt.SEASON_WINS_NEEDED, "min_games": bt.MIN_GAMES,
        "min_base": bt.MIN_BASE, "pseudo_games": dict(rt.PSEUDO_GAMES),
        "rule_choice": dict(b.selection.chosen),
        "path": {k: list(v) for k, v in b.selection.path.items()},
        "chosen": dict(b.chosen), "chosen_by": str(reason).strip() if overridden else "rule",
        "backtest": records(m), "effects": records(b.effects),
        "stability": records(b.stability), "late_weeks": records(b.late),
    }  # fmt: skip


def version_of(c: Mapping[str, Any]) -> str:
    """``matchup-<16 hex>``: :func:`twm.predictions.model_version` of the content."""
    from twm.predictions import model_version

    return model_version(module=MODULE, model=MODEL, label=LABEL, features=["opponent"],
                         params=dict(c), training_seasons=[int(s) for s in c["test_seasons"]],
                         test_season=int(c["season"]), dataset_hash="")  # fmt: skip


def spec_json(c: Mapping[str, Any]) -> str:
    return json.dumps({**c, "model_version": version_of(c)}, sort_keys=True, indent=1) + "\n"


@dataclass(frozen=True)
class Spec:
    """A read (pinned) spec: :meth:`ratings` is all the live grid uses."""

    content: dict[str, Any]

    @property
    def model_version(self) -> str:
        return str(self.content["model_version"])

    @property
    def season(self) -> int:
        return int(self.content["season"])

    @property
    def chosen(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in self.content["chosen"].items()}

    @property
    def pseudo_games(self) -> dict[str, float]:
        return {str(k): float(v) for k, v in self.content["pseudo_games"].items()}

    def ratings(self, totals: pl.DataFrame, through_week: int) -> pl.DataFrame:
        """:func:`twm.modules.playoff_planner.ratings.ratings` with the pinned pseudo-games,
        plus ``candidate`` (the position's chosen one) and ``rating`` (its multiplier: 1.0
        where the chosen candidate is 'none')."""
        r = rt.ratings(totals, through_week, self.pseudo_games)
        ch = self.chosen
        r = r.with_columns(pl.col("position").replace_strict(ch, default="none").alias("candidate"))
        expr = pl.lit(1.0)
        for c in rt.CANDIDATES[1:]:
            expr = pl.when(pl.col("candidate") == c).then(rt.multiplier(c)).otherwise(expr)
        return r.with_columns(expr.alias("rating"))

    def effect(self, position: str) -> dict[str, Any] | None:
        """The position's horizon-pooled effect row (None when absent)."""
        return next((e for e in self.content["effects"] if e["position"] == position
                     and e["horizon"] == bt.ALL), None)  # fmt: skip

    def describe(self) -> str:
        c = self.content
        ch = ", ".join(f"{p} {c['chosen'][p]}" for p in hs.POSITIONS)
        t = c["test_seasons"]
        return (f"chosen {ch} ({c['chosen_by']}); tests {t[0]}-{t[-1]}, horizons "
                f"{c['horizons']}, weeks {c['playoff_weeks']}")  # fmt: skip


def read_spec(path: Path) -> Spec:
    """The JSON file; the version inside must equal the hash of the rest (else refused)."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise PlayoffPlannerProductionError(f"{path}: unreadable ({e})") from e
    if data.get("format") != SPEC_FORMAT or data.get("module") != MODULE:
        raise PlayoffPlannerProductionError(f"{path}: not a {MODULE} spec (format {SPEC_FORMAT})")
    body = {k: v for k, v in data.items() if k != "model_version"}
    if data.get("model_version") != version_of(body):
        raise PlayoffPlannerProductionError(
            f"{path}: holds {data.get('model_version')}, its content hashes to {version_of(body)}"
        )
    return Spec(data)


def report_frames(c: Mapping[str, Any]) -> dict[str, pl.DataFrame]:
    """The committed reports' rows from the JSON content (reports/playoff_planner/*.csv)."""
    return {name: pl.DataFrame(c[name], infer_schema_length=None).select(REPORT_COLUMNS[name])
            for name in REPORTS}  # fmt: skip


def _cell(x: Any) -> str:
    if x is None:
        return "-"
    return f"{x:.{DECIMALS}f}" if isinstance(x, float) else str(x)


def report_markdown(c: Mapping[str, Any]) -> str:
    """reports/playoff_planner/report.md: the CSVs as markdown tables (the model card cites it)."""
    body = {k: v for k, v in c.items() if k != "model_version"}
    ch = ", ".join(f"{p} {c['chosen'][p]} (path {' -> '.join(c['path'][p])})"
                   for p in c["positions"])  # fmt: skip
    k = ", ".join(f"{p} {c['pseudo_games'][p]:g}" for p in c["positions"])
    lines = [f"# Playoff planner: {version_of(body)}", "",
             f"Chosen ({c['chosen_by']}): {ch}. Test seasons {c['test_seasons'][0]}-"
             f"{c['test_seasons'][-1]}; horizons {c['horizons']}; weeks {c['playoff_weeks']}; "
             f"pseudo-games {k}. Rule: {c['rule']}.", ""]  # fmt: skip
    for name, df in report_frames(c).items():
        lines += [f"## {name}", "", "| " + " | ".join(df.columns) + " |",
                  "|" + "---|" * len(df.columns)]  # fmt: skip
        lines += ["| " + " | ".join(_cell(x) for x in row) + " |" for row in df.iter_rows()]
        lines.append("")
    return "\n".join(lines)


def write_reports(c: Mapping[str, Any], directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    out = []
    for name, df in report_frames(c).items():
        path = directory / f"{name}.csv"
        df.write_csv(path, float_precision=DECIMALS)
        out.append(path)
    md = directory / "report.md"
    md.write_text(report_markdown(c))
    return [*out, md]


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None or a == "" or b == "":
        return (a is None or a == "") and (b is None or b == "")
    if isinstance(a, bool) or isinstance(b, bool):
        return str(a).lower() == str(b).lower()
    try:
        return abs(float(a) - float(b)) <= TOLERANCE
    except (TypeError, ValueError):
        return str(a) == str(b)


def report_mismatches(c: Mapping[str, Any], directory: Path) -> list[str]:
    """Where the JSON disagrees with the committed CSVs and report.md ([] = they agree)."""
    problems = []
    md = directory / "report.md"
    if not md.exists() or md.read_text() != report_markdown(c):
        problems.append(f"{md} is missing or differs from the spec")
    for name, want in report_frames(c).items():
        path = directory / f"{name}.csv"
        if not path.exists():
            problems.append(f"{path} is missing")
            continue
        got = pl.read_csv(path, infer_schema_length=0)
        if got.columns != want.columns or got.height != want.height:
            problems.append(f"{path}: {got.height} rows {got.columns}, the spec has "
                            f"{want.height} rows {want.columns}")  # fmt: skip
            continue
        for i, (g, w) in enumerate(zip(got.iter_rows(), want.iter_rows(), strict=True)):
            bad = [col for col, x, y in zip(want.columns, g, w, strict=True) if not _same(x, y)]
            if bad:
                problems.append(f"{path} row {i + 1}: {', '.join(bad)} differ")
    return problems


def artifact_dir(root: Path | None = None) -> Path:
    from twm import pins
    from twm.config import ROOT

    return (root if root is not None else ROOT) / pins.ARTIFACT_DIR / ARTIFACT_SUBDIR


def approve(c: Mapping[str, Any], *, report_dir: Path, root: Path | None = None,
            path: Path | None = None, today: Any = None) -> Any:  # fmt: skip
    """Refuse unless the content reproduces ``report_dir``'s reports; write
    ``artifacts/production_models/playoff_planner/<version>.json`` (never over a different
    file of the same name) and pin it (other pins keep theirs). Returns the new pin."""
    from datetime import UTC, datetime

    from twm import pins
    from twm.config import ROOT

    problems = report_mismatches(c, report_dir)
    if problems:
        raise PlayoffPlannerProductionError(
            f"the spec disagrees with {report_dir} in {len(problems)} places (e.g. "
            f"{problems[0]}): run `uv run twm playoff_planner build` on the same warehouse first"
        )
    base = root if root is not None else ROOT
    text, version = spec_json(c), version_of(c)
    target = artifact_dir(base) / f"{version}.json"
    if target.exists() and target.read_text() != text:
        raise PlayoffPlannerProductionError(f"{target} exists with other bytes; not overwritten")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text)
        tmp.replace(target)
    try:
        rel = target.relative_to(base).as_posix()
    except ValueError:
        rel = str(target)
    day = (today or datetime.now(UTC)).astimezone(UTC).date().isoformat()
    pin = pins.Pin(PIN_KEY, int(c["season"]), version, rel, pins.sha256_of(target), day,
                   backtest_seasons=f"{c['test_seasons'][0]}-{c['test_seasons'][-1]}",
                   model=MODEL)  # fmt: skip
    current = pins.read_pins(path)
    current[PIN_KEY] = pin
    pins.write_pins(current, path)
    return pin


def load_pinned(season: int, *, path: Path | None = None, root: Path | None = None):
    """(the approved :class:`Spec`, its pin); :class:`twm.pins.PinError` when the pin is
    missing, is not a matchup pin, scores another season, or its file is missing, not the
    approved bytes (sha256, checked before the file is read) or another version."""
    from twm import pins

    pin = pins.get_pin(PIN_KEY, path)
    if pin.model != MODEL:
        raise pins.PinError(f"the pin of {PIN_KEY} says model {pin.model!r}, not {MODEL!r}")
    if pin.season != int(season):
        raise pins.PinError(
            f"the approved {PIN_KEY} spec scores season {pin.season}, not {season}: build and "
            "approve it for this season (`uv run twm playoff_planner pin`), review and commit"
        )
    file = pin.path(root)
    if not file.exists():
        raise pins.PinError(f"the approved file is missing: {pin.file}")
    digest = pins.sha256_of(file)
    if digest != pin.sha256:
        raise pins.PinError(f"{pin.file} is not the approved file (sha256 {digest[:12]}..., the "
                            f"pin says {pin.sha256[:12]}...); it was not opened")  # fmt: skip
    try:
        spec = read_spec(file)
    except PlayoffPlannerProductionError as e:
        raise pins.PinError(str(e)) from e
    if spec.model_version != pin.model_version or spec.season != int(season):
        raise pins.PinError(f"{pin.file} holds {spec.model_version}, the pin says "
                            f"{pin.model_version}")  # fmt: skip
    return spec, pin
