"""The frozen coach-tendency history (C10c; docs/coach_tendencies.md "Frozen history").

The scheduled job's warehouse is a partial 2012+ build, so it cannot rebuild 1999-2011: a
direct build there would silently publish 2012+ career lines, persistence and fantasy link.
So every COMPLETED regular season travels in the repository, frozen on the owner's Mac by
``twm coach tendencies-pin`` and pinned in ``config/production_models.yaml`` (key
``coach_tendencies``, ``model: history``, ``backtest.seasons`` = the frozen seasons):

- the **spec** ``artifacts/production_models/coach_tendencies/history-<16 hex>.json`` (the pin's
  file and sha256; the version is a hash of its content): the season it was frozen for (the
  first season NOT in it), the frozen seasons, the module's constants and each table's sha256
  and rows;
- the **snapshot** (zstd Parquet under ``history-<16 hex>/``, each file's sha256 and rows in
  the pin): ``tendency_seasons`` (every completed coach-team-season's wide row: per metric the
  numerator ``<m>_num`` and sample ``<m>_n``, the value, the league value and the percentile;
  games, plays), ``tendency_coaches`` (their dim_coach names: the runner's dim_coach only
  knows 2012+ coaches), ``tendency_persistence`` and ``tendency_fantasy_link`` (the published
  frames of the frozen seasons).

The publish (:func:`frames_from_history`) takes the frozen seasons from the snapshot, builds
only the seasons after them from the warehouse (the season in progress), and derives the
season rows and the career lines (numerators and samples summed, :func:`.season.career`) from
both; persistence and the fantasy link are the pinned frames. :func:`load_pinned` refuses
(:class:`twm.pins.PinError`) when the pin is missing, a file is missing or not the approved
bytes (sha256 checked before reading), the history reaches the season in progress, or the
code's constants differ from the frozen ones.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from twm import pins
from twm.modules.coach_tendencies import build as bd
from twm.modules.coach_tendencies import fantasy, persistence, plays, season

PIN_KEY = MODULE = "coach_tendencies"
MODEL = "history"  # the pin's `model`: frozen counted rows, nothing fitted
SPEC_FORMAT = 1
ARTIFACT_SUBDIR = "coach_tendencies"
TABLES = pins.COACH_TENDENCY_TABLES
SEASONS, COACHES, PERSISTENCE, LINK = TABLES
PUBLISHED = {PERSISTENCE: "coach_tendency_persistence", LINK: "coach_tendency_fantasy_link"}
REPORT_DIR = Path("reports") / "coach_tendencies"


class CoachTendencyPinError(ValueError):
    """The history cannot be frozen (no completed season)."""


def constants() -> dict[str, Any]:
    """The definitions the history was built with (a change means: re-pin)."""
    return {"metrics": list(season.METRICS), "first_season": dict(season.FIRST_SEASON),
            "min_plays_ranked": season.MIN_PLAYS_RANKED,
            "min_sample_ranked": dict(season.MIN_SAMPLE_RANKED),
            "min_plays_pair": persistence.MIN_PLAYS_PAIR, "n_boot": persistence.N_BOOT,
            "seed": persistence.SEED, "max_pair_seconds": plays.MAX_PAIR_SECONDS,
            "clock_min_share": plays.CLOCK_MIN_SHARE,
            "fantasy_seasons": [fantasy.FANTASY_FIRST, fantasy.FANTASY_LAST]}  # fmt: skip


def version_of(content: Mapping[str, Any]) -> str:
    text = json.dumps(dict(content), sort_keys=True, separators=(",", ":"))
    return f"{MODEL}-{hashlib.sha256(text.encode()).hexdigest()[:16]}"


def span(years: list[int]) -> str:
    return f"{years[0]}-{years[-1]}" if years[0] != years[-1] else str(years[0])


@dataclass(frozen=True)
class History:
    """A read (pinned) history: ``season`` it was frozen for, the frozen ``seasons``, its
    ``version`` and the snapshot ``tables`` (:data:`TABLES`)."""

    season: int
    seasons: tuple[int, ...]
    version: str
    tables: dict[str, pl.DataFrame]

    @property
    def last(self) -> int:
        return self.seasons[-1]

    def describe(self) -> str:
        t = self.tables
        return (f"seasons {span(list(self.seasons))}: {t[SEASONS].height:,} coach-team seasons, "
                f"{t[COACHES].height:,} coaches, persistence {t[PERSISTENCE].height} rows, "
                f"fantasy link {t[LINK].height} rows")  # fmt: skip


def freeze(frames: Mapping[str, pl.DataFrame], names: pl.DataFrame, current: int, *,
           first: int = bd.FIRST_SEASON) -> dict[str, pl.DataFrame]:  # fmt: skip
    """The snapshot tables from :func:`build.build`'s frames (built with ``current`` as the
    season in progress) and the warehouse's dim_coach (coach_id, coach_name): every completed
    season, ``first`` to the last before ``current`` without a gap (else refused: a partial
    warehouse, e.g. the runner's 2012+ build, must never be frozen)."""
    wide = frames["coach_wide"].filter(pl.col("season") < current).sort(season.COACH_KEYS)
    years = sorted(int(s) for s in wide.get_column("season").unique().to_list())
    if not years or years != list(range(first, years[-1] + 1)):
        gaps = sorted(set(range(first, years[-1] + 1)) - set(years)) if years else []
        got = f"{span(years)}, missing {gaps}" if years else "none"
        raise CoachTendencyPinError(f"the completed seasons must run from {first} without a gap "
                                    f"(found {got}): build from the full warehouse")  # fmt: skip
    ids = wide.get_column("coach_id").unique()
    coaches = (names.select("coach_id", "coach_name").unique()
               .filter(pl.col("coach_id").is_in(ids.implode())).sort("coach_id"))  # fmt: skip
    if coaches.height != ids.len():
        raise CoachTendencyPinError("some frozen coaches are missing from dim_coach")
    return {SEASONS: wide, COACHES: coaches,
            PERSISTENCE: frames["coach_tendency_persistence"],
            LINK: frames["coach_tendency_fantasy_link"]}  # fmt: skip


def coach_names(history: History, names: pl.DataFrame) -> pl.DataFrame:
    """The warehouse's dim_coach (coach_id, coach_name) plus the frozen coaches it lacks."""
    known = names.select("coach_id", "coach_name")
    extra = history.tables[COACHES].join(known, on="coach_id", how="anti")
    return pl.concat([known, extra.select(known.columns)], how="vertical_relaxed")


def frames_from_history(history: History, runner: plays.Runner,
                        current: int) -> dict[str, pl.DataFrame]:  # fmt: skip
    """The four published frames (:data:`build.FRAME_KEYS`): the frozen seasons from the
    snapshot, the seasons after them up to the newest visible to ``runner`` (and at most
    ``current``) built from the warehouse, the season rows and career lines from both, and
    the pinned persistence and fantasy link."""
    if history.last >= current:
        raise pins.PinError(f"the frozen coach-tendency history ({span(list(history.seasons))})"
                            f" reaches the season in progress {current}")  # fmt: skip
    newest = min(bd.latest_season(runner), current)
    fresh = [bd.season_wide(runner, y, current)[0] for y in range(history.last + 1, newest + 1)]
    cw = pl.concat([history.tables[SEASONS], *fresh], how="vertical_relaxed")
    cw = cw.sort(season.COACH_KEYS)
    return {"coach_tendency_season": season.season_long(cw),
            "coach_tendency_career": season.career(cw),
            "coach_tendency_persistence": history.tables[PERSISTENCE],
            "coach_tendency_fantasy_link": history.tables[LINK]}  # fmt: skip


# --------------------------------------------------------------------------------------
# The reports (reports/coach_tendencies/: what the model card cites, checked by model check)
# --------------------------------------------------------------------------------------

DECIMALS = 6


def report_frames(tables: Mapping[str, pl.DataFrame]) -> dict[str, pl.DataFrame]:
    """seasons (per frozen season: coach-team rows, coaches, offensive snaps), persistence and
    fantasy_link (the pinned frames)."""
    seasons = (tables[SEASONS].group_by("season")
               .agg(pl.len().alias("coach_team_rows"),
                    pl.col("coach_id").n_unique().alias("coaches"),
                    pl.col("plays").sum().alias("plays"))
               .sort("season"))  # fmt: skip
    return {"seasons": seasons,
            "persistence": tables[PERSISTENCE].select(persistence.PERSISTENCE_COLUMNS),
            "fantasy_link": tables[LINK].select(fantasy.LINK_COLUMNS)}  # fmt: skip


def _cell(x: Any) -> str:
    if x is None:
        return "-"
    return f"{x:.{DECIMALS}f}" if isinstance(x, float) else str(x)


def report_markdown(history: History) -> str:
    """reports/coach_tendencies/report.md: the three frames as markdown tables."""
    t = history.tables
    lines = [f"# Coach tendencies: frozen history {history.version}", "",
             f"Regular seasons {span(list(history.seasons))} (frozen for {history.season}): "
             f"{t[SEASONS].height} coach-team seasons, {t[COACHES].height} coaches.",
             ""]  # fmt: skip
    for name, df in report_frames(t).items():
        lines += [f"## {name}", "", "| " + " | ".join(df.columns) + " |",
                  "|" + "---|" * len(df.columns)]  # fmt: skip
        lines += ["| " + " | ".join(_cell(x) for x in row) + " |" for row in df.iter_rows()]
        lines.append("")
    return "\n".join(lines)


def report_texts(history: History) -> dict[str, str]:
    """file name -> exact text of every report file."""
    out = {f"{n}.csv": df.write_csv(float_precision=DECIMALS)
           for n, df in report_frames(history.tables).items()}  # fmt: skip
    return {**out, "report.md": report_markdown(history)}


def write_reports(history: History, directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    out = []
    for name, text in report_texts(history).items():
        (directory / name).write_text(text)
        out.append(directory / name)
    return out


def report_mismatches(history: History, directory: Path) -> list[str]:
    """The report files that are missing or differ from the history ([] = they agree)."""
    out = []
    for name, text in report_texts(history).items():
        path = directory / name
        if not path.exists() or path.read_text() != text:
            out.append(f"{path} is missing or differs from the frozen history")
    return out


# --------------------------------------------------------------------------------------
# Approving (the owner's Mac) and reading (every publish)
# --------------------------------------------------------------------------------------


def artifact_dir(root: Path | None = None) -> Path:
    from twm.config import ROOT

    return (root if root is not None else ROOT) / pins.ARTIFACT_DIR / ARTIFACT_SUBDIR


def _rel(p: Path, base: Path) -> str:
    try:
        return p.relative_to(base).as_posix()
    except ValueError:
        return str(p)


def approve(tables: Mapping[str, pl.DataFrame], current: int, *, root: Path | None = None,
            path: Path | None = None, report_dir: Path | None = None,
            today: datetime | None = None) -> pins.Pin:  # fmt: skip
    """Write the snapshot (``history-<version>/<table>.parquet``), the spec
    (``history-<version>.json``) and the reports, and pin them (other pins keep theirs). Never
    writes over a different file of the same name. Returns the new pin."""
    from twm.config import ROOT

    base = root if root is not None else ROOT
    out = artifact_dir(base)
    stage = out / ".staging"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    files = {}
    for t in TABLES:
        p = stage / f"{t}.parquet"
        tables[t].write_parquet(p, compression="zstd", compression_level=pins.PARQUET_LEVEL,
                                statistics=False)  # fmt: skip
        files[t] = {"sha256": pins.sha256_of(p), "rows": tables[t].height}
    years = sorted(int(s) for s in tables[SEASONS].get_column("season").unique().to_list())
    content = {"format": SPEC_FORMAT, "module": MODULE, "season": int(current),
               "seasons": span(years), "constants": constants(), "tables": files}  # fmt: skip
    version = version_of(content)
    final = out / version
    if final.exists():
        same = all(pins.sha256_of(final / f"{t}.parquet") == files[t]["sha256"] for t in TABLES)
        shutil.rmtree(stage)
        if not same:
            raise CoachTendencyPinError(f"{final} exists with other bytes; not overwritten")
    else:
        stage.rename(final)
    spec = out / f"{version}.json"
    text = json.dumps({**content, "model_version": version}, sort_keys=True, indent=1) + "\n"
    if spec.exists() and spec.read_text() != text:
        raise CoachTendencyPinError(f"{spec} exists with other bytes; not overwritten")
    spec.write_text(text)
    snaps = {t: pins.SnapshotFile(_rel(final / f"{t}.parquet", base), files[t]["sha256"],
                                  files[t]["rows"]) for t in TABLES}  # fmt: skip
    day = (today or datetime.now(UTC)).astimezone(UTC).date().isoformat()
    pin = pins.Pin(PIN_KEY, int(current), version, _rel(spec, base), pins.sha256_of(spec), day,
                   backtest_seasons=span(years), backtest=snaps, model=MODEL)  # fmt: skip
    write_reports(History(int(current), tuple(years), version, dict(tables)),
                  report_dir if report_dir is not None else base / REPORT_DIR)  # fmt: skip
    current_pins = pins.read_pins(path)
    current_pins[PIN_KEY] = pin
    pins.write_pins(current_pins, path)
    return pin


def read_spec(file: Path) -> dict[str, Any]:
    """The spec JSON; its version must equal the hash of the rest (else PinError)."""
    try:
        data = json.loads(file.read_text())
    except (OSError, ValueError) as e:
        raise pins.PinError(f"{file}: unreadable ({e})") from e
    if not isinstance(data, dict) or data.get("format") != SPEC_FORMAT \
            or data.get("module") != MODULE:  # fmt: skip
        raise pins.PinError(f"{file}: not a coach-tendency history (format {SPEC_FORMAT})")
    body = {k: v for k, v in data.items() if k != "model_version"}
    if data.get("model_version") != version_of(body):
        raise pins.PinError(f"{file}: holds {data.get('model_version')}, its content hashes to "
                            f"{version_of(body)}")  # fmt: skip
    return data


def load_pinned(current: int, *, path: Path | None = None,
                root: Path | None = None) -> tuple[History, pins.Pin]:  # fmt: skip
    """(the frozen :class:`History`, its pin) for the season in progress ``current``;
    :class:`twm.pins.PinError` (module docstring) when it cannot be used."""
    every = pins.read_pins(path)
    if PIN_KEY not in every:
        where = path if path is not None else pins.default_pin_path()
        raise pins.PinError(f"no frozen coach-tendency history in {where}: freeze it on "
                            "the Mac (`uv run twm coach tendencies-pin`), commit")  # fmt: skip
    pin = every[PIN_KEY]
    if pin.model != MODEL:
        raise pins.PinError(f"the pin of {PIN_KEY} says model {pin.model!r}, not {MODEL!r}")
    file = pin.path(root)
    if not file.exists():
        raise pins.PinError(f"the approved file is missing: {pin.file}")
    digest = pins.sha256_of(file)
    if digest != pin.sha256:
        raise pins.PinError(f"{pin.file} is not the approved file (sha256 {digest[:12]}..., the "
                            f"pin says {pin.sha256[:12]}...); it was not opened")  # fmt: skip
    spec = read_spec(file)
    want = {t: {"sha256": s.sha256, "rows": s.rows} for t, s in pin.backtest.items()
            if t in TABLES}  # fmt: skip
    if (spec["model_version"], int(spec["season"]), spec["seasons"], spec["tables"]) != \
            (pin.model_version, pin.season, pin.backtest_seasons, want):  # fmt: skip
        raise pins.PinError(f"{pin.file} ({spec['model_version']}) does not match its pin")
    if spec["constants"] != json.loads(json.dumps(constants())):
        raise pins.PinError(f"{pin.model_version} was frozen with other definitions than the "
                            "code's: re-freeze it (`uv run twm coach tendencies-pin`)")  # fmt: skip
    frames = pins.read_snapshot(pin, TABLES, root)
    years = sorted(int(s) for s in frames[SEASONS].get_column("season").unique().to_list())
    first, _, last = pin.backtest_seasons.partition("-")
    if not years or years != list(range(int(first), int(last or first) + 1)) \
            or frames[SEASONS].get_column("is_current").any():  # fmt: skip
        raise pins.PinError(f"{pin.model_version}: its season rows are not {pin.backtest_seasons}")
    history = History(pin.season, tuple(years), pin.model_version, frames)
    if history.last >= int(current):
        raise pins.PinError(f"the frozen coach-tendency history ({pin.backtest_seasons}) "
                            f"reaches the season in progress {current}: re-freeze it")  # fmt: skip
    return history, pin
